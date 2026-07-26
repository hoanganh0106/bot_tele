"""Admin dashboard commands and callback handlers."""

import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from core.config import ADMIN_IDS
from core.helpers import (
    UI_BUTTONS,
    escape_html,
    escape_md,
    fmt_icon,
    format_money,
    format_usdt,
    format_user_link,
    get_sell_price,
    get_usdt_vnd_rate,
    is_admin,
    t,
)
from core.products import (
    classify_product,
    get_all_categories_merged,
    get_all_products_merged,
    get_products_cached,
    get_hypervin_balance,
    invalidate_cache,
)
from core.runtime import api, db
from core.screens import build_admin_product_back_keyboard, build_category_grid
from handlers.payment import process_paid_order


async def cmd_getemoji(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: Lấy custom_emoji_id từ tin nhắn chứa custom emoji."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Chỉ Admin mới dùng được lệnh này.")
        return

    target_msg = update.message.reply_to_message or update.message
    
    emoji_found = []
    if target_msg.entities:
        for entity in target_msg.entities:
            if entity.type == "custom_emoji":
                emoji_id = entity.custom_emoji_id
                emoji_text = target_msg.text[entity.offset:entity.offset + entity.length]
                emoji_found.append((emoji_text, emoji_id))
    
    if not emoji_found:
        await update.message.reply_text(
            "📌 **HƯỚNG DẪN LẤY CUSTOM EMOJI ID**\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "**Cách 1:** Gửi một custom emoji vào chat, rồi reply tin nhắn đó bằng `/getemoji`\n\n"
            "**Cách 2:** Gửi custom emoji cùng với lệnh\n\n"
            "💡 _Bạn cần Telegram Premium để tìm và gửi custom emoji._",
            parse_mode="Markdown"
        )
        return
    
    text = "🎨 **CUSTOM EMOJI ĐÃ TÌM THẤY**\n━━━━━━━━━━━━━━━━━━\n\n"
    for emoji_text, emoji_id in emoji_found:
        text += f"• {emoji_text} → ID: `{emoji_id}`\n"
    
    text += "\n📋 Để gắn emoji vào danh mục, vào:\n**Admin → Quản lý sản phẩm → 🎨 Đổi Icon danh mục**"
    
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_setrate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command: update the runtime VND value of one USDT."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Chỉ Admin mới dùng được lệnh này.")
        return
    if len(context.args) != 1:
        await update.message.reply_text(
            f"Tỷ giá hiện tại: **{get_usdt_vnd_rate():,} VND/USDT**\n"
            "Cách dùng: `/setrate 26800`",
            parse_mode="Markdown",
        )
        return
    try:
        rate = int(context.args[0].replace(",", "").replace(".", ""))
    except ValueError:
        rate = 0
    if not 10_000 <= rate <= 100_000:
        await update.message.reply_text("❌ Tỷ giá phải nằm trong khoảng 10.000–100.000 VND/USDT.")
        return
    db.set_setting("usdt_vnd_rate", rate)
    await update.message.reply_text(f"✅ Đã cập nhật tỷ giá: **{rate:,} VND/USDT**", parse_mode="Markdown")


async def handle_admin_confirm_pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin xác nhận thanh toán thủ công."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Không có quyền!", show_alert=True)
        return

    await query.answer()
    order_code = query.data.replace("adminpay_", "")
    await process_paid_order(context, order_code, "admin_manual")


async def handle_admin_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin hủy đơn — dùng atomic operation."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("⛔ Không có quyền!", show_alert=True)
        return

    await query.answer()
    order_code = query.data.replace("admincx_", "")
    
    order = db.get_order(order_code)
    if not order:
        await query.edit_message_text("Đơn hàng không tồn tại.")
        return
    
    user_id = order.get("user_id")
    cancelled = db.cancel_order_if_pending(order_code, status="cancelled")
    if cancelled:
        db.refund_order_wallet_if_needed(order_code)
        db.release_usdt_amount(order_code)
        await query.edit_message_text(f"❌ Đơn `{order_code}` đã bị admin hủy.", parse_mode="Markdown")
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=t(user_id, "admin_cancelled_order", order_code=order_code),
                parse_mode="Markdown"
            )
        except Exception:
            pass
    else:
        await query.edit_message_text(
            f"⚠️ Đơn `{order_code}` không thể hủy (trạng thái: {order.get('status', '?')}).",
            parse_mode="Markdown"
        )


def _build_block_menu(extra: str = ""):
    """Xây màn hình quản lý danh sách ID bị chặn nhận broadcast."""
    blocklist = db.get_broadcast_blocklist()
    lines = [
        "🚫 **CHẶN BROADCAST THEO ID**\n",
        "Các ID trong danh sách sẽ **không** nhận tin broadcast.",
    ]
    if blocklist:
        lines.append(f"\n📋 Đang chặn **{len(blocklist)}** ID:")
        lines.append("\n".join(f"• `{uid}`" for uid in blocklist))
    else:
        lines.append("\n_Chưa chặn ID nào._")
    if extra:
        lines.append("\n" + extra)

    buttons = [[InlineKeyboardButton("➕ Thêm ID chặn", callback_data="broadcast_block_add")]]
    # Mỗi ID 1 nút gỡ chặn (tối đa 20 nút cho gọn)
    for uid in blocklist[:20]:
        buttons.append([InlineKeyboardButton(f"❌ Bỏ chặn {uid}", callback_data=f"broadcast_unblock_{uid}")])
    if blocklist:
        buttons.append([InlineKeyboardButton("🧹 Bỏ chặn tất cả", callback_data="broadcast_block_clear")])
    buttons.append([InlineKeyboardButton("⬅️ Về Broadcast", callback_data="admin_broadcast")])
    return "\n".join(lines), InlineKeyboardMarkup(buttons)


def _build_admin_dashboard():
    """Được gọi khi hiển thị admin dashboard."""
    text = (
        "🛠 **ADMIN DASHBOARD**\n\n"
        "👇 _Chọn chức năng quản lý:_"
    )
    buttons = [
        [InlineKeyboardButton("📊 Doanh thu", callback_data="admin_stats"),
         InlineKeyboardButton("👥 Người dùng", callback_data="admin_users")],
        [InlineKeyboardButton("🔍 Tra cứu khách", callback_data="admin_user_lookup"),
         InlineKeyboardButton("📦 Sản phẩm", callback_data="admin_products")],
        [InlineKeyboardButton("👥 Khách gần đây", callback_data="admin_recent_users")],
        [InlineKeyboardButton("⚙️ Markup", callback_data="admin_markup"),
         InlineKeyboardButton("🎁 Giới thiệu", callback_data="admin_referral")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast"),
         InlineKeyboardButton("🎨 Giao diện", callback_data="admin_ui_custom")],
        [InlineKeyboardButton("📋 Xuất đơn giá", callback_data="admin_export_prices")],
    ]
    return text, buttons


async def _notify_all_admins(context, text: str):
    """Gửi thông báo cho tất cả admin đồng thời."""
    async def _send(admin_id):
        try:
            await context.bot.send_message(chat_id=admin_id, text=text, parse_mode="Markdown")
        except Exception:
            pass
    await asyncio.gather(*[_send(aid) for aid in ADMIN_IDS])


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Trang Dashboard quản lý dành cho Admin."""
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text("⛔ Tính năng chỉ dành cho Admin.")
    text, buttons = _build_admin_dashboard()
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


async def render_admin_product_detail(update, context, key):
    query = update.callback_query
    info = None
    products, _ = get_all_products_merged()
    if products and key in products:
        info = products[key]
        
    current_name = db.get_custom_name(key) or (info["name"] if info else key)
    current_cat, current_icon, _ = classify_product(key, info if info else {"name": key})
    sell_price = get_sell_price(key, info["price"] if info else 0, info.get("is_custom_local", False) if info else False)
    usdt_price = db.get_custom_price_usdt(key)
    
    stock_status = "Không rõ"
    is_custom_local = False
    if info:
        stock = info.get("stock", 0)
        status_txt = f"Còn hàng ({stock})" if stock > 0 else ("Hết hàng" if stock == 0 else "Đang cập nhật kho")
        stock_status = f"✅ {status_txt}" if stock > 0 else f"❌ {status_txt}"
        is_custom_local = info.get("is_custom_local", False)
        
    has_auto_accs = db.has_custom_accounts_enabled(key)
    source_txt = "🌐 Hàng đối tác (CTV Gốc)"

    if is_custom_local:
        if has_auto_accs:
            source_txt = "⚡ Tự bán (Tự động giao từ kho)"
        else:
            source_txt = "📝 Tự bán (Liên hệ trực tiếp)"
            
    hide_status = "🟢 Đang hiển thị"
    hide_btn_txt = "🙈 [Giao diện] ẨN SẢN PHẨM"
    if db.is_product_hidden(key):
        hide_status = "🔴 ĐÃ ẨN VỚI KHÁCH"
        hide_btn_txt = "👀 [Giao diện] HIỆN SẢN PHẨM"

    # Thông tin chênh lệch giá (delta)
    delta = db.get_price_delta(key)
    base_price = info['price'] if info else 0
    if delta is not None:
        delta_str = f"+{format_money(delta)}" if delta >= 0 else f"-{format_money(abs(delta))}"
        price_mode = f"📐 Chênh lệch đã set: **{delta_str}**"
    else:
        default_markup = db.get_setting("default_markup_fixed", 10000)
        price_mode = f"📐 Markup mặc định: +{format_money(default_markup)}"

    text = (
        f"⚙️ **Cài đặt Sản Phẩm**\n"
        f"ID: `{key}`\n"
        f"Nguồn gốc: **{source_txt}**\n"
        f"Trạng thái: **{hide_status}**\n"
        f"Số lượng kho: **{stock_status}**\n"
        f"Tên hiển thị: **{current_name}**\n"
        f"Danh mục: {current_icon} {current_cat}\n"
        f"Giá gốc (từ đối tác): {format_money(base_price)}\n"
        f"{price_mode}\n"
        f"💰 Giá bán hiện tại: **{format_money(sell_price)}**\n\n"
        f"💵 Giá hiển thị EN: **{format_usdt(usdt_price) if usdt_price is not None else 'Chưa đặt'}**\n\n"
        f"⚡ _Khi đối tác tăng giá, giá bán tự tăng theo._\n\n"
        f"Vui lòng chọn thao tác bên dưới:"
    )
    
    _, _, cid = classify_product(key, info if info else {"name": key})
    
    buttons = [
        [InlineKeyboardButton("💰 Sửa giá", callback_data=f"admin_do_price_{key}"),
         InlineKeyboardButton("💵 Giá USDT (EN)", callback_data=f"admin_do_price_usdt_{key}")],
        [InlineKeyboardButton("📦 Sửa tồn kho", callback_data=f"admin_do_stock_{key}")],
        [InlineKeyboardButton("✏️ Đổi tên hiển thị", callback_data=f"admin_do_name_{key}"),
         InlineKeyboardButton(hide_btn_txt, callback_data=f"admin_toggle_hide_{key}")],
        [InlineKeyboardButton("📜 Sửa nội dung/Mô tả", callback_data=f"admin_do_desc_{key}")],
        [InlineKeyboardButton("✏️ Tên EN", callback_data=f"admin_do_name_en_{key}"), InlineKeyboardButton("📝 Mô tả EN", callback_data=f"admin_do_desc_en_{key}")],
        [InlineKeyboardButton("🔀 Chuyển danh mục", callback_data=f"admin_do_cat_{key}")]
    ]
    
    if is_custom_local:
        buttons.append([InlineKeyboardButton("🗑️ Xóa sản phẩm (Chỉ Hàng tự bán)", callback_data=f"admin_del_prod_{key}_{cid}")])
        
    buttons.append([
        InlineKeyboardButton("⬅️ Quay lại", callback_data=f"admin_viewcat_{cid}"),
        InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")
    ])
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))


def _clear_admin_state(context: ContextTypes.DEFAULT_TYPE):
    """Xóa các state nhập liệu tạm trong dashboard admin."""
    for key_to_clear in [
        "awaiting_price_for", "awaiting_price_usdt_for", "awaiting_markup", "awaiting_broadcast",
        "broadcast_queue", "awaiting_user_lookup",
        "awaiting_stock_items_for", "awaiting_stock_manual_for",
        "awaiting_ref_reward", "awaiting_ref_newuser", "awaiting_min_deposit",
        "awaiting_wallet_adjust", "awaiting_desc_for", "awaiting_name_for", "awaiting_desc_en_for", "awaiting_name_en_for",
        "awaiting_new_cat", "awaiting_new_prod", "awaiting_rename_cat",
        "awaiting_set_emoji",
        "awaiting_welcome_msg", "awaiting_welcome_msg_en",
        "awaiting_menu_title", "awaiting_menu_title_en", "awaiting_ui_emoji",
        "awaiting_block_id",
    ]:
        context.user_data.pop(key_to_clear, None)


async def handle_admin_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý click trong Admin Dashboard."""
    query = update.callback_query
    if not is_admin(query.from_user.id):
        return await query.answer("⛔ Không có quyền!", show_alert=True)
        
    await query.answer()
    data = query.data

    if data == "admin_home":
        _clear_admin_state(context)
        text, buttons = _build_admin_dashboard()
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))
        return

    if data == "broadcast_send":
        queue = context.user_data.get("broadcast_queue") or []
        context.user_data.pop("awaiting_broadcast", None)
        context.user_data.pop("broadcast_queue", None)
        if not queue:
            await query.edit_message_text("❌ Chưa có tin nào để gửi.")
            return

        blocklist = set(db.get_broadcast_blocklist())
        users = [uid for uid in db.get_all_users() if not is_admin(uid) and uid not in blocklist]
        if not users:
            await query.edit_message_text("❌ Chưa có người dùng nào để thông báo.")
            return

        total = len(users)
        blocked_note = f" (bỏ qua {len(blocklist)} ID bị chặn)" if blocklist else ""
        await query.edit_message_text(f"⏳ Đang gửi {len(queue)} tin đến {total} người dùng{blocked_note}...")

        sem = asyncio.Semaphore(25)
        success_count = 0
        failed_count = 0
        lock = asyncio.Lock()
        admin_chat_id = query.message.chat_id

        async def _send_all_to_user(uid):
            nonlocal success_count, failed_count
            async with sem:
                try:
                    for message_id in queue:
                        await context.bot.copy_message(
                            chat_id=uid,
                            from_chat_id=admin_chat_id,
                            message_id=message_id
                        )
                    async with lock:
                        success_count += 1
                except Exception:
                    async with lock:
                        failed_count += 1

        await asyncio.gather(*[_send_all_to_user(uid) for uid in users])

        await context.bot.send_message(
            chat_id=admin_chat_id,
            text=(
                f"✅ Đã gửi **{len(queue)} tin** thành công đến **{success_count}/{total}** người dùng."
                + (f"\n❌ Thất bại: {failed_count}" if failed_count else "")
            ),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quản trị", callback_data="admin_home")]])
        )
        return

    if data == "broadcast_cancel":
        context.user_data.pop("awaiting_broadcast", None)
        context.user_data.pop("broadcast_queue", None)
        await query.edit_message_text(
            "🗑 Đã hủy broadcast.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quản trị", callback_data="admin_home")]])
        )
        return

    if data == "broadcast_block_menu":
        # Rời chế độ gom tin broadcast khi vào quản lý chặn ID
        context.user_data.pop("awaiting_broadcast", None)
        context.user_data.pop("broadcast_queue", None)
        context.user_data.pop("awaiting_block_id", None)
        text_out, markup = _build_block_menu()
        await query.edit_message_text(text_out, parse_mode="Markdown", reply_markup=markup)
        return

    if data == "broadcast_block_add":
        context.user_data["awaiting_block_id"] = True
        await query.edit_message_text(
            "➕ **THÊM ID CHẶN BROADCAST**\n\n"
            "Gửi ID người dùng bạn muốn chặn.\n"
            "Có thể gửi **nhiều ID** cùng lúc, cách nhau bằng dấu phẩy, khoảng trắng hoặc xuống dòng.\n\n"
            "Ví dụ: `123456789, 987654321`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quay lại", callback_data="broadcast_block_menu")]])
        )
        return

    if data.startswith("broadcast_unblock_"):
        context.user_data.pop("awaiting_block_id", None)
        uid_str = data[len("broadcast_unblock_"):]
        try:
            removed = db.remove_broadcast_block(int(uid_str))
        except ValueError:
            removed = False
        extra = f"✅ Đã bỏ chặn `{uid_str}`." if removed else f"ℹ️ `{uid_str}` không có trong danh sách."
        text_out, markup = _build_block_menu(extra=extra)
        await query.edit_message_text(text_out, parse_mode="Markdown", reply_markup=markup)
        return

    if data == "broadcast_block_clear":
        context.user_data.pop("awaiting_block_id", None)
        count = db.clear_broadcast_blocklist()
        extra = f"🧹 Đã bỏ chặn toàn bộ **{count}** ID." if count else "_Danh sách vốn đã trống._"
        text_out, markup = _build_block_menu(extra=extra)
        await query.edit_message_text(text_out, parse_mode="Markdown", reply_markup=markup)
        return

    # Clear awaiting state just in case
    _clear_admin_state(context)

    if data == "admin_stats":
        stats = db.get_stats()
        try:
            _, balance = await asyncio.to_thread(api.get_stock)
        except Exception:
            balance = 0
        hypervin_balance = get_hypervin_balance()
        hypervin_text = format_money(hypervin_balance) if hypervin_balance is not None else "—"
        text = (
            "📊 **THỐNG KÊ**\n━━━━━━━━━━━━━━━━━━\n\n"
            f"💰 Số dư CTV API: **{format_money(balance or 0)}**\n\n"
            f"💰 Số dư Hypervin: **{hypervin_text}**\n\n"
            f"📦 Tổng đơn: **{stats['total_orders']}**\n"
            f"✅ Thành công: **{stats['paid_orders']}**\n"
            f"❌ Hủy: **{stats['cancelled_orders']}**\n"
            f"⏳ Đang chờ: **{stats['pending_orders']}**\n\n"
            f"💵 Tổng thu: **{format_money(stats['total_revenue'])}**\n"
            f"💸 Tổng gốc: **{format_money(stats['total_cost'])}**\n"
            f"📈 Lợi nhuận: **{format_money(stats['total_profit'])}**\n"
        )
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Thoát (Về đầu)", callback_data="admin_home")]]))

    elif data == "admin_user_lookup":
        context.user_data["awaiting_user_lookup"] = True
        await query.edit_message_text(
            "🔍 **TRA CỨU KHÁCH HÀNG**\n\n"
            "Vui lòng **nhắn tin ID khách hàng hoặc Username** (có hoặc không có @) vào đây để tra cứu lịch sử mua hàng, trạng thái đơn và tổng chi tiêu.\n\n"
            "⚠️ _Hoặc bấm 'Hủy' để thoát._",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Hủy", callback_data="admin_home")]])
        )

    elif data == "admin_recent_users":
        recent = [(uid, info) for uid, info in db.get_recent_users(limit=20) if not is_admin(uid)][:10]
        if not recent:
            await query.edit_message_text(
                "❌ Chưa có khách hàng nào.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quản trị", callback_data="admin_home")]])
            )
            return

        lines = ["👥 **KHÁCH GẦN ĐÂY NHẤT**", "━━━━━━━━━━━━━━━━━━"]
        for index, (uid, info) in enumerate(recent, 1):
            name = escape_md(info.get("first_name") or "Không rõ")
            joined = (info.get("joined_at") or "")[:16].replace("T", " ") or "Không rõ"
            lines.append(
                f"{index}. {format_user_link(info.get('username'), uid)} — {name}\n"
                f"   🆔 `{uid}` | 📅 {escape_md(joined)}\n"
                f"   💰 Ví: {format_money(info.get('balance') or 0)} | 🛒 Đã mua: {format_money(info.get('total_spent') or 0)}"
            )
        lines.append("\n💡 _Xem chi tiết đơn hàng của khách: dùng 🔍 Tra cứu khách._")

        await query.edit_message_text(
            "\n".join(lines),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔍 Tra cứu khách", callback_data="admin_user_lookup")],
                [InlineKeyboardButton("⬅️ Quản trị", callback_data="admin_home")],
            ])
        )

    elif data == "admin_users":
        users = db.get_all_users()
        total_users = len(users)
        text = (
            "👥 **THỐNG KÊ NGƯỜI DÙNG**\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"📱 Tổng người đã dùng bot: **{total_users}** người\n"
        )
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Thoát (Về đầu)", callback_data="admin_home")]]))

    elif data == "admin_referral":
        reward = db.get_setting("referral_reward", 1000)
        new_user_rw = db.get_setting("referral_new_user_reward", 500)
        enabled = db.get_setting("referral_enabled", True)
        min_dep = db.get_setting("min_deposit", 5000)
        top_refs = db.get_top_referrers(5)
        
        status = "✅ BẬT" if enabled else "⏸️ TẮT"
        toggle_text = "⏸️ Tắt referral" if enabled else "✅ Bật referral"
        
        text = (
            "🎁 **CẤU HÌNH GIỚI THIỆU**\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"📊 Trạng thái: **{status}**\n"
            f"💰 Thưởng người giới thiệu: **{format_money(reward)}**\n"
            f"🎁 Thưởng người được mời: **{format_money(new_user_rw)}**\n"
            f"💳 Nạp tối thiểu: **{format_money(min_dep)}**\n\n"
        )
        
        if top_refs:
            text += "🏆 **Top giới thiệu:**\n"
            for i, ref in enumerate(top_refs, 1):
                name = ref.get("first_name") or ref.get("username") or str(ref["user_id"])
                text += f"   {i}. {name} — {ref.get('referral_count', 0)} người ({format_money(ref.get('referral_earnings', 0))})\n"
        
        buttons = [
            [InlineKeyboardButton(toggle_text, callback_data="admin_ref_toggle")],
            [InlineKeyboardButton(f"💰 Thưởng người mời ({format_money(reward)})", callback_data="admin_ref_reward")],
            [InlineKeyboardButton(f"🎁 Thưởng người được mời ({format_money(new_user_rw)})", callback_data="admin_ref_newuser")],
            [InlineKeyboardButton(f"💳 Đổi nạp tối thiểu ({format_money(min_dep)})", callback_data="admin_ref_mindeposit")],
            [InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_home")],
        ]
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))

    elif data == "admin_ref_toggle":
        current = db.get_setting("referral_enabled", True)
        db.set_setting("referral_enabled", not current)
        new_status = "TẮT" if current else "BẬT"
        await query.answer(f"Đã {new_status} hệ thống giới thiệu!")
        # Reload referral page
        reward = db.get_setting("referral_reward", 1000)
        new_user_rw = db.get_setting("referral_new_user_reward", 500)
        enabled = db.get_setting("referral_enabled", True)
        status = "✅ BẬT" if enabled else "⏸️ TẮT"
        toggle_text = "⏸️ Tắt referral" if enabled else "✅ Bật referral"
        min_dep = db.get_setting("min_deposit", 5000)
        buttons = [
            [InlineKeyboardButton(toggle_text, callback_data="admin_ref_toggle")],
            [InlineKeyboardButton(f"💰 Thưởng người mời ({format_money(reward)})", callback_data="admin_ref_reward")],
            [InlineKeyboardButton(f"🎁 Thưởng người được mời ({format_money(new_user_rw)})", callback_data="admin_ref_newuser")],
            [InlineKeyboardButton(f"💳 Đổi nạp tối thiểu ({format_money(min_dep)})", callback_data="admin_ref_mindeposit")],
            [InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_home")],
        ]
        await query.edit_message_text(
            f"🎁 **CẤU HÌNH GIỚI THIỆU**\n━━━━━━━━━━━━━━━━━━\n\n📊 Trạng thái: **{status}**\n💰 Thưởng người mời: **{format_money(reward)}**\n🎁 Thưởng người được mời: **{format_money(new_user_rw)}**\n💳 Nạp tối thiểu: **{format_money(min_dep)}**",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons)
        )

    elif data == "admin_ref_reward":
        context.user_data["awaiting_ref_reward"] = True
        await query.edit_message_text(
            "💰 **ĐỔI THƯỞNG NGƯỜI GIỚI THIỆU**\n\n"
            f"Mức hiện tại: **{format_money(db.get_setting('referral_reward', 1000))}**\n\n"
            "Nhập số tiền mới (VD: `2000`):",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Hủy", callback_data="admin_referral")]])
        )

    elif data == "admin_ref_newuser":
        context.user_data["awaiting_ref_newuser"] = True
        await query.edit_message_text(
            "🎁 **ĐỔI THƯỞNG NGƯỜI ĐƯỢC MỜI**\n\n"
            f"Mức hiện tại: **{format_money(db.get_setting('referral_new_user_reward', 500))}**\n"
            "_(Đặt 0 để tắt thưởng cho người được mời)_\n\n"
            "Nhập số tiền mới (VD: `500`):",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Hủy", callback_data="admin_referral")]])
        )

    elif data == "admin_ref_mindeposit":
        context.user_data["awaiting_min_deposit"] = True
        await query.edit_message_text(
            "💳 **ĐỔI MỨC NẠP TỐI THIỂU**\n\n"
            f"Mức hiện tại: **{format_money(db.get_setting('min_deposit', 5000))}**\n\n"
            "Nhập số tiền mới (VD: `10000`):",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Hủy", callback_data="admin_referral")]])
        )
    elif data.startswith("admin_wallet_add_"):
        target_id = int(data.replace("admin_wallet_add_", ""))
        context.user_data["awaiting_wallet_adjust"] = {"user_id": target_id, "action": "add"}
        user_info = db.get_user(target_id)
        name = user_info.get("first_name") or str(target_id)
        balance = db.get_user_balance(target_id)
        await query.edit_message_text(
            f"➕ **CỘNG VÍ — {name}** (`{target_id}`)\n"
            f"💵 Số dư hiện tại: **{format_money(balance)}**\n\n"
            "Nhập số tiền cần cộng (VD: `5000`):",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Hủy", callback_data="admin_home")]])
        )

    elif data.startswith("admin_wallet_sub_"):
        target_id = int(data.replace("admin_wallet_sub_", ""))
        context.user_data["awaiting_wallet_adjust"] = {"user_id": target_id, "action": "sub"}
        user_info = db.get_user(target_id)
        name = user_info.get("first_name") or str(target_id)
        balance = db.get_user_balance(target_id)
        await query.edit_message_text(
            f"➖ **TRỪ VÍ — {name}** (`{target_id}`)\n"
            f"💵 Số dư hiện tại: **{format_money(balance)}**\n\n"
            "Nhập số tiền cần trừ (VD: `3000`):",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Hủy", callback_data="admin_home")]])
        )

    elif data == "admin_products":
        products, _ = get_all_products_merged()
        if not products:
            return await query.edit_message_text("❌ Không lấy được dữ liệu.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Thoát (Về đầu)", callback_data="admin_home")]]))
            
        buttons, _ = build_category_grid(products, "admin_viewcat", is_admin=True)
        buttons.append([InlineKeyboardButton("➕ Thêm sản phẩm tự bán", callback_data="admin_add_prod")])
        buttons.append([
            InlineKeyboardButton("➕ Thêm danh mục", callback_data="admin_add_cat"),
            InlineKeyboardButton("✏️ Đổi tên danh mục", callback_data="admin_rename_cat_list"),
        ])
        buttons.append([InlineKeyboardButton("🎨 Đổi Icon danh mục (Custom Emoji)", callback_data="admin_set_emoji_list")])
        buttons.append([InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_home")])
        
        await query.edit_message_text(
             "⚙️ **QUẢN LÝ SẢN PHẨM**\nChọn danh mục để quản lý các tính năng (Giá, Tên hàng, Danh mục,...):",
             parse_mode="Markdown",
             reply_markup=InlineKeyboardMarkup(buttons)
        )

    elif data == "admin_add_prod":
        context.user_data["awaiting_new_prod"] = True
        await query.edit_message_text(
            "➕ **Thêm Sản Phẩm Khác (Tự điền tay)**\n\n"
            "Vui lòng nhắn tin theo cú pháp:\n"
            "`Mã_id | Tên | Giá`\n\n"
            "Ví dụ: `ytb_1m | Youtube Premium 1T | 35000`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Hủy", callback_data="admin_products")]])
        )

    elif data.startswith("admin_do_stock_"):
        key = data.replace("admin_do_stock_", "")
        
        # Hiển thị thông tin kho hiện tại
        has_auto = db.has_custom_accounts_enabled(key)
        auto_count = len(db.get_custom_accounts(key)) if has_auto else 0
        manual_stock = db.get_custom_stocks().get(key)
        
        stock_info = ""
        if has_auto:
            stock_info = f"\n📦 Kho tự động hiện tại: **{auto_count}** sản phẩm"
        elif manual_stock is not None:
            stock_info = f"\n📦 Số lượng liên hệ trực tiếp: **{manual_stock}**"
        
        await query.edit_message_text(
            f"📦 **QUẢN LÝ KHO — `{key}`**\n"
            f"━━━━━━━━━━━━━━━━━━{stock_info}\n\n"
            f"Chọn cách quản lý kho:\n\n"
            f"📥 **Thêm sản phẩm vào kho** — Paste danh sách (code, link, account...) mỗi dòng 1 cái. Khách mua bot tự cắt giao ngay.\n\n"
            f"🔢 **Số lượng liên hệ trực tiếp** — Chỉ đặt số lượng hiển thị, khách mua sẽ liên hệ Admin nhận hàng.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📥 Thêm sản phẩm vào kho", callback_data=f"admin_stock_add_items_{key}")],
                [InlineKeyboardButton("👁️ Xem tài khoản trong kho", callback_data=f"admin_stock_view_{key}")],
                [InlineKeyboardButton("🔢 Số lượng liên hệ trực tiếp", callback_data=f"admin_stock_manual_{key}")],
                [InlineKeyboardButton("🗑️ Xóa sạch kho", callback_data=f"admin_stock_reset_{key}")],
                [InlineKeyboardButton("⬅️ Quay lại", callback_data=f"admin_price_{key}"),
                 InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]
            ])
        )

    elif data.startswith("admin_stock_view_"):
        key = data.replace("admin_stock_view_", "")
        accounts = db.get_custom_accounts(key)
        
        if not accounts:
            await query.edit_message_text(
                f"📦 **KHO `{key}`**\n━━━━━━━━━━━━━━━━━━\n\n"
                f"📭 Kho trống — chưa có tài khoản nào.",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📥 Thêm vào kho", callback_data=f"admin_stock_add_items_{key}")],
                    [InlineKeyboardButton("⬅️ Quay lại", callback_data=f"admin_do_stock_{key}")]
                ])
            )
            return
        
        text = (
            f"📦 **KHO `{key}`** — {len(accounts)} tài khoản\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
        )
        
        for i, acc in enumerate(accounts, 1):
            # Hiện full nội dung để admin kiểm tra
            acc_display = acc.strip()
            if len(acc_display) > 60:
                acc_display = acc_display[:60] + "..."
            text += f"{i}. `{acc_display}`\n"
            
            if i >= 50:
                text += f"\n_... và {len(accounts) - 50} tài khoản khác_"
                break
        
        await query.edit_message_text(
            text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📥 Thêm tiếp", callback_data=f"admin_stock_add_items_{key}")],
                [InlineKeyboardButton("🗑️ Xóa sạch kho", callback_data=f"admin_stock_reset_{key}")],
                [InlineKeyboardButton("⬅️ Quay lại", callback_data=f"admin_do_stock_{key}"),
                 InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]
            ])
        )

    elif data.startswith("admin_stock_add_items_"):
        key = data.replace("admin_stock_add_items_", "")
        context.user_data["awaiting_stock_items_for"] = key
        await query.edit_message_text(
            f"📥 **THÊM SẢN PHẨM VÀO KHO — `{key}`**\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"Vui lòng **paste danh sách sản phẩm** vào đây.\n"
            f"Mỗi dòng = 1 sản phẩm (code, link, tài khoản, bất kỳ cái gì).\n\n"
            f"Ví dụ:\n"
            f"```\nhttps://example.com/key1\nABC-DEF-GHI-123\nuser@mail.com|pass123\n```\n\n"
            f"_Bot sẽ tự cắt từng sản phẩm giao cho khách khi có đơn._",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Quay lại", callback_data=f"admin_do_stock_{key}"),
                 InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]
            ])
        )

    elif data.startswith("admin_stock_manual_"):
        key = data.replace("admin_stock_manual_", "")
        context.user_data["awaiting_stock_manual_for"] = key
        await query.edit_message_text(
            f"🔢 **SỐ LƯỢNG LIÊN HỆ TRỰC TIẾP — `{key}`**\n"
            f"━━━━━━━━━━━━━━━━━━\n\n"
            f"Nhập số lượng hiển thị cho khách.\n"
            f"Khi khách mua, họ sẽ cần liên hệ Admin để nhận hàng.\n\n"
            f"VD: nhắn `10` để đặt tồn kho là 10.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Quay lại", callback_data=f"admin_do_stock_{key}"),
                 InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]
            ])
        )

    elif data.startswith("admin_stock_reset_"):
        key = data.replace("admin_stock_reset_", "")
        db.set_custom_stock(key, None)
        db.clear_custom_accounts(key)
        invalidate_cache()
        await query.edit_message_text(
            f"✅ Đã xóa sạch kho cho `{key}`.\n"
            f"Sản phẩm sẽ hiển thị lại tồn kho mặc định từ API (nếu có).",
            parse_mode="Markdown",
            reply_markup=build_admin_product_back_keyboard(key),
        )

    elif data.startswith("admin_do_desc_en_"):
        key = data.replace("admin_do_desc_en_", "")
        context.user_data["awaiting_desc_en_for"] = key
        current = db.get_custom_description_en(key) or "(chưa có)"
        await query.edit_message_text(f"📝 Gửi MÔ TẢ TIẾNG ANH cho `{key}`.\n\nHiện tại:\n`{escape_md(current)}`\n\nNhắn `reset` để xóa.", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quay lại", callback_data=f"admin_price_{key}")]]))

    elif data.startswith("admin_do_desc_"):
        key = data.replace("admin_do_desc_", "")
        context.user_data["awaiting_desc_for"] = key
        
        # Lấy mô tả hiện tại: custom > API
        current_desc = db.get_custom_description(key)
        desc_source = "📝 Mô tả tùy chỉnh"
        if not current_desc:
            products_tmp, _ = get_all_products_merged()
            info_tmp = products_tmp.get(key, {}) if products_tmp else {}
            current_desc = info_tmp.get("description") or info_tmp.get("desc")
            desc_source = "🌐 Mô tả từ API"
        
        if current_desc:
            desc_block = (
                f"\n{desc_source}:\n"
                f"```\n{current_desc}\n```\n"
            )
        else:
            desc_block = "\n⚠️ _Chưa có mô tả nào_\n"
        
        await query.edit_message_text(
            f"📜 **SỬA MÔ TẢ — `{key}`**\n"
            f"{desc_block}\n"
            f"Vui lòng **nhắn tin gửi NỘI DUNG/MÔ TẢ MỚI**.\n"
            f"Bao gồm hướng dẫn, ghi chú, v.v.\n\n"
            f"Nhắn chữ `reset` nếu muốn xóa mô tả.",
            parse_mode="Markdown",
            reply_markup=build_admin_product_back_keyboard(key),
        )
        
    elif data == "admin_add_cat":
        context.user_data["awaiting_new_cat"] = True
        await query.edit_message_text(
            "➕ **Thêm hoặc Sửa danh mục**\n\n"
            "Vui lòng nhắn tin theo đúng cú pháp sau:\n"
            "`Mã_id | Tên hiển thị | Emoji`\n\n"
            "Ví dụ thêm mới: `msoffice | Microsoft Office | 💻`\n"
            "Ví dụ sửa cũ: Nếu muốn sửa mục Khác (id là khac), nhắn: `khac | Thập Cẩm | 📦`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Hủy", callback_data="admin_products")]])
        )

    elif data == "admin_rename_cat_list":
        # Hiển thị danh sách tất cả danh mục hiện có để admin chọn đổi tên
        all_cats = get_all_categories_merged()
        buttons = []
        row = []
        for cid, (cname, cicon) in all_cats.items():
            row.append(InlineKeyboardButton(f"{cicon} {cname}", callback_data=f"admin_rename_cat_{cid}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_products")])
        await query.edit_message_text(
            "✏️ **ĐỔI TÊN DANH MỤC**\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "Chọn danh mục bạn muốn đổi tên:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    elif data.startswith("admin_rename_cat_"):
        cat_id = data.replace("admin_rename_cat_", "")
        all_cats = get_all_categories_merged()
        if cat_id not in all_cats:
            await query.edit_message_text("❌ Danh mục không tồn tại.")
            return
        current_name, current_icon = all_cats[cat_id]
        context.user_data["awaiting_rename_cat"] = cat_id
        await query.edit_message_text(
            f"✏️ **Đổi tên danh mục:** {current_icon} {current_name}\n"
            f"📌 ID: `{cat_id}`\n\n"
            f"Vui lòng nhắn tin theo cú pháp:\n"
            f"`Tên mới | Emoji mới`\n\n"
            f"Ví dụ: `ChatGPT Pro | 🤖`\n"
            f"Hoặc chỉ đổi tên: `ChatGPT Pro | {current_icon}`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Hủy", callback_data="admin_rename_cat_list")]])
        )

    elif data == "admin_set_emoji_list":
        all_cats = get_all_categories_merged()
        emoji_ids = db.get_all_category_emoji_ids()
        buttons = []
        row = []
        for cid, (cname, cicon) in all_cats.items():
            has_emoji = "✅" if cid in emoji_ids else "❌"
            row.append(InlineKeyboardButton(f"{cicon} {cname} {has_emoji}", callback_data=f"admin_set_emoji_{cid}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_products")])
        await query.edit_message_text(
            "🎨 **ĐỔI ICON DANH MỤC (Custom Emoji)**\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "✅ = Đã có custom emoji\n"
            "❌ = Chưa có (đang dùng emoji mặc định)\n\n"
            "Chọn danh mục bạn muốn đổi icon:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    elif data.startswith("admin_set_emoji_"):
        cat_id = data.replace("admin_set_emoji_", "")
        all_cats = get_all_categories_merged()
        if cat_id not in all_cats:
            await query.edit_message_text("❌ Danh mục không tồn tại.")
            return
        current_name, current_icon = all_cats[cat_id]
        current_eid = db.get_category_emoji_id(cat_id)
        context.user_data["awaiting_set_emoji"] = cat_id
        
        status = f"📌 Emoji ID hiện tại: `{current_eid}`" if current_eid else "⚠️ _Chưa có custom emoji_"
        await query.edit_message_text(
            f"🎨 **Đổi Icon danh mục:** {current_icon} {current_name}\n"
            f"{status}\n\n"
            f"👉 **Gửi trực tiếp custom emoji** vào đây — bot sẽ tự nhận diện!\n\n"
            f"Hoặc nhập emoji ID thủ công (dãy số dài).\n"
            f"Nhắn `reset` để xóa và dùng lại emoji mặc định.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Hủy", callback_data="admin_set_emoji_list")]])
        )

    elif data.startswith("admin_viewcat_"):
        cat_id = data.replace("admin_viewcat_", "")
        
        products, _ = get_all_products_merged()
        if not products:
            return await query.edit_message_text("❌ Lỗi tải dữ liệu.")
            
        buttons = []
        for key, info in products.items():
            _, _, c_id = classify_product(key, info)
            if c_id == cat_id:
                price_str = format_money(get_sell_price(key, info['price'], info.get("is_custom_local", False)))
                dname = db.get_custom_name(key) or info['name']
                stock = info.get('stock', 0)
                if stock > 0: stock_icon = f"✅ Còn: {stock}"
                elif stock == -1: stock_icon = f"🔄 Load"
                else: stock_icon = f"❌ Hết"
                
                is_local = info.get("is_custom_local", False)
                if not is_local:
                    type_icon = "🌐"
                elif db.has_custom_accounts_enabled(key):
                    type_icon = "⚡"
                else:
                    type_icon = "📝"
                    
                hidden_icon = "🙈 " if db.is_product_hidden(key) else ""
                buttons.append([InlineKeyboardButton(f"{hidden_icon}{type_icon} [{stock_icon}] {dname} ({price_str})", callback_data=f"admin_price_{key}")])
                   
        buttons.append([
            InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_products"),
            InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")
        ])
        
        await query.edit_message_text(
            f"🛒 **CHỌN SẢN PHẨM ĐỂ SỬA**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💡 _Chú thích phân loại:_\n"
            f"🌐 `Hàng đối tác API`\n"
            f"⚡ `Tự bán (Tự động giao từ kho)`\n"
            f"📝 `Tự bán (Liên hệ trực tiếp)`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        
    elif data.startswith("admin_price_"):
        key = data.replace("admin_price_", "")
        await render_admin_product_detail(update, context, key)
        
    elif data.startswith("admin_do_price_usdt_"):
        key = data.replace("admin_do_price_usdt_", "")
        context.user_data["awaiting_price_usdt_for"] = key
        current = db.get_custom_price_usdt(key)
        current_text = format_usdt(current) if current is not None else "Chưa đặt"
        await query.edit_message_text(
            f"💵 **ĐẶT GIÁ USDT CHO KHÁCH EN** — `{key}`\n\n"
            f"Hiện tại: **{current_text}**\n\n"
            "Nhập giá mỗi sản phẩm, ví dụ `2.5`. Giá này chỉ dùng để hiển thị; "
            "bot vẫn thu tiền VNĐ theo giá bán hiện tại.\n\n"
            "Nhập `reset` để xóa giá USDT.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quay lại", callback_data=f"admin_price_{key}")]]),
        )

    elif data.startswith("admin_do_price_"):
        key = data.replace("admin_do_price_", "")
        context.user_data["awaiting_price_for"] = key
        
        # Lấy giá gốc hiện tại để hiển thị cho admin
        products, _ = get_products_cached()
        base_price = 0
        if products and key in products:
            base_price = products[key].get("price", 0)
        
        current_delta = db.get_price_delta(key)
        delta_info = ""
        if current_delta is not None:
            delta_str = f"+{format_money(current_delta)}" if current_delta >= 0 else f"-{format_money(abs(current_delta))}"
            delta_info = f"\n📐 Chênh lệch hiện tại: **{delta_str}**"
        
        await query.edit_message_text(
            f"📝 **SỬA GIÁ BÁN** — `{key}`\n\n"
            f"📊 Giá gốc đối tác: **{format_money(base_price)}**{delta_info}\n\n"
            f"👉 Nhắn **GIÁ BÁN MỚI** (VND) bạn muốn (VD: `50000`).\n\n"
            f"💡 _Hệ thống sẽ tự tính mức chênh lệch. Khi đối tác tăng giá, giá bán sẽ tự tăng theo._\n\n"
            f"Nhắn `reset` để xóa và dùng markup mặc định.",
            parse_mode="Markdown",
            reply_markup=build_admin_product_back_keyboard(key),
        )

    elif data.startswith("admin_toggle_hide_"):
        key = data.replace("admin_toggle_hide_", "")
        is_hidden = db.toggle_hidden_product(key)
        await query.answer(f"{'✅ Đã ẩn' if is_hidden else '👁️ Đã hiện lại'} sản phẩm!")
        await render_admin_product_detail(update, context, key)

    elif data.startswith("admin_del_prod_"):
        # Format: admin_del_prod_KEY_CID — KEY có thể chứa dấu _
        raw = data.replace("admin_del_prod_", "")
        # Tách CID từ cuối (rsplit để giữ nguyên KEY chứa dấu _)
        parts = raw.rsplit("_", 1)
        key = parts[0]
        cid = parts[1] if len(parts) > 1 else "khac"

        
        # Xóa sản phẩm
        db.delete_custom_product(key)
        invalidate_cache()
        await query.answer("✅ Đã xóa sản phẩm thành công!", show_alert=True)
        
        # Quay lại menu trước đó bằng cách tạo data giả và chuyển hướng
        query.data = f"admin_viewcat_{cid}"
        await handle_admin_cb(update, context)

    elif data.startswith("admin_do_name_en_"):
        key = data.replace("admin_do_name_en_", "")
        context.user_data["awaiting_name_en_for"] = key
        current = db.get_custom_name_en(key) or "(chưa có)"
        await query.edit_message_text(f"✏️ Gửi TÊN TIẾNG ANH cho `{key}`.\n\nHiện tại: **{escape_md(current)}**\n\nNhắn `reset` để xóa.", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quay lại", callback_data=f"admin_price_{key}")]]))

    elif data.startswith("admin_do_name_"):
        key = data.replace("admin_do_name_", "")
        context.user_data["awaiting_name_for"] = key
        await query.edit_message_text(
            f"✏️ Vui lòng **nhắn tin gửi TÊN MỚI** cho `{key}`.\n\n"
            f"Nhắn chữ `reset` nếu muốn khôi phục tên gốc của server.",
            parse_mode="Markdown",
            reply_markup=build_admin_product_back_keyboard(key),
        )

    elif data.startswith("admin_do_cat_"):
        key = data.replace("admin_do_cat_", "")
        buttons = []
        row = []
        for cid, (cname, cicon) in get_all_categories_merged().items():
            row.append(InlineKeyboardButton(f"{cicon} {cname}", callback_data=f"admin_set_cat_{key}_{cid}"))
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row: buttons.append(row)
        buttons.append([InlineKeyboardButton("➕ Tạo ds danh mục mới", callback_data="admin_add_cat")])
        buttons.append([InlineKeyboardButton("♻️ Reset (Máy tự chọn)", callback_data=f"admin_set_cat_{key}_reset")])
        buttons.append([
            InlineKeyboardButton("⬅️ Quay lại", callback_data=f"admin_price_{key}"),
            InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")
        ])
        
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
            msg = f"✅ Đã chuyển sản phẩm sang danh mục {get_all_categories_merged()[cid][1]} {get_all_categories_merged()[cid][0]}."
            
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ Quay lại", callback_data=f"admin_price_{key}"),
             InlineKeyboardButton("🏠 Thoát", callback_data="admin_home")]
        ]))


    elif data == "admin_markup":
        context.user_data["awaiting_markup"] = True
        current = db.get_setting("default_markup_fixed", 10000)
        await query.edit_message_text(
            f"⚙️ Markup mặc định hiện tại: **+{format_money(current)}**\n\n"
            f"Vui lòng **nhắn tin số tiền mới** (VD: nhắn 10000 = giá bán sẽ cao hơn giá gốc 10.000đ).",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Hủy thay đổi", callback_data="admin_home")]])
        )

    elif data == "admin_broadcast":
        context.user_data["awaiting_broadcast"] = True
        context.user_data["broadcast_queue"] = []
        blocked_count = len(db.get_broadcast_blocklist())
        block_note = (
            f"🚫 Đang chặn **{blocked_count}** ID — các ID này sẽ **không** nhận broadcast.\n\n"
            if blocked_count else ""
        )
        await query.edit_message_text(
            "📢 **GỬI THÔNG BÁO CHO TẤT CẢ NGƯỜI DÙNG**\n\n"
            "Gửi lần lượt các tin nhắn bạn muốn broadcast — **văn bản, ảnh, video đều được**.\n"
            "Có thể gửi nhiều tin, bot sẽ gom lại.\n\n"
            + block_note +
            "Khi xong, bấm **📤 Gửi ngay** để phát đến tất cả khách.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"🚫 Quản lý chặn ID ({blocked_count})", callback_data="broadcast_block_menu")],
                [InlineKeyboardButton("⬅️ Hủy", callback_data="admin_home")],
            ])
        )

    elif data == "admin_export_prices":
        # Xuất đơn giá theo biểu mẫu
        products, _ = get_products_cached()
        if not products:
            products, _ = get_all_products_merged(force_refresh=True)
        if not products:
            await query.edit_message_text("❌ Không thể tải sản phẩm.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_home")]]))
            return

        merged_cats = get_all_categories_merged()
        # Nhóm sản phẩm theo danh mục
        cat_products = {}
        for key, info in products.items():
            if db.is_product_hidden(key) or info.get("stock", 0) == 0:
                continue
            cat_name, cat_icon, cat_id = classify_product(key, info, merged_cats)
            if cat_id not in cat_products:
                cat_products[cat_id] = {"name": cat_name, "icon": cat_icon, "items": []}
            sell_price = get_sell_price(key, info["price"], info.get("is_custom_local", False))
            dname = db.get_custom_name(key) or info["name"]
            cat_products[cat_id]["items"].append((dname, sell_price))

        # Sắp xếp theo thứ tự danh mục chuẩn
        order = ["gpt", "grok", "capcut", "gemini", "meitu", "netflix", "discord", "vpn", "spotify", "khac"]
        sorted_cats = []
        for o in order:
            if o in cat_products:
                sorted_cats.append((o, cat_products[o]))
        for k, v in cat_products.items():
            if k not in order:
                sorted_cats.append((k, v))

        lines = ["📢 THÔNG BÁO CÁC MẶT HÀNG MÌNH ĐANG CÓ\n"]
        for cat_id, cat_data in sorted_cats:
            cat_icon_html = fmt_icon(cat_id, cat_data['icon'])
            lines.append(f"{cat_icon_html} <b>{escape_html(cat_data['name'])}</b>")
            for pname, pprice in cat_data["items"]:
                lines.append(f"  • {escape_html(pname)} — {format_money(pprice)}")
            lines.append("")  # Dòng trống giữa các danh mục

        lines.append("BOT AUTO ORDER: @hoanganhshop_bot")
        lines.append("Admin: @hoanganh1162")

        export_text = "\n".join(lines)

        # Gửi tin nhắn mới (không edit) để admin dễ copy/forward
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text=export_text,
            parse_mode="HTML"
        )
        await query.answer("✅ Đã xuất đơn giá!")

    elif data == "admin_ui_custom":
        buttons = [
            [InlineKeyboardButton("✏️ Sửa lời chào /start", callback_data="admin_edit_welcome")],
            [InlineKeyboardButton("✏️ Sửa lời chào EN", callback_data="admin_edit_welcome_en")],
            [InlineKeyboardButton("✏️ Sửa Menu sản phẩm VI", callback_data="admin_edit_menu_title")],
            [InlineKeyboardButton("✏️ Sửa Menu sản phẩm EN", callback_data="admin_edit_menu_title_en")],
            [InlineKeyboardButton("🎨 Đổi Icon nút bấm", callback_data="admin_edit_btn_list")],
            [InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_home")],
        ]
        await query.edit_message_text(
            "🎨 **TÙY CHỈNH GIAO DIỆN**\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "Chọn mục bạn muốn tùy chỉnh:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    elif data == "admin_edit_menu_title":
        context.user_data["awaiting_menu_title"] = True
        current = db.get_menu_title()
        preview = f"\n\n📝 Nội dung hiện tại:\n━━━━━━━━━━━━━━━━━━\n{current}\n━━━━━━━━━━━━━━━━━━" if current else "\n\n⚠️ _Đang dùng menu tiếng Việt mặc định_"
        await query.edit_message_text(
            f"✏️ **SỬA MENU SẢN PHẨM VI**{preview}\n\n"
            "📝 Nhắn nội dung mới cho đầu trang menu sản phẩm.\n\n"
            "💡 Biến có thể dùng: `{balance}` — Số dư ví\n\n"
            "Nhắn `reset` để quay về mặc định.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Hủy", callback_data="admin_ui_custom")]]),
        )

    elif data == "admin_edit_menu_title_en":
        context.user_data["awaiting_menu_title_en"] = True
        current = db.get_menu_title_en()
        preview = f"\n\n📝 Nội dung hiện tại:\n━━━━━━━━━━━━━━━━━━\n{current}\n━━━━━━━━━━━━━━━━━━" if current else "\n\n⚠️ _Đang dùng menu tiếng Anh mặc định_"
        await query.edit_message_text(
            f"✏️ **SỬA MENU SẢN PHẨM EN**{preview}\n\n"
            "📝 Nhắn nội dung tiếng Anh mới cho đầu trang menu sản phẩm.\n\n"
            "💡 Biến có thể dùng: `{balance}` — Số dư ví\n\n"
            "Nhắn `reset` để quay về mặc định.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Hủy", callback_data="admin_ui_custom")]]),
        )

    elif data == "admin_edit_welcome_en":
        context.user_data["awaiting_welcome_msg_en"] = True
        current = db.get_welcome_message_en()
        preview = f"\n\n📝 Nội dung hiện tại:\n━━━━━━━━━━━━━━━━━━\n{current}\n━━━━━━━━━━━━━━━━━━" if current else "\n\n⚠️ _Đang dùng lời chào EN mặc định_"
        await query.edit_message_text(
            f"✏️ **SỬA LỜI CHÀO TIẾNG ANH**{preview}\n\n"
            "📝 Nhắn nội dung tiếng Anh mới.\n\n"
            "💡 Biến có thể dùng: `{name}`, `{balance}`, `{id}`\n\n"
            "Nhắn `reset` để quay về mặc định.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Hủy", callback_data="admin_ui_custom")]]),
        )

    elif data == "admin_edit_welcome":
        context.user_data["awaiting_welcome_msg"] = True
        current = db.get_welcome_message()
        preview = f"\n\n📝 Nội dung hiện tại:\n━━━━━━━━━━━━━━━━━━\n{current}\n━━━━━━━━━━━━━━━━━━" if current else "\n\n⚠️ _Đang dùng lời chào mặc định_"
        await query.edit_message_text(
            f"✏️ **SỬA LỜI CHÀO /START**{preview}\n\n"
            "📝 Nhắn tin **nội dung mới** cho lời chào.\n\n"
            "💡 Biến có thể dùng:\n"
            "• `{name}` — Tên người dùng\n"
            "• `{balance}` — Số dư ví\n"
            "• `{id}` — ID Telegram\n\n"
            "Nhắn `reset` để quay về lời chào mặc định.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Hủy", callback_data="admin_ui_custom")]])
        )

    elif data == "admin_edit_btn_list":
        ui_emojis = db.get_all_ui_emoji()
        buttons = []
        for btn_key, default_text in UI_BUTTONS.items():
            has = "✅" if btn_key in ui_emojis else "❌"
            buttons.append([InlineKeyboardButton(f"{has} {default_text}", callback_data=f"admin_edit_btn_{btn_key}")])
        buttons.append([InlineKeyboardButton("⬅️ Quay lại", callback_data="admin_ui_custom")])
        await query.edit_message_text(
            "🎨 **ĐỔI ICON NÚT BẤM**\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "✅ = Đã có custom emoji\n"
            "❌ = Đang dùng emoji mặc định\n\n"
            "ℹ️ Custom icon chỉ hiện khi chủ bot có Telegram Premium hoặc bot có username mua thêm trên Fragment.\n\n"
            "Chọn nút bạn muốn đổi icon:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    elif data.startswith("admin_edit_btn_"):
        btn_key = data.replace("admin_edit_btn_", "")
        current_eid = db.get_ui_emoji(btn_key)
        context.user_data["awaiting_ui_emoji"] = btn_key
        default_name = UI_BUTTONS.get(btn_key, btn_key)
        status = f"📌 Emoji ID hiện tại: `{current_eid}`" if current_eid else "⚠️ _Đang dùng emoji mặc định_"
        await query.edit_message_text(
            f"🎨 **Đổi Icon:** {default_name}\n"
            f"{status}\n\n"
            f"👉 **Gửi trực tiếp custom emoji** vào đây!\n"
            f"Hoặc nhập emoji ID thủ công.\n"
            f"Nhắn `reset` để xóa.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Hủy", callback_data="admin_edit_btn_list")]])
        )
