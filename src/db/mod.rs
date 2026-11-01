//! Database persistence, document models, secondary indexing, and ACID transactions.

pub mod indexes;
pub mod models;
pub mod store;
pub mod transactions;

pub use indexes::DbIndexes;
pub use models::{
    CustomProduct, DatabaseData, DepositHistoryRecord, DepositRecord, IncomingPaymentRecord,
    OrderRecord, SettingsData, UserRecord,
};
pub use store::DbStore;
pub use transactions::{OrderUpdate, PartialWalletResult};
