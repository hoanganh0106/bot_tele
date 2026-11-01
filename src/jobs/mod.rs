//! Background job schedulers, task supervision, and graceful shutdown orchestration.
//!
//! Spawns resilient background tasks for:
//! - Periodic stale order cleanup (300s)
//! - Periodic product catalog refresh & Hypervin balance monitor (30s)
//! - Periodic failed supplier order retry (120s)
//! - SePay payment processor with zero-latency wakeup
//! - Binance USDT deposit poller (25s)
//! - Post-boot crash recovery routines
//!
//! Note: Virtual phone SIM OTP polling has been 100% eliminated per user directive.

pub mod binance_poller;
pub mod cleanup;
pub mod payment_processor;
pub mod product_refresh;
pub mod recovery;
pub mod retry_orders;

pub use binance_poller::{run_binance_poller, spawn_binance_poller_job};
pub use cleanup::{cleanup_stale_orders, parse_order_time, run_cleanup_job, spawn_cleanup_job};
pub use payment_processor::{run_payment_processor, spawn_payment_processor_job};
pub use product_refresh::{refresh_products_and_check_balance, run_product_refresh_job, spawn_product_refresh_job};
pub use recovery::{run_crash_recovery, RecoveryStats};
pub use retry_orders::{retry_failed_orders, run_retry_orders_job, spawn_retry_orders_job};

use std::sync::Arc;
use tokio::sync::{broadcast, Notify};
use tokio::task::JoinHandle;
use tracing::info;

use crate::bot::BotContext;

/// Holds join handles for all running background tasks to enable graceful shutdown awaiting.
pub struct JobHandles {
    pub cleanup_handle: JoinHandle<()>,
    pub product_refresh_handle: JoinHandle<()>,
    pub payment_processor_handle: JoinHandle<()>,
    pub binance_poller_handle: Option<JoinHandle<()>>,
    pub retry_orders_handle: JoinHandle<()>,
}

impl JobHandles {
    /// Awaits completion of all background tasks.
    pub async fn wait_all(self) {
        let _ = self.cleanup_handle.await;
        let _ = self.product_refresh_handle.await;
        let _ = self.payment_processor_handle.await;
        if let Some(h) = self.binance_poller_handle {
            let _ = h.await;
        }
        let _ = self.retry_orders_handle.await;
        info!("All background jobs terminated cleanly.");
    }

    /// Aborts all background tasks immediately.
    pub fn abort_all(&self) {
        self.cleanup_handle.abort();
        self.product_refresh_handle.abort();
        self.payment_processor_handle.abort();
        if let Some(h) = &self.binance_poller_handle {
            h.abort();
        }
        self.retry_orders_handle.abort();
    }
}

/// Spawns Milestone 4 background tasks with broadcast shutdown receivers.
pub fn spawn_background_jobs(
    ctx: Arc<BotContext>,
    shutdown_tx: broadcast::Sender<()>,
    payment_notify: Arc<Notify>,
) -> JobHandles {
    info!("🚀 Spawning Milestone 4 background job workers...");

    let cleanup_handle = cleanup::spawn_cleanup_job(ctx.clone(), shutdown_tx.subscribe());
    let product_refresh_handle = product_refresh::spawn_product_refresh_job(ctx.clone(), shutdown_tx.subscribe());
    let payment_processor_handle = payment_processor::spawn_payment_processor_job(
        ctx.clone(),
        shutdown_tx.subscribe(),
        payment_notify,
    );
    let binance_poller_handle = if ctx.config.is_crypto_enabled() {
        Some(binance_poller::spawn_binance_poller_job(ctx.clone(), shutdown_tx.subscribe()))
    } else {
        None
    };
    let retry_orders_handle = retry_orders::spawn_retry_orders_job(ctx.clone(), shutdown_tx.subscribe());

    JobHandles {
        cleanup_handle,
        product_refresh_handle,
        payment_processor_handle,
        binance_poller_handle,
        retry_orders_handle,
    }
}
