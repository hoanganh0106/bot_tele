//! Access control and Group -1 silent interceptor for blacklisted customers.

use std::sync::Arc;
use teloxide::prelude::*;
use teloxide::types::Update;

use crate::config::Config;
use crate::db::DbStore;
use crate::error::AppError;

/// Determines if an incoming update originated from a blocked customer.
/// Invariant: Admins are NEVER blocked under any circumstance.
pub async fn is_blocked_filter(
    update: Update,
    db: Arc<DbStore>,
    config: Arc<Config>,
) -> bool {
    let user_id = match update.from() {
        Some(user) => user.id.0 as i64,
        None => return false,
    };

    // Admins are exempt from blacklists
    if config.admin_ids.contains(&user_id) {
        return false;
    }

    // Check customer blocklist in database settings
    db.is_customer_blocked(user_id)
}

/// Silently drops the update for blocked customers.
/// If the update is a callback query, answers the query to dismiss the client spinner.
pub async fn block_blocked_customer(bot: Bot, update: Update) -> Result<(), AppError> {
    if let teloxide::types::UpdateKind::CallbackQuery(ref query) = update.kind {
        let _ = bot.answer_callback_query(query.id.clone()).await;
    }
    // Return Ok(()) to finish this dptree branch and prevent downstream handlers from running.
    Ok(())
}

/// Helper to verify administrative privileges.
pub fn is_admin(config: &Config, user_id: i64) -> bool {
    config.admin_ids.contains(&user_id)
}
