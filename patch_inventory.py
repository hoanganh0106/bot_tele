import os

with open("database.py", "r", encoding="utf-8") as f:
    db_code = f.read()

# Add new default keys in JSON initialized in database.py
db_code = db_code.replace('"custom_category_defs": {},', '"custom_category_defs": {},\n                "custom_products": {},\n                "custom_stocks": {},')

# Add getter and setters for stocks and custom products
methods = """    def get_custom_stocks(self) -> dict:
        with self.lock:
            data = self._read()
            return data.get("custom_stocks", {})

    def set_custom_stock(self, product_key: str, stock: int):
        with self.lock:
            data = self._read()
            if "custom_stocks" not in data: data["custom_stocks"] = {}
            if stock is None:
                data["custom_stocks"].pop(product_key, None)
            else:
                data["custom_stocks"][product_key] = stock
            self._write(data)

    def get_custom_products(self) -> dict:
        with self.lock:
            data = self._read()
            return data.get("custom_products", {})

    def add_custom_product(self, key: str, name: str, price: int):
        with self.lock:
            data = self._read()
            if "custom_products" not in data: data["custom_products"] = {}
            data["custom_products"][key] = {
                "name": name,
                "price": price,
                "stock": 0,
                "is_custom_local": True
            }
            self._write(data)

    # === SETTINGS ==="""
if "def get_custom_stocks(self)" not in db_code:
    db_code = db_code.replace("    # === SETTINGS ===", methods)

with open("database.py", "w", encoding="utf-8") as f:
    f.write(db_code)
print("Updated database.py")

with open("bot.py", "r", encoding="utf-8") as f:
    bot_code = f.read()

# Make a wrapper for fetching stock so it includes custom products and overrides stocks
wrapper = """
def get_all_products_merged() -> dict:
    products, balance = api.get_stock()
    if products is None:
        products = {}
        
    custom_products = db.get_custom_products()
    for k, v in custom_products.items():
        products[k] = v
        
    custom_stocks = db.get_custom_stocks()
    for k, v in products.items():
        if k in custom_stocks:
            products[k]["stock"] = custom_stocks[k]
            
    return products, balance

"""
if "def get_all_products_merged" not in bot_code:
    bot_code = bot_code.replace("def get_all_categories_merged() -> dict:", wrapper + "def get_all_categories_merged() -> dict:")

# Replace api.get_stock() with get_all_products_merged() where appropriate inside bot handlers
bot_code = bot_code.replace("products, balance = api.get_stock()", "products, balance = get_all_products_merged()")
bot_code = bot_code.replace("products, _ = api.get_stock()", "products, _ = get_all_products_merged()")

# Update handle_admin_products buttons
old_admin_product_buttons = """        buttons = [
            [InlineKeyboardButton("💰 Sửa giá", callback_data=f"admin_do_price_{key}")],
            [InlineKeyboardButton("✏️ Đổi tên hiển thị", callback_data=f"admin_do_name_{key}")],
            [InlineKeyboardButton("📜 Sửa nội dung/Mô tả", callback_data=f"admin_do_desc_{key}")],
            [InlineKeyboardButton("🔀 Chuyển danh mục", callback_data=f"admin_do_cat_{key}")],
            [InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_products")]
        ]"""
new_admin_product_buttons = """        buttons = [
            [InlineKeyboardButton("💰 Sửa giá", callback_data=f"admin_do_price_{key}"),
             InlineKeyboardButton("📦 Sửa tồn kho", callback_data=f"admin_do_stock_{key}")],
            [InlineKeyboardButton("✏️ Đổi tên hiển thị", callback_data=f"admin_do_name_{key}")],
            [InlineKeyboardButton("📜 Sửa nội dung/Mô tả", callback_data=f"admin_do_desc_{key}")],
            [InlineKeyboardButton("🔀 Chuyển danh mục", callback_data=f"admin_do_cat_{key}")],
            [InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_products")]
        ]"""
bot_code = bot_code.replace(old_admin_product_buttons, new_admin_product_buttons)

# Add admin_add_product button
old_admin_products_btns = """        buttons.append([InlineKeyboardButton("➕ Thêm danh mục mới", callback_data="admin_add_cat")])"""
new_admin_products_btns = """        buttons.append([InlineKeyboardButton("➕ Thêm sản phẩm tự bán", callback_data="admin_add_prod")])
        buttons.append([InlineKeyboardButton("➕ Thêm danh mục mới", callback_data="admin_add_cat")])"""
if "Thêm sản phẩm tự bán" not in bot_code:
    bot_code = bot_code.replace(old_admin_products_btns, new_admin_products_btns)


old_admin_add_cat = """    elif data == "admin_add_cat":"""
new_admin_handlers = """    elif data == "admin_add_prod":
        context.user_data["awaiting_new_prod"] = True
        await query.edit_message_text(
            "➕ **Thêm Sản Phẩm Khác (Tự điền tay)**\\n\\n"
            "Vui lòng nhắn tin theo cú pháp:\\n"
            "`Mã_id | Tên | Giá`\\n\\n"
            "Ví dụ: `ytb_1m | Youtube Premium 1T | 35000`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Hủy", callback_data="admin_products")]])
        )

    elif data.startswith("admin_do_stock_"):
        key = data.replace("admin_do_stock_", "")
        context.user_data["awaiting_stock_for"] = key
        await query.edit_message_text(
            f"📦 Vui lòng nhắn tin GIÁ TRỊ TỒN KHO MỚI cho `{key}` (VD: 100).\\n"
            f"Nhắn chữ `reset` để lấy lại số lượng kho của đối tác (nếu có).",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Hủy thay đổi", callback_data="admin_products")]])
        )

    elif data == "admin_add_cat":"""
if "admin_add_prod" not in bot_code:
    bot_code = bot_code.replace(old_admin_add_cat, new_admin_handlers)

# Handle text input for new product and stock
old_text_handler = """    if context.user_data.get("awaiting_new_cat"):"""
new_text_handler = """    if context.user_data.get("awaiting_new_prod"):
        del context.user_data["awaiting_new_prod"]
        try:
            parts = [p.strip() for p in text.split("|")]
            if len(parts) == 3:
                prod_id, name, price = parts
                prod_id = prod_id.lower().replace(" ", "")
                db.add_custom_product(prod_id, name, int(price))
                await update.message.reply_text(f"✅ Đã thêm sản phẩm `{prod_id}`. Hãy vào Quản lý sản phẩm để đổi danh mục và cập nhật kho cho nó!", parse_mode="Markdown")
            else:
                await update.message.reply_text("❌ Sai cú pháp. Mẫu: `ytb_1m | Youtube Premium 1T | 35000`", parse_mode="Markdown")
        except Exception:
            await update.message.reply_text("❌ Có lỗi xảy ra.")
        return

    if context.user_data.get("awaiting_stock_for"):
        key = context.user_data["awaiting_stock_for"]
        del context.user_data["awaiting_stock_for"]
        if text.lower() == "reset":
            db.set_custom_stock(key, None)
            await update.message.reply_text(f"✅ Đã để hệ thống tự động tải kho cho `{key}`.", parse_mode="Markdown")
        else:
            try:
                ns = int(text)
                db.set_custom_stock(key, ns)
                await update.message.reply_text(f"✅ Đã set tồn kho cho `{key}` là: {ns}", parse_mode="Markdown")
            except ValueError:
                await update.message.reply_text("❌ Số lượng tồn kho phải là số.")
        return

    if context.user_data.get("awaiting_new_cat"):"""
if "awaiting_new_prod" not in bot_code:
    bot_code = bot_code.replace(old_text_handler, new_text_handler)


# --- IMPORTANT: modify the payment success handler logic ---
old_process_paid = """    result = api.buy(
        product_key=order["product_key"],
        qty=order["qty"],
        order_code=order_code
    )

    if result.get("success"):
        items = result.get("items", [])"""

new_process_paid = """    
    # Check if this is a custom manual product, skip api call
    custom_products = db.get_custom_products()
    is_custom_product = order["product_key"] in custom_products or order["product_key"] == "test_product"
    
    if is_custom_product:
        # Subtract stock locally
        cur_stock = db.get_custom_stocks().get(order["product_key"], 0)
        db.set_custom_stock(order["product_key"], max(0, cur_stock - order["qty"]))
        
        result = {
            "success": True,
            "items": [],
            "api_order_code": f"MANUAL_{order_code}"
        }
    else:
        result = api.buy(
            product_key=order["product_key"],
            qty=order["qty"],
            order_code=order_code
        )

    if result.get("success"):
        items = result.get("items", [])"""
if "is_custom_product = order" not in bot_code:
    bot_code = bot_code.replace(old_process_paid, new_process_paid)


old_confirm_msg = """        msg_lines = [
            f"✅ **THANH TOÁN THÀNH CÔNG**",
            f"**Mã đơn:** `{order_code}`",
            f"**Sản phẩm:** {order['product_name']}",
            f"**Số lượng:** {order['qty']}",
            f"**Thành tiền:** {format_money(order['total'])}",
            "",
            "📦 **Thông tin tài khoản/Sản phẩm của bạn:**"
        ]"""

new_confirm_msg = """        cdesc = db.get_custom_description(order["product_key"])
        desc_str = f"\\n📝 **Nội dung/Mô tả từ Admin:**\\n{cdesc}\\n" if cdesc else ""
        
        msg_lines = [
            f"✅ **THANH TOÁN THÀNH CÔNG**",
            f"**Mã đơn:** `{order_code}`",
            f"**Sản phẩm:** {order['product_name']}",
            f"**Số lượng:** {order['qty']}",
            f"**Thành tiền:** {format_money(order['total'])}",
            f"{desc_str}",
            "📦 **Thông tin tài khoản/Sản phẩm của bạn:**"
        ]
        
        if len(items) == 0:
            msg_lines[-1] = "*(Chưa có tải khoản hệ thống tự động xuất, vui lòng kiểm tra mục Ghi chú hoặc liên hệ Admin để nhận hàng.)*\\n"
"""
bot_code = bot_code.replace(old_confirm_msg, new_confirm_msg)


with open("bot.py", "w", encoding="utf-8") as f:
    f.write(bot_code)
print("Updated bot.py")

