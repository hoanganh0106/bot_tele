"""
Module gọi CTV API (đối tác).
"""

import requests
import logging

logger = logging.getLogger(__name__)


class CTVApi:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.headers = {
            "X-API-KEY": api_key,
            "Content-Type": "application/json"
        }

    def get_stock(self):
        """Lấy danh sách sản phẩm + stock + giá + số dư.
        Returns: (products_dict, balance) hoặc (None, 0) nếu lỗi
        """
        try:
            r = requests.get(
                f"{self.base_url}/api/dealer/stock",
                headers={"X-API-KEY": self.api_key},
                timeout=10
            )
            data = r.json()
            if data.get("success"):
                return data["products"], data.get("balance", 0)
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
            r = requests.get(
                f"{self.base_url}/api/dealer/balance",
                headers={"X-API-KEY": self.api_key},
                timeout=10
            )
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
            body = {
                "product_key": product_key,
                "qty": qty
            }
            if emails:
                body["emails"] = emails
            if order_code:
                body["order_code"] = order_code

            timeout = 180 if product_key == "slot_gpt_team" else 30

            r = requests.post(
                f"{self.base_url}/api/dealer/buy",
                headers=self.headers,
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
