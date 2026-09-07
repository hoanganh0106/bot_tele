"""Phone supplier protocol; requests are never automatically retried."""

import asyncio
import os
import re

import aiohttp


DEFAULT_BUTTON_NAME = "📱 Thuê số"
RENTAL_PRICE = 4000
NUMBER_FAULT_MESSAGE = "Message not found or Archived for another partner"


class PhoneApiError(Exception):
    pass


class PhoneNumberFault(PhoneApiError):
    """The supplier explicitly reports the configured number fault."""


def validate_response(payload, status, path):
    if not isinstance(payload, dict):
        raise PhoneApiError("API trả về dữ liệu không hợp lệ.")
    containers = [payload]
    if isinstance(payload.get("data"), dict):
        containers.append(payload["data"])
    # A code/OTP takes precedence over a contradictory error message.
    has_otp = any(c.get(k) not in (None, "") for c in containers for k in ("otp", "code"))
    fault = any(c.get(k) == NUMBER_FAULT_MESSAGE for c in containers for k in ("message", "error"))
    if path == "/get-otp" and status in (200, 400, 404) and fault and not has_otp:
        raise PhoneNumberFault(NUMBER_FAULT_MESSAGE)
    if status != 200 or payload.get("error") or payload.get("success") is False:
        raise PhoneApiError(f"API chưa thực hiện được yêu cầu (HTTP {status}). Vui lòng thử lại sau.")
    return payload


async def request_api(path, **params):
    base = os.getenv(
        "PHONE_RENTAL_API_URL",
        "https://cultural-webshots-track-say.trycloudflare.com",
    ).strip().rstrip("/")
    if not base.startswith("https://"):
        raise PhoneApiError("URL API thuê số phải dùng HTTPS.")
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.get(base + path, params=params, allow_redirects=False) as response:
                payload = await response.json(content_type=None)
                return validate_response(payload, response.status, path)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        raise PhoneApiError("Không kết nối được API hoặc phản hồi không hợp lệ. Vui lòng thử lại sau.") from exc


def parse_phone(payload):
    data = payload.get("data")
    if not isinstance(data, dict):
        raise PhoneApiError("API không trả về số điện thoại hợp lệ.")
    phone = str(data.get("phone") or "").strip()
    prefix = str(data.get("prefix") or "").strip().lstrip("+")
    if not re.fullmatch(r"\+?[0-9]{5,15}", phone) or (prefix and not re.fullmatch(r"[0-9]{1,4}", prefix)):
        raise PhoneApiError("API không trả về số điện thoại hợp lệ.")
    # Preserve the exact supplier phone for the OTP query, including leading zeroes.
    return {"phone": phone, "prefix": prefix}


def parse_otp(payload):
    data = payload.get("data", payload)
    if data is None:
        return None
    if not isinstance(data, dict):
        raise PhoneApiError("Định dạng OTP chưa được hỗ trợ; vui lòng liên hệ admin.")
    for key in ("otp", "code"):
        value = data.get(key)
        if value is not None and value != "":
            if not isinstance(value, (str, int)) or isinstance(value, bool):
                raise PhoneApiError("API trả về OTP không hợp lệ.")
            return str(value)[:512]
    if any(key in data for key in ("otp", "code")) or payload.get("status") in ("pending", "waiting"):
        return None
    raise PhoneApiError("Chưa nhận diện được phản hồi OTP; vui lòng liên hệ admin.")


async def get_phone():
    return parse_phone(await request_api("/get_phone"))


async def get_otp(phone):
    return parse_otp(await request_api("/get-otp", phone=phone))
