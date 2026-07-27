from core import screens
from pathlib import Path


class _FakeDb:
    def get_user_orders(self, _user_id):
        return {
            f"BOT{index:02d}": {
                "status": "paid",
                "product_name": f"Product {index:02d}",
                "qty": 1,
                "total": 10_000 + index,
                "created_at": f"2026-07-{index + 1:02d}T12:00:00",
            }
            for index in range(20)
        }


def test_order_history_paginates_without_cutting_an_order(monkeypatch):
    monkeypatch.setattr(screens, "db", _FakeDb())
    monkeypatch.setattr(screens, "t", lambda _uid, key, **_kwargs: key + "\n")

    first_text, first_keyboard = screens.build_orders_screen(1, page=0)
    last_text, last_keyboard = screens.build_orders_screen(1, page=2)

    assert "BOT19" in first_text
    assert "BOT11" not in first_text
    assert "BOT03" in last_text
    assert "BOT00" in last_text
    assert len(first_text) < 4000
    assert len(last_text) < 4000
    assert any(
        button.callback_data == "orders_page_1"
        for row in first_keyboard.inline_keyboard
        for button in row
    )
    assert any(
        button.callback_data == "orders_page_1"
        for row in last_keyboard.inline_keyboard
        for button in row
    )


def test_order_page_callbacks_are_registered():
    bot_source = (Path(__file__).parents[1] / "bot.py").read_text(encoding="utf-8")

    assert "orders_page_" in bot_source
