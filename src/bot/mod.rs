//! Telegram bot interaction, routers, screens, commands, and helpers.

pub mod access_control;
pub mod admin;
pub mod commands;
pub mod customer;
pub mod helpers;
pub mod payment;
pub mod screens;
pub mod state;

use std::sync::Arc;
use teloxide::dispatching::UpdateHandler;
use teloxide::prelude::*;
use teloxide::types::Update;

use crate::error::AppError;
pub use commands::Command;
pub use payment::{BotContext, FulfillmentMutex};
pub use state::{DialogState, StateManager};

/// Build the unified Teloxide dptree dispatcher with Group -1 blacklist interceptor.
pub fn make_handler() -> UpdateHandler<AppError> {
    dptree::entry()
        // 1. Group -1 Silent Blacklist Interceptor
        .branch(
            dptree::filter_async(access_control::is_blocked_filter)
                .endpoint(access_control::block_blocked_customer),
        )
        // 2. Incoming Messages
        .branch(
            Update::filter_message()
                // 2a. Bot Commands (/start, /menu, /help, etc.)
                .branch(
                    teloxide::filter_command::<Command, _>()
                        .endpoint(commands::handle_command),
                )
                // 2b. Broadcast Media Messages
                .branch(
                    dptree::filter(|msg: Message| msg.photo().is_some() || msg.video().is_some() || msg.document().is_some())
                        .endpoint(customer::handle_media_input),
                )
                // 2c. Text Input & Dialog State Handler
                .branch(
                    dptree::filter(|msg: Message| msg.text().is_some())
                        .endpoint(customer::handle_text_input),
                ),
        )
        // 3. Callback Queries
        .branch(
            Update::filter_callback_query()
                // 3a. No-op (Category separators & pagination count badge)
                .branch(
                    dptree::filter(|q: CallbackQuery| q.data.as_deref() == Some("noop"))
                        .endpoint(customer::handle_noop),
                )
                // 3b. Navigation Back & Home
                .branch(
                    dptree::filter(|q: CallbackQuery| q.data.as_deref() == Some("back_start"))
                        .endpoint(customer::handle_back_start),
                )
                .branch(
                    dptree::filter(|q: CallbackQuery| q.data.as_deref() == Some("back_menu"))
                        .endpoint(customer::handle_back_menu),
                )
                // 3c. Menu & Catalog Navigation
                .branch(
                    dptree::filter(|q: CallbackQuery| matches!(q.data.as_deref(), Some("open_menu") | Some("reload_menu")))
                        .endpoint(customer::handle_menu_callback),
                )
                .branch(
                    dptree::filter(|q: CallbackQuery| q.data.as_deref().unwrap_or("").starts_with("viewcat_"))
                        .endpoint(customer::handle_category_click),
                )
                .branch(
                    dptree::filter(|q: CallbackQuery| q.data.as_deref().unwrap_or("").starts_with("prod_"))
                        .endpoint(customer::handle_product_select),
                )
                .branch(
                    dptree::filter(|q: CallbackQuery| q.data.as_deref().unwrap_or("").starts_with("qty_"))
                        .endpoint(customer::handle_qty_select),
                )
                // 3d. Orders History
                .branch(
                    dptree::filter(|q: CallbackQuery| {
                        let d = q.data.as_deref().unwrap_or("");
                        d == "btn_myorders" || d.starts_with("orders_page_")
                    })
                    .endpoint(customer::handle_orders_callback),
                )
                // 3e. Wallet & Deposit
                .branch(
                    dptree::filter(|q: CallbackQuery| q.data.as_deref() == Some("wallet_home"))
                        .endpoint(customer::handle_wallet_home),
                )
                .branch(
                    dptree::filter(|q: CallbackQuery| q.data.as_deref() == Some("deposit_start"))
                        .endpoint(customer::handle_deposit_start),
                )
                // 3f. Referral
                .branch(
                    dptree::filter(|q: CallbackQuery| q.data.as_deref() == Some("referral_home"))
                        .endpoint(customer::handle_referral_home),
                )
                // 3g. Language Selection
                .branch(
                    dptree::filter(|q: CallbackQuery| q.data.as_deref().unwrap_or("").starts_with("setlang_"))
                        .endpoint(customer::handle_set_language),
                )
                .branch(
                    dptree::filter(|q: CallbackQuery| q.data.as_deref().unwrap_or("").starts_with("language"))
                        .endpoint(customer::handle_language_query),
                )
                // 3h. Payments
                .branch(
                    dptree::filter(|q: CallbackQuery| q.data.as_deref().unwrap_or("").starts_with("paybank_"))
                        .endpoint(payment::handle_pay_bank),
                )
                .branch(
                    dptree::filter(|q: CallbackQuery| q.data.as_deref().unwrap_or("").starts_with("paycrypto_"))
                        .endpoint(payment::handle_pay_crypto),
                )
                .branch(
                    dptree::filter(|q: CallbackQuery| q.data.as_deref().unwrap_or("").starts_with("paywallet_"))
                        .endpoint(payment::handle_pay_wallet),
                )
                .branch(
                    dptree::filter(|q: CallbackQuery| q.data.as_deref().unwrap_or("").starts_with("paypartial_"))
                        .endpoint(payment::handle_pay_partial),
                )
                .branch(
                    dptree::filter(|q: CallbackQuery| q.data.as_deref().unwrap_or("").starts_with("paid_"))
                        .endpoint(payment::handle_paid_button),
                )
                .branch(
                    dptree::filter(|q: CallbackQuery| q.data.as_deref().unwrap_or("").starts_with("cancel_"))
                        .endpoint(payment::handle_cancel_order),
                )
                // 3i. Admin Callbacks
                .branch(
                    dptree::filter(|q: CallbackQuery| {
                        let d = q.data.as_deref().unwrap_or("");
                        d.starts_with("admin_")
                            || d.starts_with("adminpay_")
                            || d.starts_with("admincx_")
                            || d.starts_with("broadcast_")
                    })
                    .endpoint(admin::handle_admin_callback),
                ),
        )
}

/// Helper runner to start polling Telegram updates.
pub async fn start_bot(ctx: Arc<BotContext>) {
    let handler = make_handler();

    Dispatcher::builder(ctx.bot.clone(), handler)
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
        .enable_ctrlc_handler()
        .build()
        .dispatch()
        .await;
}
