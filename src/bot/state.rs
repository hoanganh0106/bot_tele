//! Strongly-typed algebraic dialog state machine for Telegram bot users and admins.

use dashmap::DashMap;
use serde::{Deserialize, Serialize};

/// Strongly-typed algebraic dialogue state machine replacing 24+ untyped string keys.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub enum DialogState {
    /// Idle state — default when no active prompt is open.
    Idle,

    // --- Customer Interactive States ---
    /// Waiting for customer to provide email addresses for account activation.
    WaitingEmail {
        order_code: String,
        expected_count: usize,
        is_custom_local: bool,
    },

    // --- Admin Product Management States ---
    /// Admin setting price delta in VND.
    AdminAwaitingPriceDelta { product_key: String },
    /// Admin setting USDT price for EN display.
    AdminAwaitingPriceUsdt { product_key: String },
    /// Admin setting custom product name in Vietnamese.
    AdminAwaitingProductNameVi { product_key: String },
    /// Admin setting custom product name in English.
    AdminAwaitingProductNameEn { product_key: String },
    /// Admin setting custom description in Vietnamese.
    AdminAwaitingProductDescVi { product_key: String },
    /// Admin setting custom description in English.
    AdminAwaitingProductDescEn { product_key: String },
    /// Admin adding multi-line account credentials to FIFO local inventory.
    AdminAwaitingStockItems { product_key: String },
    /// Admin setting manual integer stock count.
    AdminAwaitingStockManual { product_key: String },
    /// Admin creating custom local product: format "id | name | price".
    AdminAwaitingNewProduct,

    // --- Admin Category & UI States ---
    /// Admin adding a new custom category: format "id | name | emoji".
    AdminAwaitingNewCategory,
    /// Admin renaming a category: format "name | emoji".
    AdminAwaitingRenameCategory { category_id: String },
    /// Admin setting custom Telegram emoji ID for a category.
    AdminAwaitingCategoryEmoji { category_id: String },
    /// Admin setting custom Telegram emoji ID for a UI button.
    AdminAwaitingUiEmoji { button_key: String },

    // --- Admin Global Settings ---
    /// Admin setting default markup amount in VND.
    AdminAwaitingMarkup,
    /// Admin setting referral reward for referrer.
    AdminAwaitingReferralReward,
    /// Admin setting welcome bonus for new invited user.
    AdminAwaitingReferralNewUser,
    /// Admin setting minimum bank deposit amount.
    AdminAwaitingMinDeposit,
    /// Admin setting welcome message template (VI).
    AdminAwaitingWelcomeMsgVi,
    /// Admin setting welcome message template (EN).
    AdminAwaitingWelcomeMsgEn,
    /// Admin setting product menu title template (VI).
    AdminAwaitingMenuTitleVi,
    /// Admin setting product menu title template (EN).
    AdminAwaitingMenuTitleEn,

    // --- Admin Moderation & Lookups ---
    /// Admin adjusting customer wallet balance.
    AdminAwaitingWalletAdjust { target_user_id: i64, is_addition: bool },
    /// Admin blocking user IDs from broadcasts.
    AdminAwaitingBroadcastBlock,
    /// Admin blocking customer IDs from bot access entirely.
    AdminAwaitingCustomerBlock,
    /// Admin searching for user by username or ID.
    AdminAwaitingUserLookup,
    /// Admin searching for order by order code or transfer content.
    AdminAwaitingOrderLookup,

    // --- Admin Broadcast ---
    /// Admin queueing broadcast messages (text, photos, documents).
    AdminAwaitingBroadcast { message_ids: Vec<i32> },
}

impl Default for DialogState {
    fn default() -> Self {
        DialogState::Idle
    }
}

/// Thread-safe in-memory state manager for dialogue state and navigation.
#[derive(Debug)]
pub struct StateManager {
    states: DashMap<i64, DialogState>,
    nav_return: DashMap<i64, String>,
}

impl Default for StateManager {
    fn default() -> Self {
        Self::new()
    }
}

impl StateManager {
    pub fn new() -> Self {
        Self {
            states: DashMap::new(),
            nav_return: DashMap::new(),
        }
    }

    /// Retrieve user's current dialog state (cloned), defaulting to `Idle`.
    pub fn get(&self, user_id: i64) -> DialogState {
        self.states.get(&user_id).map(|r| r.clone()).unwrap_or(DialogState::Idle)
    }

    /// Set dialog state for user.
    pub fn set(&self, user_id: i64, state: DialogState) {
        self.states.insert(user_id, state);
    }

    /// Clear dialog state for user.
    pub fn clear(&self, user_id: i64) {
        self.states.remove(&user_id);
    }

    /// Atomically retrieve and remove dialog state for user.
    pub fn take(&self, user_id: i64) -> DialogState {
        self.states.remove(&user_id).map(|(_, s)| s).unwrap_or(DialogState::Idle)
    }

    /// Set navigation return destination (e.g. "home", "menu", "wallet").
    pub fn set_nav_return(&self, user_id: i64, source: &str) {
        self.nav_return.insert(user_id, source.to_string());
    }

    /// Retrieve and remove navigation return destination.
    pub fn take_nav_return(&self, user_id: i64) -> String {
        self.nav_return.remove(&user_id).map(|(_, s)| s).unwrap_or_else(|| "home".to_string())
    }
}
