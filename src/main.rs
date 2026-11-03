//! Application binary entry point and signal orchestration for bot_tele.
//!
//! Initializes Tokio runtime, structured tracing, configuration, document storage,
//! API service clients, catalog caching, crash recovery routines, Axum webhook server,
//! background jobs runner, and Teloxide bot dispatcher with graceful shutdown.

use std::sync::Arc;
use std::time::Duration;
use teloxide::prelude::*;
use tokio::sync::{broadcast, watch, Notify};
use tracing::{error, info, warn};
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt, EnvFilter};

use bot_tele::bot::{self, BotContext, FulfillmentMutex, StateManager};
use bot_tele::config::Config;
use bot_tele::db::DbStore;
use bot_tele::jobs::{self, recovery};
use bot_tele::services::binance::BinanceClient;
use bot_tele::services::ctv::CtvClient;
use bot_tele::services::hypervin::HypervinClient;
use bot_tele::services::products::CatalogAggregator;
use bot_tele::services::{sepay, CatalogService};

#[tokio::main(flavor = "multi_thread")]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    // 1. Initialize Structured Logging
    tracing_subscriber::registry()
        .with(EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info")))
        .with(tracing_subscriber::fmt::layer())
        .init();

    info!("Starting bot_tele Telegram Bot (Rust Edition)...");

    // 2. Load Environment Configuration
    let config = match Config::from_env() {
        Ok(cfg) => Arc::new(cfg),
        Err(err) => {
            eprintln!("Configuration loading error: {}", err);
            std::process::exit(1);
        }
    };

    info!(
        "Configuration loaded successfully. Webhook port: {}, Admin IDs: {:?}",
        config.webhook_port, config.admin_ids
    );

    // 3. Initialize Persistent Document Storage Engine
    let db_path = config.db_path();
    let db = DbStore::init(&db_path)
        .map_err(|e| format!("Failed to initialize database store: {}", e))?;
    info!("Storage engine initialized at {:?}", db_path);

    // 4. Setup Instant Payment Wakeup Notification Primitive
    let payment_notify = Arc::new(Notify::new());
    db.set_payment_wakeup(payment_notify.clone());

    // 5. Initialize External API Service Clients
    let ctv = Arc::new(CtvClient::new(&config));
    let hypervin = Arc::new(HypervinClient::new(&config));
    let binance = if config.is_crypto_enabled() {
        Some(Arc::new(BinanceClient::new(
            config.binance_api_key.as_deref().unwrap_or(""),
            config.binance_api_secret.as_deref().unwrap_or(""),
            config.usdt_wallet_address.as_deref().unwrap_or(""),
            &config.usdt_network,
        )))
    } else {
        None
    };

    // 6. Initialize Catalog Aggregator & Pre-Warm Cache
    let catalog = Arc::new(CatalogAggregator::new(
        db.clone(),
        Some((*ctv).clone()),
        Some((*hypervin).clone()),
        (*config).clone(),
    ));

    // Preload persisted product keys into ShortKeyRegistry so historical broadcast/order buttons
    // continue resolving cleanly across restarts even if an item vanished from supplier API while out of stock.
    db.read_data(|d| {
        for k in d.price_deltas.keys()
            .chain(d.custom_names.keys())
            .chain(d.custom_descriptions.keys())
            .chain(d.custom_categories.keys())
            .chain(d.custom_products.keys())
        {
            bot_tele::bot::helpers::GLOBAL_SHORT_KEYS.preload(k);
        }
        for o in d.orders.values() {
            if !o.product_key.is_empty() {
                bot_tele::bot::helpers::GLOBAL_SHORT_KEYS.preload(&o.product_key);
            }
        }
    });

    match catalog.refresh_catalog().await {
        Ok(_) => {
            info!("Product catalog cache pre-warmed successfully.");
            if let Ok(prods) = catalog.get_catalog().await {
                for (key, p) in &prods {
                    bot_tele::bot::helpers::GLOBAL_SHORT_KEYS.register_meta(key, &p.name, &p.category, &p.category_icon);
                }
            }
        }
        Err(e) => warn!("Initial product catalog pre-warm encountered warning: {}", e),
    }

    // 7. Initialize Telegram Proxy for Native Custom Emoji Injection & Shared Context
    let proxy_port = 18081;
    bot_tele::bot::telegram_proxy::start_telegram_proxy(db.clone(), proxy_port).await?;
    let proxy_url: reqwest::Url = format!("http://127.0.0.1:{}/", proxy_port).parse()?;
    let bot = Bot::new(&config.bot_token).set_api_url(proxy_url);
    let fulfillment_locks = FulfillmentMutex::new();
    let state_mgr = Arc::new(StateManager::new());

    let ctx = Arc::new(BotContext {
        bot: bot.clone(),
        db: db.clone(),
        config: config.clone(),
        catalog: catalog.clone(),
        hypervin: hypervin.clone(),
        ctv: ctv.clone(),
        binance: binance.clone(),
        fulfillment_locks,
        state_mgr,
    });

    // 8. Execute Post-Boot Crash Recovery Routines
    let recovery_stats = recovery::run_crash_recovery(ctx.clone()).await;
    info!("Post-boot crash recovery stats: {:?}", recovery_stats);

    // 9. Setup Graceful Shutdown Coordination Channels
    let (shutdown_broadcast_tx, _) = broadcast::channel::<()>(16);
    let (webhook_shutdown_tx, webhook_shutdown_rx) = watch::channel(false);

    // 10. Spawn Axum HTTP Webhook Server (SePay)
    let webhook_port = config.webhook_port;
    let webhook_db = db.clone();
    let webhook_api_key = config.sepay_api_key.clone();

    let webhook_task = tokio::spawn(async move {
        if let Err(e) = sepay::start_sepay_server(
            webhook_port,
            webhook_db,
            webhook_api_key,
            webhook_shutdown_rx,
        )
        .await
        {
            error!("Axum SePay webhook server failed: {}", e);
        }
    });
    info!("Axum Webhook server spawned on 0.0.0.0:{}", config.webhook_port);

    // 11. Spawn Background Tasks Runner
    let jobs = jobs::spawn_background_jobs(
        ctx.clone(),
        shutdown_broadcast_tx.clone(),
        payment_notify.clone(),
    );
    info!("Background jobs runner successfully spawned.");

    // 12. Build Teloxide Bot Dispatcher with Dependency Injection
    let handler = bot::make_handler();
    let mut dispatcher = Dispatcher::builder(bot.clone(), handler)
        .dependencies(dptree::deps![
            ctx.bot.clone(),
            ctx.db.clone(),
            ctx.config.clone(),
            ctx.catalog.clone(),
            ctx.hypervin.clone(),
            ctx.ctv.clone(),
            ctx.state_mgr.clone(),
            ctx.fulfillment_locks.clone(),
            ctx.clone()
        ])
        .build();

    let bot_shutdown_token = dispatcher.shutdown_token();

    let bot_task = tokio::spawn(async move {
        dispatcher.dispatch().await;
    });
    info!("Telegram bot dispatcher started listening for updates.");

    // 13. Wait for System Termination Signals (Ctrl+C / SIGINT / SIGTERM)
    wait_for_shutdown_signal().await;

    // 14. Graceful Shutdown Orchestration Sequence
    info!("Shutdown signal received. Initiating graceful shutdown sequence...");

    // 14a. Signal Webhook Server to Stop Accepting New Requests
    let _ = webhook_shutdown_tx.send(true);
    info!("Sent shutdown signal to Axum Webhook server.");

    // 14b. Broadcast Shutdown Signal to All Background Tasks
    let _ = shutdown_broadcast_tx.send(());
    info!("Broadcast shutdown signal to background tasks runner.");

    // 14c. Stop Telegram Bot Dispatcher
    match bot_shutdown_token.shutdown() {
        Ok(shutdown_future) => {
            info!("Waiting for bot dispatcher to finish in-flight updates...");
            tokio::select! {
                _ = shutdown_future => {
                    info!("Bot dispatcher stopped gracefully.");
                }
                _ = tokio::time::sleep(Duration::from_secs(10)) => {
                    warn!("Bot dispatcher shutdown timed out after 10s.");
                }
            }
        }
        Err(_) => {
            warn!("Bot dispatcher was not actively running.");
        }
    }

    // 14d. Await Background Tasks and Webhook Server with Timeout
    let _ = tokio::time::timeout(Duration::from_secs(5), webhook_task).await;
    let _ = tokio::time::timeout(Duration::from_secs(10), jobs.wait_all()).await;
    let _ = tokio::time::timeout(Duration::from_secs(5), bot_task).await;

    // 14e. Perform Synchronous Atomic Database Flush to Disk
    info!("Executing final synchronous atomic database flush...");
    if let Err(e) = db.flush_sync() {
        error!("CRITICAL: Synchronous database shutdown flush failed: {}", e);
    } else {
        info!("Database successfully synchronized to disk.");
    }

    info!("bot_tele shutdown sequence completed successfully. Goodbye!");
    Ok(())
}

/// Listens for Ctrl+C on Windows and Ctrl+C / SIGTERM on Unix systems.
async fn wait_for_shutdown_signal() {
    let ctrl_c = async {
        tokio::signal::ctrl_c()
            .await
            .expect("Failed to install Ctrl+C signal handler");
    };

    #[cfg(unix)]
    let terminate = async {
        tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
            .expect("Failed to install SIGTERM signal handler")
            .recv()
            .await;
    };

    #[cfg(not(unix))]
    let terminate = std::future::pending::<()>();

    tokio::select! {
        _ = ctrl_c => {
            info!("Received Ctrl+C / SIGINT signal.");
        },
        _ = terminate => {
            info!("Received SIGTERM signal.");
        },
    }
}
