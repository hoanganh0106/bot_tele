import os

with open("database.py", "r", encoding="utf-8") as f:
    db_code = f.read()

# Add new default keys in JSON
db_code = db_code.replace('"custom_categories": {},', '"custom_categories": {},\n                "custom_descriptions": {},\n                "custom_category_defs": {},')

# Add getter and setters
methods = """    def get_custom_description(self, product_key: str) -> str | None:
        with self.lock:
            data = self._read()
            return data.get("custom_descriptions", {}).get(product_key)

    def set_custom_description(self, product_key: str, desc: str):
        with self.lock:
            data = self._read()
            if "custom_descriptions" not in data: data["custom_descriptions"] = {}
            if desc is None:
                data["custom_descriptions"].pop(product_key, None)
            else:
                data["custom_descriptions"][product_key] = desc
            self._write(data)

    def get_custom_category_defs(self) -> dict:
        with self.lock:
            data = self._read()
            return data.get("custom_category_defs", {})

    def add_custom_category_def(self, cat_id: str, name: str, icon: str):
        with self.lock:
            data = self._read()
            if "custom_category_defs" not in data: data["custom_category_defs"] = {}
            data["custom_category_defs"][cat_id] = [name, icon]
            self._write(data)

    def remove_custom_category_def(self, cat_id: str):
        with self.lock:
            data = self._read()
            if "custom_category_defs" not in data: return
            data["custom_category_defs"].pop(cat_id, None)
            self._write(data)

    # === SETTINGS ==="""
db_code = db_code.replace("    # === SETTINGS ===", methods)

with open("database.py", "w", encoding="utf-8") as f:
    f.write(db_code)
print("Updated database.py")


with open("bot.py", "r", encoding="utf-8") as f:
    bot_code = f.read()

old_classify = """ALL_CATEGORIES = {
    "gpt": ("ChatGPT", "🤖"),
    "grok": ("Grok", "🔮"),
    "capcut": ("CapCut", "🎬"),
    "gemini": ("Gemini", "✨"),
    "meitu": ("Meitu", "📸"),
    "netflix": ("Netflix / YT", "🍿"),
    "discord": ("Discord", "💬"),
    "vpn": ("VPN", "🛡️"),
    "spotify": ("Spotify", "🎵"),
    "khac": ("Khác", "📦")
}

def classify_product(key: str, info: dict) -> tuple:
    # Get custom category first
    custom_cat = db.get_custom_category(key)
    if custom_cat and custom_cat in ALL_CATEGORIES:
        name, icon = ALL_CATEGORIES[custom_cat]
        return name, icon, custom_cat"""

new_classify = """ALL_CATEGORIES = {
    "gpt": ["ChatGPT", "🤖"],
    "grok": ["Grok", "🔮"],
    "capcut": ["CapCut", "🎬"],
    "gemini": ["Gemini", "✨"],
    "meitu": ["Meitu", "📸"],
    "netflix": ["Netflix / YT", "🍿"],
    "discord": ["Discord", "💬"],
    "vpn": ["VPN", "🛡️"],
    "spotify": ["Spotify", "🎵"],
    "khac": ["Khác", "📦"]
}

def get_all_categories_merged() -> dict:
    cats = dict(ALL_CATEGORIES)
    custom_cats = db.get_custom_category_defs()
    for cat_id, val in custom_cats.items():
        cats[cat_id] = val
    return cats

def classify_product(key: str, info: dict) -> tuple:
    merged_cats = get_all_categories_merged()
    
    # Get custom category first
    custom_cat = db.get_custom_category(key)
    if custom_cat and custom_cat in merged_cats:
        name, icon = merged_cats[custom_cat]
        return name, icon, custom_cat"""
bot_code = bot_code.replace(old_classify, new_classify)

bot_code = bot_code.replace("""ALL_CATEGORIES.items()""", """get_all_categories_merged().items()""")
bot_code = bot_code.replace("""ALL_CATEGORIES[cid]""", """get_all_categories_merged()[cid]""")

# Add custom description to product view
old_product_view = """    dname = db.get_custom_name(key) or info['name']
    sell_price = get_sell_price(key, info['price'])"""
new_product_view = """    dname = db.get_custom_name(key) or info['name']
    sell_price = get_sell_price(key, info['price'])
    cdesc = db.get_custom_description(key)
    desc_str = f"\\n📝 Mô tả: {cdesc}\\n" if cdesc else "" """
bot_code = bot_code.replace(old_product_view, new_product_view)

old_product_text = """    f"🛒 **XÁC NHẬN MUA HÀNG**\\n"
    f"📦 Sản phẩm: {dname}\\n"
    f"💵 Giá tiền: {format_money(sell_price)}\\n" """
new_product_text = """    f"🛒 **XÁC NHẬN MUA HÀNG**\\n"
    f"📦 Sản phẩm: {dname}\\n"
    f"💵 Giá tiền: {format_money(sell_price)}\\n{desc_str}" """
bot_code = bot_code.replace(old_product_text, new_product_text)

# Add "Sửa nội dung" to admin buttons
old_admin_product_buttons = """        buttons = [
            [InlineKeyboardButton("💰 Sửa Giá thu khách", callback_data=f"admin_do_price_{key}")],
            [InlineKeyboardButton("✏️ Đổi tên hiển thị", callback_data=f"admin_do_name_{key}")],
            [InlineKeyboardButton("🔀 Chuyển danh mục", callback_data=f"admin_do_cat_{key}")],
            [InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_products")]
        ]"""
new_admin_product_buttons = """        buttons = [
            [InlineKeyboardButton("💰 Sửa Giá thu khách", callback_data=f"admin_do_price_{key}")],
            [InlineKeyboardButton("✏️ Đổi tên hiển thị", callback_data=f"admin_do_name_{key}")],
            [InlineKeyboardButton("📜 Sửa nội dung/Mô tả", callback_data=f"admin_do_desc_{key}")],
            [InlineKeyboardButton("🔀 Chuyển danh mục", callback_data=f"admin_do_cat_{key}")],
            [InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_products")]
        ]"""
bot_code = bot_code.replace(old_admin_product_buttons, new_admin_product_buttons)

# Add admin_do_desc to handler
old_admin_do_name = """    elif data.startswith("admin_do_name_"):"""
new_admin_do_name = """    elif data.startswith("admin_do_desc_"):
        key = data.replace("admin_do_desc_", "")
        context.user_data["awaiting_desc_for"] = key
        await query.edit_message_text(
            f"📜 Vui lòng **nhắn tin gửi NỘI DUNG/MÔ TẢ MỚI** cho `{key}`.\\n"
            f"Bao gồm hướng dẫn, ghi chú, v.v.\\n\\n"
            f"Nhắn chữ `reset` nếu muốn xóa mô tả.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Hủy thay đổi", callback_data="admin_products")]])
        )

    elif data.startswith("admin_do_name_"):"""
if "admin_do_desc_" not in bot_code:
    bot_code = bot_code.replace(old_admin_do_name, new_admin_do_name)

# Add add_cat to admin_products menu
old_admin_products_menu = """        for cid, (cname, cicon) in ALL_CATEGORIES.items():"""
new_admin_products_menu = """        for cid, (cname, cicon) in get_all_categories_merged().items():"""
bot_code = bot_code.replace(old_admin_products_menu, new_admin_products_menu)

old_admin_products_btns = """        buttons.append([InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_home")])"""
new_admin_products_btns = """        buttons.append([InlineKeyboardButton("➕ Thêm danh mục mới", callback_data="admin_add_cat")])
        buttons.append([InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_home")])"""
bot_code = bot_code.replace(old_admin_products_btns, new_admin_products_btns)

# Add admin_add_cat callback
old_admin_add_cat = """    elif data.startswith("admin_viewcat_"):"""
new_admin_add_cat = """    elif data == "admin_add_cat":
        context.user_data["awaiting_new_cat"] = True
        await query.edit_message_text(
            "➕ **Thêm danh mục mới**\\n\\n"
            "Vui lòng nhắn tin theo đúng cú pháp sau:\\n"
            "`Mã_id | Tên hiển thị | Emoji`\\n\\n"
            "Ví dụ: `msoffice | Microsoft Office | 💻`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Hủy", callback_data="admin_products")]])
        )

    elif data.startswith("admin_viewcat_"):"""
if "admin_add_cat" not in bot_code:
    bot_code = bot_code.replace(old_admin_add_cat, new_admin_add_cat)

# Add text handlers for desc and add_cat
old_text_handler = """    # 1.5 Handle renaming products"""
new_text_handler = """    if context.user_data.get("awaiting_new_cat"):
        del context.user_data["awaiting_new_cat"]
        try:
            parts = [p.strip() for p in text.split("|")]
            if len(parts) == 3:
                cat_id, name, icon = parts
                cat_id = cat_id.lower().replace(" ", "")
                db.add_custom_category_def(cat_id, name, icon)
                await update.message.reply_text(f"✅ Đã thêm danh mục: {icon} {name}")
            else:
                await update.message.reply_text("❌ Sai cú pháp. Vui lòng thử lại theo mẫu: `msoffice | Microsoft Office | 💻`", parse_mode="Markdown")
        except Exception:
            await update.message.reply_text("❌ Có lỗi xảy ra.")
        return

    if context.user_data.get("awaiting_desc_for"):
        key = context.user_data["awaiting_desc_for"]
        del context.user_data["awaiting_desc_for"]
        if text.lower() == "reset":
            db.set_custom_description(key, None)
            await update.message.reply_text(f"✅ Đã xóa mô tả cho sản phẩm `{key}`.", parse_mode="Markdown")
        else:
            db.set_custom_description(key, text)
            await update.message.reply_text(f"✅ Đã cập nhật mô tả cho sản phẩm `{key}`.", parse_mode="Markdown")
        return

    # 1.5 Handle renaming products"""
bot_code = bot_code.replace(old_text_handler, new_text_handler)

with open("bot.py", "w", encoding="utf-8") as f:
    f.write(bot_code)
print("Updated bot.py")
