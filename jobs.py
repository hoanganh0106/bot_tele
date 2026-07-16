"""Background cleanup, payment, Binance, and startup jobs."""

import asyncio
import os
import re
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation
from threading import Thread

from telegram import BotCommand

from binance_client import BinanceAPIError
from core.config import (
    BINANCE_POLL_FAIL_ALERT_THRESHOLD,
    BINANCE_POLL_INTERVAL,
    BINANCE_POLL_LOOKBACK_SECONDS,
    BINANCE_POLL_MAX_LOOKBACK_SECONDS,
    CRYPTO_ORDER_TIMEOUT_SECONDS,
    DATA_DIR,
    DB_PATH,
    ORDER_TIMEOUT_SECONDS,
    USDT_NETWORK,
    WEBHOOK_PORT,
    logger,
)
from core.helpers import (
    crypto_network_matches,
    crypto_poll_start_ms,
    escape_md,
    format_money,
    order_created_at_ms,
    t,
)
from core.products import API_CACHE_TTL, API_STALE_TTL, _api_cache, _do_refresh_products
from core.runtime import CRYPTO_ENABLED, binance, db, get_bot_username, set_bot_username
from handlers.admin import _notify_all_admins
from handlers.payment import process_paid_order
from sepay_server import start_webhook_server


async def _cleanup_stale_orders(application):
    """Hủy đơn pending quá hạn (chạy 1 lần khi khởi động + định kỳ)."""
    now = datetime.now()
    pending = db.get_pending_orders()
    cancelled_count = 0
    
    for code, order in pending.items():
        is_crypto = order.get("payment_method") == "crypto"
        created_str = order.get("crypto_created_at" if is_crypto else "created_at", "")
        if not created_str:
            continue
        try:
            created = datetime.fromisoformat(created_str)
        except (ValueError, TypeError):
            continue
        
        elapsed = (now - created).total_seconds()
        timeout_seconds = CRYPTO_ORDER_TIMEOUT_SECONDS if is_crypto else ORDER_TIMEOUT_SECONDS
        if elapsed > timeout_seconds:
            # Kiểm tra partial wallet payment trước khi hủy
            wallet_paid = order.get("wallet_paid", 0)
            
            # CRITICAL: Dùng cancel_order_if_pending (atomic) để tránh
            # ghi đè đơn đã được webhook xử lý (paid) trong lúc cleanup
            cancelled = db.cancel_order_if_pending(code)
            if cancelled:
                cancelled_count += 1
                # Hoàn tiền ví nếu đã trả partial
                refund_text = ""
                refunded_amount, new_balance = db.refund_order_wallet_if_needed(code)
                if refunded_amount > 0:
                    refund_text = t(order["user_id"], "refund", amount=format_money(refunded_amount), balance=format_money(new_balance))
                db.release_usdt_amount(code)
                try:
                    await application.bot.send_message(
                        chat_id=order["user_id"],
                        text=t(order["user_id"], "order_timeout", order_code=code, refund=refund_text),
                        parse_mode="Markdown"
                    )
                except Exception:
                    pass
    
    if cancelled_count:
        logger.info(f"Cleanup: cancelled {cancelled_count} stale pending orders")


async def _periodic_order_cleanup(application):
    """Job chạy mỗi 5 phút để hủy đơn pending quá hạn (phòng trường hợp bot restart mất task)."""
    while True:
        await asyncio.sleep(300)  # 5 phút
        try:
            await _cleanup_stale_orders(application)
        except Exception as e:
            logger.error(f"Periodic cleanup error: {e}")


async def _periodic_product_refresh():
    """Background refresh sản phẩm mỗi 60 giây.
    Giữ cache luôn tươi — user không bao giờ phải chờ API.
    Dùng asyncio.to_thread() để KHÔNG block event loop.
    """
    while True:
        await asyncio.sleep(30)
        try:
            products, balance = await asyncio.to_thread(_do_refresh_products)
            if products:
                global _api_cache
                _api_cache = {
                    "data": (products, balance),
                    "expiry": time.time() + API_CACHE_TTL,
                    "stale_expiry": time.time() + API_STALE_TTL,
                }
                logger.debug(f"🔄 Periodic refresh: {len(products)} products")
        except Exception as e:
            logger.error(f"Periodic product refresh error: {e}")


async def _retry_failed_orders(application):
    """Tự động retry đơn hàng failed do lỗi API tạm thời.
    Chạy mỗi 2 phút, tối đa 3 lần retry/đơn, chỉ retry đơn trong 30 phút gần đây.
    """
    await asyncio.sleep(60)  # Chờ bot ổn định trước khi bắt đầu retry
    while True:
        try:
            retryable = db.get_retryable_orders()
            for code, order in retryable.items():
                retry_count = order.get("retry_count", 0)
                logger.info(f"🔄 Retrying failed order {code} (attempt {retry_count + 1}/3)")

                # Ghi nhận lần retry (giữ status=failed — process_paid_order chấp nhận cả failed)
                db.update_order_fields(code, {"retry_count": retry_count + 1})

                result = await process_paid_order(application, code, order.get("payment_source", "sepay"))
                if result:
                    logger.info(f"✅ Retry successful for order {code}")
                    await _notify_all_admins(application,
                        f"✅ **ĐƠN RETRY THÀNH CÔNG**\n"
                        f"Mã: `{code}` | Lần thử: {retry_count + 1}\n"
                        f"📦 {order.get('product_name', '?')} x{order.get('qty', 1)}"
                    )
                else:
                    logger.warning(f"❌ Retry still failed for order {code}")

                await asyncio.sleep(5)  # Tránh spam API liên tục
        except Exception as e:
            logger.error(f"Retry failed orders error: {e}")

        await asyncio.sleep(120)  # Mỗi 2 phút


async def _payment_processor(application):
    """Poll DB mỗi 5 giây, xử lý giao dịch mới từ SePay.
    
    CRITICAL: Không bao giờ chết — tự restart nếu crash.
    """
    logger.info("💳 Payment processor started — polling every 3s")
    while True:
        try:
            await asyncio.sleep(3)
            payments = db.get_unprocessed_payments()
            if payments:
                logger.info(f"💳 Found {len(payments)} unprocessed payment(s)")
            for payment in payments:
                tid = payment.get("id", "?")
                try:
                    await _handle_payment(application, payment)
                except Exception as e:
                    logger.error(f"Error handling payment {tid}: {e}", exc_info=True)
                    # Mark processed để không bị retry vô tận
                    db.mark_payment_processed(tid)
                    
                    # FIX: Tìm order liên quan và set failed để không bị treo ở pending
                    try:
                        content = payment.get("content", "")
                        clean = content.upper().replace(" ", "").replace("-", "").replace("\n", "")
                        order_code, order = db.find_order_by_content(clean)
                        if order_code and order and order.get("status") in ("pending", "failed"):
                            db.update_order_fields(order_code, {
                                "status": "failed",
                                "error": f"Payment processing exception: {str(e)[:200]}",
                                "paid_at": datetime.now().isoformat()
                            })
                            logger.error(f"  → Set order {order_code} to failed (was stuck pending)")
                    except Exception:
                        pass

                    try:
                        await _notify_all_admins(application,
                            f"🚨 **LỖI XỬ LÝ THANH TOÁN**\n"
                            f"Transaction: `{tid}`\n"
                            f"💰 Số tiền: {payment.get('transferAmount', '?'):,}đ\n"
                            f"📝 Nội dung: {payment.get('content', '?')}\n"
                            f"Lỗi: {str(e)[:200]}\n"
                            f"⚠️ Cần kiểm tra thủ công!"
                        )
                    except Exception:
                        pass
        except Exception as e:
            logger.error(f"💥 Payment processor crashed, restarting in 10s: {e}", exc_info=True)
            await asyncio.sleep(10)  # Chờ 10s rồi restart


class _PollFailureAlert:
    def __init__(self, name: str, threshold: int):
        self.name = name
        self.threshold = threshold
        self.failures = 0
        self.alerted = False

    def record_success(self) -> None:
        self.failures = 0
        self.alerted = False

    async def record_failure(self, application, exc: Exception) -> None:
        self.failures += 1
        if self.failures >= self.threshold and not self.alerted:
            self.alerted = True
            await _notify_all_admins(
                application,
                f"🚨 **{self.name} poller lỗi liên tiếp**\n"
                f"Số lần lỗi: `{self.failures}`\nLỗi gần nhất: `{escape_md(str(exc))}`",
            )


async def _process_incoming_usdt(application, *, tx_key, amount, event_time_ms, source, source_label) -> None:
    """Match one positive USDT event, atomically claim it, then fulfill its order."""
    tx_key = str(tx_key).strip()
    if not tx_key or db.is_txid_processed(tx_key):
        return
    try:
        amount = Decimal(str(amount))
        event_time_ms = int(event_time_ms)
    except (InvalidOperation, TypeError, ValueError):
        logger.warning("Ignoring malformed %s USDT event: tx=%r amount=%r", source_label, tx_key, amount)
        return
    if not amount.is_finite() or amount <= 0 or event_time_ms <= 0:
        logger.warning("Ignoring invalid %s USDT event: tx=%r amount=%r time=%r", source_label, tx_key, amount, event_time_ms)
        return

    matches = []
    for order_code, order in db.get_crypto_matchable_orders().items():
        try:
            expected = Decimal(str(order.get("usdt_amount")))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if amount == expected and (created_at_ms := order_created_at_ms(order)) is not None and event_time_ms >= created_at_ms:
            matches.append((order_code, order))

    if not matches:
        if db.mark_txid_processed(tx_key):
            await _notify_all_admins(application, f"⚠️ **USDT VÀO KHÔNG KHỚP ĐƠN**\n💰 `{amount}` USDT\n📥 Nguồn: `{source_label}`\n🔗 Mã GD: `{escape_md(tx_key)}`\n🕒 Thời gian: `{event_time_ms}`")
        return
    if len(matches) > 1:
        if db.mark_txid_processed(tx_key):
            codes = ", ".join(code for code, _ in matches)
            await _notify_all_admins(application, f"🚨 **USDT KHỚP NHIỀU ĐƠN — KHÔNG TỰ GIAO**\n📋 Các mã: `{escape_md(codes)}`\n💰 `{amount}` USDT\n📥 Nguồn: `{source_label}`\n🔗 Mã GD: `{escape_md(tx_key)}`")
        return

    order_code, order = matches[0]
    if order.get("status") == "cancelled":
        if db.mark_txid_processed(tx_key):
            await _notify_all_admins(application, f"⚠️ **USDT VÀO CHO ĐƠN KHÁCH ĐÃ HỦY TAY**\n📋 Mã: `{order_code}`\n💰 `{amount}` USDT\n📥 Nguồn: `{source_label}`\n🔗 Mã GD: `{escape_md(tx_key)}`")
        return

    claimed = db.claim_crypto_deposit(order_code, tx_key, event_time_ms, payment_source=source)
    if not claimed:
        if db.mark_txid_processed(tx_key):
            await _notify_all_admins(application, f"⚠️ **USDT KHỚP SỐ TIỀN NHƯNG ĐƠN KHÔNG CÒN CHỜ**\n📋 Mã: `{order_code}`\n💰 `{amount}` USDT\n📥 Nguồn: `{source_label}`\n🔗 Mã GD: `{escape_md(tx_key)}`")
        return
    if claimed.get("crypto_claim_status") == "wallet_insufficient":
        db.release_usdt_amount(order_code)
        await _notify_all_admins(application, f"🚨 **USDT VÀO SAU TIMEOUT — VÍ KHÔNG ĐỦ ĐỂ TRỪ LẠI**\n📋 Mã: `{order_code}`\n👤 User: `{claimed.get('user_id')}`\n💰 Tiền vào: `{amount}` USDT\n📥 Nguồn: `{source_label}`")
        return
    if claimed.get("crypto_recovered_timeout"):
        wallet_paid = int(claimed.get("wallet_paid", 0) or 0)
        if wallet_paid > 0:
            try:
                await application.bot.send_message(chat_id=claimed["user_id"], text=t(claimed["user_id"], "wallet_rededucted", amount=format_money(wallet_paid), order_code=order_code))
            except Exception:
                pass
        await _notify_all_admins(application, f"⚡ **PHỤC HỒI ĐƠN CRYPTO TIMEOUT**\n📋 Mã: `{order_code}`\n💰 Tiền vào chậm: `{amount}` USDT\n📥 Nguồn: `{source_label}`\n✅ Đang xử lý giao hàng...")
    try:
        result = await process_paid_order(application, order_code, source)
    finally:
        db.release_usdt_amount(order_code)
    if result:
        logger.info("Binance %s matched order %s: %s USDT", source, order_code, amount)
    else:
        logger.warning("Binance %s matched %s but fulfillment returned False", source, order_code)


async def poll_binance_deposits(application):
    """Poll successful on-chain USDT deposits and pass them through the shared pipeline."""
    if not CRYPTO_ENABLED or binance is None:
        return
    source = "binance_usdt"
    lookback_ms = BINANCE_POLL_LOOKBACK_SECONDS * 1000
    max_lookback_ms = BINANCE_POLL_MAX_LOOKBACK_SECONDS * 1000
    watermark = db.get_crypto_poll_watermark(source)
    last_check = watermark if watermark is not None else int(time.time() * 1000)
    failures = _PollFailureAlert("Binance on-chain", BINANCE_POLL_FAIL_ALERT_THRESHOLD)
    logger.info("Binance USDT on-chain poller started — interval=%ss, network=%s, lookback=%ss", BINANCE_POLL_INTERVAL, USDT_NETWORK, BINANCE_POLL_LOOKBACK_SECONDS)
    while True:
        await asyncio.sleep(BINANCE_POLL_INTERVAL)
        cycle_now = int(time.time() * 1000)
        start_ms = crypto_poll_start_ms(last_check, cycle_now, lookback_ms, max_lookback_ms)
        try:
            deposits = await asyncio.to_thread(binance.get_deposit_history, start_time_ms=start_ms)
            for deposit in deposits:
                if crypto_network_matches(deposit.get("network", "")):
                    await _process_incoming_usdt(application, tx_key=deposit.get("txId", ""), amount=deposit.get("amount"), event_time_ms=deposit.get("insertTime", 0), source=source, source_label="on-chain BEP20")
            last_check = cycle_now
            db.set_crypto_poll_watermark(source, cycle_now)
            failures.record_success()
        except BinanceAPIError as exc:
            logger.error("Binance on-chain poll error: %s", exc)
            await failures.record_failure(application, exc)
            if exc.status_code in (418, 429):
                await asyncio.sleep(max(60, BINANCE_POLL_INTERVAL * 2))
        except Exception as exc:
            logger.error("Binance on-chain poll error: %s", exc, exc_info=True)
            await failures.record_failure(application, exc)


async def poll_binance_pay(application):
    """Poll positive incoming Binance Pay USDT transfers independently from on-chain deposits."""
    if not CRYPTO_ENABLED or binance is None:
        return
    source = "binance_pay"
    lookback_ms = BINANCE_POLL_LOOKBACK_SECONDS * 1000
    max_lookback_ms = BINANCE_POLL_MAX_LOOKBACK_SECONDS * 1000
    watermark = db.get_crypto_poll_watermark(source)
    last_check = watermark if watermark is not None else int(time.time() * 1000)
    failures = _PollFailureAlert("Binance Pay", BINANCE_POLL_FAIL_ALERT_THRESHOLD)
    logger.info("Binance Pay poller started — interval=%ss, lookback=%ss", BINANCE_POLL_INTERVAL, BINANCE_POLL_LOOKBACK_SECONDS)
    while True:
        await asyncio.sleep(BINANCE_POLL_INTERVAL)
        cycle_now = int(time.time() * 1000)
        start_ms = crypto_poll_start_ms(last_check, cycle_now, lookback_ms, max_lookback_ms)
        try:
            transactions = await asyncio.to_thread(binance.get_pay_transactions, start_time_ms=start_ms)
            for transaction in transactions:
                if str(transaction.get("currency", "")).upper() != "USDT":
                    continue
                transaction_id = str(transaction.get("transactionId", "")).strip()
                if not transaction_id:
                    logger.warning("Ignoring Binance Pay transaction without transactionId: %r", transaction)
                    continue
                try:
                    amount = Decimal(str(transaction.get("amount")))
                except (InvalidOperation, TypeError, ValueError):
                    logger.warning("Ignoring malformed Binance Pay transaction: %r", transaction)
                    continue
                if amount <= 0:
                    continue
                await _process_incoming_usdt(application, tx_key=f"PAY:{transaction_id}", amount=amount, event_time_ms=transaction.get("transactionTime", 0), source=source, source_label="chuyển nội bộ Binance")
            last_check = cycle_now
            db.set_crypto_poll_watermark(source, cycle_now)
            failures.record_success()
        except BinanceAPIError as exc:
            logger.error("Binance Pay poll error: %s", exc)
            await failures.record_failure(application, exc)
            if exc.status_code in (418, 429):
                await asyncio.sleep(max(60, BINANCE_POLL_INTERVAL * 2))
        except Exception as exc:
            logger.error("Binance Pay poll error: %s", exc, exc_info=True)
            await failures.record_failure(application, exc)


async def recover_confirmed_crypto_orders(application):
    """Resume crypto fulfillment after a crash that happened after txid claim."""
    for order_code, order in db.get_confirmed_crypto_orders().items():
        logger.warning("Recovering confirmed Binance order after restart: %s", order_code)
        try:
            await process_paid_order(application, order_code, order.get("payment_source", "binance_usdt"))
        finally:
            db.release_usdt_amount(order_code)


async def recover_confirmed_wallet_orders(application):
    """Resume wallet-paid fulfillment after a process restart."""
    for order_code in db.get_confirmed_wallet_orders():
        logger.warning("Recovering confirmed wallet order after restart: %s", order_code)
        await process_paid_order(application, order_code, "wallet")


async def _handle_payment(application, payment: dict):
    """Xử lý 1 giao dịch incoming — match với đơn hàng và duyệt.
    
    QUAN TRỌNG: mark_payment_processed được gọi CUỐI CÙNG,
    sau khi đã xử lý xong. Nếu crash giữa chừng, payment
    sẽ được retry ở lần poll tiếp theo.
    """
    transaction_id = payment.get("id")
    transfer_amount = int(payment.get("transferAmount", 0)) if payment.get("transferAmount") else 0
    content = payment.get("content", "")
    reference_code = payment.get("referenceCode", "")
    
    # FIX: SePay có thể gửi nội dung CK ở nhiều trường khác nhau:
    # - transactionContent: nội dung gốc khách nhập
    # - description: mô tả giao dịch (có thể chứa order code)
    # - code: mã SePay tự nhận diện
    # - content: trường cũ (đôi khi là mô tả ngân hàng, KHÔNG phải nội dung CK)
    transaction_content = payment.get("transactionContent", "")
    description = payment.get("description", "")
    sepay_code = payment.get("code", "")

    # Dedup: skip nếu đã xử lý rồi (phòng trường hợp race condition)
    if db.is_transaction_processed(transaction_id):
        logger.info(f"Payment {transaction_id} already processed (dedup), marking done")
        db.mark_payment_processed(transaction_id)
        return

    logger.info(
        f"Processing payment: id={transaction_id}, amount={transfer_amount}, "
        f"content='{content}', txContent='{transaction_content}', "
        f"desc='{description}', code='{sepay_code}'"
    )

    # Gom TẤT CẢ các trường có thể chứa order code vào 1 chuỗi
    all_text = f"{content} {transaction_content} {description} {sepay_code} {reference_code}"
    clean_content = all_text.upper().replace(" ", "").replace("-", "").replace("\n", "")

    # === KIỂM TRA NẠP TIỀN VÀO VÍ ===
    deposit_user_id = db.find_deposit_by_content(clean_content)
    if deposit_user_id:
        min_deposit = db.get_setting("min_deposit", 5000)
        if transfer_amount < min_deposit:
            logger.info(f"Deposit amount {transfer_amount} below minimum {min_deposit} for user {deposit_user_id}")
            db.mark_payment_processed(transaction_id)
            db.mark_transaction_processed(transaction_id)
            await _notify_all_admins(application,
                f"⚠️ **NẠP VÍ DƯỚI MỨC TỐI THIỂU**\n"
                f"👤 User: {deposit_user_id}\n"
                f"💰 Số tiền: {transfer_amount:,}đ (tối thiểu {min_deposit:,}đ)\n"
                f"📝 Nội dung: {content}"
            )
            return

        new_balance = db.add_balance(deposit_user_id, transfer_amount, reason="deposit")
        db.mark_payment_processed(transaction_id)
        db.mark_transaction_processed(transaction_id)
        logger.info(f"✅ Deposit: {transfer_amount}đ → user {deposit_user_id}, new balance: {new_balance}")

        # Thông báo cho user
        try:
            await application.bot.send_message(
                chat_id=deposit_user_id,
                text=t(deposit_user_id, "deposit_success", amount=format_money(transfer_amount), balance=format_money(new_balance)),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Failed to notify deposit: {e}")

        # Thông báo admin
        await _notify_all_admins(application,
            f"💳 **NẠP VÍ**\n"
            f"👤 User ID: {deposit_user_id}\n"
            f"💰 +{transfer_amount:,}đ → Số dư: {new_balance:,}đ"
        )
        return

    # Tìm đơn hàng khớp — tìm trong toàn bộ text
    order_code, order = db.find_order_by_content(clean_content)

    # Fallback: tìm regex BOT order code trong toàn bộ text
    if not order_code:
        match = re.search(r"BOT\d{10}[A-Z0-9]{6}", clean_content)
        if match:
            order_code, order = db.find_order_by_content(match.group())

    # Fallback 2: thử từng trường riêng lẻ
    if not order_code:
        for field in [transaction_content, description, content, sepay_code]:
            if field:
                clean_field = field.upper().replace(" ", "").replace("-", "").replace("\n", "")
                order_code, order = db.find_order_by_content(clean_field)
                if order_code:
                    logger.info(f"  → Found order {order_code} in field: {field[:50]}")
                    break

    if not order_code:
        logger.info(f"No matching order for payment {transaction_id}")
        db.mark_payment_processed(transaction_id)
        # Hiển thị TẤT CẢ các trường để admin debug
        detail = f"content: {content}\ntxContent: {transaction_content}\ndesc: {description}\ncode: {sepay_code}"
        await _notify_all_admins(application,
            f"⚠️ **TIỀN VÀO KHÔNG KHỚP ĐƠN**\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💰 Số tiền: {transfer_amount:,}đ\n"
            f"📝 {detail}\n"
            f"🔗 Ref: {reference_code}\n\n"
            f"_Có thể khách ghi sai nội dung CK_"
        )
        return

    # Kiểm tra trạng thái đơn
    # FIX: Phục hồi đơn cancelled_timeout khi tiền vào muộn
    recovering_timeout = order.get("status") == "cancelled_timeout"
    if order.get("status") not in ("pending", "failed", "cancelled_timeout"):
        logger.info(f"Payment for already-processed order {order_code} (status={order.get('status')})")
        db.mark_payment_processed(transaction_id)
        db.mark_transaction_processed(transaction_id)
        await _notify_all_admins(application,
            f"⚠️ **TIỀN VÀO CHO ĐƠN ĐÃ XỬ LÝ**\n"
            f"📋 Mã: `{order_code}` | Status: {order.get('status')}\n"
            f"💰 {transfer_amount:,}đ | Nội dung: {content}"
        )
        return

    # Kiểm tra số tiền
    expected = int(order.get("total", 0))
    if transfer_amount < expected:
        logger.warning(f"Amount mismatch for {order_code}: got {transfer_amount}, need {expected}")
        db.mark_payment_processed(transaction_id)
        db.mark_transaction_processed(transaction_id)
        await _notify_all_admins(application,
            f"⚠️ **THIẾU TIỀN — ĐƠN {order_code}**\n"
            f"Nhận: {transfer_amount:,}đ | Cần: {expected:,}đ\n"
            f"Chênh lệch: {expected - transfer_amount:,}đ"
        )
        return

    if recovering_timeout:
        logger.info(f"⚡ Recovering cancelled_timeout order {order_code} — late payment received!")
        wallet_paid = int(order.get("wallet_paid", 0) or 0)
        if wallet_paid > 0 and order.get("wallet_refunded"):
            if not db.restore_refunded_wallet_for_order(order_code):
                db.mark_payment_processed(transaction_id)
                db.mark_transaction_processed(transaction_id)
                await _notify_all_admins(application,
                    f"🚨 **ĐƠN TIMEOUT CÓ TIỀN VÀO — VÍ KHÔNG ĐỦ ĐỂ TRỪ LẠI**\n"
                    f"📋 Mã: `{order_code}`\n"
                    f"👤 User: {order['user_id']}\n"
                    f"💳 Cần trừ lại ví: {format_money(wallet_paid)}\n"
                    f"🏦 Tiền vừa vào: {transfer_amount:,}đ\n"
                    f"⚠️ Không giao tự động; cần xử lý tay."
                )
                return
            try:
                await application.bot.send_message(
                    chat_id=order["user_id"],
                    text=t(order["user_id"], "wallet_rededucted", amount=format_money(wallet_paid), order_code=order_code),
                )
            except Exception:
                pass
        db.update_order_fields(order_code, {"status": "pending"})
        order["status"] = "pending"
        await _notify_all_admins(application,
            f"⚡ **PHỤC HỒI ĐƠN TIMEOUT**\n"
            f"📋 Mã: `{order_code}`\n"
            f"💰 Tiền vào (chậm): {transfer_amount:,}đ\n"
            f"✅ Đang xử lý giao hàng..."
        )

    # ✅ Thanh toán hợp lệ — xử lý đơn
    logger.info(f"✅ Payment matched order {order_code} — processing!")
    
    result = await process_paid_order(application, order_code, "sepay")
    
    # CUỐI CÙNG mới mark processed — nếu crash trước đây, payment sẽ được retry
    db.mark_payment_processed(transaction_id)
    db.mark_transaction_processed(transaction_id)
    
    if result:
        logger.info(f"✅ Order {order_code} completed successfully!")
    else:
        logger.warning(f"❌ Order {order_code} processing returned False")


async def post_init(application):
    """Set bot commands + start webhook server + payment processor."""
    commands = [
        BotCommand("start", "Bắt đầu"),
        BotCommand("menu", "Xem sản phẩm & mua hàng"),
        BotCommand("myorders", "Lịch sử đơn hàng"),
        BotCommand("help", "Hướng dẫn sử dụng"),
    ]
    commands.append(BotCommand("language", "Đổi ngôn ngữ"))
    await application.bot.set_my_commands(commands)
    await application.bot.set_my_commands([
        BotCommand("start", "Start"),
        BotCommand("menu", "View products and buy"),
        BotCommand("myorders", "Order history"),
        BotCommand("help", "How to use"),
        BotCommand("language", "Change language"),
    ], language_code="en")

    # Cache bot username 1 lần duy nhất
    try:
        me = await application.bot.get_me()
        set_bot_username(me.username)
        logger.info(f"✅ Bot username cached: @{get_bot_username()}")
    except Exception as e:
        logger.error(f"❌ Failed to cache bot username: {e}")

    # === DIAGNOSTIC: Kiểm tra kết nối API khi khởi động ===
    logger.info(f"📂 Database path: {DB_PATH}")
    logger.info(f"📂 Database file exists: {os.path.exists(DB_PATH)}")

    # Pre-warm cache: gọi song song cả 2 API ngay khi boot
    # để /menu đầu tiên không phải chờ
    try:
        products, balance = _do_refresh_products()
        _api_cache.update({
            "data": (products, balance),
            "expiry": time.time() + API_CACHE_TTL,
            "stale_expiry": time.time() + API_STALE_TTL,
        })
        # Log kết quả
        api1_count = sum(1 for v in products.values() if v.get("api_source") == "CTV")
        custom_count = sum(1 for v in products.values() if v.get("is_custom_local"))
        logger.info(f"✅ Cache pre-warmed: {len(products)} products (API1: {api1_count}, Custom: {custom_count})")
    except Exception as e:
        logger.error(f"❌ Cache pre-warm failed: {e}")

    # === Auto-backup database khi khởi động ===
    _backup_database()

    # Recover đơn kẹt ở 'processing' từ crash cũ
    db.recover_stuck_orders()

    # A claimed Binance txid must still be fulfilled after a process restart.
    await recover_confirmed_crypto_orders(application)
    await recover_confirmed_wallet_orders(application)

    # Dọn dẹp đơn pending cũ từ lần chạy trước
    await _cleanup_stale_orders(application)

    # Archive đơn cũ > 7 ngày → giữ DB nhẹ
    archived = db.cleanup_old_orders(days=7)
    if archived:
        logger.info(f"🗑️ Archived {archived} old orders at startup")

    # Job định kỳ hủy đơn quá hạn
    asyncio.create_task(_periodic_order_cleanup(application))

    # 🔄 Background product refresh — giữ cache luôn tươi
    asyncio.create_task(_periodic_product_refresh())

    # 💳 Payment processor — poll DB mỗi 5 giây để xử lý thanh toán
    asyncio.create_task(_payment_processor(application))

    # ₮ Binance poller is independent from the SePay VND processor.
    if CRYPTO_ENABLED:
        asyncio.create_task(poll_binance_deposits(application))
        asyncio.create_task(poll_binance_pay(application))

    # 🔄 Retry failed orders — tự động retry đơn lỗi API tạm thời
    asyncio.create_task(_retry_failed_orders(application))

    # Webhook server — CHỈ lưu giao dịch vào DB, không cần event loop hay telegram app
    webhook_thread = Thread(
        target=start_webhook_server,
        args=(WEBHOOK_PORT,),
        kwargs={"bot_db": db},
        daemon=True
    )
    webhook_thread.start()
    logger.info(f"SePay webhook server started on port {WEBHOOK_PORT}")


def _backup_database():
    """Tự động backup database mỗi lần khởi động."""
    import shutil
    if os.path.exists(DB_PATH):
        backup_dir = os.path.join(DATA_DIR, "backups")
        os.makedirs(backup_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = os.path.join(backup_dir, f"bot_data_{timestamp}.json")
        shutil.copy2(DB_PATH, backup_path)
        logger.info(f"💾 Database backed up to: {backup_path}")
        
        # Giữ tối đa 20 bản backup gần nhất
        backups = sorted(
            [f for f in os.listdir(backup_dir) if f.startswith("bot_data_")],
            reverse=True
        )
        for old in backups[20:]:
            try:
                os.remove(os.path.join(backup_dir, old))
            except Exception:
                pass
