"""Private-chat phone rental and admin button naming."""

import time
import uuid
import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest

from core.helpers import escape_html, is_admin, ui_btn
from core.phone_rental import DEFAULT_BUTTON_NAME, RENTAL_PRICE, PhoneApiError, PhoneNumberFault, get_otp, get_phone, request_api, parse_otp
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


async def show_screen(query, state, note="", *, waiting=False, otp=None):
    rows = []
    text = "<b>" + escape_html(db.get_setting("phone_rental_button_name") or DEFAULT_BUTTON_NAME) + "</b>"
    text += "\n<b>4.000đ khi nhận OTP</b> · Chưa có OTP đổi miễn phí."
    if state:
        text += "\n\nSố điện thoại: <code>" + escape_html(state["phone"]) + "</code>"
        if state.get("prefix"):
            text += "\nMã quốc gia: <code>+" + escape_html(state["prefix"]) + "</code>"
        rows.append([InlineKeyboardButton("📩 Lấy OTP", callback_data="phone_otp_" + state["token"])])
        rows.append([
            InlineKeyboardButton("🔄 Đổi số", callback_data="phone_change_" + state["token"]),
            InlineKeyboardButton("❌ Hủy", callback_data="phone_cancel_" + state["token"]),
        ])
    else:
        text += "\n\nNhấn Nhận số để bắt đầu."
    if note:
        text += "\n\n" + escape_html(note)
    if waiting:
        text += '\n\n<tg-emoji emoji-id="5215579104807497179">⏳</tg-emoji> đang chờ OTP'
    if otp:
        text += "\n\n✅ OTP: <code>" + escape_html(otp) + "</code>"
    rows.append([InlineKeyboardButton("📱 Thuê số khác" if state else "📱 Nhận số", callback_data="phone_new")])
    rows.append([ui_btn("deposit", callback_data="deposit_start", user_id=query.from_user.id)])
    rows.append([ui_btn("home", callback_data="back_start", user_id=query.from_user.id)])
    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))
    except BadRequest as exc:
        if waiting and "emoji" in str(exc).lower():
            text = text.replace('<tg-emoji emoji-id="5215579104807497179">⏳</tg-emoji>', '⏳')
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows))
            return
        if "message is not modified" not in str(exc).lower():
            raise


async def handle_phone_rental(update, context):
    task = context.user_data.pop("_phone_poll", None)
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
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
    if action.startswith("phone_cancel_"):
        if not state or action.removeprefix("phone_cancel_") != state["token"]:
            await show_screen(query, state, "Số này không còn hiệu lực.")
            return
        refunded = db.refund_phone_rental(state["token"])
        await show_screen(query, db.get_setting(key), "Đã hủy, hoàn 4.000đ giữ trong ví." if refunded else "Số đã nhận OTP, không thể hoàn tiền.")
        return
    if action.startswith("phone_change_"):
        if not state or action.removeprefix("phone_change_") != state["token"]:
            await show_screen(query, state, "Nút này không thuộc số hiện tại của bạn.")
            return
        order = db.get_order("PHONE" + state["token"])
        if order and order.get("phone_otp_seen"):
            await show_screen(query, state, "Số đã nhận OTP. Chọn Thuê số khác để mua mới.")
            return
        token = uuid.uuid4().hex[:16]
        context.user_data["phone_confirm"] = token
        action = "phone_confirm_" + token
    if action == "phone_home":
        await show_screen(query, state)
        return
    if action == "phone_new":
        token = uuid.uuid4().hex[:16]
        context.user_data["phone_confirm"] = token
        await query.edit_message_text(
            "Giữ 4.000đ trong ví. Chỉ tính tiền khi OTP hiện trên Telegram. Đổi số miễn phí khi chưa có OTP; hủy để hoàn khoản giữ.",
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
            name = db.get_setting("phone_rental_button_name") or DEFAULT_BUTTON_NAME
            if not db.reserve_phone_rental(query.from_user.id, token, RENTAL_PRICE, name):
                await show_screen(query, state, "Ví cần ít nhất 4.000đ và không có yêu cầu thuê số đang xử lý. Vui lòng nạp tiền nếu thiếu số dư.")
                return
            try:
                phone = await get_phone()
                if state and phone == {k: state.get(k) for k in ("phone", "prefix")}:
                    raise PhoneApiError("API trả lại đúng số hiện tại, chưa cấp số khác.")
            except PhoneApiError as exc:
                db.refund_phone_rental(token)
                await show_screen(query, db.get_setting(key), "Chưa cấp được số mới. Đã hoàn khoản giữ 4.000đ.")
                return
            if not db.finish_phone_rental(query.from_user.id, token, phone):
                await show_screen(query, state, "Chưa thể hoàn tất đơn. Vui lòng liên hệ admin.")
                return
            state = db.get_setting(key)
            await show_screen(query, state, "Đã nhận số. Nhấn Lấy OTP.")
        elif action.startswith("phone_otp_"):
            if not state or action.removeprefix("phone_otp_") != state["token"]:
                await show_screen(query, state, "Nút OTP này không thuộc số hiện tại của bạn.")
                return
            if time.monotonic() - context.user_data.get("phone_last_otp", -60) < 5:
                await show_screen(query, state, "Vui lòng chờ 5 giây rồi lấy OTP lại.")
                return
            context.user_data["phone_last_otp"] = time.monotonic()
            await show_screen(query, state, waiting=True)
            context.user_data["_phone_poll"] = context.application.create_task(poll_otp(query, state))
    except PhoneNumberFault:
        await show_screen(query, state, "API báo số lỗi. Bạn có thể nhấn Hủy hoặc Đổi số khác để xác minh và hoàn tiền.")
    except PhoneApiError as exc:
        await show_screen(query, state, str(exc))


async def poll_otp(query, state):
    """Poll in the background so the update queue and change button stay usable."""
    started = time.monotonic()
    deadline = started + 180
    latest = "Đang chờ OTP…"

    async def capture(value):
        nonlocal latest
        latest = value

    while time.monotonic() < deadline:
        tick = time.monotonic()
        current = db.get_setting(f"phone_rental_user_{query.from_user.id}")
        if not current or current["token"] != state["token"]:
            return
        try:
            payload = await asyncio.wait_for(
                request_api("/get-otp", on_response=capture, phone=state["phone"]),
                timeout=min(5, deadline - tick),
            )
            otp = parse_otp(payload)
            if otp:
                # Do not interrupt between Telegram acknowledgement and settlement.
                delivery = asyncio.create_task(deliver_phone_otp(query, state, otp))
                try:
                    await asyncio.shield(delivery)
                except asyncio.CancelledError:
                    await delivery
                    raise
                return
            db.reset_phone_fault(query.from_user.id, state["token"])
        except PhoneNumberFault:
            pass
        except PhoneApiError:
            latest = "Tạm thời chưa nhận được kết quả. Vui lòng chờ…"
        except asyncio.TimeoutError:
            latest = "Tạm thời chưa nhận được kết quả. Vui lòng chờ…"
        await show_screen(query, state, waiting=True)
        await asyncio.sleep(max(0, min(tick + 5, deadline) - time.monotonic()))
    await show_screen(query, state, "Đã hết 3 phút chờ. Nhấn Lấy OTP để thử lại.")


async def deliver_phone_otp(query, state, otp):
    if not db.prepare_phone_delivery(query.from_user.id, state["token"]):
        return
    await show_screen(query, state, otp=otp)
    db.record_phone_otp(query.from_user.id, state["token"])
