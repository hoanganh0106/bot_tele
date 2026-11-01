//! Atomic transactional operations for wallets, orders, phone rentals, and incoming payments.

use super::models::{IncomingPaymentRecord, OrderRecord};
use super::store::DbStore;
use crate::error::DbError;

#[derive(Debug, Clone, Default)]
pub struct PartialWalletResult {
    pub wallet_amount: i64,
    pub remaining: i64,
    pub new_balance: i64,
    pub fully_paid: bool,
}

#[derive(Debug, Clone, Default)]
pub struct OrderUpdate {
    pub status: Option<String>,
    pub paid_at: Option<String>,
    pub items: Option<Vec<String>>,
    pub cost: Option<i64>,
    pub api_order_code: Option<String>,
    pub error: Option<String>,
    pub retry_count: Option<i64>,
    pub payment_source: Option<String>,
    pub refund_reason: Option<String>,
}

impl DbStore {
    /// Atomic order cancellation. Only cancels if the order is currently "pending".
    /// Eliminates race conditions between webhook fulfillment and auto-cancel timer.
    pub fn cancel_order_if_pending(&self, order_code: &str, status: &str) -> Result<Option<OrderRecord>, DbError> {
        if status != "cancelled" && status != "cancelled_timeout" {
            return Err(DbError::Validation("Unsupported cancellation status".into()));
        }

        let mut data = self.data.write();

        if let Some(order) = data.orders.get_mut(order_code) {
            if order.status != "pending" {
                return Ok(None);
            }
            order.status = status.to_string();
            let snapshot = order.clone();
            self.indexes.write().rebuild(&data);
            self.schedule_write(true);
            Ok(Some(snapshot))
        } else {
            Ok(None)
        }
    }

    /// Admin manual order cancellation.
    /// Permits cancelling orders in either "pending" or "failed" status.
    pub fn cancel_order_admin(&self, order_code: &str) -> Result<Option<OrderRecord>, DbError> {
        let mut data = self.data.write();

        if let Some(order) = data.orders.get_mut(order_code) {
            if order.status != "pending" && order.status != "failed" {
                return Ok(None);
            }
            order.status = "cancelled".to_string();
            let snapshot = order.clone();
            self.indexes.write().rebuild(&data);
            self.schedule_write(true);
            Ok(Some(snapshot))
        } else {
            Ok(None)
        }
    }

    /// Atomic Binance deposit claim.
    /// Deduplicates txId, locks the crypto order, and re-deducts partial wallet funds if recovered from timeout.
    pub fn claim_crypto_deposit(
        &self,
        order_code: &str,
        tx_id: &str,
        insert_time: i64,
    ) -> Result<bool, DbError> {
        if tx_id.is_empty() {
            return Ok(false);
        }

        let mut data = self.data.write();

        if self.indexes.read().is_crypto_txid_processed(tx_id) {
            return Ok(false);
        }

        let (user_id, original_status, wallet_refunded, wallet_paid) = match data.orders.get(order_code) {
            Some(o)
                if (o.status == "pending" || o.status == "cancelled_timeout")
                    && o.payment_method.as_deref().unwrap_or("").starts_with("crypto") =>
            {
                (o.user_id, o.status.clone(), o.wallet_refunded, o.wallet_paid)
            }
            _ => return Ok(false),
        };

        // Record txId into processed list (capped at 5,000)
        data.processed_crypto_txids.push(tx_id.to_string());
        if data.processed_crypto_txids.len() > 5000 {
            let excess = data.processed_crypto_txids.len() - 5000;
            data.processed_crypto_txids.drain(0..excess);
        }

        // Handle late deposit recovery for partial wallet payment
        if original_status == "cancelled_timeout" && wallet_refunded {
            let uid_str = user_id.to_string();
            let user = data.users.entry(uid_str).or_default();

            if wallet_paid > 0 && user.balance < wallet_paid {
                if let Some(order) = data.orders.get_mut(order_code) {
                    order.crypto_claim_status = Some("wallet_insufficient".to_string());
                    order.crypto_payment_confirmed = false;
                }
                self.indexes.write().rebuild(&data);
                self.schedule_write(true);
                return Ok(false);
            }

            if wallet_paid > 0 {
                user.balance -= wallet_paid;
                user.total_spent += wallet_paid;
            }
        }

        if let Some(order) = data.orders.get_mut(order_code) {
            order.crypto_txid = Some(tx_id.to_string());
            order.crypto_deposit_time = Some(insert_time);
            order.payment_source = Some("binance_usdt".to_string());
            if original_status == "cancelled_timeout" && wallet_refunded {
                order.wallet_refunded = false;
            }
            order.status = "processing".to_string();
            order.crypto_payment_confirmed = true;
            order.crypto_claim_status = Some("claimed".to_string());
            order.crypto_recovered_timeout = original_status == "cancelled_timeout";
        }

        self.indexes.write().rebuild(&data);
        self.schedule_write(true);
        Ok(true)
    }

    /// Atomic transition from pending/processing to completed payment state.
    pub fn complete_order_payment(
        &self,
        order_code: &str,
        updates: OrderUpdate,
    ) -> Result<Option<OrderRecord>, DbError> {
        let mut data = self.data.write();

        let order = match data.orders.get_mut(order_code) {
            Some(o)
                if matches!(
                    o.status.as_str(),
                    "pending" | "processing" | "failed" | "cancelled_timeout" | "paid_waiting_email"
                ) =>
            {
                o
            }
            _ => return Ok(None),
        };

        if let Some(s) = updates.status {
            order.status = s;
        }
        if let Some(p) = updates.paid_at {
            order.paid_at = Some(p);
        }
        if let Some(it) = updates.items {
            order.items = it;
        }
        if let Some(c) = updates.cost {
            order.cost = c;
        }
        if let Some(api_code) = updates.api_order_code {
            order.api_order_code = Some(api_code);
        }
        if let Some(err) = updates.error {
            order.error = Some(err);
        }
        if let Some(r) = updates.retry_count {
            order.retry_count = r;
        }
        if let Some(ps) = updates.payment_source {
            order.payment_source = Some(ps);
        }
        if let Some(rr) = updates.refund_reason {
            order.refund_reason = Some(rr);
        }

        let snapshot = order.clone();
        self.indexes.write().rebuild(&data);
        self.schedule_write(true);
        Ok(Some(snapshot))
    }

    /// Deduct wallet portion atomically and update order for partial wallet flow.
    pub fn start_partial_wallet_payment(
        &self,
        order_code: &str,
        user_id: i64,
    ) -> Result<Option<PartialWalletResult>, DbError> {
        let mut data = self.data.write();

        let total = match data.orders.get(order_code) {
            Some(o) if o.status == "pending" && o.user_id == user_id && o.wallet_paid == 0 => o.total,
            _ => return Ok(None),
        };

        if total <= 0 {
            return Ok(None);
        }

        let uid_str = user_id.to_string();
        let user = data.users.entry(uid_str).or_default();
        if user.balance <= 0 {
            return Ok(None);
        }

        let wallet_amount = std::cmp::min(user.balance, total);
        let remaining = total - wallet_amount;
        user.balance -= wallet_amount;
        user.total_spent += wallet_amount;
        let new_balance = user.balance;

        if let Some(order) = data.orders.get_mut(order_code) {
            if remaining == 0 {
                order.status = "processing".to_string();
                order.payment_method = Some("wallet".to_string());
                order.wallet_payment_confirmed = true;
                order.payment_source = Some("wallet".to_string());
                order.wallet_paid = wallet_amount;
                order.remaining_amount = 0;
            } else {
                order.payment_method = Some("bank_partial".to_string());
                order.wallet_paid = wallet_amount;
                order.wallet_refunded = false;
                order.remaining_amount = remaining;
                order.original_total = Some(total);
                order.total = remaining;
            }
        }

        self.indexes.write().rebuild(&data);
        self.schedule_write(true);

        Ok(Some(PartialWalletResult {
            wallet_amount,
            remaining,
            new_balance,
            fully_paid: remaining == 0,
        }))
    }

    /// Atomically refund partial wallet payment once and flag `wallet_refunded = true`.
    pub fn refund_order_wallet_if_needed(&self, order_code: &str) -> (i64, i64) {
        let mut data = self.data.write();
        let (user_id, amount) = match data.orders.get(order_code) {
            Some(o) if o.wallet_paid > 0 && !o.wallet_refunded => (o.user_id, o.wallet_paid),
            Some(o) => {
                let uid_str = o.user_id.to_string();
                let bal = data.users.get(&uid_str).map(|u| u.balance).unwrap_or(0);
                return (0, bal);
            }
            None => return (0, 0),
        };

        if let Some(order) = data.orders.get_mut(order_code) {
            order.wallet_refunded = true;
        }

        let uid_str = user_id.to_string();
        let user = data.users.entry(uid_str).or_default();
        user.balance += amount;
        user.total_spent = (user.total_spent - amount).max(0);

        let new_balance = user.balance;
        self.schedule_write(true);
        (amount, new_balance)
    }

    /// Re-deducts refunded wallet if order payment is confirmed late.
    pub fn restore_refunded_wallet_for_order(&self, order_code: &str) -> Result<bool, DbError> {
        let mut data = self.data.write();
        let (user_id, amount) = match data.orders.get(order_code) {
            Some(o) if o.wallet_paid > 0 && o.wallet_refunded => (o.user_id, o.wallet_paid),
            _ => return Ok(false),
        };

        let uid_str = user_id.to_string();
        let user = data.users.entry(uid_str).or_default();
        if user.balance < amount {
            return Ok(false);
        }

        user.balance -= amount;
        user.total_spent += amount;

        if let Some(order) = data.orders.get_mut(order_code) {
            order.wallet_refunded = false;
        }

        self.schedule_write(true);
        Ok(true)
    }

    /// Confirm 100% wallet payment for an order.
    pub fn confirm_wallet_payment(
        &self,
        order_code: &str,
        user_id: i64,
        amount: i64,
    ) -> Result<bool, DbError> {
        if amount <= 0 {
            return Ok(false);
        }

        let mut data = self.data.write();
        let uid_str = user_id.to_string();

        let (order_user_id, status, wallet_confirmed, wallet_paid) = match data.orders.get(order_code) {
            Some(o) => (o.user_id, o.status.clone(), o.wallet_payment_confirmed, o.wallet_paid),
            None => return Ok(false),
        };

        if order_user_id != user_id || status != "pending" || wallet_confirmed || wallet_paid > 0 {
            return Ok(false);
        }

        let user_has_balance = data
            .users
            .get(&uid_str)
            .map(|u| u.balance >= amount)
            .unwrap_or(false);

        if !user_has_balance {
            return Ok(false);
        }

        let user = data.users.entry(uid_str).or_default();
        user.balance -= amount;
        user.total_spent += amount;

        if let Some(order) = data.orders.get_mut(order_code) {
            order.status = "processing".to_string();
            order.payment_method = Some("wallet".to_string());
            order.wallet_paid = amount;
            order.wallet_payment_confirmed = true;
            order.payment_source = Some("wallet".to_string());
        }

        self.indexes.write().rebuild(&data);
        self.schedule_write(true);
        Ok(true)
    }

    /// Atomically deduct balance from user wallet.
    pub fn deduct_wallet(&self, user_id: i64, amount: i64) -> Result<i64, DbError> {
        if amount <= 0 {
            return Err(DbError::Validation("Deduction amount must be positive".into()));
        }

        let mut data = self.data.write();
        let uid_str = user_id.to_string();
        let user = match data.users.get_mut(&uid_str) {
            Some(u) if u.balance >= amount => u,
            _ => return Err(DbError::InsufficientFunds("Wallet balance insufficient".into())),
        };

        user.balance -= amount;
        user.total_spent += amount;
        let new_balance = user.balance;
        self.schedule_write(true);
        Ok(new_balance)
    }

    /// Atomically credit balance to user wallet (default deposit).
    pub fn credit_wallet(&self, user_id: i64, amount: i64) -> Result<i64, DbError> {
        self.credit_wallet_with_reason(user_id, amount, "deposit")
    }

    /// Atomically credit balance to user wallet with reason tracking.
    pub fn credit_wallet_with_reason(
        &self,
        user_id: i64,
        amount: i64,
        reason: &str,
    ) -> Result<i64, DbError> {
        if amount <= 0 {
            return Err(DbError::Validation("Credit amount must be positive".into()));
        }

        let mut data = self.data.write();
        let uid_str = user_id.to_string();
        let user = data.users.entry(uid_str).or_default();

        user.balance += amount;
        if reason == "deposit" {
            user.total_deposited += amount;
        } else if reason == "refund" {
            user.total_spent = (user.total_spent - amount).max(0);
        }

        let new_balance = user.balance;
        self.schedule_write(true);
        Ok(new_balance)
    }

    /// Store incoming payment from SePay webhook; dedupes and notifies background processor.
    pub fn store_incoming_payment(&self, payment: IncomingPaymentRecord) -> bool {
        let stored = {
            let mut data = self.data.write();
            if data.incoming_payments.iter().any(|p| p.id == payment.id) {
                false
            } else {
                data.incoming_payments.push(payment);
                if data.incoming_payments.len() > 500 {
                    let excess = data.incoming_payments.len() - 500;
                    data.incoming_payments.drain(0..excess);
                }
                self.schedule_write(true);
                true
            }
        };

        if stored {
            if let Some(notify) = self.payment_wakeup.read().as_ref() {
                notify.notify_one();
            }
        }

        stored
    }
}
