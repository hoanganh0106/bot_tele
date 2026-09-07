"""Private-chat phone rental and admin button naming."""

import time
import uuid
import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest

from core.helpers import escape_html, is_admin, ui_btn
from core.phone_rental import DEFAULT_BUTTON_NAME, RENTAL_PRICE, PhoneApiError, PhoneNumberFault, get_otp, get_phone
from core.runtime import db


async def cmd_setphonename(update, context):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Chỉ admin mới được đổi tên nút.")
        return
    name = " ".join(context.args).strip()
    if not name or len(name) > 64:
        await update.message.reply_text("Dùng /setphonename Tên nút mới (1–64 ký tự). Gửi /setphonename reset để khôi phục.")
        return
    name = DEFAULT_BUTTON_NAME if name.lower() == "reset" else name
    db.set_setting("phone_rental_button_name", name)
    db.flush()
    await update.message.reply_text(f"Đã đổi tên nút thành: {name}\nMở lại /start hoặc /menu để xem.")


async def show_screen(query, state, note=""):
    rows = []
    text = "<b>" + escape_html(db.get_setting("phone_rental_button_name") or DEFAULT_BUTTON_NAME) + "</b>"
    text += "\nGiá: <b>4.000đ / số</b> · Thanh toán bằng ví."
    if state:
        text += "\n\nSố điện thoại: <code>" + escape_html(state["phone"]) + "</code>"
        if state.get("prefix"):
            text += "\nMã quốc gia: <code>+" + escape_html(state["prefix"]) + "</code>"
        rows.append([InlineKeyboardButton("📩 Lấy OTP", callback_data="phone_otp_" + state["token"])])
        rows.append([
            InlineKeyboardButton("🔄 Đổi số khác", callback_data="phone_change_" + state["token"]),
            InlineKeyboardButton("❌ Hủy", callback_data="phone_cancel_" + state["token"]),
        ])
        text += "\nHủy/đổi miễn phí chỉ khi API xác minh số lỗi và số chưa từng nhận OTP."
    else:
        text += "\n\nNhấn Nhận số để thuê một số điện thoại, sau đó nhấn Lấy OTP."
    if note:
        text += "\n\n" + escape_html(note)
    rows.append([InlineKeyboardButton("📱 Thuê số khác" if state else "📱 Nhận số", callback_data="phone_new")])
    rows.append([ui_btn("deposit", callback_data="deposit_start", user_id=query.from_user.id)])
    rows.append([ui_btn("home", callback_data="back_start", user_id=query.from_user.id)])
    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))
    except BadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


async def handle_phone_rental(update, context):
    # Serialize OTP delivery, cancellation and allocation for the same user.
    lock = context.user_data.setdefault("_phone_lock", asyncio.Lock())
    async with lock:
        await _handle_phone_rental(update, context)


async def _handle_phone_rental(update, context):
    query = update.callback_query
    if not update.effective_chat or update.effective_chat.type != "private":
        await query.answer("Vui lòng thuê số trong tin nhắn riêng với bot.", show_alert=True)
        return
    await query.answer()
    key = f"phone_rental_user_{query.from_user.id}"
    state = db.get_setting(key)
    action = query.data
    if action == "phone_home":
        await show_screen(query, state)
        return
    if action == "phone_new":
        token = uuid.uuid4().hex[:16]
        context.user_data["phone_confirm"] = token
        await query.edit_message_text(
            "Xác nhận thuê số mới với giá 4.000đ? Tiền được trừ từ ví khi nhận số, lấy OTP không thu thêm phí."
            + (" Số hiện tại sẽ được thay thế trong bot." if state else ""),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Nhận số", callback_data="phone_confirm_" + token)],
                [InlineKeyboardButton("⬅️ Quay lại", callback_data="phone_home")],
            ]),
        )
        return
    try:
        if action.startswith(("phone_cancel_", "phone_change_")):
            token = action.split("_", 2)[2]
            if not state or token != state["token"]:
                await show_screen(query, state, "Nút này không thuộc số hiện tại của bạn.")
                return
            reason = db.begin_phone_fault_check(query.from_user.id, token)
            if reason:
                await show_screen(query, state, reason)
                return
            try:
                otp = await get_otp(state["phone"])
            except PhoneNumberFault:
                result = db.refund_verified_phone_fault(query.from_user.id, token)
                if result == "verify_again":
                    await show_screen(query, state, "API báo số lỗi lần 1. Chờ 10 giây rồi nhấn lại Hủy hoặc Đổi số khác để xác minh lần 2 (trong 2 phút).")
                    return
                if result != "refunded":
                    await show_screen(query, db.get_setting(key), "Không thể hoàn tự động. Đơn đã xử lý hoặc không còn đủ điều kiện; vui lòng liên hệ admin.")
                    return
                state = None
                if action.startswith("phone_cancel_"):
                    await show_screen(query, None, "Đã hủy số lỗi và hoàn 4.000đ vào ví.")
                    return
                # The refund is committed before reserving the replacement fee.
                # If allocation fails or the bot stops here, the refund remains safe.
                token = uuid.uuid4().hex[:16]
                context.user_data["phone_confirm"] = token
                context.user_data.pop("phone_last_rent", None)
                action = "phone_confirm_" + token
            except PhoneApiError:
                db.reset_phone_fault(query.from_user.id, token)
                raise
            else:
                db.reset_phone_fault(query.from_user.id, token)
                if otp:
                    db.record_phone_otp(query.from_user.id, token)
                    await show_screen(query, state, f"Số đã có OTP: {otp}. Không thể hủy hoặc đổi miễn phí.")
                else:
                    await show_screen(query, state, "API chưa xác nhận số lỗi. Chưa có OTP không đồng nghĩa với số lỗi; chưa hoàn tiền.")
                return
        if action.startswith("phone_confirm_"):
            token = action.removeprefix("phone_confirm_")
            if token != context.user_data.get("phone_confirm"):
                await show_screen(query, state, "Yêu cầu này đã được xử lý hoặc hết hiệu lực.")
                return
            del context.user_data["phone_confirm"]
            if time.monotonic() - context.user_data.get("phone_last_rent", -60) < 30:
                await show_screen(query, state, "Vui lòng chờ 30 giây giữa hai lần thuê số.")
                return
            context.user_data["phone_last_rent"] = time.monotonic()
            name = db.get_setting("phone_rental_button_name") or DEFAULT_BUTTON_NAME
            if not db.reserve_phone_rental(query.from_user.id, token, RENTAL_PRICE, name):
                await show_screen(query, state, "Ví cần ít nhất 4.000đ và không có yêu cầu thuê số đang xử lý. Vui lòng nạp tiền nếu thiếu số dư.")
                return
            try:
                phone = await get_phone()
            except PhoneApiError as exc:
                db.refund_phone_rental(token)
                await show_screen(query, state, f"{exc}\nĐã hoàn 4.000đ vào ví do không cấp được số.")
                return
            if not db.finish_phone_rental(query.from_user.id, token, phone):
                await show_screen(query, state, "Chưa thể hoàn tất đơn. Vui lòng liên hệ admin.")
                return
            state = db.get_setting(key)
            await show_screen(query, state, "Đã thanh toán 4.000đ từ ví. Lấy OTP không thu thêm phí.")
        elif action.startswith("phone_otp_"):
            if not state or action.removeprefix("phone_otp_") != state["token"]:
                await show_screen(query, state, "Nút OTP này không thuộc số hiện tại của bạn.")
                return
            if time.monotonic() - context.user_data.get("phone_last_otp", -60) < 5:
                await show_screen(query, state, "Vui lòng chờ 5 giây rồi lấy OTP lại.")
                return
            context.user_data["phone_last_otp"] = time.monotonic()
            otp = await get_otp(state["phone"])
            if otp and not db.record_phone_otp(query.from_user.id, state["token"]):
                await show_screen(query, db.get_setting(key), "Đơn không còn hợp lệ để lấy OTP.")
                return
            if not otp:
                db.reset_phone_fault(query.from_user.id, state["token"])
            await show_screen(query, state, f"OTP: {otp}" if otp else "Chưa có OTP. Vui lòng chờ rồi nhấn Lấy OTP lại.")
    except PhoneNumberFault:
        await show_screen(query, state, "API báo số lỗi. Bạn có thể nhấn Hủy hoặc Đổi số khác để xác minh và hoàn tiền.")
    except PhoneApiError as exc:
        await show_screen(query, state, str(exc))
