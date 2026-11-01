//! Comprehensive test suite for Configuration Loader.

use std::collections::HashMap;
use std::env;
use std::path::PathBuf;
use std::sync::Mutex;
use bot_tele::config::{Config, ConfigError};

static ENV_MUTEX: Mutex<()> = Mutex::new(());

fn reset_test_env() {
    env::remove_var("TELEGRAM_BOT_TOKEN");
    env::remove_var("ADMIN_TELEGRAM_IDS");
    env::remove_var("CTV_API_KEY");
    env::remove_var("CTV_API_URL");
    env::remove_var("HYPERVIN_API_KEY");
    env::remove_var("HYPERVIN_API_URL");
    env::remove_var("HYPERVIN_LOW_BALANCE_ALERT");
    env::remove_var("WEBHOOK_PORT");
    env::remove_var("BANK_NAME");
    env::remove_var("BANK_ACCOUNT_NUMBER");
    env::remove_var("BANK_ACCOUNT_NAME");
    env::remove_var("BANK_BIN");
    env::remove_var("BINANCE_API_KEY");
    env::remove_var("BINANCE_API_SECRET");
    env::remove_var("USDT_WALLET_ADDRESS");
    env::remove_var("BINANCE_USDT_ADDRESS");
    env::remove_var("USDT_NETWORK");
    env::remove_var("BINANCE_USDT_NETWORK");
    env::remove_var("USDT_VND_RATE");
    env::remove_var("BINANCE_POLL_INTERVAL");
    env::remove_var("CRYPTO_ORDER_TIMEOUT_SECONDS");
    env::remove_var("BINANCE_POLL_FAIL_ALERT_THRESHOLD");
}

#[test]
fn test_missing_bot_token_fails() {
    let _lock = ENV_MUTEX.lock().unwrap();
    reset_test_env();
    env::set_var("CTV_API_KEY", "test_ctv_key");

    let result = Config::from_env();
    assert!(result.is_err());
    match result.unwrap_err() {
        ConfigError::MissingRequiredField(field) => assert_eq!(field, "TELEGRAM_BOT_TOKEN"),
        ConfigError::MissingVar(field) => assert_eq!(field, "TELEGRAM_BOT_TOKEN"),
        other => panic!("Expected MissingRequiredField, got {:?}", other),
    }
}

#[test]
fn test_default_values_populated() {
    let _lock = ENV_MUTEX.lock().unwrap();
    reset_test_env();
    env::set_var("TELEGRAM_BOT_TOKEN", "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11");
    env::set_var("CTV_API_KEY", "ctv_key_mock");

    let config = Config::from_env().expect("Should load config with defaults");
    assert_eq!(config.bot_token, "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11");
    assert_eq!(config.ctv_api_key, "ctv_key_mock");
    assert_eq!(config.ctv_api_url, "http://103.69.87.202:5000");
    assert_eq!(config.hypervin_api_url, "https://hypervin.xyz");
    assert_eq!(config.webhook_port, 8443);
    assert_eq!(config.bank_name, "Vietcombank");
    assert_eq!(config.bank_bin, "970436");
    assert_eq!(config.usdt_network, "BEP20");
    assert_eq!(config.usdt_vnd_rate, 26_500);
    assert_eq!(config.binance_poll_interval_secs, 25);
    assert_eq!(config.crypto_order_timeout_secs, 1800);
    assert_eq!(config.binance_poll_fail_alert_threshold, 5);
}

#[test]
fn test_out_of_range_clamping() {
    let _lock = ENV_MUTEX.lock().unwrap();
    reset_test_env();
    env::set_var("TELEGRAM_BOT_TOKEN", "mock_token");
    env::set_var("CTV_API_KEY", "mock_ctv");

    // Test below minimum limits
    env::set_var("BINANCE_POLL_INTERVAL", "5"); // min 15
    env::set_var("CRYPTO_ORDER_TIMEOUT_SECONDS", "30"); // min 60
    env::set_var("BINANCE_POLL_FAIL_ALERT_THRESHOLD", "1"); // min 2
    env::set_var("HYPERVIN_LOW_BALANCE_ALERT", "-500"); // min 0
    env::set_var("USDT_VND_RATE", "5000"); // clamped to 26500 default if <10000

    let config = Config::from_env().expect("Config should parse and clamp values");
    assert_eq!(config.binance_poll_interval_secs, 15);
    assert_eq!(config.crypto_order_timeout_secs, 60);
    assert_eq!(config.binance_poll_fail_alert_threshold, 2);
    assert_eq!(config.hypervin_low_balance_alert, 0);
    assert_eq!(config.usdt_vnd_rate, 26_500);
}

#[test]
fn test_admin_ids_parsing_with_whitespace_and_invalids() {
    let _lock = ENV_MUTEX.lock().unwrap();
    reset_test_env();
    env::set_var("TELEGRAM_BOT_TOKEN", "mock_token");
    env::set_var("CTV_API_KEY", "mock_ctv");
    env::set_var("ADMIN_TELEGRAM_IDS", " 112233 , 445566, not_a_num, 778899 , ");

    let config = Config::from_env().expect("Config should parse admin IDs");
    assert_eq!(config.admin_ids, vec![112233, 445566, 778899]);
}

#[test]
fn test_derived_paths() {
    let mut map = HashMap::new();
    map.insert("TELEGRAM_BOT_TOKEN".into(), "123456:ABC-DEF1234ghIkl".into());
    map.insert("DATA_DIR".into(), "/custom/data/path".into());
    let cfg = Config::from_map(&map).unwrap();

    assert_eq!(cfg.db_path(), PathBuf::from("/custom/data/path/bot_data.json"));
    assert_eq!(cfg.backup_dir(), PathBuf::from("/custom/data/path/backups"));
    assert_eq!(cfg.archive_path(), PathBuf::from("/custom/data/path/bot_data_archive.json"));
}
