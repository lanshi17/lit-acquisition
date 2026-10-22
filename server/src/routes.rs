//! HTTP routes exposed by the service.

use std::{
    sync::Arc,
    time::{Duration, Instant},
};

use axum::{
    body::Body,
    extract::{rejection::JsonRejection, DefaultBodyLimit, Request, State},
    http::{header, StatusCode},
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use serde::Deserialize;
use serde_json::{json, Value};
use tokio::sync::OnceCell;
use tower_http::trace::TraceLayer;
use uuid::Uuid;

use crate::{bridge::WorkerPool, config::Config, error::AppError};

const MAX_QUERY_CHARS: usize = 512;
const MAX_LIMIT: u32 = 50;
const VALID_LANGUAGES: [&str; 2] = ["auto", "multilingual"];
const VALID_LITERATURE_TYPES: [&str; 3] = ["case_report", "sequencing", "functional"];

/// Shared handler state.
#[derive(Clone)]
pub struct AppState {
    pub cfg: Arc<Config>,
    pub pool: Arc<WorkerPool>,
    pub started_at: Instant,
    pub python_info: Arc<OnceCell<Value>>,
}

impl AppState {
    pub fn new(cfg: Arc<Config>, pool: Arc<WorkerPool>) -> Self {
        Self {
            cfg,
            pool,
            started_at: Instant::now(),
            python_info: Arc::new(OnceCell::new()),
        }
    }
}

/// Build the service router.
pub fn router(state: AppState) -> Router {
    let max_body_bytes = state.cfg.max_body_bytes;
    Router::new()
        .route("/", get(index))
        .route("/healthz", get(healthz))
        .route("/readyz", get(readyz))
        .route("/v1/version", get(version))
        .route("/v1/search", post(search))
        .layer(DefaultBodyLimit::max(max_body_bytes))
        .layer(middleware::from_fn_with_state(
            state.clone(),
            require_api_key,
        ))
        .layer(TraceLayer::new_for_http())
        .with_state(state)
}

async fn index() -> Json<Value> {
    Json(json!({
        "service": "lit-acquisition",
        "version": env!("CARGO_PKG_VERSION"),
        "endpoints": {
            "GET /healthz": "liveness probe (never touches the worker pool)",
            "GET /readyz": "readiness probe (round-trips a ping through the pool)",
            "GET /v1/version": "service and Python package versions",
            "POST /v1/search": "search literature across the configured providers"
        }
    }))
}

async fn healthz(State(state): State<AppState>) -> Json<Value> {
    Json(json!({
        "status": "ok",
        "service": "lit-acquisition-server",
        "version": env!("CARGO_PKG_VERSION"),
        "uptime_seconds": state.started_at.elapsed().as_secs(),
    }))
}

async fn readyz(State(state): State<AppState>) -> Response {
    match state.pool.ping().await {
        Ok(_) => (
            StatusCode::OK,
            Json(json!({
                "status": "ready",
                "workers": state.pool.worker_count(),
            })),
        )
            .into_response(),
        Err(error) => (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({
                "status": "not_ready",
                "workers": state.pool.worker_count(),
                "error": error.to_string(),
            })),
        )
            .into_response(),
    }
}

async fn version(State(state): State<AppState>) -> Json<Value> {
    let python = match state.python_info.get() {
        Some(info) => info.clone(),
        None => match state
            .pool
            .call("version", json!({}), Duration::from_secs(5))
            .await
        {
            Ok(info) => {
                let _ = state.python_info.set(info.clone());
                info
            }
            Err(error) => json!({ "available": false, "error": error.to_string() }),
        },
    };

    Json(json!({
        "service": "lit-acquisition-server",
        "version": env!("CARGO_PKG_VERSION"),
        "python": python,
    }))
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct SearchRequest {
    query: String,
    #[serde(default)]
    limit: Option<u32>,
    #[serde(default)]
    language: Option<String>,
    #[serde(default)]
    provider: Option<String>,
    #[serde(default)]
    literature_types: Option<Vec<String>>,
}

#[derive(Debug)]
struct ValidatedSearch {
    query: String,
    limit: u32,
    language: String,
    provider: Option<String>,
    literature_types: Vec<String>,
}

impl SearchRequest {
    fn validate(self) -> Result<ValidatedSearch, AppError> {
        let query = self.query.trim().to_string();
        if query.is_empty() {
            return Err(AppError::bad_request(
                "invalid_request",
                "query must be a non-empty string",
            ));
        }
        if query.chars().count() > MAX_QUERY_CHARS {
            return Err(AppError::bad_request(
                "invalid_request",
                format!("query must be at most {MAX_QUERY_CHARS} characters"),
            ));
        }

        let limit = self.limit.unwrap_or(10);
        if !(1..=MAX_LIMIT).contains(&limit) {
            return Err(AppError::bad_request(
                "invalid_request",
                format!("limit must be between 1 and {MAX_LIMIT}"),
            ));
        }

        let language = self
            .language
            .map(|value| value.trim().to_ascii_lowercase())
            .filter(|value| !value.is_empty())
            .unwrap_or_else(|| "auto".to_string());
        if !VALID_LANGUAGES.contains(&language.as_str()) {
            return Err(AppError::bad_request(
                "invalid_request",
                "language must be one of: auto, multilingual",
            ));
        }

        let provider = self
            .provider
            .map(|value| value.trim().to_ascii_lowercase())
            .filter(|value| !value.is_empty());
        if let Some(name) = &provider {
            if name.len() > 64 || !name.chars().all(|c| c.is_ascii_alphanumeric() || c == '_') {
                return Err(AppError::bad_request(
                    "invalid_request",
                    "provider must match [a-z0-9_]{1,64}",
                ));
            }
        }

        let mut literature_types = Vec::new();
        for raw in self.literature_types.unwrap_or_default() {
            let value = raw.trim().to_ascii_lowercase();
            if !VALID_LITERATURE_TYPES.contains(&value.as_str()) {
                return Err(AppError::bad_request(
                    "invalid_request",
                    format!(
                        "unsupported literature_type {value:?}; expected one of case_report, sequencing, functional"
                    ),
                ));
            }
            if !literature_types.contains(&value) {
                literature_types.push(value);
            }
        }

        Ok(ValidatedSearch {
            query,
            limit,
            language,
            provider,
            literature_types,
        })
    }
}

async fn search(
    State(state): State<AppState>,
    body: Result<Json<SearchRequest>, JsonRejection>,
) -> Result<Response, AppError> {
    let Json(request) = body.map_err(|rejection| {
        if rejection.status() == StatusCode::PAYLOAD_TOO_LARGE {
            AppError::payload_too_large()
        } else {
            AppError::bad_request("invalid_json", rejection.body_text())
        }
    })?;
    let request = request.validate()?;

    let params = json!({
        "query": request.query,
        "limit": request.limit,
        "language": request.language,
        "provider": request.provider,
        "literature_types": request.literature_types,
    });

    let request_id = Uuid::new_v4().to_string();
    let deadline = state.cfg.request_timeout + Duration::from_secs(5);
    let result = tokio::time::timeout(
        deadline,
        state.pool.call("search", params, state.cfg.request_timeout),
    )
    .await
    .map_err(|_| AppError::timeout("search request exceeded the service deadline"))?
    .map_err(AppError::upstream)?;

    let schema_version = result
        .get("schema_version")
        .and_then(Value::as_str)
        .unwrap_or("unknown")
        .to_string();
    let payload =
        serde_json::to_vec(&result).map_err(|error| AppError::internal(error.to_string()))?;

    Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "application/json")
        .header("x-request-id", request_id)
        .header("x-schema-version", schema_version)
        .header("x-service", "lit-acquisition-server")
        .body(Body::from(payload))
        .map_err(|error| AppError::internal(error.to_string()))
}

async fn require_api_key(
    State(state): State<AppState>,
    request: Request,
    next: Next,
) -> Result<Response, AppError> {
    if let Some(expected) = state.cfg.api_key.as_deref() {
        let path = request.uri().path();
        let is_probe = path == "/healthz" || path == "/readyz";
        if !is_probe {
            let provided = request
                .headers()
                .get(header::AUTHORIZATION)
                .and_then(|value| value.to_str().ok())
                .and_then(|value| value.strip_prefix("Bearer "))
                .map(str::trim)
                .unwrap_or("");
            if !constant_time_eq(provided.as_bytes(), expected.as_bytes()) {
                return Err(AppError::unauthorized());
            }
        }
    }
    Ok(next.run(request).await)
}

fn constant_time_eq(left: &[u8], right: &[u8]) -> bool {
    if left.len() != right.len() {
        return false;
    }
    let mut diff = 0u8;
    for (left, right) in left.iter().zip(right.iter()) {
        diff |= left ^ right;
    }
    diff == 0
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::http::Request as HttpRequest;
    use std::collections::HashMap;
    use tower::ServiceExt;

    fn config(pairs: &[(&str, &str)]) -> Arc<Config> {
        let values: HashMap<String, String> = pairs
            .iter()
            .map(|(key, value)| ((*key).to_string(), (*value).to_string()))
            .collect();
        Arc::new(Config::from_map(|key| values.get(key).cloned()).expect("config"))
    }

    fn state(pairs: &[(&str, &str)]) -> (AppState, Arc<WorkerPool>) {
        let cfg = config(pairs);
        let pool = Arc::new(WorkerPool::start(Arc::clone(&cfg)));
        (AppState::new(cfg, Arc::clone(&pool)), pool)
    }

    fn parse(body: &str) -> SearchRequest {
        serde_json::from_str(body).expect("request")
    }

    #[test]
    fn blank_query_is_rejected() {
        let error = parse(r#"{"query":"   "}"#)
            .validate()
            .expect_err("blank query");
        assert!(error.to_string().contains("invalid_request"));
    }

    #[test]
    fn limit_out_of_range_is_rejected() {
        assert!(parse(r#"{"query":"MECP2","limit":0}"#).validate().is_err());
        assert!(parse(r#"{"query":"MECP2","limit":51}"#).validate().is_err());
    }

    #[test]
    fn provider_is_validated() {
        assert!(parse(r#"{"query":"MECP2","provider":"pubmed; rm -rf /"}"#)
            .validate()
            .is_err());
        assert_eq!(
            parse(r#"{"query":"MECP2","provider":"PUDMED"}"#)
                .validate()
                .unwrap()
                .provider
                .as_deref(),
            Some("pudmed")
        );
    }

    #[test]
    fn literature_type_is_validated() {
        assert!(parse(r#"{"query":"MECP2","literature_types":["review"]}"#)
            .validate()
            .is_err());
        let validated =
            parse(r#"{"query":"MECP2","literature_types":["Case_Report","case_report"]}"#)
                .validate()
                .unwrap();
        assert_eq!(validated.literature_types, vec!["case_report"]);
    }

    #[test]
    fn language_defaults_to_auto() {
        let validated = parse(r#"{"query":"MECP2"}"#).validate().unwrap();
        assert_eq!(validated.language, "auto");
        assert_eq!(validated.limit, 10);
    }

    #[tokio::test]
    async fn healthz_does_not_need_a_worker() {
        let (state, pool) = state(&[("LIT_SERVICE_PYTHON", "definitely-not-python")]);
        let app = router(state);
        let response = app
            .oneshot(
                HttpRequest::builder()
                    .uri("/healthz")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        pool.shutdown().await;
    }

    #[tokio::test]
    async fn malformed_search_body_returns_json_400() {
        let (state, pool) = state(&[]);
        let app = router(state);
        let request = HttpRequest::builder()
            .method("POST")
            .uri("/v1/search")
            .header(header::CONTENT_TYPE, "application/json")
            .body(Body::from("{not json"))
            .unwrap();
        let response = app.oneshot(request).await.unwrap();
        assert_eq!(response.status(), StatusCode::BAD_REQUEST);

        let bytes = axum::body::to_bytes(response.into_body(), usize::MAX)
            .await
            .unwrap();
        let body: Value = serde_json::from_slice(&bytes).unwrap();
        assert_eq!(body["error"]["code"], "invalid_json");
        pool.shutdown().await;
    }

    #[tokio::test]
    async fn broken_worker_returns_502() {
        let (state, pool) = state(&[("LIT_SERVICE_PYTHON", "definitely-not-python")]);
        let app = router(state);
        let request = HttpRequest::builder()
            .method("POST")
            .uri("/v1/search")
            .header(header::CONTENT_TYPE, "application/json")
            .body(Body::from(r#"{"query":"MECP2"}"#))
            .unwrap();
        let response = app.oneshot(request).await.unwrap();
        assert_eq!(response.status(), StatusCode::BAD_GATEWAY);
        pool.shutdown().await;
    }

    #[tokio::test]
    async fn wrong_token_of_same_length_is_rejected() {
        // "secret" is six bytes; a six-byte wrong token exercises the
        // constant-time comparison instead of the early length guard.
        let (state, pool) = state(&[("LIT_SERVICE_API_KEY", "secret")]);
        let app = router(state);

        let request = HttpRequest::builder()
            .method("POST")
            .uri("/v1/search")
            .header(header::CONTENT_TYPE, "application/json")
            .header(header::AUTHORIZATION, "Bearer sekret")
            .body(Body::from(r#"{"query":"MECP2"}"#))
            .unwrap();
        let response = app.oneshot(request).await.unwrap();
        assert_eq!(response.status(), StatusCode::UNAUTHORIZED);
        pool.shutdown().await;
    }

    #[tokio::test]
    async fn oversized_body_returns_413() {
        let (state, pool) = state(&[("LIT_SERVICE_MAX_BODY_BYTES", "1024")]);
        let app = router(state);
        let payload = format!(r#"{{"query":"{}"}}"#, "x".repeat(4000));
        let request = HttpRequest::builder()
            .method("POST")
            .uri("/v1/search")
            .header(header::CONTENT_TYPE, "application/json")
            .body(Body::from(payload))
            .unwrap();
        let response = app.oneshot(request).await.unwrap();
        assert_eq!(response.status(), StatusCode::PAYLOAD_TOO_LARGE);

        let bytes = axum::body::to_bytes(response.into_body(), usize::MAX)
            .await
            .unwrap();
        let body: Value = serde_json::from_slice(&bytes).unwrap();
        assert_eq!(body["error"]["code"], "payload_too_large");
        pool.shutdown().await;
    }

    #[tokio::test]
    async fn api_key_is_enforced_except_on_probes() {
        let (state, pool) = state(&[("LIT_SERVICE_API_KEY", "secret")]);
        let app = router(state);

        let request = HttpRequest::builder()
            .method("POST")
            .uri("/v1/search")
            .header(header::CONTENT_TYPE, "application/json")
            .body(Body::from(r#"{"query":"MECP2"}"#))
            .unwrap();
        let response = app.clone().oneshot(request).await.unwrap();
        assert_eq!(response.status(), StatusCode::UNAUTHORIZED);

        let probe = HttpRequest::builder()
            .uri("/healthz")
            .body(Body::empty())
            .unwrap();
        let response = app.oneshot(probe).await.unwrap();
        assert_eq!(response.status(), StatusCode::OK);
        pool.shutdown().await;
    }
}
