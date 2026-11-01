//! High-performance, memory-safe localization engine for telegram-bot-rust.

pub mod keys;

use dashmap::DashMap;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fmt;
use std::str::FromStr;
use std::sync::LazyLock;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum Language {
    #[default]
    Vi,
    En,
}

impl Language {
    pub const fn as_str(&self) -> &'static str {
        match self {
            Language::Vi => "vi",
            Language::En => "en",
        }
    }
}

impl fmt::Display for Language {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.as_str())
    }
}

impl FromStr for Language {
    type Err = std::convert::Infallible;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s.trim().to_lowercase().as_str() {
            "en" | "eng" | "english" => Ok(Language::En),
            _ => Ok(Language::Vi),
        }
    }
}

pub const DEFAULT_LANG: Language = Language::Vi;

// --- Static Dictionary Lookup Tables ---
static TEXTS_VI: LazyLock<HashMap<&'static str, &'static str>> = LazyLock::new(|| {
    let mut m = HashMap::with_capacity(100);
    m.insert(keys::PRODUCT_NOT_FOR_SALE, "Sản phẩm này hiện không còn bán.");
    m.insert(keys::ORDER_CANCELED_PRODUCT_HIDDEN, "Sản phẩm này hiện không còn bán. Đơn hàng đã được hủy.");
    m.insert(keys::ORDER_REFUNDED_PRODUCT_HIDDEN, "Sản phẩm đã ngừng bán. {amount} đã được hoàn vào ví của bạn.");
    m.insert(keys::NEED_EMAIL, "✅ Đơn **#{order_code}** đã thanh toán thành công!\n\n📧 Sản phẩm này yêu cầu bạn cung cấp thông tin/email.\nVui lòng nhắn tin gửi **{qty} email** (mỗi email viết trên 1 dòng):\n\nVí dụ:\n```\nemail1@gmail.com\nemail2@gmail.com\n```");
    m.insert(keys::DELIVERY_DELAYED, "✅ Đơn **#{order_code}** thanh toán thành công!\n\nTuy nhiên kho hàng tự động vừa hết đột xuất. Vui lòng chờ Admin xử lý giao tài khoản bù cho bạn trong chốc lát nhé!");
    m.insert(keys::DELIVERY_COMPLETE, "🎉 **ĐƠN HÀNG #{order_code} HOÀN TẤT!**\n\n🔑 **TÀI KHOẢN CỦA BẠN:**\n{items}\n{description}\n💬 _Cảm ơn bạn! Cần hỗ trợ xin liên hệ Admin._");
    m.insert(keys::MANUAL_PROCESSING, "✅ **GỬI THÔNG TIN THÀNH CÔNG!**\nĐơn hàng **#{order_code}** của bạn đang được chuyển đến hệ thống.\n\n⏳ Admin đang tiến hành duyệt và xử lý cấp quyền cho bạn.\nVui lòng đợi một lát nhé! (Cần hỗ trợ gấp: nhắn mục *Liên Hệ Admin*)");
    m.insert(keys::API_DELIVERY_COMPLETE, "🎉 **ĐƠN HÀNG #{order_code} THÀNH CÔNG!**\n\n📦 {product}\n🔢 Số lượng: {qty}\n\n🔑 **TÀI KHOẢN CỦA BẠN:**\n{items}\n{description}\n⚠️ _Vui lòng lưu lại thông tin ngay!_");
    m.insert(keys::REFERRAL_PAGE, "🎁 <b>TRUNG TÂM GIỚI THIỆU</b>\n━━━━━━━━━━━━━━━━━━\n\n👤 <b>THỐNG KÊ CỦA BẠN</b>\n<blockquote>👥 Đã giới thiệu: <b>{count}</b> người\n💵 Tổng thưởng: <b>{earnings}</b>\n📡 Chương trình: {status}</blockquote>\n\n💰 <b>MỨC THƯỞNG</b>\n<blockquote>• Bạn nhận: <b>{reward}/người</b>\n{friend_reward}</blockquote>\n\n🔗 <b>LINK GIỚI THIỆU</b>\n<code>{link}</code>\n\n💡 <i>Bấm Chia sẻ để gửi link trực tiếp cho bạn bè.</i>");
    m.insert(keys::REFERRAL_CREDITED, "🎉 **THƯỞNG GIỚI THIỆU!**\n\n👤 **{name}** đã tham gia qua link mời của bạn!\n\n💰 Bạn nhận được: **+{reward}**\n💵 Số dư ví hiện tại: **{balance}**");
    m.insert(keys::PARTIAL_PAYMENT, "✅ Đã trừ **{wallet_amount}** từ ví!\n💰 Số dư còn lại: **{balance}**\n\n🏦 **CHUYỂN KHOẢN PHẦN CÒN LẠI:**\n\n🏦 Ngân hàng: **{bank}**\n💳 STK: `{account}`\n👤 Tên: **{account_name}**\n💰 Số tiền: **{remain}**\n📝 Nội dung: `{order_code}`\n\n📱 Quét QR:\n[QR Thanh toán]({qr_url})\n\n⏰ Đơn tự hủy sau **5 phút** (hoàn tiền ví nếu hủy)\n✅ Hệ thống tự xác nhận sau khi nhận được CK");
    m.insert(keys::LANGUAGE_PROMPT, "🌐 **Chọn ngôn ngữ / Choose language**");
    m.insert(keys::LANGUAGE_UPDATED, "✅ Đã chuyển ngôn ngữ sang Tiếng Việt.");
    m.insert(keys::BTN_MENU, "🛍️ MENU SẢN PHẨM");
    m.insert(keys::BTN_WALLET, "💳 Ví");
    m.insert(keys::BTN_REFERRAL, "🎁 Giới thiệu");
    m.insert(keys::BTN_HISTORY, "📋 Lịch sử");
    m.insert(keys::BTN_CONTACT, "☎️ Liên hệ Admin");
    m.insert(keys::BTN_RELOAD, "🔄 Cập nhật");
    m.insert(keys::BTN_BACK, "⬅️ Quay lại");
    m.insert(keys::BTN_HOME, "🏠 Trang chủ");
    m.insert(keys::BTN_LANGUAGE, "🌐 Ngôn ngữ / Language");
    m.insert(keys::BTN_PAID, "✅ Đã chuyển");
    m.insert(keys::BTN_CANCEL, "❌ Hủy đơn");
    m.insert(keys::BTN_PAY_WALLET, "💰 Thanh toán bằng ví ({amount})");
    m.insert(keys::BTN_PAY_PARTIAL, "💰 Ví {balance} + CK {amount}");
    m.insert(keys::BTN_PAY_BANK, "💳 Chuyển khoản {amount}");
    m.insert(keys::BTN_PAY_CRYPTO, "₮ USDT Crypto ({amount})");
    m.insert(keys::CRYPTO_WARNING, "⚠️ Chỉ gửi USDT qua đúng mạng {network}. Sai mạng có thể mất tiền; sai số tiền sẽ không được xác nhận tự động.");
    m.insert(keys::CRYPTO_PAYMENT, "₮ <b>THANH TOÁN USDT</b>\n\n<blockquote>🌐 Mạng: <b>{network}</b>\n📬 Địa chỉ: <code>{address}</code>\n💰 Số tiền chính xác: <code>{amount}</code> USDT</blockquote>\n\n<b>Hãy chuyển CHÍNH XÁC số tiền trên.</b>\n\n{warning}\n\n📱 <a href=\"{qr_url}\">Mở mã QR ví</a>\n⏰ Đơn crypto tự hủy sau <b>{timeout_minutes} phút</b>.");
    m.insert(keys::CRYPTO_PAYMENT_CAPTION, "₮ <b>THANH TOÁN USDT</b>\n\n<blockquote>🌐 Mạng: <b>{network}</b>\n📬 Địa chỉ: <code>{address}</code>\n💰 Số tiền chính xác: <code>{amount}</code> USDT</blockquote>\n\n<b>Quét mã QR và chuyển CHÍNH XÁC số tiền trên.</b>\n\n{warning}\n\n⏰ Đơn crypto tự hủy sau <b>{timeout_minutes} phút</b>.");
    m.insert(keys::CRYPTO_PAID_WAITING, "⏳ Đang chờ Binance xác nhận giao dịch blockchain. Quá trình này thường mất 1–5 phút. Bot sẽ tự động giao hàng khi tiền vào.");
    m.insert(keys::CRYPTO_UNAVAILABLE, "❌ Thanh toán USDT hiện chưa khả dụng. Vui lòng chọn chuyển khoản ngân hàng.");
    m.insert(keys::CRYPTO_RESERVATION_FAILED, "❌ Chưa thể tạo số tiền USDT độc nhất. Vui lòng hủy đơn và thử lại.");
    m.insert(keys::WALLET_REDEDUCTED, "💰 Khoản chuyển khoản đến sau khi đơn hết hạn. Hệ thống đã trừ lại {amount} từ ví và tiếp tục xử lý đơn #{order_code}.");
    m.insert(keys::ADMIN_UNLOCKED, "🔑 Xin chào Admin, bảng Quản trị đã được mở khóa!");
    m.insert(keys::WELCOME, "✨ Xin chào <b>{name}</b>! ✨\n\n🏪 <b>SHOP TÀI KHOẢN PREMIUM</b>\n\n<blockquote>⚡ Thanh toán → Xác nhận <b>1 phút</b>\n📦 Nhận tài khoản <b>ngay lập tức</b>\n💬 Hỗ trợ <b>nhanh chóng</b>\n🤖 Tự động <b>24/7</b></blockquote>\n\n💰 <b>Số dư ví:</b> {balance}\n\n👇 <i>Chọn chức năng bên dưới</i> 👇");
    m.insert(keys::WELCOME_BONUS, "\n🎁 <b>Quà chào mừng: +{amount}</b> đã cộng vào ví!\n");
    m.insert(keys::HELP, "📖 **HƯỚNG DẪN SỬ DỤNG**\n\n1️⃣ Gõ /menu để xem danh sách sản phẩm\n2️⃣ Chọn sản phẩm muốn mua\n3️⃣ Chọn số lượng\n4️⃣ Bot tạo mã QR thanh toán\n5️⃣ Chuyển khoản đúng nội dung\n6️⃣ Hệ thống tự xác nhận & gửi tài khoản\n\n⏰ Thanh toán được xác nhận tự động trong 1-3 phút\n❓ Cần hỗ trợ? Liên hệ admin");
    m.insert(keys::LOADING_PRODUCTS, "⏳ Đang tải sản phẩm...");
    m.insert(keys::PRODUCTS_UNAVAILABLE, "❌ Không thể tải sản phẩm lúc này. Vui lòng thử lại sau!");
    m.insert(keys::MENU_TITLE, "🛍️ <b>MENU SẢN PHẨM</b>\n════════════════════\n\n💰 Số dư ví: <b>{balance}</b>\n\n👇 <i>Chọn danh mục sản phẩm</i>:");
    m.insert(keys::PRODUCT_MISSING, "❌ Sản phẩm không tồn tại hoặc server lỗi!");
    m.insert(keys::PRODUCT_OUT_OF_STOCK, "❌ **{name}** hiện đã hết hàng!\nVui lòng quay lại sau.");
    m.insert(keys::PRODUCT_UPDATING, "🔄 **{name}** đang cập nhật kho.\nVui lòng thử lại sau 1-2 phút.");
    m.insert(keys::PRODUCT_DETAIL, "{icon} <b>{name}</b>\n\n💰 Giá: <b>{price}</b> / cái\n📦 Kho: <b>{stock}</b> còn lại\n{description}{note}\n👇 Chọn số lượng muốn mua:");
    m.insert(keys::PRODUCT_AUTO_DELIVERY, "\n⚡ <i>Nhận tự động sau thanh toán</i>\n");
    m.insert(keys::PRODUCT_EMAIL_NOTE, "\n⚠️ <i>Sản phẩm này cần cung cấp email sau khi thanh toán</i>");
    m.insert(keys::ORDER_PAYMENT, "🛒 <b>ĐƠN HÀNG #{order_code}</b>\n\n<blockquote>📦 {product}\n🔢 Số lượng: <b>{qty}</b>\n💰 Đơn giá: <b>{price}</b>\n💵 Tổng: <u><b>{total}</b></u></blockquote>\n\n{wallet}🔽 <b>Chọn phương thức thanh toán:</b>\n");
    m.insert(keys::WALLET_BALANCE, "💰 Số dư ví: <b>{balance}</b>\n\n");
    m.insert(keys::ORDER_INVALID, "❌ Đơn hàng không tồn tại hoặc đã được xử lý.");
    m.insert(keys::BANK_PAYMENT, "🛒 <b>ĐƠN HÀNG #{order_code}</b>\n\n📦 {product}\n💵 Tổng: <u><b>{total}</b></u>\n\n<blockquote>🏦 Ngân hàng: <b>{bank}</b>\n💳 STK: <code>{account}</code>\n👤 Tên: <b>{account_name}</b>\n💰 Số tiền: <b>{total}</b>\n📝 Nội dung: <code>{order_code}</code></blockquote>\n\n📱 Quét QR bên dưới để thanh toán nhanh:\n<a href=\"{qr_url}\">QR Thanh toán</a>\n\n⏰ Đơn hàng tự hủy sau <b>5 phút</b>\n✅ Thanh toán sẽ được xác nhận <b>TỰ ĐỘNG</b>");
    m.insert(keys::PAID_WAITING, "⏳ Đơn **#{order_code}** đang chờ xác nhận thanh toán.\n\nHệ thống sẽ tự động xác nhận trong **1-3 phút** sau khi nhận được tiền.\nBạn sẽ nhận được thông báo ngay khi hoàn tất! 🔔");
    m.insert(keys::WALLET_PAID, "✅ Đã thanh toán **{amount}** từ ví!\n💰 Số dư còn lại: **{balance}**\n\n⏳ Đang xử lý đơn hàng **#{order_code}**...");
    m.insert(keys::CUSTOMER_ORDER_ERROR, "⚠️ Đơn **#{order_code}** gặp lỗi trong quá trình xử lý.\n\n✅ Thanh toán của bạn **đã được ghi nhận** — Admin đã nhận thông báo và sẽ giao hàng hoặc hoàn tiền sớm nhất.\n\n🚫 **Vui lòng KHÔNG chuyển khoản lại lần nữa.**\n💬 Cần hỗ trợ nhanh, hãy liên hệ admin kèm mã đơn `#{order_code}`.");
    m.insert(keys::DEPOSIT_SUCCESS, "✅ **NẠP TIỀN THÀNH CÔNG!**\n──────────────────\n💰 Số tiền: **+{amount}**\n💵 Số dư mới: **{balance}**\n\nBạn có thể dùng ví để mua sản phẩm ngay! Gõ /menu");
    m.insert(keys::SESSION_ERROR, "❌ Lỗi phiên. Vui lòng /menu lại.");
    m.insert(keys::STOCK_ERROR, "❌ **{name}** hiện đã hết hàng!\nVui lòng quay lại sau.");
    m.insert(keys::ORDER_TIMEOUT, "⏰ Đơn hàng **#{order_code}** đã tự động hủy do quá thời gian thanh toán.{refund}");
    m.insert(keys::REFUND, "\n💰 Đã hoàn {amount} vào ví (Số dư: {balance})");
    m.insert(keys::WALLET_INSUFFICIENT, "❌ Số dư ví không đủ ({balance}).\nCần: {amount}");
    m.insert(keys::PARTIAL_NO_BALANCE, "❌ Số dư ví = 0. Vui lòng chuyển khoản toàn bộ.");
    m.insert(keys::ORDER_CANCELLED, "❌ Đơn **#{order_code}** đã được hủy.{refund}");
    m.insert(keys::ORDER_ALREADY_PROCESSED, "Đơn hàng này đã được xử lý hoặc không tồn tại.");
    m.insert(keys::NO_ORDERS, "📭 Bạn chưa có đơn hàng nào.");
    m.insert(keys::ORDERS_TITLE, "📋 **LỊCH SỬ ĐƠN HÀNG** (10 gần nhất)\n\n");
    m.insert(keys::WALLET_HOME, "💳 <b>VÍ CỦA BẠN</b>\n\n💵 <b>Số dư:</b> <u>{balance}</u>\n\n<blockquote>💳 Đã nạp: {deposited}\n🛒 Đã chi: {spent}\n🎁 Thưởng giới thiệu: {earnings}</blockquote>\n\n💡 <i>Dùng số dư ví để mua hàng không cần CK.</i>");
    m.insert(keys::DEPOSIT_PAGE, "💳 <b>NẠP TIỀN VÀO VÍ</b>\n\n<blockquote>🏦 Ngân hàng: <b>{bank}</b>\n💳 Số TK: <code>{account}</code>\n👤 Tên: <b>{account_name}</b>\n📝 Nội dung: <code>{code}</code></blockquote>\n\n💰 Số tiền: <b>Tùy ý</b> (tối thiểu {minimum})\n\n📱 Quét QR bên dưới:\n<a href=\"{qr_url}\">QR Nạp tiền</a>\n\n✅ Tiền sẽ <b>tự động cộng</b> vào ví sau 1-2 phút.\n🔔 Bạn sẽ nhận thông báo khi nạp thành công!");
    m.insert(keys::REF_ACTIVE, "✅ Đang hoạt động");
    m.insert(keys::REF_PAUSED, "⏸️ Tạm dừng");
    m.insert(keys::REF_FRIEND_REWARD, "🎁 Bạn bè nhận: <b>{amount}</b>\n");
    m.insert(keys::REF_SHARE, "Mua tài khoản Premium giá rẻ, tự động 24/7!");
    m.insert(keys::BTN_DEPOSIT, "💳 Nạp tiền vào ví");
    m.insert(keys::BTN_INVITE, "🎁 Giới thiệu bạn bè");
    m.insert(keys::BTN_BUY, "🛒 Mua sản phẩm");
    m.insert(keys::BTN_BALANCE, "💰 Xem số dư");
    m.insert(keys::BTN_SHARE, "📤 Chia sẻ cho bạn bè");
    m.insert(keys::BTN_VIEW_PRODUCT, "⬅️ Xem lại sản phẩm");
    m.insert(keys::BTN_CATEGORY, "🛍️ Danh mục");
    m.insert(keys::BTN_EXIT, "🏠 Thoát");
    m.insert(keys::CATEGORY_OTHER, "Khác");
    m.insert(keys::CATEGORY_PRODUCTS, "Sản phẩm");
    m.insert(keys::CATEGORY_PANEL, "{icon} <b>{name}</b>\n\n<i>💰 Giá  │  ✅ Còn hàng  │  ❌ Hết  │  🔄 Đang cập nhật</i>");
    m.insert(keys::BTN_BACK_CATEGORIES, "⬅️ Quay lại danh mục");
    m.insert(keys::ORDER_EXPIRY, "\n⏰ Đơn hàng tự hủy sau <b>5 phút</b>");
    m.insert(keys::ADMIN_CANCELLED_ORDER, "❌ Đơn hàng **#{order_code}** đã bị hủy bởi admin.");
    m.insert(keys::ADMIN_WALLET_ADDED, "💰 **Admin đã cộng {amount} vào ví của bạn!**\n💵 Số dư mới: **{balance}**");
    m.insert(keys::ADMIN_WALLET_DEDUCTED, "💰 **Admin đã trừ {amount} từ ví của bạn.**\n💵 Số dư mới: **{balance}**");
    m.insert(keys::EMAIL_COUNT_ERROR, "❌ Cần đúng **{required}** email, bạn gửi {received}.\nVui lòng gửi lại (mỗi email 1 dòng).");
    m.insert(keys::EMAIL_INVALID, "❌ Email không hợp lệ: {emails}\nVui lòng kiểm tra và gửi lại.");
    m.insert(keys::INFO_PROCESSING, "⏳ Đang xử lý ghi nhận thông tin...");
    m.insert(keys::COMMAND_START, "Bắt đầu");
    m.insert(keys::COMMAND_MENU, "Xem sản phẩm và mua hàng");
    m.insert(keys::COMMAND_ORDERS, "Lịch sử đơn hàng");
    m.insert(keys::COMMAND_HELP, "Hướng dẫn sử dụng");
    m.insert(keys::COMMAND_LANGUAGE, "Đổi ngôn ngữ");
    m
});

static TEXTS_EN: LazyLock<HashMap<&'static str, &'static str>> = LazyLock::new(|| {
    let mut m = HashMap::with_capacity(100);
    m.insert(keys::PRODUCT_NOT_FOR_SALE, "This product is no longer available.");
    m.insert(keys::ORDER_CANCELED_PRODUCT_HIDDEN, "This product is no longer available. Your order was cancelled.");
    m.insert(keys::ORDER_REFUNDED_PRODUCT_HIDDEN, "This product is no longer available. {amount} was refunded to your wallet.");
    m.insert(keys::ADMIN_CANCELLED_ORDER, "❌ Order **#{order_code}** was cancelled by the admin.");
    m.insert(keys::ADMIN_WALLET_ADDED, "💰 **The admin added {amount} to your wallet!**\n💵 New balance: **{balance}**");
    m.insert(keys::ADMIN_WALLET_DEDUCTED, "💰 **The admin deducted {amount} from your wallet.**\n💵 New balance: **{balance}**");
    m.insert(keys::EMAIL_COUNT_ERROR, "❌ Exactly **{required}** email address(es) are required; you sent {received}.\nPlease send them again, one per line.");
    m.insert(keys::EMAIL_INVALID, "❌ Invalid email address(es): {emails}\nPlease check and send them again.");
    m.insert(keys::INFO_PROCESSING, "⏳ Recording your information...");
    m.insert(keys::NEED_EMAIL, "✅ Order **#{order_code}** was paid successfully!\n\n📧 This product requires your information/email.\nPlease send **{qty} email address(es)**, one per line:\n\nExample:\n```\nemail1@gmail.com\nemail2@gmail.com\n```");
    m.insert(keys::DELIVERY_DELAYED, "✅ Order **#{order_code}** was paid successfully!\n\nAutomatic inventory has just run out. Please wait while the admin delivers a replacement shortly.");
    m.insert(keys::DELIVERY_COMPLETE, "🎉 **ORDER #{order_code} COMPLETED!**\n\n🔑 **YOUR ACCOUNT(S):**\n{items}\n{description}\n💬 _Thank you! Contact the admin if you need help._");
    m.insert(keys::MANUAL_PROCESSING, "✅ **INFORMATION SUBMITTED!**\nOrder **#{order_code}** is being sent for processing.\n\n⏳ The admin is reviewing it and will grant access shortly.\nPlease wait a moment. Use *Contact admin* if you need urgent help.");
    m.insert(keys::API_DELIVERY_COMPLETE, "🎉 **ORDER #{order_code} COMPLETED!**\n\n📦 {product}\n🔢 Quantity: {qty}\n\n🔑 **YOUR ACCOUNT(S):**\n{items}\n{description}\n⚠️ _Save this information now!_");
    m.insert(keys::REFERRAL_PAGE, "🎁 <b>REFERRAL CENTER</b>\n━━━━━━━━━━━━━━━━━━\n\n👤 <b>YOUR STATS</b>\n<blockquote>👥 Friends referred: <b>{count}</b>\n💵 Total rewards: <b>{earnings}</b>\n📡 Program: {status}</blockquote>\n\n💰 <b>REWARDS</b>\n<blockquote>• You receive: <b>{reward}/person</b>\n{friend_reward}</blockquote>\n\n🔗 <b>YOUR REFERRAL LINK</b>\n<code>{link}</code>\n\n💡 <i>Tap Share to send your link directly to friends.</i>");
    m.insert(keys::REFERRAL_CREDITED, "🎉 **REFERRAL REWARD!**\n\n👤 **{name}** joined through your referral link!\n\n💰 You received: **+{reward}**\n💵 Current wallet balance: **{balance}**");
    m.insert(keys::PARTIAL_PAYMENT, "✅ **{wallet_amount}** was deducted from your wallet!\n💰 Remaining balance: **{balance}**\n\n🏦 **TRANSFER THE REMAINING AMOUNT:**\n\n🏦 Bank: **{bank}**\n💳 Account: `{account}`\n👤 Name: **{account_name}**\n💰 Amount: **{remain}**\n📝 Content: `{order_code}`\n\n📱 Scan QR:\n[Payment QR]({qr_url})\n\n⏰ The order expires after **5 minutes** (wallet funds are refunded if cancelled)\n✅ The system confirms automatically after receiving the transfer");
    m.insert(keys::SESSION_ERROR, "❌ Session expired. Please send /menu again.");
    m.insert(keys::STOCK_ERROR, "❌ **{name}** is out of stock!\nPlease come back later.");
    m.insert(keys::ORDER_TIMEOUT, "⏰ Order **#{order_code}** was automatically cancelled because payment timed out.{refund}");
    m.insert(keys::REFUND, "\n💰 {amount} was refunded to your wallet (Balance: {balance})");
    m.insert(keys::WALLET_INSUFFICIENT, "❌ Insufficient wallet balance ({balance}).\nRequired: {amount}");
    m.insert(keys::PARTIAL_NO_BALANCE, "❌ Your wallet balance is zero. Please pay the full amount by bank transfer.");
    m.insert(keys::ORDER_CANCELLED, "❌ Order **#{order_code}** was cancelled.{refund}");
    m.insert(keys::ORDER_ALREADY_PROCESSED, "This order has already been processed or does not exist.");
    m.insert(keys::NO_ORDERS, "📭 You have no orders yet.");
    m.insert(keys::ORDERS_TITLE, "📋 **ORDER HISTORY** (latest 10)\n\n");
    m.insert(keys::WALLET_HOME, "💳 <b>YOUR WALLET</b>\n\n💵 <b>Balance:</b> <u>{balance}</u>\n\n<blockquote>💳 Deposited: {deposited}\n🛒 Spent: {spent}\n🎁 Referral rewards: {earnings}</blockquote>\n\n💡 <i>Use your wallet balance to buy without a bank transfer.</i>");
    m.insert(keys::DEPOSIT_PAGE, "💳 <b>DEPOSIT TO WALLET</b>\n\n<blockquote>🏦 Bank: <b>{bank}</b>\n💳 Account: <code>{account}</code>\n👤 Name: <b>{account_name}</b>\n📝 Content: <code>{code}</code></blockquote>\n\n💰 Amount: <b>Any amount</b> (minimum {minimum})\n\n📱 Scan the QR code below:\n<a href=\"{qr_url}\">Deposit QR</a>\n\n✅ Funds are <b>added automatically</b> within 1–2 minutes.\n🔔 You will be notified when the deposit succeeds!");
    m.insert(keys::REF_ACTIVE, "✅ Active");
    m.insert(keys::REF_PAUSED, "⏸️ Paused");
    m.insert(keys::REF_FRIEND_REWARD, "🎁 Your friend receives: <b>{amount}</b>\n");
    m.insert(keys::REF_SHARE, "Buy affordable premium accounts with 24/7 automatic delivery!");
    m.insert(keys::BTN_DEPOSIT, "💳 Deposit to wallet");
    m.insert(keys::BTN_INVITE, "🎁 Invite friends");
    m.insert(keys::BTN_BUY, "🛒 Buy products");
    m.insert(keys::BTN_BALANCE, "💰 View balance");
    m.insert(keys::BTN_SHARE, "📤 Share with friends");
    m.insert(keys::BTN_VIEW_PRODUCT, "⬅️ View product");
    m.insert(keys::BTN_CATEGORY, "🛍️ Categories");
    m.insert(keys::BTN_EXIT, "🏠 Exit");
    m.insert(keys::CATEGORY_OTHER, "Other");
    m.insert(keys::CATEGORY_PRODUCTS, "Products");
    m.insert(keys::CATEGORY_PANEL, "{icon} <b>{name}</b>\n\n<i>💰 Price  │  ✅ Available  │  ❌ Sold out  │  🔄 Updating</i>");
    m.insert(keys::BTN_BACK_CATEGORIES, "⬅️ Back to categories");
    m.insert(keys::ORDER_EXPIRY, "\n⏰ Order expires after <b>5 minutes</b>");
    m.insert(keys::LANGUAGE_PROMPT, "🌐 **Choose language / Chọn ngôn ngữ**");
    m.insert(keys::LANGUAGE_UPDATED, "✅ Language changed to English.");
    m.insert(keys::BTN_MENU, "🛍️ PRODUCT MENU");
    m.insert(keys::BTN_WALLET, "💳 Wallet");
    m.insert(keys::BTN_REFERRAL, "🎁 Referral");
    m.insert(keys::BTN_HISTORY, "📋 Order history");
    m.insert(keys::BTN_CONTACT, "☎️ Contact admin");
    m.insert(keys::BTN_RELOAD, "🔄 Refresh");
    m.insert(keys::BTN_BACK, "⬅️ Back");
    m.insert(keys::BTN_HOME, "🏠 Home");
    m.insert(keys::BTN_LANGUAGE, "🌐 Language / Ngôn ngữ");
    m.insert(keys::BTN_PAID, "✅ I have transferred");
    m.insert(keys::BTN_CANCEL, "❌ Cancel order");
    m.insert(keys::BTN_PAY_WALLET, "💰 Pay with wallet ({amount})");
    m.insert(keys::BTN_PAY_PARTIAL, "💰 Wallet {balance} + bank {amount}");
    m.insert(keys::BTN_PAY_BANK, "💳 Bank transfer {amount}");
    m.insert(keys::BTN_PAY_CRYPTO, "₮ USDT Crypto ({amount})");
    m.insert(keys::CRYPTO_WARNING, "⚠️ Send USDT only through the {network} network. The wrong network may lose funds; the wrong amount cannot be confirmed automatically.");
    m.insert(keys::CRYPTO_PAYMENT, "₮ <b>USDT PAYMENT</b>\n\n<blockquote>🌐 Network: <b>{network}</b>\n📬 Address: <code>{address}</code>\n💰 Exact amount: <code>{amount}</code> USDT</blockquote>\n\n<b>Send EXACTLY the amount shown above.</b>\n\n{warning}\n\n📱 <a href=\"{qr_url}\">Open wallet QR code</a>\n⏰ This crypto order expires after <b>{timeout_minutes} minutes</b>.");
    m.insert(keys::CRYPTO_PAYMENT_CAPTION, "₮ <b>USDT PAYMENT</b>\n\n<blockquote>🌐 Network: <b>{network}</b>\n📬 Address: <code>{address}</code>\n💰 Exact amount: <code>{amount}</code> USDT</blockquote>\n\n<b>Scan the QR code and send EXACTLY the amount shown above.</b>\n\n{warning}\n\n⏰ This crypto order expires after <b>{timeout_minutes} minutes</b>.");
    m.insert(keys::CRYPTO_PAID_WAITING, "⏳ Waiting for Binance to confirm the blockchain deposit. This usually takes 1–5 minutes. The bot will deliver automatically after funds arrive.");
    m.insert(keys::CRYPTO_UNAVAILABLE, "❌ USDT payment is currently unavailable. Please use bank transfer.");
    m.insert(keys::CRYPTO_RESERVATION_FAILED, "❌ A unique USDT amount could not be created. Please cancel the order and try again.");
    m.insert(keys::WALLET_REDEDUCTED, "💰 Your transfer arrived after the order expired. {amount} was deducted from your wallet again and order #{order_code} is being processed.");
    m.insert(keys::ADMIN_UNLOCKED, "🔑 Welcome Admin. The administration panel is unlocked!");
    m.insert(keys::WELCOME, "✨ Welcome <b>{name}</b>! ✨\n\n🏪 <b>PREMIUM ACCOUNT SHOP</b>\n\n<blockquote>⚡ Payment confirmed in <b>1 minute</b>\n📦 Receive your account <b>instantly</b>\n💬 <b>Fast</b> support\n🤖 Automated <b>24/7</b></blockquote>\n\n💰 <b>Wallet balance:</b> {balance}\n\n👇 <i>Choose an option below</i> 👇");
    m.insert(keys::WELCOME_BONUS, "\n🎁 <b>Welcome bonus: +{amount}</b> has been added to your wallet!\n");
    m.insert(keys::HELP, "📖 **HOW TO USE**\n\n1️⃣ Send /menu to view products\n2️⃣ Choose a product\n3️⃣ Choose quantity\n4️⃣ The bot creates a payment QR code\n5️⃣ Transfer with the exact content\n6️⃣ The system confirms and sends your account automatically\n\n⏰ Payments are confirmed automatically within 1–3 minutes\n❓ Need help? Contact admin");
    m.insert(keys::LOADING_PRODUCTS, "⏳ Loading products...");
    m.insert(keys::PRODUCTS_UNAVAILABLE, "❌ Products cannot be loaded right now. Please try again later!");
    m.insert(keys::MENU_TITLE, "🛍️ <b>PRODUCT MENU</b>\n════════════════════\n\n💰 Wallet balance: <b>{balance}</b>\n\n👇 <i>Choose a product category</i>:");
    m.insert(keys::PRODUCT_MISSING, "❌ The product does not exist or the server has an error!");
    m.insert(keys::PRODUCT_OUT_OF_STOCK, "❌ **{name}** is out of stock!\nPlease come back later.");
    m.insert(keys::PRODUCT_UPDATING, "🔄 **{name}** inventory is being updated.\nPlease try again in 1–2 minutes.");
    m.insert(keys::PRODUCT_DETAIL, "{icon} <b>{name}</b>\n\n💰 Price: <b>{price}</b> each\n📦 Stock: <b>{stock}</b> remaining\n{description}{note}\n👇 Choose quantity:");
    m.insert(keys::PRODUCT_AUTO_DELIVERY, "\n⚡ <i>Automatic delivery after payment</i>\n");
    m.insert(keys::PRODUCT_EMAIL_NOTE, "\n⚠️ <i>This product requires your email after payment</i>");
    m.insert(keys::ORDER_PAYMENT, "🛒 <b>ORDER #{order_code}</b>\n\n<blockquote>📦 {product}\n🔢 Quantity: <b>{qty}</b>\n💰 Unit price: <b>{price}</b>\n💵 Total: <u><b>{total}</b></u></blockquote>\n\n{wallet}🔽 <b>Choose a payment method:</b>\n");
    m.insert(keys::WALLET_BALANCE, "💰 Wallet balance: <b>{balance}</b>\n\n");
    m.insert(keys::ORDER_INVALID, "❌ This order does not exist or has already been processed.");
    m.insert(keys::BANK_PAYMENT, "🛒 <b>ORDER #{order_code}</b>\n\n📦 {product}\n💵 Total: <u><b>{total}</b></u>\n\n<blockquote>🏦 Bank: <b>{bank}</b>\n💳 Account: <code>{account}</code>\n👤 Name: <b>{account_name}</b>\n💰 Amount: <b>{total}</b>\n📝 Content: <code>{order_code}</code></blockquote>\n\n📱 Scan the QR code below for quick payment:\n<a href=\"{qr_url}\">Payment QR</a>\n\n⏰ This order automatically expires after <b>5 minutes</b>\n✅ Payment will be confirmed <b>AUTOMATICALLY</b>");
    m.insert(keys::PAID_WAITING, "⏳ Order **#{order_code}** is waiting for payment confirmation.\n\nThe system will confirm automatically within **1–3 minutes** after receiving the payment.\nYou will be notified as soon as it is completed! 🔔");
    m.insert(keys::WALLET_PAID, "✅ **{amount}** paid from your wallet!\n💰 Remaining balance: **{balance}**\n\n⏳ Processing order **#{order_code}**...");
    m.insert(keys::CUSTOMER_ORDER_ERROR, "⚠️ Order **#{order_code}** encountered an error while being processed.\n\n✅ Your payment **has been recorded** — the admin has been notified and will deliver your item or issue a refund soon.\n\n🚫 **Please DO NOT transfer again.**\n💬 For quick support, contact the admin with order code `#{order_code}`.");
    m.insert(keys::DEPOSIT_SUCCESS, "✅ **DEPOSIT SUCCESSFUL!**\n──────────────────\n💰 Amount: **+{amount}**\n💵 New balance: **{balance}**\n\nYou can now use your wallet to buy products! Send /menu");
    m.insert(keys::COMMAND_START, "Start");
    m.insert(keys::COMMAND_MENU, "View products and buy");
    m.insert(keys::COMMAND_ORDERS, "Order history");
    m.insert(keys::COMMAND_HELP, "How to use");
    m.insert(keys::COMMAND_LANGUAGE, "Change language");
    m
});

// --- In-Memory Language Cache ---
static LANG_CACHE: LazyLock<DashMap<i64, Language>> = LazyLock::new(DashMap::new);

/// Safe template parameter interpolation.
/// Replaces every occurrence of `{key}` with `value`.
/// Does not panic if template has unexpected brackets or missing parameters.
pub fn interpolate(template: &str, args: &[(&str, &str)]) -> String {
    if args.is_empty() || !template.contains('{') {
        return template.to_string();
    }
    let mut result = template.to_string();
    for (key, val) in args {
        let pattern = format!("{{{}}}", key);
        result = result.replace(&pattern, val);
    }
    result
}

/// Fallback hierarchy: User Language -> English -> Vietnamese -> Raw key.
pub fn get_text(lang: &str, key: &str, args: &[(&str, &str)]) -> String {
    let parsed_lang: Language = lang.parse().unwrap_or(DEFAULT_LANG);
    let template = match parsed_lang {
        Language::En => TEXTS_EN
            .get(key)
            .copied()
            .or_else(|| TEXTS_VI.get(key).copied()),
        Language::Vi => TEXTS_VI
            .get(key)
            .copied()
            .or_else(|| TEXTS_EN.get(key).copied()),
    }
    .unwrap_or(key);

    interpolate(template, args)
}

/// Retrieve user's active language with cache.
pub fn get_user_lang<F>(user_id: i64, db_lookup: F) -> Language
where
    F: FnOnce(i64) -> Option<String>,
{
    if let Some(entry) = LANG_CACHE.get(&user_id) {
        return *entry;
    }

    let lang = db_lookup(user_id)
        .and_then(|s| s.parse::<Language>().ok())
        .unwrap_or(DEFAULT_LANG);

    LANG_CACHE.insert(user_id, lang);
    lang
}

/// Update user's active language in both cache and DB callback.
pub fn set_user_lang<F>(user_id: i64, lang: Language, db_save: F)
where
    F: FnOnce(i64, &str),
{
    LANG_CACHE.insert(user_id, lang);
    db_save(user_id, lang.as_str());
}

/// Clear cache (useful in testing).
pub fn clear_lang_cache() {
    LANG_CACHE.clear();
}
