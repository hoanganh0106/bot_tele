//! Unified product catalog aggregator, category classification, and pricing engine.
//!
//! Concurrently queries CTV and Hypervin suppliers, merges with custom local products
//! from `DbStore`, applies stock overrides, price deltas, and USDT conversions, and
//! provides a two-tier resilient in-memory cache (120s fresh, 1800s stale fallback).

use std::collections::HashMap;
use std::str::FromStr;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use async_trait::async_trait;
use parking_lot::RwLock;
use regex::Regex;
use rust_decimal::prelude::*;
use rust_decimal::Decimal;
use serde::{Deserialize, Serialize};
use tracing::{debug, warn, Instrument};

use crate::config::Config;
use crate::db::DbStore;
use crate::diagnostics::measure;
use super::ctv::CtvClient;
use super::hypervin::HypervinClient;
use super::{CatalogService, ServiceError};

pub const API_CACHE_TTL_SECS: u64 = 120;
pub const API_STALE_TTL_SECS: u64 = 1800;
pub const DEFAULT_USDT_VND_RATE: i64 = 26500;
pub const DEFAULT_MARKUP_FIXED: i64 = 10000;

// ----------------------------------------------------------------------------
// Domain Models
// ----------------------------------------------------------------------------

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "UPPERCASE")]
pub enum ApiSource {
    Local,
    #[serde(rename = "CTV")]
    Ctv,
    #[serde(rename = "HYPERVIN")]
    Hypervin,
}

impl std::fmt::Display for ApiSource {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ApiSource::Local => write!(f, "LOCAL"),
            ApiSource::Ctv => write!(f, "CTV"),
            ApiSource::Hypervin => write!(f, "HYPERVIN"),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProductItem {
    pub key: String,
    pub name: String,
    pub name_en: Option<String>,
    pub description: Option<String>,
    pub description_en: Option<String>,
    pub base_price: i64,
    pub price: i64, // Effective selling price in VND
    pub price_usdt: Option<Decimal>,
    pub stock: i64,
    pub api_source: ApiSource,
    pub category: String,        // e.g. "gpt", "vpn", "khac"
    pub category_name: String,   // e.g. "ChatGPT", "VPN", "Khác"
    pub category_icon: String,   // e.g. "🤖", "🛡️", "📦"
    pub is_custom_local: bool,
    pub is_auto_delivered: bool,
    pub is_hidden: bool,
}

impl ProductItem {
    pub fn display_name<'a>(&'a self, lang: &str) -> &'a str {
        if lang == "en" {
            if let Some(ref en) = self.name_en {
                if !en.is_empty() {
                    return en.as_str();
                }
            }
        }
        &self.name
    }

    pub fn display_desc<'a>(&'a self, lang: &str) -> &'a str {
        if lang == "en" {
            if let Some(ref en) = self.description_en {
                if !en.is_empty() {
                    return en.as_str();
                }
            }
        }
        self.description.as_deref().unwrap_or("")
    }

    pub fn is_available(&self, qty: i64) -> bool {
        !self.is_hidden && self.stock >= qty
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CategoryInfo {
    pub id: String,
    pub name: String,
    pub icon: String,
    pub keywords: Vec<String>,
}

// ----------------------------------------------------------------------------
// Built-In Categories & Keyword Extraction
// ----------------------------------------------------------------------------

pub fn get_default_categories() -> HashMap<String, CategoryInfo> {
    let raw: [(&str, &str, &str); 10] = [
        ("gpt", "ChatGPT", "🤖"),
        ("grok", "Grok", "🔮"),
        ("capcut", "CapCut", "🎬"),
        ("gemini", "Gemini", "✨"),
        ("meitu", "Meitu", "📸"),
        ("netflix", "Netflix / YT", "🍿"),
        ("discord", "Discord", "💬"),
        ("vpn", "VPN", "🛡️"),
        ("spotify", "Spotify", "🎵"),
        ("khac", "Khác", "📦"),
    ];

    let mut map = HashMap::new();
    for (id, name, icon) in raw {
        let keywords = extract_category_keywords(id, name);
        map.insert(
            id.to_string(),
            CategoryInfo {
                id: id.to_string(),
                name: name.to_string(),
                icon: icon.to_string(),
                keywords,
            },
        );
    }
    map
}

pub fn extract_category_keywords(cat_id: &str, cat_name: &str) -> Vec<String> {
    let re = Regex::new(r"[+/|,]+").unwrap();
    let mut keywords = Vec::new();
    for part in re.split(cat_name) {
        let trimmed = part.trim().to_lowercase();
        if !trimmed.is_empty() && !keywords.contains(&trimmed) {
            keywords.push(trimmed);
        }
    }
    let cid = cat_id.trim().to_lowercase();
    let name_squashed = cat_name.to_lowercase().replace(' ', "");
    if cid.len() >= 3 && name_squashed.contains(&cid) && !keywords.contains(&cid) {
        keywords.push(cid);
    }
    keywords
}

pub fn classify_product(
    _product_key: &str,
    product_name: &str,
    categories: &HashMap<String, CategoryInfo>,
    admin_override: Option<&str>,
) -> (String, String, String) {
    let other_info = categories
        .get("khac")
        .cloned()
        .unwrap_or_else(|| CategoryInfo {
            id: "khac".into(),
            name: "Khác".into(),
            icon: "📦".into(),
            keywords: vec![],
        });

    // 1. Highest priority: Admin manual category override
    if let Some(cat_id) = admin_override {
        if cat_id == "khac" {
            return (other_info.name, other_info.icon, "khac".into());
        }
        if let Some(cat) = categories.get(cat_id) {
            return (cat.name.clone(), cat.icon.clone(), cat.id.clone());
        }
    }

    // 2. Keyword matching on lowercased product name
    let pname = product_name.to_lowercase();
    if !pname.is_empty() {
        let mut matched: Vec<&CategoryInfo> = Vec::new();

        for (cid, cat) in categories {
            if cid == "khac" {
                continue;
            }
            let mut hit = false;
            for kw in &cat.keywords {
                if kw.len() <= 2 {
                    let pattern = format!(r"\b{}\b", regex::escape(kw));
                    if let Ok(re) = Regex::new(&pattern) {
                        if re.is_match(&pname) {
                            hit = true;
                            break;
                        }
                    }
                } else if pname.contains(kw.as_str()) {
                    hit = true;
                    break;
                }
            }
            if hit {
                matched.push(cat);
            }
        }

        // Exact single match -> assign category
        if matched.len() == 1 {
            let cat = matched[0];
            return (cat.name.clone(), cat.icon.clone(), cat.id.clone());
        }
        // Collision (>= 2 matches) or No match (0) -> falls back to "Khác"
    }

    (other_info.name, other_info.icon, "khac".into())
}

// ----------------------------------------------------------------------------
// Price & USDT Formulas
// ----------------------------------------------------------------------------

pub fn calculate_selling_price(
    product_key: &str,
    base_price: i64,
    is_custom_local: bool,
    price_deltas: &HashMap<String, i64>,
    default_markup_fixed: i64,
) -> i64 {
    if let Some(&delta) = price_deltas.get(product_key) {
        return base_price + delta;
    }
    if is_custom_local {
        return base_price;
    }
    base_price + default_markup_fixed
}

pub fn calculate_usdt_price(
    product_key: &str,
    vnd_selling_price: i64,
    custom_prices_usdt: &HashMap<String, String>,
    configured_rate: Option<i64>,
    default_rate: i64,
) -> Decimal {
    if let Some(usdt_str) = custom_prices_usdt.get(product_key) {
        if let Ok(d) = Decimal::from_str(usdt_str) {
            return d.round_dp_with_strategy(3, RoundingStrategy::MidpointAwayFromZero);
        }
    }

    let rate = match configured_rate {
        Some(r) if (10_000..=100_000).contains(&r) => r,
        _ if (10_000..=100_000).contains(&default_rate) => default_rate,
        _ => DEFAULT_USDT_VND_RATE,
    };

    let vnd = Decimal::from(vnd_selling_price);
    let r = Decimal::from(rate);
    (vnd / r).round_dp_with_strategy(2, RoundingStrategy::MidpointAwayFromZero)
}

// ----------------------------------------------------------------------------
// Two-Tier Resilient Cache
// ----------------------------------------------------------------------------

#[derive(Clone)]
pub struct CachedCatalog {
    pub products: HashMap<String, ProductItem>,
    pub ctv_balance: i64,
    pub hypervin_balance: Option<i64>,
    pub fetched_at: Instant,
    pub fresh_until: Instant,
    pub stale_until: Instant,
}

impl CachedCatalog {
    pub fn is_fresh(&self, now: Instant) -> bool {
        now < self.fresh_until
    }

    pub fn is_stale_serviceable(&self, now: Instant) -> bool {
        now < self.stale_until
    }
}

// ----------------------------------------------------------------------------
// Catalog Aggregator
// ----------------------------------------------------------------------------

pub struct CatalogAggregator {
    db: Arc<DbStore>,
    ctv: Option<CtvClient>,
    hypervin: Option<HypervinClient>,
    config: Config,
    cache: Arc<RwLock<Option<CachedCatalog>>>,
    refreshing: Arc<AtomicBool>,
}

impl CatalogAggregator {
    pub fn new(
        db: Arc<DbStore>,
        ctv: Option<CtvClient>,
        hypervin: Option<HypervinClient>,
        config: Config,
    ) -> Self {
        Self {
            db,
            ctv,
            hypervin,
            config,
            cache: Arc::new(RwLock::new(None)),
            refreshing: Arc::new(AtomicBool::new(false)),
        }
    }

    pub fn get_categories_map(&self) -> HashMap<String, CategoryInfo> {
        let mut categories = get_default_categories();
        // Check custom category definitions from DbStore
        self.db.read_data(|data| {
            for (cid, def) in &data.custom_category_defs {
                let name = def.get(0).cloned().unwrap_or_else(|| cid.clone());
                let icon = def.get(1).cloned().unwrap_or_else(|| "📦".to_string());
                let keywords = extract_category_keywords(cid, &name);
                categories.insert(
                    cid.clone(),
                    CategoryInfo {
                        id: cid.clone(),
                        name,
                        icon,
                        keywords,
                    },
                );
            }
        });
        categories
    }

    /// Internal synchronous or background catalog fetch and merge.
    async fn fetch_and_merge(&self) -> Result<CachedCatalog, ServiceError> {
        measure("catalog.fetch_merge", async {
        let now = Instant::now();

        // 1. Fetch CTV products & balance
        let (ctv_products, ctv_balance) = if let Some(ref ctv_client) = self.ctv {
            match measure("supplier.ctv.get_stock", ctv_client.get_stock()).await {
                Ok((prods, bal)) => (prods, bal),
                Err(e) => {
                    warn!("Failed to fetch CTV products: {}", e);
                    (HashMap::new(), 0)
                }
            }
        } else {
            (HashMap::new(), 0)
        };

        // 2. Fetch Hypervin products & balance
        let (hv_products, hv_balance) = if let Some(ref hv_client) = self.hypervin {
            if hv_client.is_enabled() {
                let prods = match measure("supplier.hypervin.get_products", hv_client.get_products()).await {
                    Ok(p) => p,
                    Err(e) => {
                        warn!("Failed to fetch Hypervin products: {}", e);
                        HashMap::new()
                    }
                };
                let bal = match measure("supplier.hypervin.get_balance", hv_client.get_balance()).await {
                    Ok(b) => Some(b),
                    Err(e) => {
                        warn!("Failed to fetch Hypervin balance: {}", e);
                        None
                    }
                };
                (prods, bal)
            } else {
                (HashMap::new(), None)
            }
        } else {
            (HashMap::new(), None)
        };

        // 3. Merge with DbStore custom products, stock overrides, deltas, and categories
        let categories = self.get_categories_map();

        let merged_products = self.db.read_data(|data| {
            let mut result: HashMap<String, ProductItem> = HashMap::new();

            // Insert CTV products
            for (key, p) in ctv_products {
                let base_price = p.price;
                let is_custom_local = false;
                let sell_price = calculate_selling_price(
                    &key,
                    base_price,
                    is_custom_local,
                    &data.price_deltas,
                    DEFAULT_MARKUP_FIXED,
                );
                let price_usdt = Some(calculate_usdt_price(
                    &key,
                    sell_price,
                    &data.custom_prices_usdt,
                    Some(self.config.usdt_vnd_rate),
                    DEFAULT_USDT_VND_RATE,
                ));

                let stock = if let Some(inv) = data.custom_accounts_inventory.get(&key) {
                    inv.len() as i64
                } else if let Some(&st) = data.custom_stocks.get(&key) {
                    st
                } else {
                    p.stock
                };

                let is_auto_delivered = data.custom_accounts_inventory.contains_key(&key);
                let is_hidden = data.custom_hiddens.contains(&key);

                let display_name = data.custom_names.get(&key).cloned().unwrap_or(p.name);
                let name_en = data.custom_names_en.get(&key).cloned();
                let desc = data.custom_descriptions.get(&key).cloned();
                let desc_en = data.custom_descriptions_en.get(&key).cloned();

                let admin_override = data.custom_categories.get(&key).map(|s| s.as_str());
                let (cat_name, cat_icon, cat_id) = classify_product(&key, &display_name, &categories, admin_override);

                result.insert(
                    key.clone(),
                    ProductItem {
                        key,
                        name: display_name,
                        name_en,
                        description: desc,
                        description_en: desc_en,
                        base_price,
                        price: sell_price,
                        price_usdt,
                        stock,
                        api_source: ApiSource::Ctv,
                        category: cat_id,
                        category_name: cat_name,
                        category_icon: cat_icon,
                        is_custom_local,
                        is_auto_delivered,
                        is_hidden,
                    },
                );
            }

            // Insert Hypervin products (already prefixed with "hv_")
            for (key, p) in hv_products {
                let base_price = p.price;
                let is_custom_local = false;
                let sell_price = calculate_selling_price(
                    &key,
                    base_price,
                    is_custom_local,
                    &data.price_deltas,
                    DEFAULT_MARKUP_FIXED,
                );
                let price_usdt = Some(calculate_usdt_price(
                    &key,
                    sell_price,
                    &data.custom_prices_usdt,
                    Some(self.config.usdt_vnd_rate),
                    DEFAULT_USDT_VND_RATE,
                ));

                let stock = if let Some(inv) = data.custom_accounts_inventory.get(&key) {
                    inv.len() as i64
                } else if let Some(&st) = data.custom_stocks.get(&key) {
                    st
                } else {
                    p.stock
                };

                let is_auto_delivered = data.custom_accounts_inventory.contains_key(&key);
                let is_hidden = data.custom_hiddens.contains(&key);

                let display_name = data.custom_names.get(&key).cloned().unwrap_or(p.name);
                let name_en = data.custom_names_en.get(&key).cloned();
                let desc = data.custom_descriptions.get(&key).cloned();
                let desc_en = data.custom_descriptions_en.get(&key).cloned();

                let admin_override = data.custom_categories.get(&key).map(|s| s.as_str());
                let (cat_name, cat_icon, cat_id) = classify_product(&key, &display_name, &categories, admin_override);

                result.insert(
                    key.clone(),
                    ProductItem {
                        key,
                        name: display_name,
                        name_en,
                        description: desc,
                        description_en: desc_en,
                        base_price,
                        price: sell_price,
                        price_usdt,
                        stock,
                        api_source: ApiSource::Hypervin,
                        category: cat_id,
                        category_name: cat_name,
                        category_icon: cat_icon,
                        is_custom_local,
                        is_auto_delivered,
                        is_hidden,
                    },
                );
            }

            // Insert Local custom products
            for (key, cp) in &data.custom_products {
                let base_price = cp.price;
                let is_custom_local = true;
                let sell_price = calculate_selling_price(
                    key,
                    base_price,
                    is_custom_local,
                    &data.price_deltas,
                    DEFAULT_MARKUP_FIXED,
                );
                let price_usdt = Some(calculate_usdt_price(
                    key,
                    sell_price,
                    &data.custom_prices_usdt,
                    Some(self.config.usdt_vnd_rate),
                    DEFAULT_USDT_VND_RATE,
                ));

                let stock = if let Some(inv) = data.custom_accounts_inventory.get(key) {
                    inv.len() as i64
                } else if let Some(&st) = data.custom_stocks.get(key) {
                    st
                } else {
                    cp.stock
                };

                let is_auto_delivered = data.custom_accounts_inventory.contains_key(key);
                let is_hidden = data.custom_hiddens.contains(key);

                let display_name = data.custom_names.get(key).cloned().unwrap_or_else(|| cp.name.clone());
                let name_en = data.custom_names_en.get(key).cloned();
                let desc = data.custom_descriptions.get(key).cloned();
                let desc_en = data.custom_descriptions_en.get(key).cloned();

                let admin_override = data.custom_categories.get(key).map(|s| s.as_str());
                let (cat_name, cat_icon, cat_id) = classify_product(key, &display_name, &categories, admin_override);

                result.insert(
                    key.clone(),
                    ProductItem {
                        key: key.clone(),
                        name: display_name,
                        name_en,
                        description: desc,
                        description_en: desc_en,
                        base_price,
                        price: sell_price,
                        price_usdt,
                        stock,
                        api_source: ApiSource::Local,
                        category: cat_id,
                        category_name: cat_name,
                        category_icon: cat_icon,
                        is_custom_local,
                        is_auto_delivered,
                        is_hidden,
                    },
                );
            }

            result
        });

        let catalog = CachedCatalog {
            products: merged_products,
            ctv_balance,
            hypervin_balance: hv_balance,
            fetched_at: now,
            fresh_until: now + Duration::from_secs(API_CACHE_TTL_SECS),
            stale_until: now + Duration::from_secs(API_STALE_TTL_SECS),
        };

        // Update in-memory cache
        *self.cache.write() = Some(catalog.clone());

        Ok(catalog)
        })
        .await
    }
}

#[async_trait]
impl CatalogService for CatalogAggregator {
    async fn get_catalog(&self) -> Result<HashMap<String, ProductItem>, ServiceError> {
        measure("catalog.get", async {
        let now = Instant::now();

        // 1. Check existing cache
        let cached = self.cache.read().clone();
        if let Some(entry) = cached {
            if entry.is_fresh(now) {
                debug!(target: "bot_latency", cache = "fresh", "catalog cache decision");
                return Ok(entry.products);
            }

            if entry.is_stale_serviceable(now) {
                debug!(target: "bot_latency", cache = "stale", "catalog cache decision");
                // If stale, attempt non-blocking background refresh
                if !self.refreshing.swap(true, Ordering::SeqCst) {
                    let this = Arc::new(Self {
                        db: self.db.clone(),
                        ctv: self.ctv.clone(),
                        hypervin: self.hypervin.clone(),
                        config: self.config.clone(),
                        cache: self.cache.clone(),
                        refreshing: self.refreshing.clone(),
                    });

                    tokio::spawn(
                        async move {
                            let _ = measure("catalog.background_refresh", this.fetch_and_merge()).await;
                            this.refreshing.store(false, Ordering::SeqCst);
                        }
                        .in_current_span(),
                    );
                }
                return Ok(entry.products);
            }
        }

        // Cache missing or expired > 1800s -> synchronous fetch
        debug!(target: "bot_latency", cache = "miss_or_expired", "catalog cache decision");
        let catalog = self.fetch_and_merge().await?;
        Ok(catalog.products)
        })
        .await
    }

    async fn get_product(&self, key: &str) -> Result<Option<ProductItem>, ServiceError> {
        let catalog = self.get_catalog().await?;
        Ok(catalog.get(key).cloned())
    }

    async fn refresh_catalog(&self) -> Result<(), ServiceError> {
        measure("catalog.forced_refresh", async {
        self.fetch_and_merge().await?;
        Ok(())
        })
        .await
    }

    async fn get_categories(&self) -> Result<HashMap<String, CategoryInfo>, ServiceError> {
        Ok(self.get_categories_map())
    }

    async fn get_hypervin_balance(&self) -> Result<Option<i64>, ServiceError> {
        let cached = self.cache.read().clone();
        if let Some(entry) = cached {
            Ok(entry.hypervin_balance)
        } else {
            let cat = self.fetch_and_merge().await?;
            Ok(cat.hypervin_balance)
        }
    }

    async fn get_ctv_balance(&self) -> Result<Option<i64>, ServiceError> {
        let cached = self.cache.read().clone();
        if let Some(entry) = cached {
            Ok(Some(entry.ctv_balance))
        } else {
            let cat = self.fetch_and_merge().await?;
            Ok(Some(cat.ctv_balance))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_category_single_match() {
        let categories = get_default_categories();
        let (name, icon, id) = classify_product("p1", "Tài khoản ChatGPT Plus 1 Tháng", &categories, None);
        assert_eq!(id, "gpt");
        assert_eq!(name, "ChatGPT");
        assert_eq!(icon, "🤖");

        let (name2, icon2, id2) = classify_product("p2", "ExpressVPN 1 Năm bản quyền", &categories, None);
        assert_eq!(id2, "vpn");
        assert_eq!(name2, "VPN");
        assert_eq!(icon2, "🛡️");
    }

    #[test]
    fn test_category_zero_match_fallback() {
        let categories = get_default_categories();
        let (name, icon, id) = classify_product("p_random", "Grammarly Premium 1 Năm", &categories, None);
        assert_eq!(id, "khac");
        assert_eq!(name, "Khác");
        assert_eq!(icon, "📦");
    }

    #[test]
    fn test_category_ambiguous_fallback() {
        let categories = get_default_categories();
        // Matches both GPT and Gemini!
        let (name, icon, id) = classify_product("p_combo", "Combo AI: ChatGPT + Gemini Pro", &categories, None);
        assert_eq!(id, "khac");
        assert_eq!(name, "Khác");
        assert_eq!(icon, "📦");
    }

    #[test]
    fn test_category_admin_override() {
        let categories = get_default_categories();
        // Product has no keywords, but admin assigned "spotify"
        let (name, icon, id) = classify_product("p_unknown", "Music Account Pro", &categories, Some("spotify"));
        assert_eq!(id, "spotify");
        assert_eq!(name, "Spotify");
        assert_eq!(icon, "🎵");

        // Product has "ChatGPT", but admin forced "khac"
        let (name2, icon2, id2) = classify_product("p_gpt", "ChatGPT Account", &categories, Some("khac"));
        assert_eq!(id2, "khac");
        assert_eq!(name2, "Khác");
        assert_eq!(icon2, "📦");
    }

    #[test]
    fn test_price_delta_and_margin() {
        let mut deltas = HashMap::new();
        deltas.insert("prod_a".to_string(), 15000);

        // Delta exists -> base + delta
        let price_a = calculate_selling_price("prod_a", 50000, false, &deltas, 10000);
        assert_eq!(price_a, 65000);

        // Custom local with no delta -> base price directly
        let price_local = calculate_selling_price("prod_local", 40000, true, &deltas, 10000);
        assert_eq!(price_local, 40000);

        // External supplier API product with no delta -> base + default markup
        let price_api = calculate_selling_price("prod_api", 50000, false, &deltas, 10000);
        assert_eq!(price_api, 60000);
    }

    #[test]
    fn test_usdt_price_conversion() {
        let mut custom_usdt = HashMap::new();
        custom_usdt.insert("key_fixed".to_string(), "2.4567".to_string());

        // Custom price rounded to 3 decimals
        let usdt_fixed = calculate_usdt_price("key_fixed", 100000, &custom_usdt, None, 26500);
        assert_eq!(usdt_fixed, Decimal::from_str("2.457").unwrap());

        // Standard rate conversion rounded to 2 decimals
        // 53,000 VND / 26,500 = 2.00 USDT
        let usdt_conv = calculate_usdt_price("key_calc", 53000, &custom_usdt, None, 26500);
        assert_eq!(usdt_conv, Decimal::from_str("2.00").unwrap());
    }
}
