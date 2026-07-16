from test_binance import check_pay_history


def test_pay_history_check_reports_transaction_count():
    class Client:
        api_key = "key"
        api_secret = "secret"

        def get_pay_transactions(self, start_time_ms):
            assert isinstance(start_time_ms, int)
            return [{"transactionId": "M_P_1"}, {"transactionId": "M_P_2"}]

    passed, message = check_pay_history(Client())

    assert passed is True
    assert "2" in message
