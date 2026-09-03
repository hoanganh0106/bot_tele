import pytest
from unittest.mock import AsyncMock, MagicMock
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from handlers import admin, customer


@pytest.fixture
def mock_many_products():
    """Tạo 150 sản phẩm thuộc danh mục 'khac'."""
    products = {}
    for i in range(150):
        key = f"prod_{i:03d}"
        products[key] = {
            "name": f"Sản phẩm Khác #{i + 1}",
            "price": 10000 + i * 500,
            "stock": 10,
            "is_custom_local": False,
        }
    return products


def test_pagination_math_and_limits(mock_many_products):
    """Đảm bảo phân trang 150 sản phẩm không bao giờ vượt quá giới hạn 100 nút."""
    total_items = len(mock_many_products)
    page_size = 10
    total_pages = max(1, (total_items + page_size - 1) // page_size)
    assert total_pages == 15

    for page in range(total_pages):
        start_idx = page * page_size
        items = list(mock_many_products.items())[start_idx : start_idx + page_size]
        assert len(items) <= 10

        # Số nút = số sản phẩm (<= 10) + nav (<= 5) + footer (2) = tối đa 17 nút
        max_possible_buttons = len(items) + 5 + 2
        assert max_possible_buttons <= 100


@pytest.mark.asyncio
async def test_admin_viewcat_pagination(monkeypatch, mock_many_products):
    """Kiểm tra admin_viewcat mở thành công trang 1 và trang giữa khi có 150 sản phẩm."""
    monkeypatch.setattr(admin, "get_all_products_merged", lambda: (mock_many_products, 0))
    monkeypatch.setattr(admin, "classify_product", lambda key, info: ("Khác", "📦", "khac"))
    monkeypatch.setattr(admin, "get_all_categories_merged", lambda: {"khac": ("Khác", "📦")})

    # Test mở trang 0 từ 'admin_viewcat_khac'
    query = AsyncMock()
    query.data = "admin_viewcat_khac"
    update = MagicMock()
    update.callback_query = query
    context = MagicMock()
    context.user_data = {}

    await admin.handle_admin_cb(update, context)

    assert query.edit_message_text.called
    call_args = query.edit_message_text.call_args
    text = call_args[0][0] if call_args[0] else call_args[1].get("text", "")
    reply_markup = call_args[1].get("reply_markup")

    assert "CHỌN SẢN PHẨM ĐỂ SỬA" in text
    assert "150 SP" in text
    assert "Trang 1/15" in text
    assert isinstance(reply_markup, InlineKeyboardMarkup)

    # Đếm tổng số nút
    all_buttons = [btn for row in reply_markup.inline_keyboard for btn in row]
    assert len(all_buttons) <= 100
    assert len(all_buttons) == 10 + 3 + 2  # 10 sp + 3 nav ([1/15], [➡️], [⏭️]) + 2 footer

    # Kiểm tra nút chuyển trang sang trang 2
    next_btn = [btn for btn in all_buttons if btn.callback_data == "admin_viewcat_khac:1"]
    assert len(next_btn) == 1

    # Test mở trang 5 ('admin_viewcat_khac:5')
    query.reset_mock()
    query.data = "admin_viewcat_khac:5"
    await admin.handle_admin_cb(update, context)

    call_args = query.edit_message_text.call_args
    text = call_args[0][0] if call_args[0] else call_args[1].get("text", "")
    reply_markup = call_args[1].get("reply_markup")
    assert "Trang 6/15" in text

    all_buttons = [btn for row in reply_markup.inline_keyboard for btn in row]
    # Trang giữa có đầy đủ: ⏮️, ⬅️, 6/15, ➡️, ⏭️
    assert any(btn.callback_data == "admin_viewcat_khac:0" for btn in all_buttons)
    assert any(btn.callback_data == "admin_viewcat_khac:4" for btn in all_buttons)
    assert any(btn.callback_data == "admin_viewcat_khac:6" for btn in all_buttons)
    assert any(btn.callback_data == "admin_viewcat_khac:14" for btn in all_buttons)


@pytest.mark.asyncio
async def test_customer_viewcat_pagination(monkeypatch, mock_many_products):
    """Kiểm tra viewcat_ của khách hàng khi có 150 sản phẩm."""
    monkeypatch.setattr(customer, "get_products_cached", lambda: (mock_many_products, 0))
    monkeypatch.setattr(customer, "classify_product", lambda key, info: ("Khác", "📦", "khac"))
    monkeypatch.setattr(customer, "get_all_categories_merged", lambda: {"khac": ("Khác", "📦")})
    monkeypatch.setattr(customer.db, "is_product_hidden", lambda key: False)

    query = AsyncMock()
    query.data = "viewcat_khac"
    query.from_user.id = 123456
    update = MagicMock()
    update.effective_user.id = 123456
    update.callback_query = query
    context = MagicMock()
    context.user_data = {}

    await customer.handle_category_click(update, context)

    assert query.edit_message_text.called
    call_args = query.edit_message_text.call_args
    reply_markup = call_args[1].get("reply_markup")
    assert isinstance(reply_markup, InlineKeyboardMarkup)

    all_buttons = [btn for row in reply_markup.inline_keyboard for btn in row]
    assert len(all_buttons) <= 100
    assert any(btn.callback_data == "viewcat_khac:1" for btn in all_buttons)
