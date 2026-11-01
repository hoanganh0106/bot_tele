//! Translation key constants for the bot.
//!
//! Total keys: 96 covering all customer screens, orders, wallets, crypto, referrals, and admin alerts.

// --- Product & Catalog Keys (18) ---
pub const PRODUCT_NOT_FOR_SALE: &str = "product_not_for_sale";
pub const ORDER_CANCELED_PRODUCT_HIDDEN: &str = "order_canceled_product_hidden";
pub const ORDER_REFUNDED_PRODUCT_HIDDEN: &str = "order_refunded_product_hidden";
pub const LOADING_PRODUCTS: &str = "loading_products";
pub const PRODUCTS_UNAVAILABLE: &str = "products_unavailable";
pub const MENU_TITLE: &str = "menu_title";
pub const PRODUCT_MISSING: &str = "product_missing";
pub const PRODUCT_OUT_OF_STOCK: &str = "product_out_of_stock";
pub const PRODUCT_UPDATING: &str = "product_updating";
pub const PRODUCT_DETAIL: &str = "product_detail";
pub const PRODUCT_AUTO_DELIVERY: &str = "product_auto_delivery";
pub const PRODUCT_EMAIL_NOTE: &str = "product_email_note";
pub const CATEGORY_OTHER: &str = "category_other";
pub const CATEGORY_PRODUCTS: &str = "category_products";
pub const CATEGORY_PANEL: &str = "category_panel";
pub const BTN_BACK_CATEGORIES: &str = "btn_back_categories";
pub const BTN_VIEW_PRODUCT: &str = "btn_view_product";
pub const BTN_CATEGORY: &str = "btn_category";

// --- Orders & Cart Keys (14) ---
pub const ORDER_PAYMENT: &str = "order_payment";
pub const ORDER_EXPIRY: &str = "order_expiry";
pub const WALLET_BALANCE: &str = "wallet_balance";
pub const ORDER_INVALID: &str = "order_invalid";
pub const ORDER_TIMEOUT: &str = "order_timeout";
pub const REFUND: &str = "refund";
pub const ORDER_CANCELLED: &str = "order_cancelled";
pub const ORDER_ALREADY_PROCESSED: &str = "order_already_processed";
pub const NO_ORDERS: &str = "no_orders";
pub const ORDERS_TITLE: &str = "orders_title";
pub const SESSION_ERROR: &str = "session_error";
pub const STOCK_ERROR: &str = "stock_error";
pub const CUSTOMER_ORDER_ERROR: &str = "customer_order_error";
pub const BTN_PAID: &str = "btn_paid";
pub const BTN_CANCEL: &str = "btn_cancel";

// --- Banking & Wallet Keys (16) ---
pub const BANK_PAYMENT: &str = "bank_payment";
pub const PAID_WAITING: &str = "paid_waiting";
pub const WALLET_PAID: &str = "wallet_paid";
pub const WALLET_INSUFFICIENT: &str = "wallet_insufficient";
pub const PARTIAL_PAYMENT: &str = "partial_payment";
pub const PARTIAL_NO_BALANCE: &str = "partial_no_balance";
pub const WALLET_REDEDUCTED: &str = "wallet_rededucted";
pub const WALLET_HOME: &str = "wallet_home";
pub const DEPOSIT_PAGE: &str = "deposit_page";
pub const DEPOSIT_SUCCESS: &str = "deposit_success";
pub const BTN_WALLET: &str = "btn_wallet";
pub const BTN_PAY_WALLET: &str = "btn_pay_wallet";
pub const BTN_PAY_PARTIAL: &str = "btn_pay_partial";
pub const BTN_PAY_BANK: &str = "btn_pay_bank";
pub const BTN_DEPOSIT: &str = "btn_deposit";
pub const BTN_BALANCE: &str = "btn_balance";

// --- Crypto USDT Keys (7) ---
pub const BTN_PAY_CRYPTO: &str = "btn_pay_crypto";
pub const CRYPTO_WARNING: &str = "crypto_warning";
pub const CRYPTO_PAYMENT: &str = "crypto_payment";
pub const CRYPTO_PAYMENT_CAPTION: &str = "crypto_payment_caption";
pub const CRYPTO_PAID_WAITING: &str = "crypto_paid_waiting";
pub const CRYPTO_UNAVAILABLE: &str = "crypto_unavailable";
pub const CRYPTO_RESERVATION_FAILED: &str = "crypto_reservation_failed";

// --- Referral System Keys (8) ---
pub const REFERRAL_PAGE: &str = "referral_page";
pub const REFERRAL_CREDITED: &str = "referral_credited";
pub const REF_ACTIVE: &str = "ref_active";
pub const REF_PAUSED: &str = "ref_paused";
pub const REF_FRIEND_REWARD: &str = "ref_friend_reward";
pub const REF_SHARE: &str = "ref_share";
pub const BTN_REFERRAL: &str = "btn_referral";
pub const BTN_INVITE: &str = "btn_invite";
pub const BTN_SHARE: &str = "btn_share";

// --- Fulfillment & User Input Keys (8) ---
pub const NEED_EMAIL: &str = "need_email";
pub const DELIVERY_DELAYED: &str = "delivery_delayed";
pub const DELIVERY_COMPLETE: &str = "delivery_complete";
pub const MANUAL_PROCESSING: &str = "manual_processing";
pub const API_DELIVERY_COMPLETE: &str = "api_delivery_complete";
pub const EMAIL_COUNT_ERROR: &str = "email_count_error";
pub const EMAIL_INVALID: &str = "email_invalid";
pub const INFO_PROCESSING: &str = "info_processing";

// --- Admin Alerts Sent to Users (4) ---
pub const ADMIN_CANCELLED_ORDER: &str = "admin_cancelled_order";
pub const ADMIN_WALLET_ADDED: &str = "admin_wallet_added";
pub const ADMIN_WALLET_DEDUCTED: &str = "admin_wallet_deducted";
pub const ADMIN_UNLOCKED: &str = "admin_unlocked";

// --- Navigation, UI & Onboarding Keys (14) ---
pub const WELCOME: &str = "welcome";
pub const WELCOME_BONUS: &str = "welcome_bonus";
pub const HELP: &str = "help";
pub const BTN_MENU: &str = "btn_menu";
pub const BTN_HISTORY: &str = "btn_history";
pub const BTN_CONTACT: &str = "btn_contact";
pub const BTN_RELOAD: &str = "btn_reload";
pub const BTN_BACK: &str = "btn_back";
pub const BTN_HOME: &str = "btn_home";
pub const BTN_EXIT: &str = "btn_exit";
pub const BTN_BUY: &str = "btn_buy";
pub const BTN_LANGUAGE: &str = "btn_language";
pub const LANGUAGE_PROMPT: &str = "language_prompt";
pub const LANGUAGE_UPDATED: &str = "language_updated";

// --- Command Descriptions (5) ---
pub const COMMAND_START: &str = "command_start";
pub const COMMAND_MENU: &str = "command_menu";
pub const COMMAND_ORDERS: &str = "command_orders";
pub const COMMAND_HELP: &str = "command_help";
pub const COMMAND_LANGUAGE: &str = "command_language";

/// Exhaustive slice of all 96 valid translation keys.
pub const ALL_KEYS: &[&str] = &[
    ADMIN_CANCELLED_ORDER,
    ADMIN_UNLOCKED,
    ADMIN_WALLET_ADDED,
    ADMIN_WALLET_DEDUCTED,
    API_DELIVERY_COMPLETE,
    BANK_PAYMENT,
    BTN_BACK,
    BTN_BACK_CATEGORIES,
    BTN_BALANCE,
    BTN_BUY,
    BTN_CANCEL,
    BTN_CATEGORY,
    BTN_CONTACT,
    BTN_DEPOSIT,
    BTN_EXIT,
    BTN_HISTORY,
    BTN_HOME,
    BTN_INVITE,
    BTN_LANGUAGE,
    BTN_MENU,
    BTN_PAID,
    BTN_PAY_BANK,
    BTN_PAY_CRYPTO,
    BTN_PAY_PARTIAL,
    BTN_PAY_WALLET,
    BTN_REFERRAL,
    BTN_RELOAD,
    BTN_SHARE,
    BTN_VIEW_PRODUCT,
    BTN_WALLET,
    CATEGORY_OTHER,
    CATEGORY_PANEL,
    CATEGORY_PRODUCTS,
    COMMAND_HELP,
    COMMAND_LANGUAGE,
    COMMAND_MENU,
    COMMAND_ORDERS,
    COMMAND_START,
    CRYPTO_PAID_WAITING,
    CRYPTO_PAYMENT,
    CRYPTO_PAYMENT_CAPTION,
    CRYPTO_RESERVATION_FAILED,
    CRYPTO_UNAVAILABLE,
    CRYPTO_WARNING,
    CUSTOMER_ORDER_ERROR,
    DELIVERY_COMPLETE,
    DELIVERY_DELAYED,
    DEPOSIT_PAGE,
    DEPOSIT_SUCCESS,
    EMAIL_COUNT_ERROR,
    EMAIL_INVALID,
    HELP,
    INFO_PROCESSING,
    LANGUAGE_PROMPT,
    LANGUAGE_UPDATED,
    LOADING_PRODUCTS,
    MANUAL_PROCESSING,
    MENU_TITLE,
    NEED_EMAIL,
    NO_ORDERS,
    ORDER_ALREADY_PROCESSED,
    ORDER_CANCELED_PRODUCT_HIDDEN,
    ORDER_CANCELLED,
    ORDER_EXPIRY,
    ORDER_INVALID,
    ORDER_PAYMENT,
    ORDER_REFUNDED_PRODUCT_HIDDEN,
    ORDER_TIMEOUT,
    ORDERS_TITLE,
    PAID_WAITING,
    PARTIAL_NO_BALANCE,
    PARTIAL_PAYMENT,
    PRODUCT_AUTO_DELIVERY,
    PRODUCT_DETAIL,
    PRODUCT_EMAIL_NOTE,
    PRODUCT_MISSING,
    PRODUCT_NOT_FOR_SALE,
    PRODUCT_OUT_OF_STOCK,
    PRODUCT_UPDATING,
    PRODUCTS_UNAVAILABLE,
    REF_ACTIVE,
    REF_FRIEND_REWARD,
    REF_PAUSED,
    REF_SHARE,
    REFERRAL_CREDITED,
    REFERRAL_PAGE,
    REFUND,
    SESSION_ERROR,
    STOCK_ERROR,
    WALLET_BALANCE,
    WALLET_HOME,
    WALLET_INSUFFICIENT,
    WALLET_PAID,
    WALLET_REDEDUCTED,
    WELCOME,
    WELCOME_BONUS,
];
