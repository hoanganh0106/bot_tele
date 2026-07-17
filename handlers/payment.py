"""Order payment callbacks and paid-order fulfillment."""

import asyncio
import random
from datetime import datetime
from decimal import Decimal
from urllib.parse import urlencode

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from core.config import (
    BANK_ACCOUNT_NAME,
    BANK_ACCOUNT_NUMBER,
    BANK_NAME,
    BINANCE_PAY_UID,
    CRYPTO_ORDER_TIMEOUT_SECONDS,
    USDT_NETWORK,
    USDT_WALLET_ADDRESS,
    logger,
)
from i18n import get_text
from core.helpers import (
    crypto_network_label,
    escape_html,
    escape_md,
    estimate_order_usdt,
    format_money,
    format_usdt_exact,
    format_user_link,
    generate_qr_url,
    product_display_desc,
    t,
    user_lang,
)
from core.products import async_refresh_products_cache, get_all_products_merged
from core.runtime import CRYPTO_ENABLED, api, db, hypervin


async def _notify_all_admins(context, text: str):
    # local import: avoid the payment <-> admin module cycle
    from handlers.admin import _notify_all_admins as notify

    return await notify(context, text)


def _crypto_internal_option(user_id: int, amount: str = "{amount}", lang: str | None = None) -> str:
    """Render the optional Binance Pay/UID instruction without an empty block."""
    if not BINANCE_PAY_UID:
        return ""
    return get_text(
        lang or user_lang(user_id),
        "crypto_internal_option",
        pay_uid=escape_html(BINANCE_PAY_UID),
        amount=amount,
    )


def get_owned_pending_order(order_code: str, user_id: int) -> dict | None:
    order = db.get_order(order_code)
    if not order or order.get("status") != "pending" or order.get("user_id") != user_id:
        return None
    return order


def _is_api_balance_error(error_msg: str) -> bool:
    """Nhận diện lỗi do tài khoản API đối tác không đủ số dư."""
    if not error_msg:
        return False
    msg = str(error_msg).lower()
    keywords = [
        "số dư", "so du", "khong du", "không đủ", "insufficient",
        "balance", "hết tiền", "het tien",
    ]
    return any(keyword in msg for keyword in keywords)


def _paid_order_customer_error_text(order_code: str, user_id: int = None) -> str:
    """Thông báo lỗi xử lý đơn cho khách, không lộ nguyên nhân nội bộ."""
    if user_id is not None:
        return t(user_id, "customer_order_error", order_code=order_code)
    return (
        f"⚠️ Đơn **#{order_code}** gặp lỗi trong quá trình xử lý.\n\n"
        f"✅ Thanh toán của bạn **đã được ghi nhận** — Admin đã nhận thông báo "
        f"và sẽ giao hàng hoặc hoàn tiền cho bạn sớm nhất.\n\n"
        f"🚫 **Vui lòng KHÔNG chuyển khoản lại lần nữa.**\n"
        f"💬 Cần hỗ trợ nhanh, hãy liên hệ admin kèm mã đơn `#{order_code}`."
    )


async def auto_cancel_order(context, order_code, user_id, delay):
    """Tự hủy đơn sau thời gian chờ."""
    await asyncio.sleep(delay)
    
    # Kiểm tra wallet partial trước khi cancel
    order = db.get_order(order_code)
    if not order or order.get("status") != "pending":
        return
    # The original 5-minute task remains alive after the user selects crypto.
    # Let the dedicated crypto timeout task own cancellation instead.
    if order.get("payment_method") == "crypto" and delay < CRYPTO_ORDER_TIMEOUT_SECONDS:
        return
    wallet_paid = order.get("wallet_paid", 0) if order else 0
    
    # CRITICAL: Dùng cancel_order_if_pending (atomic) để tránh race condition
    # với webhook đang xử lý thanh toán cùng lúc
    cancelled = db.cancel_order_if_pending(order_code)
    if cancelled:
        # Hoàn tiền ví nếu có
        refund_text = ""
        refunded_amount, new_balance = db.refund_order_wallet_if_needed(order_code)
        if refunded_amount > 0:
            refund_text = t(user_id, "refund", amount=format_money(refunded_amount), balance=format_money(new_balance))
        db.release_usdt_amount(order_code)
        
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=t(user_id, "order_timeout", order_code=order_code, refund=refund_text),
                parse_mode="Markdown"
            )
        except Exception:
            pass


async def handle_pay_bank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Khi khách chọn chuyển khoản — hiển thị QR."""
    query = update.callback_query
    await query.answer()
    order_code = query.data.replace("paybank_", "")
    
    order = get_owned_pending_order(order_code, query.from_user.id)
    if not order:
        await query.edit_message_text(t(query.from_user.id, "order_invalid"))
        return
    db.release_usdt_amount(order_code)
    db.update_order_fields(order_code, {"payment_method": "bank"})
    order["payment_method"] = "bank"

    total = int(order.get("total", 0))
    qr_url = generate_qr_url(total, order_code)

    if user_lang(query.from_user.id) == "en":
        await query.edit_message_text(
            t(query.from_user.id, "bank_payment", order_code=order_code,
              product=escape_html(order.get("product_name", "?")), total=format_money(total),
              bank=escape_html(BANK_NAME), account=escape_html(BANK_ACCOUNT_NUMBER),
              account_name=escape_html(BANK_ACCOUNT_NAME), qr_url=qr_url),
            parse_mode="HTML", reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(t(query.from_user.id, "btn_paid"), callback_data=f"paid_{order_code}")],
                [InlineKeyboardButton(t(query.from_user.id, "btn_cancel"), callback_data=f"cancel_{order_code}")],
            ]), disable_web_page_preview=False,
        )
        return

    text = (
        f"🛒 <b>ĐƠN HÀNG #{order_code}</b>\n\n"
        f"📦 {escape_html(order.get('product_name', '?'))}\n"
        f"💵 Tổng: <u><b>{format_money(total)}</b></u>\n\n"
        "<blockquote>"
        f"🏦 Ngân hàng: <b>{escape_html(BANK_NAME)}</b>\n"
        f"💳 STK: <code>{escape_html(BANK_ACCOUNT_NUMBER)}</code>\n"
        f"👤 Tên: <b>{escape_html(BANK_ACCOUNT_NAME)}</b>\n"
        f"💰 Số tiền: <b>{format_money(total)}</b>\n"
        f"📝 Nội dung: <code>{order_code}</code>"
        "</blockquote>\n\n"
        f"📱 Quét QR bên dưới để thanh toán nhanh:\n"
        f"<a href=\"{qr_url}\">QR Thanh toán</a>\n\n"
        f"⏰ Đơn hàng tự hủy sau <b>5 phút</b>\n"
        f"✅ Thanh toán sẽ được xác nhận <b>TỰ ĐỘNG</b>"
    )

    buttons = [
        [InlineKeyboardButton(t(query.from_user.id, "btn_paid"), callback_data=f"paid_{order_code}")],
        [InlineKeyboardButton(t(query.from_user.id, "btn_cancel"), callback_data=f"cancel_{order_code}")],
    ]

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
        disable_web_page_preview=False
    )


async def handle_pay_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reserve an exact USDT amount and show Binance deposit instructions."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    order_code = query.data.replace("paycrypto_", "", 1)

    if not CRYPTO_ENABLED:
        await query.edit_message_text(t(user_id, "crypto_unavailable"))
        return

    order = get_owned_pending_order(order_code, user_id)
    if not order:
        await query.edit_message_text(t(user_id, "order_invalid"))
        return

    crypto_created_at = order.get("crypto_created_at") or datetime.now().isoformat()
    amount_text = None
    if order.get("payment_method") == "crypto" and order.get("usdt_amount"):
        amount_text = db.activate_crypto_payment(
            order_code,
            user_id,
            str(order["usdt_amount"]),
            crypto_created_at,
            USDT_NETWORK,
        )

    if not amount_text:
        base = estimate_order_usdt(order)
        valid_offsets = [
            offset for offset in range(-100, 101)
            if base + (Decimal(offset) / Decimal("1000")) > 0
        ]
        offsets = random.sample(valid_offsets, min(10, len(valid_offsets)))
        for offset in offsets:
            candidate = base + (Decimal(offset) / Decimal("1000"))
            candidate_text = format_usdt_exact(candidate)
            activated_amount = db.activate_crypto_payment(
                order_code,
                user_id,
                candidate_text,
                crypto_created_at,
                USDT_NETWORK,
            )
            if activated_amount:
                amount_text = activated_amount
                break

    if not amount_text:
        await query.edit_message_text(t(user_id, "crypto_reservation_failed"))
        return

    qr_url = "https://api.qrserver.com/v1/create-qr-code/?" + urlencode({
        "size": "300x300",
        "data": USDT_WALLET_ADDRESS,
    })
    network_label = crypto_network_label()
    warning = t(user_id, "crypto_warning", network=network_label)
    internal = _crypto_internal_option(user_id, amount_text)
    text = t(
        user_id,
        "crypto_payment",
        network=network_label,
        address=escape_html(USDT_WALLET_ADDRESS),
        amount=amount_text,
        warning=warning,
        internal=internal,
        qr_url=escape_html(qr_url),
        timeout_minutes=CRYPTO_ORDER_TIMEOUT_SECONDS // 60,
    )
    caption = t(
        user_id,
        "crypto_payment_caption",
        network=network_label,
        address=escape_html(USDT_WALLET_ADDRESS),
        amount=amount_text,
        warning=warning,
        internal=internal,
        timeout_minutes=CRYPTO_ORDER_TIMEOUT_SECONDS // 60,
    )
    buttons = [
        [InlineKeyboardButton(t(user_id, "btn_paid"), callback_data=f"paid_{order_code}")],
        [InlineKeyboardButton(t(user_id, "btn_cancel"), callback_data=f"cancel_{order_code}")],
    ]
    markup = InlineKeyboardMarkup(buttons)
    try:
        await query.message.reply_photo(
            photo=qr_url,
            caption=caption,
            parse_mode="HTML",
            reply_markup=markup,
        )
    except Exception as exc:
        logger.warning("Unable to send USDT QR image for order %s: %s", order_code, exc)
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=markup,
            disable_web_page_preview=False,
        )
    else:
        try:
            await query.message.delete()
        except Exception as exc:
            logger.warning("Unable to remove payment chooser for order %s: %s", order_code, exc)
    asyncio.create_task(
        auto_cancel_order(context, order_code, user_id, CRYPTO_ORDER_TIMEOUT_SECONDS)
    )


async def edit_payment_message(query, text: str, *, parse_mode=None, reply_markup=None):
    """Edit either a text payment message or a QR photo caption."""
    if query.message and query.message.photo:
        return await query.edit_message_caption(
            caption=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )
    return await query.edit_message_text(
        text=text,
        parse_mode=parse_mode,
        reply_markup=reply_markup,
    )


async def edit_navigation_message(
    query,
    text: str,
    *,
    parse_mode=None,
    reply_markup=None,
    disable_web_page_preview=None,
):
    """Replace a QR photo with a text screen, or edit an existing text screen."""
    kwargs = {
        "parse_mode": parse_mode,
        "reply_markup": reply_markup,
    }
    if disable_web_page_preview is not None:
        kwargs["disable_web_page_preview"] = disable_web_page_preview

    if query.message and query.message.photo:
        sent = await query.message.reply_text(text=text, **kwargs)
        try:
            await query.message.delete()
        except Exception as exc:
            logger.warning("Unable to remove QR message after navigation: %s", exc)
        return sent
    return await query.edit_message_text(text=text, **kwargs)


async def handle_paid_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Khi khách nhấn đã chuyển khoản."""
    query = update.callback_query
    await query.answer()
    order_code = query.data.replace("paid_", "")

    order = get_owned_pending_order(order_code, query.from_user.id)
    if not order:
        await edit_payment_message(query, t(query.from_user.id, "order_invalid"))
        return

    if order.get("payment_method") == "crypto":
        await edit_payment_message(
            query,
            t(query.from_user.id, "crypto_paid_waiting"), parse_mode="Markdown"
        )
        return

    if user_lang(query.from_user.id) == "en":
        await edit_payment_message(query, t(query.from_user.id, "paid_waiting", order_code=order_code), parse_mode="Markdown")
        return

    await edit_payment_message(
        query,
        f"⏳ Đơn **#{order_code}** đang chờ xác nhận thanh toán.\n\n"
        "Hệ thống sẽ tự động xác nhận trong **1-3 phút** sau khi nhận được tiền.\n"
        "Bạn sẽ nhận được thông báo ngay khi hoàn tất! 🔔",
        parse_mode="Markdown"
    )


async def handle_pay_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Thanh toán đơn hàng 100% bằng ví."""
    query = update.callback_query
    await query.answer()
    order_code = query.data.replace("paywallet_", "")
    
    order = db.claim_order_for_payment(order_code, query.from_user.id, "wallet")
    if not order:
        await query.edit_message_text(t(query.from_user.id, "order_invalid"))
        return
    db.release_usdt_amount(order_code)

    total = int(order.get("total", 0))
    user_id = query.from_user.id
    
    # Trừ ví + ghi nhận nghĩa vụ giao hàng trong cùng một transaction JSON.
    new_balance = db.confirm_wallet_payment(order_code, user_id, total)
    if new_balance is None:
        db.release_order_payment_claim(order_code)
        await query.edit_message_text(
            t(user_id, "wallet_insufficient", balance=format_money(db.get_user_balance(user_id)), amount=format_money(total))
        )
        return
    
    await query.edit_message_text(
        t(user_id, "wallet_paid", amount=format_money(total), balance=format_money(new_balance), order_code=order_code),
        parse_mode="Markdown"
    )

    # Xử lý đơn hàng
    result = await process_paid_order(context, order_code, payment_source="wallet")
    if result:
        logger.info(f"✅ Wallet payment: Order {order_code} completed, {format_money(total)} deducted from user {user_id}")


async def handle_pay_partial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Thanh toán một phần bằng ví, phần còn lại chuyển khoản."""
    query = update.callback_query
    await query.answer()
    order_code = query.data.replace("paypartial_", "")
    
    user_id = query.from_user.id
    payment = db.start_partial_wallet_payment(order_code, user_id)
    if not payment:
        await query.edit_message_text(t(query.from_user.id, "order_invalid"))
        return
    db.release_usdt_amount(order_code)

    wallet_amount = payment["wallet_amount"]
    remain = payment["remaining"]
    new_balance = payment["new_balance"]
    if payment["fully_paid"]:
        await query.edit_message_text(
            t(user_id, "wallet_paid", amount=format_money(wallet_amount), balance=format_money(new_balance), order_code=order_code),
            parse_mode="Markdown",
        )
        await process_paid_order(context, order_code, payment_source="wallet")
        return

    qr_url = generate_qr_url(remain, order_code)

    buttons = [
        [InlineKeyboardButton(t(user_id, "btn_paid"), callback_data=f"paid_{order_code}")],
        [InlineKeyboardButton(t(user_id, "btn_cancel"), callback_data=f"cancel_{order_code}")],
    ]

    await query.edit_message_text(
        t(user_id, "partial_payment", wallet_amount=format_money(wallet_amount), balance=format_money(new_balance), bank=BANK_NAME, account=BANK_ACCOUNT_NUMBER, account_name=BANK_ACCOUNT_NAME, remain=format_money(remain), order_code=order_code, qr_url=qr_url),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
        disable_web_page_preview=False
    )


async def handle_cancel_order(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hủy đơn hàng — dùng atomic operation để tránh race condition."""
    query = update.callback_query
    await query.answer()
    order_code = query.data.replace("cancel_", "")

    # Kiểm tra có trả ví partial không
    order = db.get_order(order_code)
    if not order or order.get("user_id") != query.from_user.id or order.get("status") != "pending":
        await edit_payment_message(query, t(query.from_user.id, "order_invalid"))
        return
    db.release_usdt_amount(order_code)
    wallet_paid = order.get("wallet_paid", 0) if order else 0
    product_key = order.get("product_key") if order else None

    # CRITICAL: Dùng cancel_order_if_pending (atomic) thay vì read-check-write
    cancelled = db.cancel_order_if_pending(order_code, status="cancelled")
    if cancelled:
        refund_text = ""
        refunded_amount, new_balance = db.refund_order_wallet_if_needed(order_code)
        if refunded_amount > 0:
            refund_text = t(query.from_user.id, "refund", amount=f"**{format_money(refunded_amount)}**", balance=format_money(new_balance))
        
        # Tạo dòng nút điều hướng thông minh quay lại
        buttons = []
        row = []
        if product_key:
            row.append(InlineKeyboardButton(t(query.from_user.id, "btn_view_product"), callback_data=f"prod_{product_key}"))
        row.append(InlineKeyboardButton(t(query.from_user.id, "btn_menu"), callback_data="back_menu"))
        buttons.append(row)
        buttons.append([InlineKeyboardButton(t(query.from_user.id, "btn_home"), callback_data="back_start")])

        await edit_payment_message(
            query,
            t(query.from_user.id, "order_cancelled", order_code=order_code, refund=refund_text),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        db.release_usdt_amount(order_code)
    else:
        await edit_payment_message(query, t(query.from_user.id, "order_already_processed"))


async def process_paid_order(context, order_code: str, payment_source: str = "sepay"):
    """Xử lý đơn hàng đã thanh toán.
    Dùng atomic complete_order_payment để tránh race condition.
    
    CRITICAL: Toàn bộ body được wrap trong try/except để đảm bảo
    MỌI lỗi đều set order = 'failed' + thông báo admin.
    Trước đây chỉ có phần API call được bảo vệ → orders bị treo vĩnh viễn.
    """
    order = db.get_order(order_code)
    if not order:
        logger.warning(f"Order {order_code} not found")
        return False

    if order["status"] not in ("pending", "processing", "failed"):
        logger.info(f"Order {order_code} already processed: {order['status']}")
        return False

    product_key = order["product_key"]
    qty = order["qty"]
    user_id = order["user_id"]

    logger.info(f"📦 Processing order {order_code}: product={product_key}, qty={qty}, user={user_id}, source={payment_source}")

    try:
        # Backward compatibility cho các đơn cũ
        is_custom_local = order.get("is_custom_local")
        if is_custom_local is None:
            products, _ = get_all_products_merged()
            is_custom_local = False
            if products and product_key in products:
                is_custom_local = products[product_key].get("is_custom_local", False)

        logger.info(f"  → is_custom_local={is_custom_local}")

        # Nếu là slot_gpt_team hoặc Hàng tự bán CẦN cung cấp thông tin
        if order.get("needs_email") and not order.get("emails"):
            result = db.complete_order_payment(order_code, {
                "status": "paid_waiting_email",
                "paid_at": datetime.now().isoformat(),
                "payment_source": payment_source,
                "is_custom_local": is_custom_local
            })
            if not result:
                logger.info(f"Order {order_code} was taken by another thread")
                return False

            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=t(user_id, "need_email", order_code=order_code, qty=qty),
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Failed to send email request: {e}")

            logger.info(f"  ✅ Order {order_code} → paid_waiting_email")
            return True

        # Nếu là SẢN PHẨM KHÔNG QUA API (TỰ BÁN): Xử lý luôn và báo cho Admin thủ công
        if is_custom_local:
            if order.get("is_auto_delivered", False):
                # Lấy list account từ inventory db
                accounts = db.pop_custom_accounts(product_key, qty)
                if len(accounts) < qty:
                    # Nếu khách mua lúc vừa hết (tranh chấp), đưa về dạng chờ xử lý tay
                    result = db.complete_order_payment(order_code, {
                        "status": "paid",
                        "paid_at": datetime.now().isoformat(),
                        "payment_source": payment_source
                    })
                    if not result:
                        return False
                    
                    admin_text = (
                        f"🚨 **[HÀNG TỰ BÁN] AUTO GIAO HÀNG HẾT KHO**\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"📋 Mã: `{order_code}`\n"
                        f"👤 Khách: {format_user_link(order.get('username'), user_id)}\n"
                        f"📦 Sản phẩm: {order.get('product_name', '?')} x{qty}\n"
                        f"⚠️ Không đủ tài khoản trong kho để tự động giao!\n"
                        f"⚡ Hãy ib khách và trả tài khoản tay nhé!"
                    )
                    await _notify_all_admins(context, admin_text)
                    
                    try:
                        await context.bot.send_message(user_id, t(user_id, "delivery_delayed", order_code=order_code))
                    except Exception: pass
                    
                    logger.info(f"  ✅ Order {order_code} → paid (auto delivery out of stock)")
                    return True

                # Giao thành công — atomic set paid + items
                result = db.complete_order_payment(order_code, {
                    "status": "paid",
                    "items": accounts,
                    "paid_at": datetime.now().isoformat(),
                    "payment_source": payment_source
                })
                if not result:
                    return False
                
                items_str = "```\n" + "\n".join(accounts) + "\n```"
                
                # Lấy mô tả sản phẩm
                _desc = product_display_desc(product_key, {}, user_lang(user_id))
                desc_block = f"\n📝 _{_desc}_\n" if _desc else ""
                
                # Gửi cho khách
                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=t(user_id, "delivery_complete", order_code=order_code, items=items_str, description=desc_block),
                        parse_mode="Markdown"
                    )
                except Exception: pass
                
                # Báo Admin
                await _notify_all_admins(context, 
                    f"🔔 **[HÀNG TỰ BÁN] AUTO GIAO HÀNG THÀNH CÔNG**\n"
                    f"Mã: `{order_code}`\n"
                    f"👤 Khách: {format_user_link(order.get('username'), user_id)} | Mua x{qty}\n"
                    f"🔑 Đã tự động xuất {qty} tài khoản từ kho để giao cho khách:\n"
                    f"{items_str}"
                )
                logger.info(f"  ✅ Order {order_code} → paid (auto delivered {qty} accounts)")
                return True
                
            else:
                # Xử lý TAY (gửi email/info để admin duyệt)
                result = db.complete_order_payment(order_code, {
                    "status": "paid",
                    "paid_at": datetime.now().isoformat(),
                    "payment_source": payment_source
                })
                if not result:
                    return False
                
                emails_text = "\n".join(order.get("emails", []))
                
                # Trừ tồn kho hiển thị (để tránh người khác mua quá mức)
                current_stock = db.get_custom_stocks().get(product_key, 0)
                if current_stock > 0:
                    db.set_custom_stock(product_key, max(0, current_stock - qty))
        
                # Thông báo Admin
                admin_text = (
                    f"🔔 **[HÀNG TỰ BÁN] CẦN Admin DUYỆT THỦ CÔNG**\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"📋 Mã: `{order_code}`\n"
                    f"👤 Khách: {format_user_link(order.get('username'), user_id)}\n"
                    f"📦 {order.get('product_name', '?')} x{qty}\n"
                    f"💰 Thu: {format_money(order['total'])}\n"
                    f"💳 Nguồn: {payment_source}\n"
                    f"📧 **THÔNG TIN KHÁCH GỬI:**\n"
                    f"```\n{emails_text}\n```\n"
                    f"⚡ Hãy chủ động nhắn tin giao tài khoản cho khách nhé!"
                )
                await _notify_all_admins(context, admin_text)

            # Thông báo KHÁCH
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=t(user_id, "manual_processing", order_code=order_code),
                    parse_mode="Markdown"
                )
            except Exception:
                pass
            logger.info(f"  ✅ Order {order_code} → paid (custom_local manual)")
            return True

        # Mua hàng từ API đối tác
        # KHÔNG force_refresh — dùng cache (background task giữ data tươi mỗi 90s)
        products, _ = get_all_products_merged()
        api_custom_local = False
        api_source = "CTV"
        if products and product_key in products:
            api_custom_local = products[product_key].get("is_custom_local", False)
            api_source = products[product_key].get("api_source", "CTV")

        # Nếu cache hoàn toàn trống (hiếm — cả 2 API chết), thử refresh 1 lần
        if not products:
            logger.warning(f"  ⚠️ Product cache empty, attempting refresh for order {order_code}")
            try:
                products, _ = await async_refresh_products_cache()
                if products and product_key in products:
                    api_custom_local = products[product_key].get("is_custom_local", False)
                    api_source = products[product_key].get("api_source", "CTV")
            except Exception:
                pass

        if not api_custom_local and (not products or product_key not in products):
            # Sản phẩm đối tác đã bị xóa/đổi key → không gọi buy
            logger.warning(f"  ❌ Product {product_key} not found in API — order {order_code}")
            db.complete_order_payment(order_code, {
                "status": "failed",
                "error": f"Sản phẩm '{product_key}' không còn trên API đối tác",
                "paid_at": datetime.now().isoformat()
            })

            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=_paid_order_customer_error_text(order_code, user_id),
                    parse_mode="Markdown"
                )
            except Exception:
                pass

            await _notify_all_admins(context,
                f"🚨 **SẢN PHẨM KHÔNG TỒN TẠI — CẦN HOÀN TIỀN**\n"
                f"Mã: `{order_code}`\n"
                f"👤 Khách: {format_user_link(order.get('username'), user_id)}\n"
                f"Sản phẩm: `{product_key}` — ĐÃ BỊ ĐỐI TÁC XÓA/ĐỔI KEY\n"
                f"💰 Khách đã thanh toán {format_money(order['total'])} — cần hoàn tiền!"
            )
            return False

        emails = order.get("emails")
        
        logger.info("Calling %s API for %s x%s", api_source, product_key, qty)
        if api_source == "HYPERVIN":
            if hypervin is None:
                result = {"success": False, "error": "Nguồn Hypervin đang tắt"}
            else:
                result = await asyncio.to_thread(hypervin.create_order, product_key.removeprefix("hv_"), qty)
        else:
            result = await asyncio.to_thread(
                lambda: api.buy(product_key, qty, emails=emails if emails else None)
            )

        logger.info(f"  → API response for {order_code}: success={result.get('success')}")

        if result.get("success"):
            items = result.get("items", [])

            # Atomic: pending → paid + lưu kết quả API
            saved = db.complete_order_payment(order_code, {
                "status": "paid",
                "paid_at": datetime.now().isoformat(),
                "payment_source": payment_source,
                "items": items,
                "api_order_code": result.get("api_order_code") or result.get("order_code", ""),
                "cost": result.get("total_charged", 0)
            })
            if not saved:
                logger.warning(f"Order {order_code} was taken by another thread after API buy")
                return False

            # Format items cho khách
            items_text = "```\n" + "\n".join(items) + "\n```"

            # Lấy mô tả sản phẩm
            _desc2 = product_display_desc(product_key, {}, user_lang(user_id))
            desc_block2 = f"\n📝 _{_desc2}_\n" if _desc2 else ""

            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=t(user_id, "api_delivery_complete", order_code=order_code, product=order.get('product_name', product_key), qty=qty, items=items_text, description=desc_block2),
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Failed to send items to user: {e}")

            # Thông báo admin
            profit = order["total"] - result.get("total_charged", 0)
            admin_text = (
                f"🔔 **ĐƠN HÀNG MỚI THÀNH CÔNG**\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📋 Mã: `{order_code}`\n"
                f"👤 Khách: {format_user_link(order.get('username'), user_id)}\n"
                f"📦 {order.get('product_name', '?')} x{qty}\n"
                f"💰 Thu: {format_money(order['total'])} | Gốc: {format_money(result.get('total_charged', 0))}\n"
                f"📈 Lãi: **{format_money(profit)}**\n"
                f"💳 Nguồn: {payment_source}"
            )
            await _notify_all_admins(context, admin_text)

            logger.info(f"  ✅ Order {order_code} → COMPLETED! Items delivered.")
            return True
        else:
            error_msg = str(result.get("error") or "Lỗi không xác định")
            logger.warning(f"  ❌ API returned error for {order_code}: {error_msg}")
            db.complete_order_payment(order_code, {
                "status": "failed",
                "error": error_msg,
                "paid_at": datetime.now().isoformat()
            })

            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=_paid_order_customer_error_text(order_code, user_id),
                    parse_mode="Markdown"
                )
            except Exception:
                pass

            if _is_api_balance_error(error_msg):
                admin_alert = (
                    f"🔴🔴 **API HẾT SỐ DƯ — NẠP TIỀN GẤP!** 🔴🔴\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"Mã đơn: `{order_code}`\n"
                    f"👤 Khách: {format_user_link(order.get('username'), user_id)}\n"
                    f"Sản phẩm: {order.get('product_name', '?')} x{qty}\n"
                    f"💰 Khách đã thanh toán {format_money(order['total'])}.\n\n"
                    f"➡️ Nạp tiền vào tài khoản CTV API rồi xử lý lại đơn.\n"
                    f"Đã báo khách là đơn lỗi và dặn KHÔNG chuyển khoản lại.\n"
                    f"Lỗi gốc: {escape_md(error_msg)}"
                )
            else:
                admin_alert = (
                    f"🚨 **ĐƠN LỖI — CẦN XỬ LÝ**\n"
                    f"Mã: `{order_code}`\n"
                    f"👤 Khách: {format_user_link(order.get('username'), user_id)}\n"
                    f"Sản phẩm: {order.get('product_name', '?')} x{qty}\n"
                    f"Lỗi API: {escape_md(error_msg)}\n"
                    f"💰 Khách đã thanh toán {format_money(order['total'])} — cần hoàn tiền!"
                )
            await _notify_all_admins(context, admin_alert)

            return False

    except Exception as e:
        logger.error(f"💥 CRITICAL: Unhandled exception in process_paid_order {order_code}: {e}", exc_info=True)
        # LUÔN set failed để order không bị treo vĩnh viễn ở pending
        try:
            db.complete_order_payment(order_code, {
                "status": "failed",
                "error": f"Exception: {str(e)}",
                "paid_at": datetime.now().isoformat()
            })
        except Exception:
            # Fallback: force update nếu complete_order_payment cũng lỗi
            db.update_order_fields(order_code, {
                "status": "failed",
                "error": f"Exception: {str(e)}",
                "paid_at": datetime.now().isoformat()
            })

        # Thông báo cho khách
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text=_paid_order_customer_error_text(order_code, user_id),
                parse_mode="Markdown"
            )
        except Exception:
            pass

        # Thông báo admin
        await _notify_all_admins(context,
            f"🚨 **ĐƠN LỖI EXCEPTION — CẦN XỬ LÝ GẤP**\n"
            f"Mã: `{order_code}`\n"
            f"👤 Khách: {format_user_link(order.get('username'), user_id)}\n"
            f"Sản phẩm: {order.get('product_name', '?')} x{qty}\n"
            f"Lỗi: `{str(e)[:200]}`\n"
            f"💰 Khách đã thanh toán {format_money(order['total'])} — cần hoàn tiền hoặc giao tay!"
        )
        return False
