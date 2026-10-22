//! Binary entry point for the lit-acquisition HTTP service.

use std::{process::ExitCode, sync::Arc};

use lit_acquisition_server::{
    bridge::WorkerPool,
    config::Config,
    routes::{router, AppState},
};
use tokio::net::TcpListener;
use tracing::{error, info};
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt, EnvFilter};

#[tokio::main]
async fn main() -> ExitCode {
    let _ = dotenvy::dotenv();
    init_tracing();

    let cfg = match Config::from_env() {
        Ok(cfg) => Arc::new(cfg),
        Err(err) => {
            error!(error = %err, "invalid service configuration");
            return ExitCode::FAILURE;
        }
    };

    let listener = match TcpListener::bind(cfg.addr).await {
        Ok(listener) => listener,
        Err(err) => {
            error!(addr = %cfg.addr, error = %err, "failed to bind service address");
            return ExitCode::FAILURE;
        }
    };
    let local_addr = listener.local_addr().unwrap_or(cfg.addr);

    let pool = Arc::new(WorkerPool::start(Arc::clone(&cfg)));
    let app = router(AppState::new(Arc::clone(&cfg), Arc::clone(&pool)));

    info!(
        addr = %local_addr,
        workers = cfg.workers,
        python = %cfg.python,
        module = %cfg.python_module,
        auth = cfg.api_key.is_some(),
        "lit-acquisition HTTP service listening"
    );

    if let Err(err) = axum::serve(listener, app)
        .with_graceful_shutdown(shutdown_signal())
        .await
    {
        error!(error = %err, "server error");
    }

    info!("stopping worker pool");
    pool.shutdown().await;
    info!("shutdown complete");

    ExitCode::SUCCESS
}

fn init_tracing() {
    let filter = EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| EnvFilter::new("info,tower_http=info"));
    tracing_subscriber::registry()
        .with(filter)
        .with(tracing_subscriber::fmt::layer())
        .init();
}

async fn shutdown_signal() {
    let ctrl_c = async {
        if let Err(err) = tokio::signal::ctrl_c().await {
            error!(error = %err, "failed to install ctrl-c handler");
        }
    };

    #[cfg(unix)]
    let terminate = async {
        match tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate()) {
            Ok(mut stream) => {
                stream.recv().await;
            }
            Err(err) => error!(error = %err, "failed to install SIGTERM handler"),
        }
    };

    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();

    tokio::select! {
        _ = ctrl_c => {},
        _ = terminate => {},
    }
}
