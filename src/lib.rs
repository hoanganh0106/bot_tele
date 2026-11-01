//! bot_tele crate root.
//!
//! Exposes modules for Telegram bot, storage engine, configuration, localization, and error architecture.

pub mod bot;
pub mod config;
pub mod db;
pub mod diagnostics;
pub mod error;
pub mod i18n;
pub mod jobs;
pub mod services;

pub use config::Config;
pub use error::{AppError, AppResult};

