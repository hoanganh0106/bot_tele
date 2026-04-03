"""
Module gọi CTV API (đối tác).
Tối ưu: dùng requests.Session() cho connection pooling, retry logic.
"""

import os
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import logging

logger = logging.getLogger(__name__)

# Test product flag — set ENABLE_TEST_PRODUCT=1 trong config.env để bật
ENABLE_TEST_PRODUCT = os.getenv("ENABLE_TEST_PRODUCT", "0") == "1"


class CTVApi:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

        # Connection pooling + auto retry
        self.session = requests.Session()
        self.session.headers.update({
            "X-API-KEY": api_key,
            "Content-Type": "application/json"
        })
        retry_strategy = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET"]  # Chỉ retry GET, không retry POST (tránh mua trùng)
        )
        adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=5, pool_maxsize=10)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def get_stock(self):
        """Lấy danh sách sản phẩm + stock + giá + số dư.
        Returns: (products_dict, balance) hoặc (None, 0) nếu lỗi
        """
        try:
            r = self.session.get(f"{self.base_url}/api/dealer/stock", timeout=10)
            data = r.json()
            if data.get("success"):
                products = data["products"]

                if ENABLE_TEST_PRODUCT:
                    products["test_product"] = {
                        "name": "🔥 TEST SEPAY - 5K VNĐ",
                        "price": 5000,
                        "stock": 999
                    }

                return products, data.get("balance", 0)
            else:
                logger.error(f"API stock error: {data.get('error')}")
                return None, 0
        except requests.exceptions.ConnectionError:
            logger.error("API connection error - server down?")
            return None, 0
        except requests.exceptions.Timeout:
            logger.error("API timeout")
            return None, 0
        except Exception as e:
            logger.error(f"API unexpected error: {e}")
            return None, 0

    def get_balance(self):
        """Lấy số dư CTV."""
        try:
            r = self.session.get(f"{self.base_url}/api/dealer/balance", timeout=10)
            data = r.json()
            if data.get("success"):
                return data.get("balance", 0)
            return 0
        except Exception as e:
            logger.error(f"Balance check error: {e}")
            return 0

    def buy(self, product_key: str, qty: int, emails: list = None, order_code: str = None):
        """Mua hàng từ API.
        Returns: dict với success, items, total_charged, etc.
        """
        try:
            # Intercept test product
            if product_key == "test_product" and ENABLE_TEST_PRODUCT:
                return {
                    "success": True,
                    "items": [f"TEST_ACCOUNT_{i}@gmail.com|pass123|Ảo" for i in range(qty)],
                    "total_charged": 5000 * qty,
                    "api_order_code": f"FAKE_ORDER_{qty}"
                }

            body = {"product_key": product_key, "qty": qty}
            if emails:
                body["emails"] = emails
            if order_code:
                body["order_code"] = order_code

            timeout = 180 if product_key == "slot_gpt_team" else 30

            r = self.session.post(
                f"{self.base_url}/api/dealer/buy",
                json=body,
                timeout=timeout
            )

            # Check HTTP errors
            if r.status_code == 401:
                return {"success": False, "error": "Thiếu API key (401)"}
            elif r.status_code == 403:
                return {"success": False, "error": "API key sai (403)"}
            elif r.status_code == 404:
                return {"success": False, "error": f"Sản phẩm '{product_key}' không tồn tại (404)"}

            return r.json()

        except requests.exceptions.ConnectionError:
            return {"success": False, "error": "Không kết nối được server đối tác"}
        except requests.exceptions.Timeout:
            return {"success": False, "error": "Timeout — server đối tác không phản hồi"}
        except ValueError:
            return {"success": False, "error": "Server trả về dữ liệu không hợp lệ"}
        except Exception as e:
            return {"success": False, "error": str(e)}
