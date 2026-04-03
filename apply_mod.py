import os

with open("database.py", "r", encoding="utf-8") as f:
    db_code = f.read()

# Add get_custom_names etc
methods = """    def get_custom_name(self, product_key: str) -> str | None:
        with self.lock:
            data = self._read()
            return data.get("custom_names", {}).get(product_key)

    def set_custom_name(self, product_key: str, name: str):
        with self.lock:
            data = self._read()
            if "custom_names" not in data: data["custom_names"] = {}
            if name is None:
                data["custom_names"].pop(product_key, None)
            else:
                data["custom_names"][product_key] = name
            self._write(data)

    def get_custom_category(self, product_key: str) -> str | None:
        with self.lock:
            data = self._read()
            return data.get("custom_categories", {}).get(product_key)

    def set_custom_category(self, product_key: str, cat_id: str):
        with self.lock:
            data = self._read()
            if "custom_categories" not in data: data["custom_categories"] = {}
            if cat_id is None:
                data["custom_categories"].pop(product_key, None)
            else:
                data["custom_categories"][product_key] = cat_id
            self._write(data)

    # === SETTINGS ==="""
db_code = db_code.replace("    # === SETTINGS ===", methods)

with open("database.py", "w", encoding="utf-8") as f:
    f.write(db_code)
print("Updated database.py")

with open("bot.py", "r", encoding="utf-8") as f:
    bot_code = f.read()

# Modify classify_product in bot.py
old_classify = """
def classify_product(key: str, info: dict) -> tuple:
    k = key.lower()
    n = info["name"].lower()
    if "gpt" in k or "gpt" in n or "openai" in n: return "ChatGPT", "🤖", "gpt"
    if "grok" in k or "grok" in n: return "Grok", "🔮", "grok"
    if "cc" in k or "capcut" in n: return "CapCut", "🎬", "capcut"
    if "gemini" in k or "gemini" in n: return "Gemini", "✨", "gemini"
    if "meitu" in k or "meitu" in n: return "Meitu", "📸", "meitu"
    if "netflix" in k or "netflix" in n or "yt" in k or "youtube" in n: return "Netflix / YT", "🍿", "netflix"
    if "discord" in k or "discord" in n: return "Discord", "💬", "discord"
    if "vpn" in k or "vpn" in n or "warp" in k or "1.1.1.1" in n: return "VPN", "🛡️", "vpn"
    if "spotify" in k or "spotify" in n or "music" in n: return "Spotify", "🎵", "spotify"
    return "Khác", "📦", "khac"
"""

new_classify = """
ALL_CATEGORIES = {
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
        return name, icon, custom_cat

    k = key.lower()
    n = info["name"].lower()
    if "gpt" in k or "gpt" in n or "openai" in n: return "ChatGPT", "🤖", "gpt"
    if "grok" in k or "grok" in n: return "Grok", "🔮", "grok"
    if "cc" in k or "capcut" in n: return "CapCut", "🎬", "capcut"
    if "gemini" in k or "gemini" in n: return "Gemini", "✨", "gemini"
    if "meitu" in k or "meitu" in n: return "Meitu", "📸", "meitu"
    if "netflix" in k or "netflix" in n or "yt" in k or "youtube" in n: return "Netflix / YT", "🍿", "netflix"
    if "discord" in k or "discord" in n: return "Discord", "💬", "discord"
    if "vpn" in k or "vpn" in n or "warp" in k or "1.1.1.1" in n: return "VPN", "🛡️", "vpn"
    if "spotify" in k or "spotify" in n or "music" in n: return "Spotify", "🎵", "spotify"
    return "Khác", "📦", "khac"
"""
if old_classify in bot_code:
    bot_code = bot_code.replace(old_classify, new_classify)
print("Replaced classify")

# Update get_product_display_name to apply custom name
old_handle_sel = """    info = products[product_key]
    sell_price = get_sell_price(product_key, info["price"])"""

new_handle_sel = """    info = products[product_key]
    custom_name = db.get_custom_name(product_key)
    if custom_name:
        info["name"] = custom_name
    sell_price = get_sell_price(product_key, info["price"])"""
bot_code = bot_code.replace(old_handle_sel, new_handle_sel)

# We also need to inject custom_name in handle_category_click menu rendering
old_cat_render = """        if c_id == cat_id:
            sell_price = get_sell_price(key, info['price'])
            money = format_money(sell_price)
            buttons.append([InlineKeyboardButton(f"✅ {info['name']} - {money}", callback_data=f"prod_{key}")])"""

new_cat_render = """        if c_id == cat_id:
            sell_price = get_sell_price(key, info['price'])
            money = format_money(sell_price)
            dname = db.get_custom_name(key) or info['name']
            buttons.append([InlineKeyboardButton(f"✅ {dname} - {money}", callback_data=f"prod_{key}")])"""
bot_code = bot_code.replace(old_cat_render, new_cat_render)


# Admin handlers - show product options
old_admin_price_req = """        key = data.replace("admin_price_", "")
        context.user_data["awaiting_price_for"] = key
        await query.edit_message_text(
            f"📝 Vui lòng **nhắn tin gửi giá bán mới** (VND) cho `{key}` (gửi trực tiếp vào chat, ví dụ 50000).\\n\\n"
            f"Nhắn chữ `reset` nếu muốn xóa giá cài tay (đưa về markup chung).",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Hủy thay đổi", callback_data="admin_products")]])
        )"""

new_admin_price_req = """        key = data.replace("admin_price_", "")
        
        info = None
        products, _ = api.get_stock()
        if products and key in products:
            info = products[key]
            
        current_name = db.get_custom_name(key) or (info["name"] if info else key)
        current_cat, current_icon, _ = classify_product(key, info if info else {"name": key})
            
        sell_price = get_sell_price(key, info["price"] if info else 0)
        
        text = (
            f"⚙️ **Cài đặt Sản Phẩm**\\n"
            f"ID: `{key}`\\n"
            f"Tên hiển thị: **{current_name}**\\n"
            f"Danh mục: {current_icon} {current_cat}\\n"
            f"Giá bán hiện tại: {format_money(sell_price)}\\n\\n"
            f"Vui lòng chọn thao tác bên dưới:"
        )
        
        buttons = [
            [InlineKeyboardButton("💰 Sửa Giá thu khách", callback_data=f"admin_do_price_{key}")],
            [InlineKeyboardButton("✏️ Đổi tên hiển thị", callback_data=f"admin_do_name_{key}")],
            [InlineKeyboardButton("🔀 Chuyển danh mục", callback_data=f"admin_do_cat_{key}")],
            [InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_products")]
        ]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))
        
    elif data.startswith("admin_do_price_"):
        key = data.replace("admin_do_price_", "")
        context.user_data["awaiting_price_for"] = key
        await query.edit_message_text(
            f"📝 Vui lòng **nhắn tin gửi GIÁ BÁN MỚI** (VND) cho `{key}` (VD: 50000).\\n\\n"
            f"Nhắn chữ `reset` nếu muốn xóa giá cài tay (đưa về tự động cộng Markup).",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Hủy thay đổi", callback_data="admin_products")]])
        )

    elif data.startswith("admin_do_name_"):
        key = data.replace("admin_do_name_", "")
        context.user_data["awaiting_name_for"] = key
        await query.edit_message_text(
            f"✏️ Vui lòng **nhắn tin gửi TÊN MỚI** cho `{key}`.\\n\\n"
            f"Nhắn chữ `reset` nếu muốn khôi phục tên gốc của server.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Hủy thay đổi", callback_data="admin_products")]])
        )

    elif data.startswith("admin_do_cat_"):
        key = data.replace("admin_do_cat_", "")
        buttons = []
        row = []
        for cid, (cname, cicon) in ALL_CATEGORIES.items():
            row.append(InlineKeyboardButton(f"{cicon} {cname}", callback_data=f"admin_set_cat_{key}_{cid}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row: buttons.append(row)
        buttons.append([InlineKeyboardButton("♻️ Reset (Máy tự chọn)", callback_data=f"admin_set_cat_{key}_reset")])
        buttons.append([InlineKeyboardButton("⬅️ Hủy thay đổi", callback_data="admin_products")])
        
        await query.edit_message_text(f"🔀 Chọn danh mục mới cho `{key}`:", reply_markup=InlineKeyboardMarkup(buttons))
        
    elif data.startswith("admin_set_cat_"):
        # Format: admin_set_cat_KEY_CATID
        parts = data[14:].split("_")
        cid = parts[-1]
        key = "_".join(parts[:-1])
        
        if cid == "reset":
            db.set_custom_category(key, None)
            msg = "✅ Đã xóa chỉ định danh mục tay, kích hoạt tự động."
        else:
            db.set_custom_category(key, cid)
            msg = f"✅ Đã chuyển sản phẩm sang danh mục {ALL_CATEGORIES[cid][1]} {ALL_CATEGORIES[cid][0]}."
            
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quay lại quản lý", callback_data="admin_products")]]))
"""

if "elif data.startswith(\"admin_do_price_\"):" not in bot_code:
    bot_code = bot_code.replace(old_admin_price_req, new_admin_price_req)


# Add input handlers for name
old_text_handler = """    # Clear awaiting broadcast if they type incorrectly
    if context.user_data.get("awaiting_broadcast"):"""
    
new_text_handler = """    if context.user_data.get("awaiting_name_for"):
        key = context.user_data["awaiting_name_for"]
        del context.user_data["awaiting_name_for"]
        
        if text.lower() == "reset":
            db.set_custom_name(key, None)
            await update.message.reply_text(f"✅ Đã reset tên sản phẩm `{key}` về gốc.", parse_mode="Markdown")
        else:
            db.set_custom_name(key, text)
            await update.message.reply_text(f"✅ Đã đổi tên sản phẩm `{key}` thành:\\n**{text}**", parse_mode="Markdown")
        return

    # Clear awaiting broadcast if they type incorrectly
    if context.user_data.get("awaiting_broadcast"):"""

if "awaiting_name_for" not in bot_code:
    bot_code = bot_code.replace(old_text_handler, new_text_handler)


# Update admin viewcat to use custom_name
old_admin_viewcat = """        if products:
            for key, info in products.items():
                _, _, c_id = classify_product(key, info)
                if c_id == cat_id:
                    price_str = format_money(get_sell_price(key, info['price']))
                    buttons.append([InlineKeyboardButton(f"Sửa: {info['name']} ({price_str})", callback_data=f"admin_price_{key}")])"""

new_admin_viewcat = """        if products:
            for key, info in products.items():
                _, _, c_id = classify_product(key, info)
                if c_id == cat_id:
                    price_str = format_money(get_sell_price(key, info['price']))
                    dname = db.get_custom_name(key) or info['name']
                    buttons.append([InlineKeyboardButton(f"Sửa: {dname} ({price_str})", callback_data=f"admin_price_{key}")])"""
bot_code = bot_code.replace(old_admin_viewcat, new_admin_viewcat)

# Add admin callbacks to regex
old_admin_handler = """app.add_handler(CallbackQueryHandler(handle_admin_cb, pattern="^admin_"))"""
new_admin_handler = """app.add_handler(CallbackQueryHandler(handle_admin_cb, pattern="^admin_"))"""

with open("bot.py", "w", encoding="utf-8") as f:
    f.write(bot_code)
print("Updated bot.py")
