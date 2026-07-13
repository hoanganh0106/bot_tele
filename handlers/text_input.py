"""Admin text/media state dispatcher and broadcast collection."""

import re
from decimal import Decimal, InvalidOperation

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from core.helpers import UI_BUTTONS, format_money, format_usdt, is_admin, t
from core.products import (
    get_all_products_merged,
    get_products_cached,
    invalidate_cache,
    invalidate_categories_cache,
)
from core.runtime import db
from handlers.admin import _build_block_menu
from handlers.payment import process_paid_order


async def collect_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Gom 1 tin broadcast vào queue. Trả về True nếu đang ở chế độ broadcast."""
    if not context.user_data.get("awaiting_broadcast"):
        return False
    if not is_admin(update.effective_user.id):
        return False

    message = update.effective_message
    if not message:
        return False

    queue = context.user_data.setdefault("broadcast_queue", [])
    queue.append(message.message_id)
    count = len(queue)
    await message.reply_text(
        f"📝 Đã nhận **{count}** tin. Gửi thêm hoặc bấm nút bên dưới.",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(f"📤 Gửi ngay ({count} tin)", callback_data="broadcast_send"),
            InlineKeyboardButton("🗑 Hủy bỏ", callback_data="broadcast_cancel"),
        ]])
    )
    return True


async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý nhập text (email, sửa giá, sửa markup, v.v.)."""
    user_id = update.effective_user.id
    text = update.message.text.strip()
    db.add_user(user_id)

    # Giá USDT chỉ dùng để hiển thị cho giao diện tiếng Anh; thanh toán vẫn VND.
    if context.user_data.get("awaiting_price_usdt_for"):
        product_key = context.user_data["awaiting_price_usdt_for"]
        if text.lower() == "reset":
            db.set_custom_price_usdt(product_key, None)
            del context.user_data["awaiting_price_usdt_for"]
            await update.message.reply_text(
                f"✅ Đã xóa giá USDT của `{product_key}`.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quay lại cài đặt", callback_data=f"admin_price_{product_key}")]]),
            )
            return

        try:
            value = Decimal(text.replace(",", ".").strip())
            if not value.is_finite() or value <= 0 or value > Decimal("1000000"):
                raise InvalidOperation
            if value.as_tuple().exponent < -8:
                raise InvalidOperation
            normalized = format(value.normalize(), "f")
            db.set_custom_price_usdt(product_key, normalized)
            del context.user_data["awaiting_price_usdt_for"]
            await update.message.reply_text(
                f"✅ Đã đặt giá tiếng Anh cho `{product_key}`: **{format_usdt(normalized)}**\n\n"
                "ℹ️ Khách EN thấy giá USDT kèm số VNĐ cần thanh toán.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quay lại cài đặt", callback_data=f"admin_price_{product_key}")]]),
            )
        except (InvalidOperation, ValueError):
            await update.message.reply_text("❌ Giá USDT không hợp lệ. Ví dụ: `2.5` hoặc nhập `reset`.", parse_mode="Markdown")
        return

    # 1. Check nếu đang chờ setup giá
    if context.user_data.get("awaiting_price_for"):
        product_key = context.user_data["awaiting_price_for"]
        if text.lower() == "reset":
            db.remove_price_delta(product_key)
            db.remove_custom_price(product_key)  # Xóa cả legacy custom_price nếu còn
            invalidate_cache()
            del context.user_data["awaiting_price_for"]
            await update.message.reply_text(f"✅ Đã reset giá `{product_key}` về markup mặc định.", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quay lại cài đặt", callback_data=f"admin_price_{product_key}"), InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]]))
            return
            
        try:
            new_price = int(text.replace(",", "").replace(".", ""))
            
            # Lấy giá gốc hiện tại từ API để tính delta
            products, _ = get_products_cached()
            base_price = 0
            if products and product_key in products:
                base_price = products[product_key].get("price", 0)
            
            if base_price <= 0:
                await update.message.reply_text(
                    f"❌ Không tìm thấy giá gốc cho `{product_key}`. "
                    "Vui lòng thử lại sau khi API cập nhật.",
                    parse_mode="Markdown"
                )
                return
            
            delta = new_price - base_price
            db.set_price_delta(product_key, delta)
            invalidate_cache()
            del context.user_data["awaiting_price_for"]
            
            delta_str = f"+{format_money(delta)}" if delta >= 0 else f"-{format_money(abs(delta))}"
            await update.message.reply_text(
                f"✅ Đã cập nhật giá bán cho `{product_key}`\n\n"
                f"💰 Giá bán: **{format_money(new_price)}**\n"
                f"📊 Giá gốc hiện tại: {format_money(base_price)}\n"
                f"📐 Mức chênh lệch: **{delta_str}**\n\n"
                f"⚡ _Khi đối tác tăng giá gốc, giá bán sẽ tự động tăng theo mức chênh lệch này._",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quay lại cài đặt", callback_data=f"admin_price_{product_key}"), InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]])
            )
        except ValueError:
            await update.message.reply_text("❌ Giá không hợp lệ. Vui lòng nhập số (VD: 50000) hoặc chữ `reset`.")
        return

    # Referral reward config
    if context.user_data.get("awaiting_ref_reward"):
        del context.user_data["awaiting_ref_reward"]
        try:
            new_reward = int(text.replace(",", "").replace(".", ""))
            if new_reward < 0:
                raise ValueError
            db.set_setting("referral_reward", new_reward)
            await update.message.reply_text(
                f"✅ Đã cập nhật thưởng giới thiệu: **{format_money(new_reward)}/người**",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_referral")]])
            )
        except ValueError:
            await update.message.reply_text("❌ Số tiền không hợp lệ. VD: `2000`", parse_mode="Markdown")
        return

    # New user reward config
    if context.user_data.get("awaiting_ref_newuser"):
        del context.user_data["awaiting_ref_newuser"]
        try:
            new_reward = int(text.replace(",", "").replace(".", ""))
            if new_reward < 0:
                raise ValueError
            db.set_setting("referral_new_user_reward", new_reward)
            status = f"**{format_money(new_reward)}**" if new_reward > 0 else "**TẮT**"
            await update.message.reply_text(
                f"✅ Đã cập nhật thưởng người được mời: {status}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_referral")]])
            )
        except ValueError:
            await update.message.reply_text("❌ Số tiền không hợp lệ. VD: `500` hoặc `0` để tắt", parse_mode="Markdown")
        return

    # Min deposit config
    if context.user_data.get("awaiting_min_deposit"):
        del context.user_data["awaiting_min_deposit"]
        try:
            new_min = int(text.replace(",", "").replace(".", ""))
            if new_min < 1000:
                raise ValueError
            db.set_setting("min_deposit", new_min)
            await update.message.reply_text(
                f"✅ Đã cập nhật nạp tối thiểu: **{format_money(new_min)}**",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_referral")]])
            )
        except ValueError:
            await update.message.reply_text("❌ Số tiền không hợp lệ (tối thiểu 1,000đ). VD: `5000`", parse_mode="Markdown")
        return

    # Wallet adjust (admin cộng/trừ ví)
    if context.user_data.get("awaiting_wallet_adjust"):
        adjust = context.user_data.pop("awaiting_wallet_adjust")
        target_id = adjust["user_id"]
        action = adjust["action"]
        try:
            amount = int(text.replace(",", "").replace(".", ""))
            if amount <= 0:
                raise ValueError
        except ValueError:
            await update.message.reply_text("❌ Số tiền không hợp lệ. Nhập số dương. VD: `5000`", parse_mode="Markdown")
            return
        
        user_info = db.get_user(target_id)
        name = user_info.get("first_name") or str(target_id)
        
        if action == "add":
            new_balance = db.add_balance(target_id, amount, reason="admin_add")
            action_text = f"➕ Cộng **{format_money(amount)}**"
            notify_text = t(target_id, "admin_wallet_added", amount=format_money(amount), balance=format_money(new_balance))
        else:
            current = db.get_user_balance(target_id)
            if amount > current:
                await update.message.reply_text(
                    f"❌ Không thể trừ {format_money(amount)} — Số dư chỉ có {format_money(current)}"
                )
                return
            db.deduct_balance(target_id, amount)
            new_balance = db.get_user_balance(target_id)
            action_text = f"➖ Trừ **{format_money(amount)}**"
            notify_text = t(target_id, "admin_wallet_deducted", amount=format_money(amount), balance=format_money(new_balance))
        
        await update.message.reply_text(
            f"✅ **ĐÃ CẬP NHẬT VÍ**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👤 User: **{name}** (`{target_id}`)\n"
            f"📝 Thao tác: {action_text}\n"
            f"💵 Số dư mới: **{format_money(new_balance)}**",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]])
        )
        
        try:
            await context.bot.send_message(chat_id=target_id, text=notify_text, parse_mode="Markdown")
        except Exception:
            pass
        return

    if context.user_data.get("awaiting_new_prod"):
        del context.user_data["awaiting_new_prod"]
        try:
            parts = [p.strip() for p in text.split("|")]
            if len(parts) == 3:
                prod_id, name, price = parts
                prod_id = prod_id.lower().replace(" ", "")
                # Kiểm tra trùng mã ID
                existing_products, _ = get_all_products_merged()
                if existing_products and prod_id in existing_products:
                    existing_name = existing_products[prod_id].get('name', '?')
                    await update.message.reply_text(
                        f"⚠️ **Mã `{prod_id}` đã tồn tại!**\n"
                        f"Sản phẩm hiện tại: {existing_name}\n\n"
                        f"Vui lòng chọn mã khác (VD: `{prod_id}_2`).",
                        parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quản trị", callback_data="admin_home")]])
                    )
                    return
                db.add_custom_product(prod_id, name, int(price))
                invalidate_cache()
                await update.message.reply_text(f"✅ Đã thêm sản phẩm `{prod_id}`. Hãy vào Quản lý sản phẩm để đổi danh mục và cập nhật kho cho nó!", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Cài đặt sản phẩm", callback_data=f"admin_price_{prod_id}"), InlineKeyboardButton("⬅️ Quản trị", callback_data="admin_home")]]))
            else:
                await update.message.reply_text("❌ Sai cú pháp. Mẫu: `ytb_1m | Youtube Premium 1T | 35000`", parse_mode="Markdown")
        except Exception:
            await update.message.reply_text("❌ Có lỗi xảy ra.")
        return

    # Chế độ thêm sản phẩm vào kho tự động (mỗi dòng = 1 item, bất kể format)
    if context.user_data.get("awaiting_stock_items_for"):
        key = context.user_data["awaiting_stock_items_for"]
        del context.user_data["awaiting_stock_items_for"]
        items = [line.strip() for line in text.split("\n") if line.strip()]
        if not items:
            await update.message.reply_text("❌ Không có sản phẩm nào được nhận. Vui lòng gửi mỗi dòng 1 sản phẩm.")
            return
        added = db.add_custom_accounts(key, items)
        invalidate_cache()
        await update.message.reply_text(
            f"✅ Đã **THÊM {len(items)}** sản phẩm vào kho `{key}`.\n"
            f"📦 Tổng kho tự động hiện tại: **{added}**\n\n"
            f"_Khi khách mua, bot sẽ tự động cắt sản phẩm trong kho ra trả._",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📥 Thêm tiếp", callback_data=f"admin_stock_add_items_{key}")],
                [InlineKeyboardButton("⬅️ Quay lại", callback_data=f"admin_price_{key}"), InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]
            ])
        )
        return

    # Chế độ set số lượng liên hệ trực tiếp
    if context.user_data.get("awaiting_stock_manual_for"):
        key = context.user_data["awaiting_stock_manual_for"]
        del context.user_data["awaiting_stock_manual_for"]
        try:
            ns = int(text.replace(",", "").replace(".", ""))
            db.set_custom_stock(key, ns)
            db.clear_custom_accounts(key)  # Xóa kho tự động nếu có
            invalidate_cache()
            await update.message.reply_text(
                f"✅ Đã set tồn kho cho `{key}` là: **{ns}**\n"
                f"_Khách mua sẽ cần liên hệ Admin để nhận hàng._",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quay lại cài đặt", callback_data=f"admin_price_{key}"), InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]])
            )
        except ValueError:
            await update.message.reply_text("❌ Vui lòng nhập một số nguyên (VD: `10`).\nThử lại hoặc bấm nút Quay lại.", parse_mode="Markdown")
        return

    if context.user_data.get("awaiting_new_cat"):
        del context.user_data["awaiting_new_cat"]
        try:
            parts = [p.strip() for p in text.split("|")]
            if len(parts) == 3:
                cat_id, name, icon = parts
                cat_id = cat_id.lower().replace(" ", "")
                db.add_custom_category_def(cat_id, name, icon)
                invalidate_categories_cache()
                await update.message.reply_text(f"✅ Đã thêm danh mục: {icon} {name}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quản trị", callback_data="admin_home")]]))
            else:
                await update.message.reply_text("❌ Sai cú pháp. Vui lòng thử lại theo mẫu: `msoffice | Microsoft Office | 💻`", parse_mode="Markdown")
        except Exception:
            await update.message.reply_text("❌ Có lỗi xảy ra.")
        return

    if context.user_data.get("awaiting_rename_cat"):
        cat_id = context.user_data["awaiting_rename_cat"]
        del context.user_data["awaiting_rename_cat"]
        try:
            parts = [p.strip() for p in text.split("|")]
            if len(parts) == 2:
                name, icon = parts
                db.add_custom_category_def(cat_id, name, icon)
                invalidate_cache()
                invalidate_categories_cache()
                await update.message.reply_text(
                    f"✅ Đã đổi tên danh mục `{cat_id}` thành: {icon} {name}",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Quay lại danh mục", callback_data="admin_rename_cat_list"),
                         InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]
                    ])
                )
            else:
                await update.message.reply_text(
                    "❌ Sai cú pháp. Vui lòng nhập theo mẫu:\n`Tên mới | Emoji`\n\nVí dụ: `ChatGPT Pro | 🤖`",
                    parse_mode="Markdown"
                )
        except Exception:
            await update.message.reply_text("❌ Có lỗi xảy ra.")
        return

    if context.user_data.get("awaiting_set_emoji"):
        cat_id = context.user_data["awaiting_set_emoji"]
        del context.user_data["awaiting_set_emoji"]
        try:
            # Kiểm tra xem tin nhắn có chứa custom emoji không
            emoji_id_from_entity = None
            if update.message.entities:
                for entity in update.message.entities:
                    if entity.type == "custom_emoji":
                        emoji_id_from_entity = entity.custom_emoji_id
                        break
            
            if text.lower() == "reset":
                db.set_category_emoji_id(cat_id, None)
                await update.message.reply_text(
                    f"✅ Đã xóa custom emoji cho danh mục `{cat_id}`. Sẽ dùng emoji mặc định.",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_set_emoji_list"),
                         InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]
                    ])
                )
            elif emoji_id_from_entity:
                # Admin gửi trực tiếp custom emoji → tự lấy ID
                db.set_category_emoji_id(cat_id, emoji_id_from_entity)
                await update.message.reply_text(
                    f"✅ Đã set custom emoji cho danh mục `{cat_id}`!\n"
                    f"Emoji ID: `{emoji_id_from_entity}`",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_set_emoji_list"),
                         InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]
                    ])
                )
            elif text.strip().isdigit() and len(text.strip()) > 10:
                # Admin nhập ID thủ công
                db.set_category_emoji_id(cat_id, text.strip())
                await update.message.reply_text(
                    f"✅ Đã set custom emoji cho danh mục `{cat_id}`!\n"
                    f"Emoji ID: `{text.strip()}`",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_set_emoji_list"),
                         InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]
                    ])
                )
            else:
                await update.message.reply_text(
                    "❌ Không nhận diện được emoji.\n"
                    "Hãy gửi **custom emoji trực tiếp** hoặc nhập **emoji ID** (dãy số dài).",
                    parse_mode="Markdown"
                )
        except Exception:
            await update.message.reply_text("❌ Có lỗi xảy ra.")
        return

    if context.user_data.get("awaiting_welcome_msg"):
        del context.user_data["awaiting_welcome_msg"]
        try:
            if text.lower() == "reset":
                db.set_welcome_message(None)
                await update.message.reply_text(
                    "✅ Đã quay về lời chào mặc định.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_ui_custom"),
                         InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]
                    ])
                )
            else:
                # Giữ nguyên toàn bộ định dạng Telegram (đậm, nghiêng, link,
                # quote, code, custom emoji...) khi lưu lời chào dưới dạng HTML.
                msg = update.message
                raw_text = msg.text or ""
                entities = msg.entities or []

                # text_html xử lý đúng offset UTF-16 và lồng entity. Nếu admin
                # không dùng định dạng Telegram thì vẫn cho phép nhập HTML tay.
                html_welcome = msg.text_html if entities else raw_text
                
                db.set_welcome_message(html_welcome)
                await update.message.reply_text(
                    f"✅ Đã cập nhật lời chào /start!\n\n"
                    f"📝 Xem trước: gõ /start để kiểm tra.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_ui_custom"),
                         InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]
                    ])
                )
        except Exception:
            await update.message.reply_text("❌ Có lỗi xảy ra.")
        return

    if context.user_data.get("awaiting_menu_title"):
        context.user_data.pop("awaiting_menu_title", None)
        try:
            if text.lower() == "reset":
                db.set_menu_title(None)
                result = "✅ Đã quay về tiêu đề menu sản phẩm mặc định."
            else:
                msg = update.message
                html_title = msg.text_html if (msg.entities or []) else (msg.text or "")
                db.set_menu_title(html_title)
                result = "✅ Đã cập nhật menu sản phẩm tiếng Việt.\n\n📝 Mở /menu để kiểm tra."
            await update.message.reply_text(
                result,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_ui_custom"),
                    InlineKeyboardButton("🏠 Thoát", callback_data="admin_home"),
                ]]),
            )
        except Exception:
            await update.message.reply_text("❌ Có lỗi xảy ra.")
        return

    if context.user_data.get("awaiting_ui_emoji"):
        btn_key = context.user_data["awaiting_ui_emoji"]
        del context.user_data["awaiting_ui_emoji"]
        try:
            # Tự nhận custom emoji từ entities
            emoji_id_from_entity = None
            if update.message.entities:
                for entity in update.message.entities:
                    if entity.type == "custom_emoji":
                        emoji_id_from_entity = entity.custom_emoji_id
                        break

            btn_name = UI_BUTTONS.get(btn_key, btn_key)
            if text.lower() == "reset":
                db.set_ui_emoji(btn_key, None)
                await update.message.reply_text(
                    f"✅ Đã xóa custom emoji cho nút `{btn_name}`.",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_edit_btn_list"),
                         InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]
                    ])
                )
            elif emoji_id_from_entity:
                db.set_ui_emoji(btn_key, emoji_id_from_entity)
                await update.message.reply_text(
                    f"✅ Đã set custom emoji cho nút `{btn_name}`!\n"
                    f"Emoji ID: `{emoji_id_from_entity}`",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_edit_btn_list"),
                         InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]
                    ])
                )
            elif text.strip().isdigit() and len(text.strip()) > 10:
                db.set_ui_emoji(btn_key, text.strip())
                await update.message.reply_text(
                    f"✅ Đã set custom emoji cho nút `{btn_name}`!\n"
                    f"Emoji ID: `{text.strip()}`",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_edit_btn_list"),
                         InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]
                    ])
                )
            else:
                await update.message.reply_text(
                    "❌ Không nhận diện được.\n"
                    "Gửi **custom emoji trực tiếp** hoặc nhập **emoji ID** (dãy số dài).",
                    parse_mode="Markdown"
                )
        except Exception:
            await update.message.reply_text("❌ Có lỗi xảy ra.")
        return

    if context.user_data.get("awaiting_desc_for"):
        key = context.user_data["awaiting_desc_for"]
        del context.user_data["awaiting_desc_for"]
        if text.lower() == "reset":
            db.set_custom_description(key, None)
            await update.message.reply_text(f"✅ Đã xóa mô tả cho sản phẩm `{key}`.", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quay lại cài đặt", callback_data=f"admin_price_{key}"), InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]]))
        else:
            db.set_custom_description(key, text)
            await update.message.reply_text(f"✅ Đã cập nhật mô tả cho sản phẩm `{key}`.", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quay lại cài đặt", callback_data=f"admin_price_{key}"), InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]]))
        return

    if context.user_data.get("awaiting_welcome_msg_en"):
        del context.user_data["awaiting_welcome_msg_en"]
        try:
            if text.lower() == "reset":
                db.set_welcome_message_en(None)
                await update.message.reply_text(
                    "✅ Đã quay về lời chào tiếng Anh mặc định.",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_ui_custom"),
                        InlineKeyboardButton("🏠 Thoát", callback_data="admin_home"),
                    ]]),
                )
            else:
                msg = update.message
                raw_text = msg.text or ""
                html_welcome = msg.text_html if (msg.entities or []) else raw_text
                db.set_welcome_message_en(html_welcome)
                await update.message.reply_text(
                    "✅ Đã cập nhật lời chào tiếng Anh.\n\n📝 Đổi tài khoản sang EN rồi gõ /start để kiểm tra.",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_ui_custom"),
                        InlineKeyboardButton("🏠 Thoát", callback_data="admin_home"),
                    ]]),
                )
        except Exception:
            await update.message.reply_text("❌ Có lỗi xảy ra.")
        return

    if context.user_data.get("awaiting_menu_title_en"):
        context.user_data.pop("awaiting_menu_title_en", None)
        try:
            if text.lower() == "reset":
                db.set_menu_title_en(None)
                result = "✅ Đã quay về tiêu đề menu sản phẩm tiếng Anh mặc định."
            else:
                msg = update.message
                html_title = msg.text_html if (msg.entities or []) else (msg.text or "")
                db.set_menu_title_en(html_title)
                result = "✅ Đã cập nhật menu sản phẩm tiếng Anh.\n\n📝 Chuyển sang EN rồi mở /menu để kiểm tra."
            await update.message.reply_text(
                result,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_ui_custom"),
                    InlineKeyboardButton("🏠 Thoát", callback_data="admin_home"),
                ]]),
            )
        except Exception:
            await update.message.reply_text("❌ Có lỗi xảy ra.")
        return

    if context.user_data.get("awaiting_desc_en_for"):
        key = context.user_data.pop("awaiting_desc_en_for")
        db.set_custom_description_en(key, None if text.lower() == "reset" else text)
        await update.message.reply_text(f"✅ Đã cập nhật mô tả EN cho sản phẩm `{key}`.", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quay lại cài đặt", callback_data=f"admin_price_{key}")]]))
        return

    if context.user_data.get("awaiting_name_en_for"):
        key = context.user_data.pop("awaiting_name_en_for")
        db.set_custom_name_en(key, None if text.lower() == "reset" else text)
        await update.message.reply_text(f"✅ Đã cập nhật tên EN cho sản phẩm `{key}`.", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quay lại cài đặt", callback_data=f"admin_price_{key}")]]))
        return

    # 1.5 Handle renaming products
    if context.user_data.get("awaiting_name_for"):
        key = context.user_data["awaiting_name_for"]
        del context.user_data["awaiting_name_for"]
        
        if text.lower() == "reset":
            db.set_custom_name(key, None)
            await update.message.reply_text(f"✅ Đã reset tên sản phẩm `{key}` về gốc.", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quay lại cài đặt", callback_data=f"admin_price_{key}"), InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]]))
        else:
            db.set_custom_name(key, text)
            await update.message.reply_text(f"✅ Đã đổi tên sản phẩm `{key}` thành:\n**{text}**", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quay lại cài đặt", callback_data=f"admin_price_{key}"), InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]]))
        return

    # 2. Check nếu đang chờ setup markup
    if context.user_data.get("awaiting_markup"):
        try:
            amount = int(text)
            if amount < 0 or amount > 10000000: raise ValueError
            db.set_setting("default_markup_fixed", amount)
            invalidate_cache()  # Xóa cache để tính lại giá tất cả sản phẩm
            del context.user_data["awaiting_markup"]
            await update.message.reply_text(f"✅ Đã cập nhật Markup mặc định thành **+{format_money(amount)}**", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quản trị", callback_data="admin_home")]]))
        except ValueError:
            await update.message.reply_text("❌ Vui lòng nhập số từ 0 đến 10.000.000.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quản trị", callback_data="admin_home")]]))
        return

    # 2b. Check nếu đang chờ nhập ID để chặn broadcast (ĐẶT TRƯỚC broadcast)
    if context.user_data.get("awaiting_block_id"):
        if not is_admin(user_id):
            context.user_data.pop("awaiting_block_id", None)
            return
        context.user_data.pop("awaiting_block_id", None)
        # Tách nhiều ID theo dấu phẩy, khoảng trắng, xuống dòng
        tokens = re.split(r"[\s,;]+", text.strip())
        added, duplicated, invalid = [], [], []
        for tok in tokens:
            if not tok:
                continue
            try:
                uid = int(tok)
            except ValueError:
                invalid.append(tok)
                continue
            if is_admin(uid):
                invalid.append(f"{tok}(admin)")
                continue
            if db.add_broadcast_block(uid):
                added.append(uid)
            else:
                duplicated.append(uid)
        lines = []
        if added:
            lines.append("✅ Đã chặn: " + ", ".join(f"`{x}`" for x in added))
        if duplicated:
            lines.append("ℹ️ Đã bị chặn từ trước: " + ", ".join(f"`{x}`" for x in duplicated))
        if invalid:
            lines.append("❌ Không hợp lệ (bỏ qua): " + ", ".join(f"`{x}`" for x in invalid))
        if not lines:
            lines.append("❌ Không nhận được ID hợp lệ nào.")
        text_out, markup = _build_block_menu(extra="\n".join(lines))
        await update.message.reply_text(text_out, parse_mode="Markdown", reply_markup=markup)
        return

    # 3. Check nếu đang chờ gửi thông báo broadcast
    if await collect_broadcast_message(update, context):
        return

    # 4. Tra cứu người dùng (ĐẶT TRƯỚC email handler để không bị chặn bởi return)
    if context.user_data.get("awaiting_user_lookup"):
        del context.user_data["awaiting_user_lookup"]
        target_id, target_username, user_orders = db.find_user_orders_by_query(text)
        
        if target_id is None:
            await update.message.reply_text(
                f"❌ Không tìm thấy thông tin khách hàng nào khớp với `{text}`.\n"
                f"Vui lòng kiểm tra lại Username hoặc ID.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Thử lại", callback_data="admin_lookup"), InlineKeyboardButton("⬅️ Quản trị", callback_data="admin_home")]])
            )
            return

        recent = sorted(user_orders.items(), key=lambda x: x[1].get("created_at", ""), reverse=True)[:10]
        total_spent = sum(o.get("total", 0) for o in user_orders.values() if o.get("status") == "paid")
        user_info = db.get_user(target_id)
        user_balance = db.get_user_balance(target_id)
        display_username = target_username or user_info.get("username") or "Không có"
        display_name = user_info.get("first_name") or "Không rõ"
        joined_at = user_info.get("joined_at", "Không rõ")
        if joined_at and joined_at != "Không rõ":
            joined_at = joined_at[:16]
        
        msg = (
            f"🔍 **THÔNG TIN KHÁCH HÀNG**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👤 ID: `{target_id}`\n"
            f"👤 Tên: {display_name}\n"
            f"👤 Username: {display_username}\n"
            f"📅 Tham gia: {joined_at}\n"
            f"💰 Số dư ví: **{format_money(user_balance)}**\n"
            f"💳 Đã chi (đơn thành công): **{format_money(total_spent)}**\n"
            f"📦 Tổng số đơn: **{len(user_orders)}**\n"
            f"🎁 Đã giới thiệu: **{user_info.get('referral_count', 0)}** người\n\n"
            f"📋 **10 ĐƠN GẦN NHẤT:**\n"
        )
        
        if not recent:
            msg += "_Chưa có đơn hàng nào_\n"
        else:
            for code, order in recent:
                status_icon = {
                    "pending": "⏳", "paid": "✅", "cancelled": "❌",
                    "cancelled_timeout": "⏰", "failed": "💔"
                }.get(order["status"], "❓")
                
                msg += (
                    f"{status_icon} `{code}` - {format_money(order.get('total', 0))}\n"
                    f"   Sản phẩm: {order.get('product_name', '?')} x{order.get('qty', 1)}\n"
                    f"   Thời gian: {order.get('created_at', '?')[:16]}\n"
                )
                
                items = order.get("items", [])
                if items:
                    msg += "   🔑 **Tài khoản đã giao:**\n"
                    for item in items:
                        msg += f"   `{item}`\n"
                msg += "\n"

        buttons = [
            [
                InlineKeyboardButton(f"➕ Cộng ví", callback_data=f"admin_wallet_add_{target_id}"),
                InlineKeyboardButton(f"➖ Trừ ví", callback_data=f"admin_wallet_sub_{target_id}"),
            ],
            [InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]
        ]
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))
        return

    # 5. Mặc định xử lý nhập email cho đơn chờ email
    waiting_order = db.find_order_waiting_email(user_id)
    if not waiting_order:
        return
        
    order_code, order = waiting_order
    text_lines = [e.strip() for e in text.split("\n") if e.strip()]

    is_custom_local = order.get("is_custom_local", False)
    
    # Đối với sản phẩm liên kết API bắt buộc phải là Email chuẩn
    if not is_custom_local:
        emails = [e for e in text_lines if "@" in e]
        if len(emails) != order["qty"]:
            await update.message.reply_text(
                t(user_id, "email_count_error", required=order["qty"], received=len(emails)),
                parse_mode="Markdown"
            )
            return

        email_regex = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        invalid = [e for e in emails if not re.match(email_regex, e)]
        if invalid:
            await update.message.reply_text(
                t(user_id, "email_invalid", emails=', '.join(invalid)),
                parse_mode="Markdown"
            )
            return
        order["emails"] = emails
    else:
        # Nếu là hàng tự bán của admin, chấp nhận bất kỳ thông tin gì khách gửi
        order["emails"] = text_lines

    order["status"] = "pending"
    db.save_order(order_code, order)
    await update.message.reply_text(t(user_id, "info_processing"))
    await process_paid_order(context, order_code, order.get("payment_source", "sepay"))


async def handle_media_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Nhận ảnh/video/file; hiện chỉ phục vụ gom tin broadcast của admin."""
    await collect_broadcast_message(update, context)
