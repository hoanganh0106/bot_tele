"""Customer-facing Vietnamese/English strings for the Telegram bot.

Keep this module independent from the bot and database to avoid circular imports.
"""

DEFAULT_LANG = "vi"
LANGS = ("vi", "en")

TEXTS = {
    "vi": {
        "language_prompt": "🌐 **Chọn ngôn ngữ / Choose language**",
        "language_updated": "✅ Đã chuyển ngôn ngữ sang Tiếng Việt.",
        "btn_menu": "🛍️ MENU SẢN PHẨM", "btn_wallet": "💳 Ví", "btn_referral": "🎁 Giới thiệu",
        "btn_history": "📋 Lịch sử", "btn_contact": "☎️ Liên hệ Admin", "btn_reload": "🔄 Cập nhật",
        "btn_back": "⬅️ Quay lại", "btn_home": "🏠 Trang chủ", "btn_language": "🌐 Ngôn ngữ / Language",
        "btn_paid": "✅ Đã chuyển khoản", "btn_cancel": "⬅️ Hủy đơn & Quay lại",
        "btn_pay_wallet": "💰 Thanh toán bằng ví ({amount})", "btn_pay_partial": "💰 Ví {balance} + CK {amount}",
        "btn_pay_bank": "💳 Chuyển khoản {amount}", "welcome": "✨ Xin chào <b>{name}</b>! ✨\n\n🏪 <b>SHOP TÀI KHOẢN PREMIUM</b>\n\n<blockquote>⚡ Thanh toán → Xác nhận <b>1 phút</b>\n📦 Nhận tài khoản <b>ngay lập tức</b>\n💬 Hỗ trợ <b>nhanh chóng</b>\n🤖 Tự động <b>24/7</b></blockquote>\n\n💰 <b>Số dư ví:</b> {balance}\n\n👇 <i>Chọn chức năng bên dưới</i> 👇",
        "welcome_bonus": "\n🎁 <b>Quà chào mừng: +{amount}</b> đã cộng vào ví!\n",
        "help": "📖 **HƯỚNG DẪN SỬ DỤNG**\n\n1️⃣ Gõ /menu để xem danh sách sản phẩm\n2️⃣ Chọn sản phẩm muốn mua\n3️⃣ Chọn số lượng\n4️⃣ Bot tạo mã QR thanh toán\n5️⃣ Chuyển khoản đúng nội dung\n6️⃣ Hệ thống tự xác nhận & gửi tài khoản\n\n⏰ Thanh toán được xác nhận tự động trong 1-3 phút\n❓ Cần hỗ trợ? Liên hệ admin",
        "loading_products": "⏳ Đang tải sản phẩm...", "products_unavailable": "❌ Không thể tải sản phẩm lúc này. Vui lòng thử lại sau!",
        "menu_title": "🛍️ <b>MENU SẢN PHẨM</b>\n════════════════════\n\n💰 Số dư ví: <b>{balance}</b>\n\n👇 <i>Chọn danh mục sản phẩm</i>:",
        "product_missing": "❌ Sản phẩm không tồn tại hoặc server lỗi!", "product_out_of_stock": "❌ **{name}** hiện đã hết hàng!\nVui lòng quay lại sau.",
        "product_updating": "🔄 **{name}** đang cập nhật kho.\nVui lòng thử lại sau 1-2 phút.",
        "product_detail": "{icon} <b>{name}</b>\n\n💰 Giá: <b>{price}</b> / cái\n📦 Kho: <b>{stock}</b> còn lại\n{description}{note}\n👇 Chọn số lượng muốn mua:",
        "product_auto_delivery": "\n⚡ <i>Nhận tự động sau thanh toán</i>\n", "product_email_note": "\n⚠️ <i>Sản phẩm này cần cung cấp email sau khi thanh toán</i>",
        "order_payment": "🛒 <b>ĐƠN HÀNG #{order_code}</b>\n\n<blockquote>📦 {product}\n🔢 Số lượng: <b>{qty}</b>\n💰 Đơn giá: <b>{price}</b>\n💵 Tổng: <u><b>{total}</b></u></blockquote>\n\n{wallet}🔽 <b>Chọn phương thức thanh toán:</b>\n",
        "wallet_balance": "💰 Số dư ví: <b>{balance}</b>\n\n", "order_invalid": "❌ Đơn hàng không tồn tại hoặc đã được xử lý.",
        "bank_payment": "🛒 <b>ĐƠN HÀNG #{order_code}</b>\n\n📦 {product}\n💵 Tổng: <u><b>{total}</b></u>\n\n<blockquote>🏦 Ngân hàng: <b>{bank}</b>\n💳 STK: <code>{account}</code>\n👤 Tên: <b>{account_name}</b>\n💰 Số tiền: <b>{total}</b>\n📝 Nội dung: <code>{order_code}</code></blockquote>\n\n📱 Quét QR bên dưới để thanh toán nhanh:\n<a href=\"{qr_url}\">QR Thanh toán</a>\n\n⏰ Đơn hàng tự hủy sau <b>5 phút</b>\n✅ Thanh toán sẽ được xác nhận <b>TỰ ĐỘNG</b>",
        "paid_waiting": "⏳ Đơn **#{order_code}** đang chờ xác nhận thanh toán.\n\nHệ thống sẽ tự động xác nhận trong **1-3 phút** sau khi nhận được tiền.\nBạn sẽ nhận được thông báo ngay khi hoàn tất! 🔔",
        "wallet_paid": "✅ Đã thanh toán **{amount}** từ ví!\n💰 Số dư còn lại: **{balance}**\n\n⏳ Đang xử lý đơn hàng **#{order_code}**...",
        "customer_order_error": "⚠️ Đơn **#{order_code}** gặp lỗi trong quá trình xử lý.\n\n✅ Thanh toán của bạn **đã được ghi nhận** — Admin đã nhận thông báo và sẽ giao hàng hoặc hoàn tiền sớm nhất.\n\n🚫 **Vui lòng KHÔNG chuyển khoản lại lần nữa.**\n💬 Cần hỗ trợ nhanh, hãy liên hệ admin kèm mã đơn `#{order_code}`.",
        "deposit_success": "✅ **NẠP TIỀN THÀNH CÔNG!**\n──────────────────\n💰 Số tiền: **+{amount}**\n💵 Số dư mới: **{balance}**\n\nBạn có thể dùng ví để mua sản phẩm ngay! Gõ /menu",
        "command_start": "Bắt đầu", "command_menu": "Xem sản phẩm và mua hàng", "command_orders": "Lịch sử đơn hàng", "command_help": "Hướng dẫn sử dụng", "command_language": "Đổi ngôn ngữ",
    },
    "en": {
        "language_prompt": "🌐 **Choose language / Chọn ngôn ngữ**", "language_updated": "✅ Language changed to English.",
        "btn_menu": "🛍️ PRODUCT MENU", "btn_wallet": "💳 Wallet", "btn_referral": "🎁 Referral", "btn_history": "📋 Order history", "btn_contact": "☎️ Contact admin", "btn_reload": "🔄 Refresh", "btn_back": "⬅️ Back", "btn_home": "🏠 Home", "btn_language": "🌐 Language / Ngôn ngữ", "btn_paid": "✅ I have transferred", "btn_cancel": "⬅️ Cancel order & go back", "btn_pay_wallet": "💰 Pay with wallet ({amount})", "btn_pay_partial": "💰 Wallet {balance} + bank {amount}", "btn_pay_bank": "💳 Bank transfer {amount}",
        "welcome": "✨ Welcome <b>{name}</b>! ✨\n\n🏪 <b>PREMIUM ACCOUNT SHOP</b>\n\n<blockquote>⚡ Payment confirmed in <b>1 minute</b>\n📦 Receive your account <b>instantly</b>\n💬 <b>Fast</b> support\n🤖 Automated <b>24/7</b></blockquote>\n\n💰 <b>Wallet balance:</b> {balance}\n\n👇 <i>Choose an option below</i> 👇", "welcome_bonus": "\n🎁 <b>Welcome bonus: +{amount}</b> has been added to your wallet!\n", "help": "📖 **HOW TO USE**\n\n1️⃣ Send /menu to view products\n2️⃣ Choose a product\n3️⃣ Choose quantity\n4️⃣ The bot creates a payment QR code\n5️⃣ Transfer with the exact content\n6️⃣ The system confirms and sends your account automatically\n\n⏰ Payments are confirmed automatically within 1–3 minutes\n❓ Need help? Contact admin", "loading_products": "⏳ Loading products...", "products_unavailable": "❌ Products cannot be loaded right now. Please try again later!", "menu_title": "🛍️ <b>PRODUCT MENU</b>\n════════════════════\n\n💰 Wallet balance: <b>{balance}</b>\n\n👇 <i>Choose a product category</i>:", "product_missing": "❌ The product does not exist or the server has an error!", "product_out_of_stock": "❌ **{name}** is out of stock!\nPlease come back later.", "product_updating": "🔄 **{name}** inventory is being updated.\nPlease try again in 1–2 minutes.", "product_detail": "{icon} <b>{name}</b>\n\n💰 Price: <b>{price}</b> each\n📦 Stock: <b>{stock}</b> remaining\n{description}{note}\n👇 Choose quantity:", "product_auto_delivery": "\n⚡ <i>Automatic delivery after payment</i>\n", "product_email_note": "\n⚠️ <i>This product requires your email after payment</i>", "order_payment": "🛒 <b>ORDER #{order_code}</b>\n\n<blockquote>📦 {product}\n🔢 Quantity: <b>{qty}</b>\n💰 Unit price: <b>{price}</b>\n💵 Total: <u><b>{total}</b></u></blockquote>\n\n{wallet}🔽 <b>Choose a payment method:</b>\n", "wallet_balance": "💰 Wallet balance: <b>{balance}</b>\n\n", "order_invalid": "❌ This order does not exist or has already been processed.", "bank_payment": "🛒 <b>ORDER #{order_code}</b>\n\n📦 {product}\n💵 Total: <u><b>{total}</b></u>\n\n<blockquote>🏦 Bank: <b>{bank}</b>\n💳 Account: <code>{account}</code>\n👤 Name: <b>{account_name}</b>\n💰 Amount: <b>{total}</b>\n📝 Content: <code>{order_code}</code></blockquote>\n\n📱 Scan the QR code below for quick payment:\n<a href=\"{qr_url}\">Payment QR</a>\n\n⏰ This order automatically expires after <b>5 minutes</b>\n✅ Payment will be confirmed <b>AUTOMATICALLY</b>", "paid_waiting": "⏳ Order **#{order_code}** is waiting for payment confirmation.\n\nThe system will confirm automatically within **1–3 minutes** after receiving the payment.\nYou will be notified as soon as it is completed! 🔔", "wallet_paid": "✅ **{amount}** paid from your wallet!\n💰 Remaining balance: **{balance}**\n\n⏳ Processing order **#{order_code}**...", "customer_order_error": "⚠️ Order **#{order_code}** encountered an error while being processed.\n\n✅ Your payment **has been recorded** — the admin has been notified and will deliver your item or issue a refund soon.\n\n🚫 **Please DO NOT transfer again.**\n💬 For quick support, contact the admin with order code `#{order_code}`.", "deposit_success": "✅ **DEPOSIT SUCCESSFUL!**\n──────────────────\n💰 Amount: **+{amount}**\n💵 New balance: **{balance}**\n\nYou can now use your wallet to buy products! Send /menu", "command_start": "Start", "command_menu": "View products and buy", "command_orders": "Order history", "command_help": "How to use", "command_language": "Change language",
    },
}


def get_text(lang: str, key: str, **kwargs) -> str:
    """Return text with safe EN -> VI -> key fallback."""
    language = lang if lang in LANGS else DEFAULT_LANG
    text = TEXTS.get(language, {}).get(key, TEXTS[DEFAULT_LANG].get(key, key))
    try:
        return text.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        return text
