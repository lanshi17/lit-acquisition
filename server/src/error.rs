//! HTTP error type shared by every route.

use axum::{
    http::{header, StatusCode},
    response::{IntoResponse, Response},
    Json,
};
use serde_json::json;
use tracing::error;

use crate::bridge::BridgeError;

/// Route-level error rendered as a consistent JSON envelope.
#[derive(Debug)]
pub enum AppError {
    BadRequest { code: String, message: String },
    PayloadTooLarge,
    Unauthorized,
    Upstream(BridgeError),
    Timeout(String),
    Internal(String),
}

impl AppError {
    pub fn bad_request(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self::BadRequest {
            code: code.into(),
            message: message.into(),
        }
    }

    pub fn payload_too_large() -> Self {
        Self::PayloadTooLarge
    }

    pub fn unauthorized() -> Self {
        Self::Unauthorized
    }

    pub fn upstream(error: BridgeError) -> Self {
        Self::Upstream(error)
    }

    pub fn timeout(message: impl Into<String>) -> Self {
        Self::Timeout(message.into())
    }

    pub fn internal(message: impl Into<String>) -> Self {
        Self::Internal(message.into())
    }
}

impl std::fmt::Display for AppError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::BadRequest { code, message } => write!(f, "{code}: {message}"),
            Self::PayloadTooLarge => f.write_str("payload too large"),
            Self::Unauthorized => f.write_str("unauthorized"),
            Self::Upstream(error) => write!(f, "{error}"),
            Self::Timeout(message) => f.write_str(message),
            Self::Internal(message) => f.write_str(message),
        }
    }
}

impl std::error::Error for AppError {}

impl IntoResponse for AppError {
    fn into_response(self) -> Response {
        let (status, code, message) = match self {
            Self::BadRequest { code, message } => (StatusCode::BAD_REQUEST, code, message),
            Self::PayloadTooLarge => (
                StatusCode::PAYLOAD_TOO_LARGE,
                "payload_too_large".to_string(),
                "request body exceeds the configured limit".to_string(),
            ),
            Self::Unauthorized => (
                StatusCode::UNAUTHORIZED,
                "unauthorized".to_string(),
                "missing or invalid bearer token".to_string(),
            ),
            Self::Timeout(message) => (
                StatusCode::GATEWAY_TIMEOUT,
                "upstream_timeout".to_string(),
                message,
            ),
            Self::Internal(message) => {
                error!(error = %message, "internal server error");
                (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    "internal_error".to_string(),
                    "internal server error".to_string(),
                )
            }
            Self::Upstream(err) => {
                let (status, code) = match &err {
                    BridgeError::Timeout => (StatusCode::GATEWAY_TIMEOUT, "worker_timeout"),
                    BridgeError::Closed => (StatusCode::SERVICE_UNAVAILABLE, "worker_unavailable"),
                    BridgeError::Spawn(_) | BridgeError::Io(_) | BridgeError::Protocol(_) => {
                        (StatusCode::BAD_GATEWAY, "worker_error")
                    }
                    BridgeError::Remote { code, .. }
                        if code.as_str() == "invalid_request"
                            || code.as_str() == "method_not_found" =>
                    {
                        (StatusCode::BAD_REQUEST, "invalid_request")
                    }
                    BridgeError::Remote { .. } => (StatusCode::BAD_GATEWAY, "worker_error"),
                };
                if status.is_server_error() {
                    error!(error = %err, "worker call failed");
                }
                (status, code.to_string(), err.to_string())
            }
        };

        let mut response = Json(json!({
            "error": { "code": code, "message": message }
        }))
        .into_response();
        *response.status_mut() = status;
        if status == StatusCode::UNAUTHORIZED {
            response.headers_mut().insert(
                header::WWW_AUTHENTICATE,
                header::HeaderValue::from_static("Bearer"),
            );
        }
        response
    }
}
