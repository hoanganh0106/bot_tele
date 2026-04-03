import os

with open("bot.py", "r", encoding="utf-8") as f:
    code = f.read()

# 1. Define the separate render function
render_fn = """
async def render_admin_product_detail(update, context, key):
    query = update.callback_query
    info = None
    products, _ = get_all_products_merged()
    if products and key in products:
        info = products[key]
        
    current_name = db.get_custom_name(key) or (info["name"] if info else key)
    current_cat, current_icon, _ = classify_product(key, info if info else {"name": key})
    sell_price = get_sell_price(key, info["price"] if info else 0)
    
    stock_status = "Không rõ"
    is_custom_local = False
    if info:
        stock = info.get("stock", 0)
        status_txt = f"Còn hàng ({stock})" if stock > 0 else ("Hết hàng" if stock == 0 else "Đang cập nhật kho")
        stock_status = f"✅ {status_txt}" if stock > 0 else f"❌ {status_txt}"
        is_custom_local = info.get("is_custom_local", False)
        
    source_txt = "🏷️ Hàng tự bán (Kho riêng)" if is_custom_local else "🌐 Hàng đối tác (API gốc)"
    hide_status = "🟢 Đang hiển thị"
    hide_btn_txt = "🙈 [Giao diện] ẨN SẢN PHẨM"
    if db.is_product_hidden(key):
        hide_status = "🔴 ĐÃ ẨN VỚI KHÁCH"
        hide_btn_txt = "👀 [Giao diện] HIỆN SẢN PHẨM"

    text = (
        f"⚙️ **Cài đặt Sản Phẩm**\\n"
        f"ID: `{key}`\\n"
        f"Nguồn gốc: **{source_txt}**\\n"
        f"Trạng thái: **{hide_status}**\\n"
        f"Số lượng kho: **{stock_status}**\\n"
        f"Tên hiển thị: **{current_name}**\\n"
        f"Danh mục: {current_icon} {current_cat}\\n"
        f"Giá bán hiện tại: {format_money(sell_price)}\\n\\n"
        f"Vui lòng chọn thao tác bên dưới:"
    )
    
    buttons = [
        [InlineKeyboardButton("💰 Sửa giá", callback_data=f"admin_do_price_{key}"),
         InlineKeyboardButton("📦 Sửa tồn kho", callback_data=f"admin_do_stock_{key}")],
        [InlineKeyboardButton("✏️ Đổi tên hiển thị", callback_data=f"admin_do_name_{key}"),
         InlineKeyboardButton(hide_btn_txt, callback_data=f"admin_toggle_hide_{key}")],
        [InlineKeyboardButton("📜 Sửa nội dung/Mô tả", callback_data=f"admin_do_desc_{key}")],
        [InlineKeyboardButton("🔀 Chuyển danh mục", callback_data=f"admin_do_cat_{key}")],
        [InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_products")]
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))
"""

# Insert the render function
if "async def render_admin_product_detail" not in code:
    code = code.replace("async def handle_admin_cb", render_fn + "\\nasync def handle_admin_cb")

# Replace the logic in admin_price_
old_price_block = """    elif data.startswith("admin_price_"):
        key = data.replace("admin_price_", "")
        
        info = None
        products, _ = get_all_products_merged()
        if products and key in products:
            info = products[key]
            
        current_name = db.get_custom_name(key) or (info["name"] if info else key)
        current_cat, current_icon, _ = classify_product(key, info if info else {"name": key})
            
        sell_price = get_sell_price(key, info["price"] if info else 0)
        
        stock_status = "Không rõ"
        is_custom_local = False
        if info:
            stock = info.get("stock", 0)
            status_txt = f"Còn hàng ({stock})" if stock > 0 else ("Hết hàng" if stock == 0 else "Đang cập nhật kho")
            stock_status = f"✅ {status_txt}" if stock > 0 else f"❌ {status_txt}"
            is_custom_local = info.get("is_custom_local", False)
            
        source_txt = "🏷️ Hàng tự bán (Kho riêng)" if is_custom_local else "🌐 Hàng đối tác (API gốc)"
        hide_status = "👁️ Đang hiển thị"
        hide_btn_txt = "🙈 Ẩn sản phẩm"
        if db.is_product_hidden(key):
            hide_status = "❌ Đã ẨN với khách"
            hide_btn_txt = "👀 Hiện sản phẩm"

        text = (
            f"⚙️ **Cài đặt Sản Phẩm**\\n"
            f"ID: `{key}`\\n"
            f"Nguồn gốc: **{source_txt}**\\n"
            f"Trạng thái: **{hide_status}**\\n"
            f"Số lượng kho: **{stock_status}**\\n"
            f"Tên hiển thị: **{current_name}**\\n"
            f"Danh mục: {current_icon} {current_cat}\\n"
            f"Giá bán hiện tại: {format_money(sell_price)}\\n\\n"
            f"Vui lòng chọn thao tác bên dưới:"
        )
        
        buttons = [
            [InlineKeyboardButton("💰 Sửa giá", callback_data=f"admin_do_price_{key}"),
             InlineKeyboardButton("📦 Sửa tồn kho", callback_data=f"admin_do_stock_{key}")],
            [InlineKeyboardButton("✏️ Đổi tên hiển thị", callback_data=f"admin_do_name_{key}"),
             InlineKeyboardButton(hide_btn_txt, callback_data=f"admin_toggle_hide_{key}")],
            [InlineKeyboardButton("📜 Sửa nội dung/Mô tả", callback_data=f"admin_do_desc_{key}")],
            [InlineKeyboardButton("🔀 Chuyển danh mục", callback_data=f"admin_do_cat_{key}")],
            [InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_products")]
        ]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))"""

new_price_block = """    elif data.startswith("admin_price_"):
        key = data.replace("admin_price_", "")
        await render_admin_product_detail(update, context, key)"""

code = code.replace(old_price_block, new_price_block)

# Replace the logic in admin_toggle_hide_
old_hide_block = """    elif data.startswith("admin_toggle_hide_"):
        key = data.replace("admin_toggle_hide_", "")
        is_hidden = db.toggle_hidden_product(key)
        await query.answer(f"{'✅ Đã ẩn' if is_hidden else '👁️ Đã hiện lại'} sản phẩm!", show_alert=True)
        # Quay lại trang cài đặt sản phẩm đó để thấy update
        query.data = f"admin_price_{key}"
        await handle_admin_cb(update, context)"""

new_hide_block = """    elif data.startswith("admin_toggle_hide_"):
        key = data.replace("admin_toggle_hide_", "")
        is_hidden = db.toggle_hidden_product(key)
        await query.answer(f"{'✅ Đã ẩn' if is_hidden else '👁️ Đã hiện lại'} sản phẩm!")
        await render_admin_product_detail(update, context, key)"""

code = code.replace(old_hide_block, new_hide_block)

with open("bot.py", "w", encoding="utf-8") as f:
    f.write(code)
print("bot.py updated with refactored render function.")
