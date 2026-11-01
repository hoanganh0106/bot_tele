//! External services and integration clients for bot_tele.
//!
//! Exposes API integrations for:
//! - Binance signed REST client & crypto deposit polling
//! - Hypervin digital goods supplier with 3-failure circuit breaker
//! - CTV dealer API client with test product mock interception
//! - Virtual SIM phone rental & OTP extractor (Cloudflare tunnel)
//! - SePay banking transfer webhook receiver
//! - Resilient product catalog aggregator & category classification

pub mod binance;
pub mod ctv;
pub mod hypervin;
pub mod products;
pub mod sepay;

pub use binance::{
    BinanceClient, BinanceDeposit, BinanceDepositAddress, BinanceDepositRecord, BinanceService,
};
pub use ctv::{CtvBuyResult, CtvClient, CtvProduct};
pub use hypervin::{
    CircuitBreaker, CircuitState, HypervinBuyResult, HypervinClient, HypervinProduct,
    SupplierOrderResult, SupplierProduct,
};
pub use products::{
    classify_product, get_default_categories, ApiSource, CachedCatalog, CatalogAggregator,
    CategoryInfo, ProductItem,
};
pub use sepay::{
    build_sepay_router, create_sepay_router, start_sepay_server, SePayWebhookPayload,
    SepayServerState, SepayWebhookPayload,
};

use std::collections::HashMap;
use async_trait::async_trait;
use thiserror::Error;

use crate::error::{AppError, BinanceError, DbError, PaymentError, SupplierError};

/// Unified error enum for the service layer.
#[derive(Debug, Error)]
pub enum ServiceError {
    #[error("Database error: {0}")]
    Db(#[from] DbError),

    #[error("External supplier error: {0}")]
    Supplier(#[from] SupplierError),

    #[error("Binance API error: {0}")]
    Binance(#[from] BinanceError),

    #[error("Payment error: {0}")]
    Payment(#[from] PaymentError),

    #[error("HTTP request error: {0}")]
    Http(#[from] reqwest::Error),

    #[error("Service circuit breaker open: {0}")]
    CircuitBreakerOpen(&'static str),

    #[error("Service internal error: {0}")]
    Internal(String),
}

impl From<ServiceError> for AppError {
    fn from(err: ServiceError) -> Self {
        match err {
            ServiceError::Db(e) => AppError::Db(e),
            ServiceError::Supplier(e) => AppError::Supplier(e),
            ServiceError::Binance(e) => AppError::Binance(e),
            ServiceError::Payment(e) => AppError::Payment(e),
            ServiceError::Http(e) => AppError::Http(e),
            ServiceError::CircuitBreakerOpen(s) => {
                AppError::Supplier(SupplierError::CircuitBreakerOpen {
                    service: s,
                    cooldown_remaining_secs: 60,
                })
            }
            ServiceError::Internal(msg) => AppError::Custom(msg),
        }
    }
}

/// Universal trait for external product catalog suppliers.
#[async_trait]
pub trait SupplierClient: Send + Sync {
    fn supplier_name(&self) -> &'static str;
    async fn fetch_products(&self) -> Result<HashMap<String, SupplierProduct>, SupplierError>;
    async fn fetch_balance(&self) -> Result<i64, SupplierError>;
}

/// Catalog management trait for bot handlers and background jobs.
#[async_trait]
pub trait CatalogService: Send + Sync {
    async fn get_catalog(&self) -> Result<HashMap<String, ProductItem>, ServiceError>;
    async fn get_product(&self, key: &str) -> Result<Option<ProductItem>, ServiceError>;
    async fn refresh_catalog(&self) -> Result<(), ServiceError>;
    async fn get_categories(&self) -> Result<HashMap<String, CategoryInfo>, ServiceError>;
    async fn get_hypervin_balance(&self) -> Result<Option<i64>, ServiceError>;
    async fn get_ctv_balance(&self) -> Result<Option<i64>, ServiceError>;
}

/// Mock implementation of `CatalogService` for unit and integration testing.
pub struct MockCatalogService {
    pub catalog: parking_lot::RwLock<HashMap<String, ProductItem>>,
    pub categories: parking_lot::RwLock<HashMap<String, CategoryInfo>>,
    pub hypervin_balance: parking_lot::RwLock<Option<i64>>,
    pub ctv_balance: parking_lot::RwLock<Option<i64>>,
}

impl Default for MockCatalogService {
    fn default() -> Self {
        Self::new()
    }
}

impl MockCatalogService {
    pub fn new() -> Self {
        Self {
            catalog: parking_lot::RwLock::new(HashMap::new()),
            categories: parking_lot::RwLock::new(products::get_default_categories()),
            hypervin_balance: parking_lot::RwLock::new(Some(500000)),
            ctv_balance: parking_lot::RwLock::new(Some(1000000)),
        }
    }

    pub fn insert_product(&self, product: ProductItem) {
        self.catalog.write().insert(product.key.clone(), product);
    }
}

#[async_trait]
impl CatalogService for MockCatalogService {
    async fn get_catalog(&self) -> Result<HashMap<String, ProductItem>, ServiceError> {
        Ok(self.catalog.read().clone())
    }

    async fn get_product(&self, key: &str) -> Result<Option<ProductItem>, ServiceError> {
        Ok(self.catalog.read().get(key).cloned())
    }

    async fn refresh_catalog(&self) -> Result<(), ServiceError> {
        Ok(())
    }

    async fn get_categories(&self) -> Result<HashMap<String, CategoryInfo>, ServiceError> {
        Ok(self.categories.read().clone())
    }

    async fn get_hypervin_balance(&self) -> Result<Option<i64>, ServiceError> {
        Ok(*self.hypervin_balance.read())
    }

    async fn get_ctv_balance(&self) -> Result<Option<i64>, ServiceError> {
        Ok(*self.ctv_balance.read())
    }
}
