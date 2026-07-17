"""HTTP client for Hypervin's supplier API."""

import logging
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class HypervinApi:
    """Normalize Hypervin responses to the supplier contract used by the bot."""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"X-API-Key": api_key, "Content-Type": "application/json"})

        retry = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET"],
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=5, pool_maxsize=10)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def get_products(self) -> dict[str, dict[str, Any]] | None:
        """Return Hypervin products in the shared bot-product namespace."""
        try:
            data = self.session.get(f"{self.base_url}/api/products", timeout=5).json()
            if not isinstance(data, dict) or data.get("success") is not True or not isinstance(data.get("products"), list):
                logger.error("Hypervin returned an invalid products response")
                return None

            products: dict[str, dict[str, Any]] = {}
            for product in data["products"]:
                if not isinstance(product, dict):
                    logger.error("Hypervin returned a non-object product")
                    return None
                product_id = product.get("id")
                name = product.get("name")
                price = product.get("price")
                stock = product.get("stock")
                if product_id is None or not isinstance(name, str) or not isinstance(price, (int, float)) or not isinstance(stock, int):
                    logger.error("Hypervin returned a product with invalid fields")
                    return None
                products[f"hv_{product_id}"] = {
                    "name": name.strip(),
                    "price": price,
                    "stock": stock,
                    "api_source": "HYPERVIN",
                }
            return products
        except requests.exceptions.ConnectionError:
            logger.error("Hypervin connection error")
        except requests.exceptions.Timeout:
            logger.error("Hypervin products request timed out")
        except (ValueError, TypeError):
            logger.error("Hypervin returned invalid JSON for products")
        except Exception as exc:
            logger.error("Hypervin products request failed: %s", exc)
        return None

    def get_balance(self) -> int | None:
        """Return the partner wallet balance, or None when it cannot be read."""
        try:
            data = self.session.post(f"{self.base_url}/api/wallet/balance", json={}, timeout=5).json()
            if not isinstance(data, dict) or data.get("success") is not True:
                logger.error("Hypervin returned an invalid balance response")
                return None
            return int(data["balance"])
        except (KeyError, TypeError, ValueError):
            logger.error("Hypervin returned a balance with an invalid value")
        except requests.exceptions.ConnectionError:
            logger.error("Hypervin balance connection error")
        except requests.exceptions.Timeout:
            logger.error("Hypervin balance request timed out")
        except Exception as exc:
            logger.error("Hypervin balance request failed: %s", exc)
        return None

    def create_order(self, product_id: str, quantity: int) -> dict[str, Any]:
        """Create one order without retries to prevent duplicate purchases."""
        try:
            response = self.session.post(
                f"{self.base_url}/api/orders",
                json={"product_id": product_id, "quantity": quantity},
                timeout=30,
            )
            data = response.json()
            if not isinstance(data, dict):
                return {"success": False, "error": "Server trả về dữ liệu không hợp lệ"}
            if data.get("success") is not True:
                return {"success": False, "error": str(data.get("error") or "Đặt đơn Hypervin thất bại")}

            items = data.get("items") or data.get("accounts") or data.get("data")
            order_code = data.get("order_id") or data.get("order_code") or data.get("code")
            total_charged = data.get("total_charged") or data.get("total") or data.get("amount")
            if not isinstance(items, list) or order_code is None or total_charged is None:
                logger.error("Hypervin returned an unrecognized successful order response")
                return {"success": False, "error": "Hypervin trả về đơn hàng không đủ dữ liệu"}
            return {
                "success": True,
                "items": [str(item) for item in items],
                "total_charged": int(total_charged),
                "api_order_code": str(order_code),
            }
        except requests.exceptions.ConnectionError:
            return {"success": False, "error": "Không kết nối được server đối tác"}
        except requests.exceptions.Timeout:
            return {"success": False, "error": "Timeout — server đối tác không phản hồi"}
        except (TypeError, ValueError):
            return {"success": False, "error": "Server trả về dữ liệu không hợp lệ"}
        except Exception as exc:
            logger.error("Hypervin order request failed: %s", exc)
            return {"success": False, "error": str(exc)}
