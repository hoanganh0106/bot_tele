"""Phone supplier protocol; requests are never automatically retried."""

import asyncio
import os
import re
import json
import hashlib
from datetime import datetime

import aiohttp


DEFAULT_BUTTON_NAME = "📱 Thuê số"
RENTAL_PRICE = 4000
NUMBER_FAULT_MESSAGE = "Message not found or Archived for another partner"


class PhoneApiError(Exception):
    pass


class PhoneNumberFault(PhoneApiError):
    """The supplier explicitly reports the configured number fault."""


def decode_response(raw):
    # This endpoint also returns its number-fault message as plain text.
    if raw.strip() == NUMBER_FAULT_MESSAGE:
        return {"message": NUMBER_FAULT_MESSAGE}
    try:
        payload = json.loads(raw)
    except ValueError:
        return {"message": raw[:8000]}
    if isinstance(payload, (str, int)):
        return {"message": str(payload)}
    return payload


def customer_message(payload):
    """Only recognized public statuses may cross into customer messages."""
    containers = [payload] if isinstance(payload, dict) else []
    if containers and isinstance(payload.get("data"), dict):
        containers.append(payload["data"])
    if any(c.get(k) == NUMBER_FAULT_MESSAGE for c in containers for k in ("message", "error")):
        return "đang chờ OTP"
    return "Chưa có OTP. Vui lòng chờ…"


def validate_response(payload, status, path):
    if path == "/get-otp" and isinstance(payload, list):
        if status == 200 and all(isinstance(item, dict) for item in payload):
            return payload
        raise PhoneApiError("Chưa thể xử lý yêu cầu. Vui lòng thử lại sau.")
    if not isinstance(payload, dict):
        raise PhoneApiError("API trả về dữ liệu không hợp lệ.")
    containers = [payload]
    if isinstance(payload.get("data"), dict):
        containers.append(payload["data"])
    # A code/OTP takes precedence over a contradictory error message.
    has_otp = bool(parse_otp(payload)) if path == "/get-otp" else False
    fault = any(c.get(k) == NUMBER_FAULT_MESSAGE for c in containers for k in ("message", "error"))
    if path == "/get-otp" and status in (200, 400, 404) and fault and not has_otp:
        raise PhoneNumberFault(NUMBER_FAULT_MESSAGE)
    if status != 200 or payload.get("error") or payload.get("success") is False:
        raise PhoneApiError("Chưa thể xử lý yêu cầu. Vui lòng thử lại sau.")
    return payload


async def request_api(path, on_response=None, **params):
    base = os.getenv(
        "PHONE_RENTAL_API_URL",
        "https://cultural-webshots-track-say.trycloudflare.com",
    ).strip().rstrip("/")
    if not base.startswith("https://"):
        raise PhoneApiError("Dịch vụ tạm thời không khả dụng. Vui lòng thử lại sau.")
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.get(base + path, params=params, allow_redirects=False) as response:
                raw = await response.text()
                payload = decode_response(raw)
                if on_response:
                    await on_response(customer_message(payload))
                return validate_response(payload, response.status, path)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        raise PhoneApiError("Tạm thời chưa nhận được kết quả. Vui lòng chờ…") from exc


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
    """Extract a unique six-digit code from explicit OTP or message fields only."""
    if isinstance(payload, list):
        codes = {code for item in payload if (code := parse_otp(item))}
        return codes.pop() if len(codes) == 1 else None
    if not isinstance(payload, dict):
        return None
    if isinstance(payload.get("data"), list):
        return parse_otp(payload["data"])
    containers = [payload]
    if isinstance(payload.get("data"), dict):
        containers.insert(0, payload["data"])
    for keys in (("otp", "code"), ("message", "text", "sms", "content", "body")):
        codes = set()
        for container in containers:
            for key in keys:
                value = container.get(key)
                if isinstance(value, (str, int)) and not isinstance(value, bool):
                    codes.update(re.findall(r"(?<![\w])([0-9]{6})(?![\w])", str(value)))
        if len(codes) == 1:
            return codes.pop()
        if codes:
            return None
    if isinstance(payload.get("data"), str):
        return parse_otp({"message": payload["data"]})
    return None


async def get_phone(phone=None):
    params = {"phone": phone} if phone else {}
    result = parse_phone(await request_api("/get_phone", **params))
    if phone and result["phone"] != str(phone):
        raise PhoneApiError("Không lấy được đúng số đã yêu cầu.")
    return result


async def get_otp(phone):
    return parse_otp(await request_api("/get-otp", phone=phone))


def otp_messages(payload):
    """Normalize supplier timestamps without assuming its timezone matches ours."""
    if isinstance(payload, dict):
        payload = payload.get("data", payload)
    rows = payload if isinstance(payload, list) else [payload]
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            stamp = datetime.strptime(str(row.get("timestamp", "")), "%d%m%Y %H%M%S").strftime("%Y%m%d%H%M%S")
        except ValueError:
            continue
        code = parse_otp(row)
        identity = hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        result.append({"timestamp": stamp, "id": identity, "otp": code})
    return result


async def get_otp_baseline(phone):
    try:
        payload = await request_api("/get-otp", phone=phone)
    except PhoneNumberFault:
        payload = []
    messages = otp_messages(payload)
    if payload and not messages:
        raise PhoneApiError("Chưa thể bắt đầu phiên thuê lại. Vui lòng thử lại.")
    return {"timestamp": max((m["timestamp"] for m in messages), default=""),
            "ids": [m["id"] for m in messages]}


def newest_otp(payload, baseline):
    messages = [m for m in otp_messages(payload) if m["otp"]
                and m["timestamp"] > baseline["timestamp"] and m["id"] not in baseline["ids"]]
    if not messages:
        return None
    latest = max(m["timestamp"] for m in messages)
    candidates = [m for m in messages if m["timestamp"] == latest]
    if len({m["otp"] for m in candidates}) != 1:
        return None
    return candidates[0]
