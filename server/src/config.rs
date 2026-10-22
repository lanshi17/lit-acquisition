//! Environment-driven configuration for the axum service.

use std::{env, fmt, net::SocketAddr, time::Duration};

/// Configuration error raised before the server starts.
#[derive(Debug)]
pub struct ConfigError {
    message: String,
}

impl ConfigError {
    fn invalid(var: &str, value: &str, reason: impl fmt::Display) -> Self {
        Self {
            message: format!("invalid {var}={value:?}: {reason}"),
        }
    }

    fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
        }
    }
}

impl fmt::Display for ConfigError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for ConfigError {}

/// Runtime configuration, resolved once at startup.
#[derive(Clone, Debug)]
pub struct Config {
    /// Address the HTTP server binds to.
    pub addr: SocketAddr,
    /// Python interpreter used for worker processes.
    pub python: String,
    /// Module executed as the JSON-lines worker.
    pub python_module: String,
    /// Optional `PYTHONPATH` prepended for the worker (handy for `src/` layouts).
    pub python_path: Option<String>,
    /// Number of worker processes / concurrent searches.
    pub workers: usize,
    /// Hard timeout for a single worker RPC.
    pub request_timeout: Duration,
    /// Maximum accepted JSON request body.
    pub max_body_bytes: usize,
    /// Optional bearer token required on non-probe endpoints.
    pub api_key: Option<String>,
}

impl Config {
    /// Load configuration from process environment variables.
    pub fn from_env() -> Result<Self, ConfigError> {
        Self::from_map(|key| env::var(key).ok())
    }

    /// Load configuration from an arbitrary key/value source (used by tests).
    pub fn from_map<F>(get: F) -> Result<Self, ConfigError>
    where
        F: Fn(&str) -> Option<String>,
    {
        let addr = resolve_addr(&get)?;

        let python = non_empty(get("LIT_SERVICE_PYTHON")).unwrap_or_else(|| "python3".to_string());
        let python_module = non_empty(get("LIT_SERVICE_PYTHON_MODULE"))
            .unwrap_or_else(|| "lit_acquisition.service_worker".to_string());
        let python_path = non_empty(get("LIT_SERVICE_PYTHONPATH")).map(expand_home);

        let workers = match non_empty(get("LIT_SERVICE_WORKERS")) {
            Some(raw) => parse_usize("LIT_SERVICE_WORKERS", &raw)?,
            None => std::thread::available_parallelism()
                .map(|n| n.get())
                .unwrap_or(2)
                .clamp(1, 4),
        };
        if workers == 0 {
            return Err(ConfigError::new("LIT_SERVICE_WORKERS must be at least 1"));
        }

        let request_timeout = match non_empty(get("LIT_SERVICE_REQUEST_TIMEOUT")) {
            Some(raw) => Duration::from_secs(parse_u64("LIT_SERVICE_REQUEST_TIMEOUT", &raw)?),
            None => Duration::from_secs(180),
        };
        if request_timeout.is_zero() {
            return Err(ConfigError::new(
                "LIT_SERVICE_REQUEST_TIMEOUT must be greater than zero",
            ));
        }

        let max_body_bytes = match non_empty(get("LIT_SERVICE_MAX_BODY_BYTES")) {
            Some(raw) => parse_usize("LIT_SERVICE_MAX_BODY_BYTES", &raw)?,
            None => 128 * 1024,
        };
        if max_body_bytes < 1024 {
            return Err(ConfigError::new(
                "LIT_SERVICE_MAX_BODY_BYTES must be at least 1024",
            ));
        }

        let api_key = non_empty(get("LIT_SERVICE_API_KEY"));

        Ok(Self {
            addr,
            python,
            python_module,
            python_path,
            workers,
            request_timeout,
            max_body_bytes,
            api_key,
        })
    }
}

fn non_empty(value: Option<String>) -> Option<String> {
    value
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
}

fn resolve_addr<F>(get: &F) -> Result<SocketAddr, ConfigError>
where
    F: Fn(&str) -> Option<String>,
{
    if let Some(raw) = non_empty(get("LIT_SERVICE_ADDR")) {
        return raw
            .parse::<SocketAddr>()
            .map_err(|err| ConfigError::invalid("LIT_SERVICE_ADDR", &raw, err));
    }

    let host = non_empty(get("LIT_SERVICE_HOST")).unwrap_or_else(|| "0.0.0.0".to_string());
    let port = non_empty(get("LIT_SERVICE_PORT"))
        .or_else(|| non_empty(get("PORT")))
        .unwrap_or_else(|| "8080".to_string());
    let candidate = if host.contains(':') && !host.starts_with('[') {
        format!("[{host}]:{port}")
    } else {
        format!("{host}:{port}")
    };
    candidate
        .parse::<SocketAddr>()
        .map_err(|err| ConfigError::invalid("LIT_SERVICE_ADDR", &candidate, err))
}

fn parse_usize(var: &str, raw: &str) -> Result<usize, ConfigError> {
    raw.parse::<usize>()
        .map_err(|err| ConfigError::invalid(var, raw, err))
}

fn parse_u64(var: &str, raw: &str) -> Result<u64, ConfigError> {
    raw.parse::<u64>()
        .map_err(|err| ConfigError::invalid(var, raw, err))
}

fn expand_home(path: String) -> String {
    if let Some(rest) = path.strip_prefix("~/") {
        if let Some(home) = env::var_os("HOME") {
            return format!("{}/{}", home.to_string_lossy(), rest);
        }
    }
    path
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    fn map(pairs: &[(&str, &str)]) -> HashMap<String, String> {
        pairs
            .iter()
            .map(|(key, value)| ((*key).to_string(), (*value).to_string()))
            .collect()
    }

    #[test]
    fn defaults_are_sane() {
        let cfg = Config::from_map(|_| None).expect("default config");
        assert_eq!(cfg.addr.to_string(), "0.0.0.0:8080");
        assert_eq!(cfg.python, "python3");
        assert_eq!(cfg.python_module, "lit_acquisition.service_worker");
        assert!(cfg.workers >= 1);
        assert_eq!(cfg.request_timeout, Duration::from_secs(180));
        assert_eq!(cfg.max_body_bytes, 128 * 1024);
        assert!(cfg.api_key.is_none());
    }

    #[test]
    fn port_env_is_honoured() {
        let values = map(&[("PORT", "9099")]);
        let cfg = Config::from_map(|key| values.get(key).cloned()).expect("config");
        assert_eq!(cfg.addr.to_string(), "0.0.0.0:9099");
    }

    #[test]
    fn explicit_addr_wins() {
        let values = map(&[("LIT_SERVICE_ADDR", "127.0.0.1:7000"), ("PORT", "9099")]);
        let cfg = Config::from_map(|key| values.get(key).cloned()).expect("config");
        assert_eq!(cfg.addr.to_string(), "127.0.0.1:7000");
    }

    #[test]
    fn invalid_port_is_rejected() {
        let values = map(&[("PORT", "not-a-port")]);
        assert!(Config::from_map(|key| values.get(key).cloned()).is_err());
    }

    #[test]
    fn zero_workers_are_rejected() {
        let values = map(&[("LIT_SERVICE_WORKERS", "0")]);
        assert!(Config::from_map(|key| values.get(key).cloned()).is_err());
    }
}
