from database import Database


def test_claim_crypto_deposit_persists_binance_pay_source(tmp_path):
    db = Database(str(tmp_path / "bot_data.json"))
    db.save_order(
        "PAY-ORDER",
        {
            "user_id": 42,
            "status": "pending",
            "payment_method": "crypto",
            "usdt_amount": "10.003",
        },
    )

    claimed = db.claim_crypto_deposit(
        "PAY-ORDER",
        "PAY:M_P_71505104267788288",
        1_700_000_000_000,
        payment_source="binance_pay",
    )

    assert claimed["payment_source"] == "binance_pay"
    assert db.get_order("PAY-ORDER")["payment_source"] == "binance_pay"
