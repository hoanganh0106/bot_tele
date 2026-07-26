"""Telegram bot entrypoint and handler wiring."""

import asyncio
import os

from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from core.config import BOT_TOKEN, CTV_API_KEY, DATA_DIR, logger
from core.runtime import db
from handlers.admin import (
    cmd_admin,
    cmd_getemoji,
    cmd_setrate,
    handle_admin_cancel,
    handle_admin_cb,
    handle_admin_confirm_pay,
)
from handlers.customer import (
    cmd_help,
    cmd_language,
    cmd_menu,
    cmd_myorders,
    cmd_start,
    handle_back_menu,
    handle_back_start,
    handle_category_click,
    handle_deposit_start,
    handle_noop,
    handle_product_select,
    handle_qty_select,
    handle_referral_home,
    handle_set_language,
    handle_wallet_home,
)
from handlers.payment import (
    handle_cancel_order,
    handle_paid_button,
    handle_pay_bank,
    handle_pay_crypto,
    handle_pay_partial,
    handle_pay_wallet,
)
from handlers.text_input import handle_media_input, handle_text_input
from jobs import post_init


def main():
    """Start bot."""
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
        logger.critical("❌ Chưa điền TELEGRAM_BOT_TOKEN trong config.env!")
        return

    if not CTV_API_KEY or CTV_API_KEY == "DLR_YOUR_API_KEY_HERE":
        logger.critical("❌ Chưa điền CTV_API_KEY trong config.env!")
        return

    # Tạo thư mục data (DATA_DIR đã được tạo ở trên)
    os.makedirs(DATA_DIR, exist_ok=True)

    # Migration: xóa custom_prices cũ (giá tuyệt đối) → dùng price_deltas (chênh lệch) thay thế
    cleared = db.clear_all_custom_prices()
    if cleared:
        logger.info(f"🔄 Đã xóa {cleared} custom_prices cũ. Admin cần set lại giá nếu muốn.")

    # Build bot
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("language", cmd_language))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("myorders", cmd_myorders))

    # Admin commands
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(CommandHandler("getemoji", cmd_getemoji))
    app.add_handler(CommandHandler("setrate", cmd_setrate))


    # Callback handlers
    app.add_handler(CallbackQueryHandler(handle_noop, pattern="^noop$"))
    app.add_handler(CallbackQueryHandler(handle_set_language, pattern="^setlang_"))
    app.add_handler(CallbackQueryHandler(cmd_language, pattern="^language(?:_from_(?:home|menu|wallet|referral))?$"))
    app.add_handler(CallbackQueryHandler(handle_product_select, pattern="^prod_"))
    app.add_handler(CallbackQueryHandler(handle_qty_select, pattern="^qty_"))
    app.add_handler(CallbackQueryHandler(handle_paid_button, pattern="^paid_"))
    app.add_handler(CallbackQueryHandler(handle_pay_bank, pattern="^paybank_"))
    app.add_handler(CallbackQueryHandler(handle_pay_crypto, pattern="^paycrypto_"))
    app.add_handler(CallbackQueryHandler(handle_pay_wallet, pattern="^paywallet_"))
    app.add_handler(CallbackQueryHandler(handle_pay_partial, pattern="^paypartial_"))
    app.add_handler(CallbackQueryHandler(handle_cancel_order, pattern="^cancel_"))
    app.add_handler(CallbackQueryHandler(handle_back_menu, pattern="^back_menu$"))
    app.add_handler(CallbackQueryHandler(handle_back_start, pattern="^back_start$"))
    app.add_handler(CallbackQueryHandler(handle_wallet_home, pattern="^wallet_home$"))
    app.add_handler(CallbackQueryHandler(handle_deposit_start, pattern="^deposit_start$"))
    app.add_handler(CallbackQueryHandler(handle_referral_home, pattern="^referral_home$"))
    app.add_handler(CallbackQueryHandler(handle_admin_confirm_pay, pattern="^adminpay_"))
    app.add_handler(CallbackQueryHandler(handle_admin_cancel, pattern="^admincx_"))
    app.add_handler(CallbackQueryHandler(handle_admin_cb, pattern="^(admin_|broadcast_)"))
    app.add_handler(CallbackQueryHandler(handle_category_click, pattern="^viewcat_|^(?:open|reload)_menu$|^btn_myorders$"))

    # Text input handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_input))
    app.add_handler(MessageHandler(
        (filters.PHOTO | filters.VIDEO | filters.ANIMATION | filters.Document.ALL) & ~filters.COMMAND,
        handle_media_input
    ))

    # Run bot
    logger.info("🤖 Bot started!")
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    app.run_polling(drop_pending_updates=True)

    # Flush pending DB writes khi bot tắt
    logger.info("💾 Flushing database before shutdown...")
    db.flush()


if __name__ == "__main__":
    main()
