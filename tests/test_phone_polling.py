import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from handlers import phone_rental as h


def test_poll_updates_then_records_otp(monkeypatch):
    state = {"phone": "012345678", "prefix": "855", "token": "a" * 16}
    db = Mock()
    db.get_setting.return_value = state
    db.record_phone_otp.return_value = True
    monkeypatch.setattr(h, "db", db)
    screen = AsyncMock()
    monkeypatch.setattr(h, "show_screen", screen)
    monkeypatch.setattr(h.asyncio, "sleep", AsyncMock())
    responses = iter([{"status": "pending"}, {"otp": "001234"}])

    async def request(*args, on_response, **kwargs):
        value = next(responses)
        await on_response(str(value))
        return value

    monkeypatch.setattr(h, "request_api", request)
    asyncio.run(h.poll_otp(SimpleNamespace(from_user=SimpleNamespace(id=42)), state))
    assert screen.await_count == 2
    assert "pending" in screen.await_args_list[0].args[2]
    assert "001234" in screen.await_args_list[1].args[2]
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
