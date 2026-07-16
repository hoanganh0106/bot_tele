"""Minimal signed Binance REST client used by the crypto payment poller."""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from urllib.parse import urlencode

import requests


logger = logging.getLogger(__name__)


class BinanceAPIError(RuntimeError):
    """Raised when Binance rejects a request or returns invalid data."""

    def __init__(self, message: str, *, status_code: int | None = None, code=None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class BinanceClient:
    BASE_URL = "https://api.binance.com"

    def __init__(self, api_key: str, api_secret: str, timeout: int = 10):
        self.api_key = api_key.strip()
        self.api_secret = api_secret.strip()
        self.timeout = timeout

    def _signed_request(self, method: str, path: str, params: dict | None = None):
        if not self.api_key or not self.api_secret:
            raise BinanceAPIError("Binance API credentials are not configured")

        signed_params = dict(params or {})
        signed_params["timestamp"] = int(time.time() * 1000)
        signed_params["recvWindow"] = 10_000
        query = urlencode(signed_params)
        signed_params["signature"] = hmac.new(
            self.api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        try:
            response = requests.request(
                method.upper(),
                f"{self.BASE_URL}{path}",
                params=signed_params,
                headers={"X-MBX-APIKEY": self.api_key},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise BinanceAPIError(f"Binance request failed: {exc}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise BinanceAPIError(
                f"Binance returned non-JSON response (HTTP {response.status_code})",
                status_code=response.status_code,
            ) from exc

        if response.status_code >= 400:
            code = payload.get("code") if isinstance(payload, dict) else None
            message = payload.get("msg") if isinstance(payload, dict) else str(payload)
            if code == -1021:
                logger.error("Binance timestamp rejected (-1021); synchronize the server clock with NTP")
            elif response.status_code in (418, 429):
                logger.warning("Binance rate limit response: HTTP %s", response.status_code)
            raise BinanceAPIError(
                f"Binance API error HTTP {response.status_code}, code={code}: {message}",
                status_code=response.status_code,
                code=code,
            )

        return payload

    def get_deposit_history(self, start_time_ms: int) -> list[dict]:
        payload = self._signed_request(
            "GET",
            "/sapi/v1/capital/deposit/hisrec",
            {
                "coin": "USDT",
                "status": 1,
                "startTime": int(start_time_ms),
                "limit": 100,
            },
        )
        if not isinstance(payload, list):
            raise BinanceAPIError("Binance deposit history response is not a list")
        return payload

    def get_pay_transactions(self, start_time_ms: int) -> list[dict]:
        """Return Binance Pay transactions received since ``start_time_ms``."""
        payload = self._signed_request(
            "GET",
            "/sapi/v1/pay/transactions",
            {"startTime": int(start_time_ms), "limit": 100},
        )
        if not isinstance(payload, dict):
            raise BinanceAPIError("Binance Pay transaction response is not an object")

        code = payload.get("code")
        if code != "000000" or not payload.get("success"):
            raise BinanceAPIError(
                f"Binance Pay transaction request failed: code={code}, message={payload.get('message', '')}",
                code=code,
            )

        data = payload.get("data", [])
        if not isinstance(data, list):
            raise BinanceAPIError("Binance Pay transaction response data is not a list", code=code)
        return data

    def get_deposit_address(self, coin: str = "USDT", network: str = "BEP20") -> dict:
        payload = self._signed_request(
            "GET",
            "/sapi/v1/capital/deposit/address",
            {"coin": coin, "network": network},
        )
        if not isinstance(payload, dict):
            raise BinanceAPIError("Binance deposit address response is not an object")
        return payload
