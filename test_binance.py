"""Kiểm tra cấu hình Binance chỉ-đọc mà không đụng tới bot hoặc database."""

from __future__ import annotations

import os
import sys
import time

from dotenv import load_dotenv
import requests

from binance_client import BinanceAPIError, BinanceClient


TIME_URL = f"{BinanceClient.BASE_URL}/api/v3/time"
MAX_CLOCK_DRIFT_SECONDS = 5
DAY_MS = 24 * 60 * 60 * 1000


def configure_stdout() -> None:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure:
        reconfigure(encoding="utf-8", errors="replace")


def configured(value: str) -> bool:
    value = (value or "").strip()
    return bool(value) and not value.upper().startswith("YOUR_")


def explain_binance_error(exc: BinanceAPIError) -> str:
    if exc.code == -1021:
        return "Đồng hồ máy bị lệch. Hãy bật đồng bộ NTP rồi chạy lại."
    if exc.code in (-2014, -2015):
        return (
            "API key/secret sai, chưa bật Enable Reading, hoặc IP hiện tại không nằm "
            "trong whitelist. Nếu key giới hạn IP, hãy chạy script trên EC2."
        )
    if exc.status_code in (418, 429):
        return "Binance đang rate-limit IP. Chờ vài phút rồi chạy lại."
    return (
        f"{exc}. Kiểm tra API key, quyền Enable Reading, IP whitelist và kết nối mạng."
    )


def check_server_time() -> tuple[bool, str]:
    try:
        response = requests.get(TIME_URL, timeout=10)
        response.raise_for_status()
        payload = response.json()
        server_time_ms = int(payload["serverTime"])
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        return False, f"Không gọi được Binance public API: {exc}. Kiểm tra mạng/firewall/DNS."

    drift_seconds = abs((time.time() * 1000) - server_time_ms) / 1000
    if drift_seconds > MAX_CLOCK_DRIFT_SECONDS:
        return (
            False,
            f"Lệch giờ {drift_seconds:.2f}s (> {MAX_CLOCK_DRIFT_SECONDS}s). "
            "Hãy đồng bộ NTP để tránh lỗi Binance -1021.",
        )
    return True, f"Kết nối được; đồng hồ lệch {drift_seconds:.2f}s."


def check_deposit_address(
    client: BinanceClient,
    network: str,
    expected_wallet: str,
) -> tuple[bool, str]:
    if not configured(client.api_key) or not configured(client.api_secret):
        return False, "Thiếu BINANCE_API_KEY/BINANCE_API_SECRET trong config.env."
    if not configured(expected_wallet):
        return False, "Thiếu USDT_WALLET_ADDRESS trong config.env."

    try:
        payload = client.get_deposit_address("USDT", network)
    except BinanceAPIError as exc:
        return False, explain_binance_error(exc)

    returned_wallet = str(payload.get("address", "")).strip()
    if not returned_wallet:
        return False, f"Binance không trả về địa chỉ USDT cho network {network}."
    if returned_wallet.casefold() != expected_wallet.casefold():
        return (
            False,
            "Địa chỉ ví không khớp config.env. "
            f"Binance: {returned_wallet}; config: {expected_wallet}.",
        )
    return True, f"Địa chỉ nạp USDT {network} khớp config.env: {returned_wallet}."


def check_deposit_history(client: BinanceClient) -> tuple[bool, str]:
    if not configured(client.api_key) or not configured(client.api_secret):
        return False, "Thiếu API key/secret nên không thể đọc lịch sử nạp."

    start_time_ms = int(time.time() * 1000) - DAY_MS
    try:
        deposits = client.get_deposit_history(start_time_ms)
    except BinanceAPIError as exc:
        return False, explain_binance_error(exc)
    return True, f"Đọc được lịch sử nạp 24 giờ: {len(deposits)} giao dịch hoàn tất."


def check_pay_history(client: BinanceClient) -> tuple[bool, str]:
    """Verify the read-only Binance Pay history permission from the whitelisted host."""
    if not configured(client.api_key) or not configured(client.api_secret):
        return False, "Thiếu API key/secret nên không thể đọc lịch sử Binance Pay."

    start_time_ms = int(time.time() * 1000) - DAY_MS
    try:
        transactions = client.get_pay_transactions(start_time_ms)
    except BinanceAPIError as exc:
        return False, explain_binance_error(exc) + " Kiểm tra API key đã có quyền đọc Binance Pay."
    return True, f"Đọc được lịch sử Binance Pay 24 giờ: {len(transactions)} giao dịch."


def report(index: int, title: str, result: tuple[bool, str]) -> bool:
    passed, detail = result
    icon = "✅" if passed else "❌"
    print(f"{icon} {index}. {title}: {detail}")
    return passed


def main() -> int:
    configure_stdout()
    load_dotenv("config.env")

    api_key = os.getenv("BINANCE_API_KEY", "").strip()
    api_secret = os.getenv("BINANCE_API_SECRET", "").strip()
    wallet = os.getenv("USDT_WALLET_ADDRESS", "").strip()
    network = os.getenv("USDT_NETWORK", "BEP20").strip().upper() or "BEP20"
    client = BinanceClient(api_key, api_secret)

    print("BINANCE USDT READ-ONLY CHECK")
    print("Script chỉ gửi GET; không tạo giao dịch và không đọc/ghi database.\n")

    results = [
        report(1, "Kết nối và đồng hồ", check_server_time()),
        report(2, "Địa chỉ nạp USDT", check_deposit_address(client, network, wallet)),
        report(3, "Lịch sử nạp 24 giờ", check_deposit_history(client)),
        report(4, "Lịch sử Binance Pay 24 giờ", check_pay_history(client)),
    ]
    passed_count = sum(results)
    if passed_count == len(results):
        print("\n4/4 PASS")
        return 0

    failed = ", ".join(str(index + 1) for index, passed in enumerate(results) if not passed)
    print(f"\n{passed_count}/4 PASS; mục lỗi: {failed}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
