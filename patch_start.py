import re

with open("bot.py", "r", encoding="utf-8") as f:
    code = f.read()

old_start = '''async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.add_user(user.id)
    text = (
        f"👋 Chào **{user.first_name}**!\\n\\n"
        "🛒 Bot bán tài khoản Premium tự động\\n"
        "💳 Thanh toán chuyển khoản — xác nhận tự động\\n\\n"
        "📌 **Các lệnh chính:**\\n"
        "  /menu — Xem sản phẩm & mua hàng\\n"
        "  /myorders — Xem lịch sử đơn hàng\\n"
        "  /help — Hướng dẫn sử dụng\\n"
    )
    if is_admin(user.id):
        text += (
            "\\n🔧 **Lệnh Admin:**\\n"
            "  /admin — Mở trang quản trị (Giá, Markup, Thống kê)"
        )
    await update.message.reply_text(text, parse_mode="Markdown")'''

new_start = '''async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.add_user(user.id)
    text = (
        f"👋 Xin chào **{user.first_name}**!\\n\\n"
        "Chào mừng bạn đến với hệ thống bán tài khoản Premium tự động 🤖\\n\\n"
        "🔹 **Thanh toán tự động** 24/7, xác nhận trong 1 phút\\n"
        "🔹 **Nhận tài khoản ngay** sau khi thanh toán\\n"
        "🔹 **Hỗ trợ tận tình** nhanh chóng\\n\\n"
        "👇 Bấm vào nút bên dưới để chọn sản phẩm 👇"
    )
    
    buttons = [
        [InlineKeyboardButton("🛒 MENU SẢN PHẨM", callback_data="reload_menu")],
        [
            InlineKeyboardButton("📋 Lịch sử mua hàng", callback_data="btn_myorders"),
            InlineKeyboardButton("📖 Hỗ trợ / Support", url="https://t.me/thangnguyen_real")
        ]
    ]
    
    if is_admin(user.id):
        buttons.append([InlineKeyboardButton("⚙️ Quản trị Admin", callback_data="admin_home")])
        text += "\\n\\n_🔑 Xin chào Admin, bảng Quản trị đã được mở khóa!_"
        
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))'''

code = code.replace(old_start, new_start)

# Add btn_myorders in handle_category_click handler logic
handle_cat_start = '''    if data == "reload_menu":
        # Hack to reload menu without creating a new message every time
        fake_update = Update(update_id=update.update_id, message=query.message)
        await cmd_menu(fake_update, context) 
        return'''
        
handle_cat_new = '''    if data == "reload_menu":
        fake_update = Update(update_id=update.update_id, message=query.message)
        await cmd_menu(fake_update, context) 
        return
        
    if data == "btn_myorders":
        fake_update = Update(update_id=update.update_id, message=query.message, effective_user=update.effective_user)
        await cmd_myorders(fake_update, context)
        return'''

code = code.replace(handle_cat_start, handle_cat_new)

# Update handler regex
old_handler = '''app.add_handler(CallbackQueryHandler(handle_category_click, pattern="^viewcat_|^reload_menu$"))'''
new_handler = '''app.add_handler(CallbackQueryHandler(handle_category_click, pattern="^viewcat_|^reload_menu$|^btn_myorders$"))'''
code = code.replace(old_handler, new_handler)

with open("bot.py", "w", encoding="utf-8") as f:
    f.write(code)

print("Patching done.")
