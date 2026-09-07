import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from handlers import phone_rental as h
from core.phone_rental import customer_message, decode_response, NUMBER_FAULT_MESSAGE, parse_otp, PhoneNumberFault


def test_plain_text_status_and_private_fields():
    assert customer_message(decode_response(NUMBER_FAULT_MESSAGE)) == "đang chờ OTP"
    for payload in (
        {"message": "Traceback: /home/ubuntu/private.py", "token": "secret"},
        {"error": "https://private.example/api?token=secret"},
        {"data": {"debug": "JSONDecodeError", "password": "secret"}},
    ):
        assert customer_message(payload) == "Chưa có OTP. Vui lòng chờ…"


def test_poll_updates_then_records_otp(monkeypatch):
    state = {"phone": "012345678", "prefix": "855", "token": "a" * 16}
    db = Mock()
    db.get_setting.return_value = state
    db.record_phone_otp.return_value = True
    monkeypatch.setattr(h, "db", db)
    screen = AsyncMock()
    monkeypatch.setattr(h, "show_screen", screen)
    monkeypatch.setattr(h.asyncio, "sleep", AsyncMock())
    responses = iter([None, {"message": "Your login code: 001234. Do not share it."}])

    async def request(*args, on_response, **kwargs):
        value = next(responses)
        if value is None:
            raise PhoneNumberFault(NUMBER_FAULT_MESSAGE)
        await on_response(str(value))
        return value

    monkeypatch.setattr(h, "request_api", request)
    asyncio.run(h.poll_otp(SimpleNamespace(from_user=SimpleNamespace(id=42)), state))
    assert screen.await_count == 2
    assert screen.await_args_list[0].kwargs["waiting"] is True
    assert screen.await_args_list[1].kwargs["otp"] == "001234"
    db.record_phone_otp.assert_called_once_with(42, state["token"])


def test_change_requests_confirmation_without_charging(monkeypatch):
    state = {"phone": "012345678", "token": "a" * 16}
    db = Mock()
    db.get_setting.return_value = state
    monkeypatch.setattr(h, "db", db)
    query = SimpleNamespace(data="phone_change_" + state["token"],
                            from_user=SimpleNamespace(id=42), answer=AsyncMock(),
                            edit_message_text=AsyncMock())
    update = SimpleNamespace(callback_query=query, effective_chat=SimpleNamespace(type="private"))
    context = SimpleNamespace(user_data={})
    asyncio.run(h.handle_phone_rental(update, context))
    assert "phone_confirm" in context.user_data
    assert "4.000đ" in query.edit_message_text.await_args.args[0]
    db.reserve_phone_rental.assert_not_called()


def test_poll_stops_at_deadline(monkeypatch):
    state = {"phone": "012345678", "token": "a" * 16}
    db = Mock()
    db.get_setting.return_value = state
    monkeypatch.setattr(h, "db", db)
    screen = AsyncMock()
    monkeypatch.setattr(h, "show_screen", screen)
    clock = SimpleNamespace(now=0)
    monkeypatch.setattr(h, "time", SimpleNamespace(monotonic=lambda: clock.now))

    async def sleep(delay):
        clock.now += delay

    monkeypatch.setattr(h.asyncio, "sleep", sleep)
    request = AsyncMock(return_value={"status": "pending"})
    monkeypatch.setattr(h, "request_api", request)
    asyncio.run(h.poll_otp(SimpleNamespace(from_user=SimpleNamespace(id=42)), state))
    assert request.await_count == 36
    assert clock.now == 180
    assert "Đã hết 3 phút" in screen.await_args.args[2]
    db.record_phone_otp.assert_not_called()


def test_six_digit_extraction():
    assert parse_otp(decode_response("Your OTP is 001234.")) == "001234"
    assert parse_otp({"data": {"message": "Mã xác nhận: 987654"}}) == "987654"
    assert parse_otp({"otp": "12345"}) is None
    assert parse_otp({"message": "phone 182957569"}) is None
    assert parse_otp({"message": "123456 or 654321"}) is None
    assert parse_otp({"debug": "123456"}) is None


def test_loading_and_copyable_otp(monkeypatch):
    db = Mock()
    db.get_setting.return_value = "Thuê số"
    monkeypatch.setattr(h, "db", db)
    monkeypatch.setattr(h, "ui_btn", lambda *a, **kw: h.InlineKeyboardButton("Home", callback_data="back_start"))
    query = SimpleNamespace(from_user=SimpleNamespace(id=42), edit_message_text=AsyncMock())
    asyncio.run(h.show_screen(query, None, waiting=True))
    assert 'emoji-id="5215579104807497179"' in query.edit_message_text.await_args.args[0]
    asyncio.run(h.show_screen(query, None, otp="001234"))
    text = query.edit_message_text.await_args.args[0]
    assert "<code>001234</code>" in text
    assert "tg-emoji" not in text
