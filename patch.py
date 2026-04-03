import re

with open("bot.py", "r", encoding="utf-8") as f:
    code = f.read()

# 1. Remove build_category_buttons completely.
code = re.sub(r'def build_category_buttons[\s\S]*?return buttons\n', '', code)

# 2. Insert new classify_product
classify_func = '''
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

def build_category_grid(products, callback_prefix):
    categories = {}
    for key, info in products.items():
        cat_name, icon, cat_id = classify_product(key, info)
        if cat_id not in categories:
            categories[cat_id] = {"name": cat_name, "icon": icon, "count": 0}
        categories[cat_id]["count"] += 1

    # Specific order
    order = ["gpt", "grok", "capcut", "gemini", "meitu", "netflix", "discord", "vpn", "spotify", "khac"]
    sorted_cats = []
    for o in order:
        if o in categories:
            sorted_cats.append((o, categories[o]))
            del categories[o]
    for k, v in categories.items():
        sorted_cats.append((k, v))

    buttons = []
    row = []
    for cat_id, data in sorted_cats:
        btn_text = f"{data['icon']} {data['name']} ({data['count']})"
        row.append(InlineKeyboardButton(btn_text, callback_data=f"{callback_prefix}_{cat_id}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return buttons
'''

code = code.replace("# ============================================\n# COMMAND HANDLERS\n# ============================================", classify_func + "\n# ============================================\n# COMMAND HANDLERS\n# ============================================")

# 3. Modify cmd_menu
old_cmd_menu = '''    # Phân loại sản phẩm
    def get_btn(key, info):
        stock = info["stock"]
        if stock == 0: status = "❌"
        elif stock == -1: status = "🔄"
        else: status = f"✅{stock}"
        sell_price = get_sell_price(key, info["price"])
        return InlineKeyboardButton(f"{info['name']} | {format_money(sell_price)} | {status}", callback_data=f"prod_{key}")

    buttons = build_category_buttons(products, get_btn)

    await msg.edit_text(
        "🛒 **MENU SẢN PHẨM**\\n"
        "━━━━━━━━━━━━━━━━━━\\n"
        "Chọn sản phẩm để mua:\\n\\n"
        "_💡 Giá | ✅Còn hàng | ❌Hết | 🔄Đang cập nhật_",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )'''

new_cmd_menu = '''    buttons = build_category_grid(products, "viewcat")
    
    # Thêm nút cố định
    buttons.append([
        InlineKeyboardButton("📞 Liên hệ hỗ trợ", url="https://t.me/thangnguyen_real"),
        InlineKeyboardButton("🔄 Cập nhật sản phẩm", callback_data="reload_menu")
    ])

    await msg.edit_text(
        "🛒 **MENU SẢN PHẨM**\\n"
        "━━━━━━━━━━━━━━━━━━\\n"
        "Chọn danh mục sản phẩm bạn muốn xem:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )'''

code = code.replace(old_cmd_menu, new_cmd_menu)


# 4. Modify admin_products
old_admin_products = '''    elif data == "admin_products":
        products, _ = api.get_stock()
        if not products:
            return await query.edit_message_text("❌ Không lấy được dữ liệu.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_home")]]))
            
        text = "💰 **BẤM SẢN PHẨM ĐỂ SỬA GIÁ BÁN:**\\n\\n"
        
        def get_admin_btn(key, info):
            sell = get_sell_price(key, info['price'])
            return InlineKeyboardButton(f"{info['name']} - {format_money(sell)}", callback_data=f"admin_price_{key}")
            
        buttons = build_category_buttons(products, get_admin_btn)
        buttons.append([InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_home")])
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))'''

new_admin_products = '''    elif data == "admin_products":
        products, _ = api.get_stock()
        if not products:
            return await query.edit_message_text("❌ Không lấy được dữ liệu.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_home")]]))
            
        buttons = build_category_grid(products, "admin_viewcat")
        buttons.append([InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_home")])
        
        await query.edit_message_text(
             "💰 **QUẢN LÝ GIÁ BÁN**\\nChọn danh mục chứa sản phẩm bạn muốn sửa giá:",
             parse_mode="Markdown",
             reply_markup=InlineKeyboardMarkup(buttons)
        )
        
    elif data.startswith("admin_viewcat_"):
        cat_id = data.replace("admin_viewcat_", "")
        
        products, _ = api.get_stock()
        if not products:
            return await query.edit_message_text("❌ Lỗi tải dữ liệu.")
            
        buttons = []
        for key, info in products.items():
            _, _, c_id = classify_product(key, info)
            if c_id == cat_id:
                sell_price = get_sell_price(key, info['price'])
                buttons.append([InlineKeyboardButton(f"{info['name']} - {format_money(sell_price)}", callback_data=f"admin_price_{key}")])
                   
        buttons.append([InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_products")])
        
        await query.edit_message_text(
            f"🛒 **SỬA GIÁ BÁN**\\n━━━━━━━━━━━━━━━━━━",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )'''

code = code.replace(old_admin_products, new_admin_products)

# 5. Remove old admin commands from cmd_prices to cmd_pending_orders
code = re.sub(r'async def cmd_prices[\s\S]*?(?=async def process_paid_order)', '', code)

# Clean up handlers config
code = re.sub(r' +app\.add_handler\(CommandHandler\("setprice", cmd_setprice\)\)\n', '', code)
code = re.sub(r' +app\.add_handler\(CommandHandler\("setmarkup", cmd_setmarkup\)\)\n', '', code)
code = re.sub(r' +app\.add_handler\(CommandHandler\("prices", cmd_prices\)\)\n', '', code)
code = re.sub(r' +app\.add_handler\(CommandHandler\("stats", cmd_stats\)\)\n', '', code)
code = re.sub(r' +app\.add_handler\(CommandHandler\("pendingorders", cmd_pending_orders\)\)\n', '', code)

# 6. Add viewcat handlers
handle_cat_code = '''
async def handle_category_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data == "reload_menu":
        # Hack to reload menu without creating a new message every time
        fake_update = Update(update_id=update.update_id, message=query.message)
        await cmd_menu(fake_update, context) 
        return

    cat_id = data.replace("viewcat_", "")
    products, _ = api.get_stock()
    if not products:
        await query.edit_message_text("❌ Lỗi tải dữ liệu.")
        return
        
    buttons = []
    for key, info in products.items():
        _, _, c_id = classify_product(key, info)
        if c_id == cat_id:
            sell_price = get_sell_price(key, info['price'])
            stock = info["stock"]
            if stock == 0: status = "❌"
            elif stock == -1: status = "🔄"
            else: status = f"✅{stock}"
            buttons.append([InlineKeyboardButton(f"{info['name']} | {format_money(sell_price)} | {status}", callback_data=f"prod_{key}")])
               
    buttons.append([InlineKeyboardButton("⬅️ Quay lại danh mục", callback_data="back_menu")])
    
    await query.edit_message_text(
        f"🛒 **DANH SÁCH SẢN PHẨM**\\n━━━━━━━━━━━━━━━━━━\\n_💡 Giá | ✅Còn hàng | ❌Hết | 🔄Đang cập nhật_",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons)
    )
'''
code = code.replace("async def handle_admin_cb(update:", handle_cat_code + "\nasync def handle_admin_cb(update:")

# Update handlers list for viewcat
handler_update = '''    app.add_handler(CallbackQueryHandler(handle_admin_cancel, pattern="^admincx_"))
    app.add_handler(CallbackQueryHandler(handle_admin_cb, pattern="^admin_"))
    app.add_handler(CallbackQueryHandler(handle_category_click, pattern="^viewcat_|^reload_menu$"))'''
code = re.sub(r' +app\.add_handler\(CallbackQueryHandler\(handle_admin_cancel, pattern="\^admincx_"\)\)\n +app\.add_handler\(CallbackQueryHandler\(handle_admin_cb, pattern="\^admin_"\)\)', handler_update, code)

# Remove the help command mentioning admin old commands
code = re.sub(r'    if is_admin\(user\.id\):[\s\S]*?            "  /pendingorders — Đơn hàng chờ thanh toán\\n"\n        \)', 
              '''    if is_admin(user.id):
        text += (
            "\\n🔧 **Lệnh Admin:**\\n"
            "  /admin — Mở trang quản trị (Giá, Markup, Thống kê)"
        )''', code)

with open("bot.py", "w", encoding="utf-8") as f:
    f.write(code)

print("Patched!")
