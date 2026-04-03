import os

with open("bot.py", "r", encoding="utf-8") as f:
    text = f.read()

# 1. Add admin_add_cat handler if missing
old_admin_viewcat = """    elif data.startswith("admin_viewcat_"):"""
new_admin_add_cat_handler = """    elif data == "admin_add_cat":
        context.user_data["awaiting_new_cat"] = True
        await query.edit_message_text(
            "➕ **Thêm hoặc Sửa danh mục**\\n\\n"
            "Vui lòng nhắn tin theo đúng cú pháp sau:\\n"
            "`Mã_id | Tên hiển thị | Emoji`\\n\\n"
            "Ví dụ thêm mới: `msoffice | Microsoft Office | 💻`\\n"
            "Ví dụ sửa cũ: Nếu muốn sửa mục Khác (id là khac), nhắn: `khac | Thập Cẩm | 📦`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Hủy", callback_data="admin_products")]])
        )

    elif data.startswith("admin_viewcat_"):"""

if "elif data == \"admin_add_cat\":" not in text:
    text = text.replace(old_admin_viewcat, new_admin_add_cat_handler)

# 2. Fix the "Sửa Giá thu khách" -> "Sửa giá"
text = text.replace("💰 Sửa Giá thu khách", "💰 Sửa giá")

# 3. Add "Thêm danh mục" to admin_do_cat_ menu
old_admin_do_cat = """        if row: buttons.append(row)
        buttons.append([InlineKeyboardButton("♻️ Reset (Máy tự chọn)", callback_data=f"admin_set_cat_{key}_reset")])
        buttons.append([InlineKeyboardButton("⬅️ Hủy thay đổi", callback_data="admin_products")])"""
new_admin_do_cat = """        if row: buttons.append(row)
        buttons.append([InlineKeyboardButton("➕ Tạo ds danh mục mới", callback_data="admin_add_cat")])
        buttons.append([InlineKeyboardButton("♻️ Reset (Máy tự chọn)", callback_data=f"admin_set_cat_{key}_reset")])
        buttons.append([InlineKeyboardButton("⬅️ Hủy thay đổi", callback_data="admin_products")])"""
if "Tạo ds danh mục mới" not in text:
    text = text.replace(old_admin_do_cat, new_admin_do_cat)

with open("bot.py", "w", encoding="utf-8") as f:
    f.write(text)
print("bot.py updated.")
