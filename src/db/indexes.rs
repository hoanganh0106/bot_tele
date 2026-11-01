//! In-memory secondary index structures for O(1) order lookups and deduplication.

use std::collections::{HashMap, HashSet};
use super::models::{DatabaseData, OrderRecord};

#[derive(Debug, Default, Clone)]
pub struct DbIndexes {
    pub orders_by_user: HashMap<i64, HashSet<String>>,
    pub orders_by_status: HashMap<String, HashSet<String>>,
    pub processed_txns: HashSet<String>,
    pub processed_crypto_txids: HashSet<String>,
    pub hidden_products: HashSet<String>,
}

impl DbIndexes {
    /// Rebuild all lookup maps and deduplication sets from the primary DatabaseData.
    pub fn rebuild(&mut self, data: &DatabaseData) {
        let mut by_user: HashMap<i64, HashSet<String>> = HashMap::new();
        let mut by_status: HashMap<String, HashSet<String>> = HashMap::new();

        for (code, order) in &data.orders {
            by_user.entry(order.user_id).or_default().insert(code.clone());
            by_status.entry(order.status.clone()).or_default().insert(code.clone());
        }

        self.orders_by_user = by_user;
        self.orders_by_status = by_status;
        self.processed_txns = data.processed_transactions.iter().cloned().collect();
        self.processed_crypto_txids = data.processed_crypto_txids.iter().cloned().collect();
        self.hidden_products = data.custom_hiddens.iter().cloned().collect();
    }

    /// O(1) query for customer orders.
    pub fn get_user_orders<'a>(&self, user_id: i64, data: &'a DatabaseData) -> Vec<&'a OrderRecord> {
        if let Some(codes) = self.orders_by_user.get(&user_id) {
            codes.iter().filter_map(|code| data.orders.get(code)).collect()
        } else {
            Vec::new()
        }
    }

    /// O(1) query for orders matching a specific status.
    pub fn get_orders_by_status<'a>(&self, status: &str, data: &'a DatabaseData) -> Vec<&'a OrderRecord> {
        if let Some(codes) = self.orders_by_status.get(status) {
            codes.iter().filter_map(|code| data.orders.get(code)).collect()
        } else {
            Vec::new()
        }
    }

    /// Deduplication check for SePay webhook transaction ID.
    pub fn is_txn_processed(&self, tx_id: &str) -> bool {
        self.processed_txns.contains(tx_id)
    }

    /// Deduplication check for Binance deposit txId.
    pub fn is_crypto_txid_processed(&self, tx_id: &str) -> bool {
        self.processed_crypto_txids.contains(tx_id)
    }

    /// O(1) check if a product is hidden.
    pub fn is_product_hidden(&self, key: &str) -> bool {
        self.hidden_products.contains(key)
    }

    /// Content matching with priority hierarchy:
    /// 1. Pending / Failed orders
    /// 2. Cancelled Timeout orders (recoverable)
    /// 3. Fallback matching any order
    pub fn find_order_by_content<'a>(
        &self,
        content: &str,
        data: &'a DatabaseData,
    ) -> Option<(String, &'a OrderRecord)> {
        let clean = content
            .chars()
            .filter(|c| !c.is_whitespace() && *c != '-' && *c != '\n')
            .collect::<String>()
            .to_uppercase();

        // Priority 1: Pending & Failed
        let mut priority_codes = HashSet::new();
        if let Some(set) = self.orders_by_status.get("pending") {
            priority_codes.extend(set.iter());
        }
        if let Some(set) = self.orders_by_status.get("failed") {
            priority_codes.extend(set.iter());
        }

        for code in &priority_codes {
            if let Some(order) = data.orders.get(*code) {
                let transfer = order.transfer_content.as_deref().unwrap_or("").replace('-', "").to_uppercase();
                if clean.contains(*code) || (!transfer.is_empty() && clean.contains(&transfer)) {
                    return Some(((*code).clone(), order));
                }
            }
        }

        // Priority 2: Cancelled Timeout (late payment recovery)
        if let Some(timeout_set) = self.orders_by_status.get("cancelled_timeout") {
            for code in timeout_set {
                if let Some(order) = data.orders.get(code) {
                    let transfer = order.transfer_content.as_deref().unwrap_or("").replace('-', "").to_uppercase();
                    if clean.contains(code) || (!transfer.is_empty() && clean.contains(&transfer)) {
                        return Some((code.clone(), order));
                    }
                }
            }
        }

        // Fallback: any order
        for (code, order) in &data.orders {
            let transfer = order.transfer_content.as_deref().unwrap_or("").replace('-', "").to_uppercase();
            if clean.contains(code) || (!transfer.is_empty() && clean.contains(&transfer)) {
                return Some((code.clone(), order));
            }
        }

        None
    }
}
