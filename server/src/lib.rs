//! HTTP service entry point for `lit-acquisition`, built on axum.
//!
//! The crate keeps the Python acquisition pipeline out of the HTTP process:
//! [`bridge::WorkerPool`] owns a set of long-lived `python -m
//! lit_acquisition.service_worker` subprocesses and exchanges one JSON object
//! per line with them. That keeps the axum layer fast and restartable while the
//! Python side keeps its connection pools warm for the lifetime of a worker.

pub mod bridge;
pub mod config;
pub mod error;
pub mod routes;

pub use config::Config;
pub use routes::{router, AppState};
