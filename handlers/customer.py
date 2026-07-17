"""Customer commands, catalog navigation, wallet, and referral callbacks."""

import asyncio
from datetime import datetime
from urllib.parse import urlencode

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

import i18n
from core.config import (
    BANK_ACCOUNT_NAME,
    BANK_ACCOUNT_NUMBER,
    BANK_NAME,
    ORDER_TIMEOUT_SECONDS,
    logger,
)
from core.helpers import (
    escape_html,
    estimate_order_usdt,
    fmt_icon,
    format_money,
    format_usdt,
    generate_order_code,
    generate_qr_url,
    get_sell_price,
    is_admin,
    product_display_desc,
    product_display_name,
    product_display_price,
    set_user_lang,
    t,
    ui_btn,
    user_lang,
)
from core.products import classify_product, get_all_categories_merged, get_products_cached
from core.runtime import CRYPTO_ENABLED, db, get_bot_username
from core.screens import (
    build_home_keyboard,
    build_menu_screen,
    build_orders_screen,
    build_product_back_keyboard,
    render_home_text,
)
from handlers.payment import auto_cancel_order, edit_navigation_message


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    # Kiểm tra referral link: /start ref_123456789
    referred_by = None
    if context.args and context.args[0].startswith("ref_"):
        try:
            referred_by = int(context.args[0].replace("ref_", ""))
            if referred_by == user.id:
                referred_by = None  # Không tự giới thiệu chính mình
        except (ValueError, IndexError):
            referred_by = None

    # Đăng ký user (xử lý referral trong database)
    is_new, referral_credited, new_user_reward = db.register_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        referred_by=referred_by
    )

    # Thông báo cho người giới thiệu — referral_credited chỉ True khi is_new,
    # nên phải gửi TRƯỚC nhánh return chọn ngôn ngữ để không mất thông báo.
    if referral_credited and referred_by:
        reward = db.get_setting("referral_reward", 1000)
        ref_balance = db.get_user_balance(referred_by)
        try:
            await context.bot.send_message(
                chat_id=referred_by,
                text=t(referred_by, "referral_credited", name=user.first_name or "?", reward=format_money(reward), balance=format_money(ref_balance)),
                parse_mode="Markdown"
            )
        except Exception:
            pass

    # Referral is registered first; only then ask genuinely new users to choose a language.
    if is_new:
        prompt = i18n.get_text("vi", "language_prompt")
        if new_user_reward > 0:
            prompt += (
                f"\n\n🎁 Quà chào mừng **+{format_money(new_user_reward)}** đã cộng vào ví!\n"
                f"🎁 Welcome bonus **+{format_money(new_user_reward)}** added to your wallet!"
            )
        await update.message.reply_text(
            prompt,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🇻🇳 Tiếng Việt", callback_data="setlang_vi"),
                InlineKeyboardButton("🇬🇧 English", callback_data="setlang_en"),
            ]]),
        )
        return

    balance = db.get_user_balance(user.id)
    await update.message.reply_text(
        render_home_text(user.id, user.first_name, balance),
        parse_mode="HTML",
        reply_markup=build_home_keyboard(user.id, balance),
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if user_lang(update.effective_user.id) == "en":
        await update.message.reply_text(t(update.effective_user.id, "help"), parse_mode="Markdown")
        return
    text = (
        "📖 **HƯỚNG DẪN SỬ DỤNG**\n\n"
        "1️⃣ Gõ /menu để xem danh sách sản phẩm\n"
        "2️⃣ Chọn sản phẩm muốn mua\n"
        "3️⃣ Chọn số lượng\n"
        "4️⃣ Bot tạo mã QR thanh toán\n"
        "5️⃣ Chuyển khoản đúng nội dung\n"
        "6️⃣ Hệ thống tự xác nhận & gửi tài khoản\n\n"
        "⏰ Thanh toán được xác nhận tự động trong 1-3 phút\n"
        "❓ Cần hỗ trợ? Liên hệ admin"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    source = "home"
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        if query.data.startswith("language_from_"):
            source = query.data.removeprefix("language_from_")
        context.user_data["language_return"] = source
        await query.edit_message_text(
            t(update.effective_user.id, "language_prompt"),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🇻🇳 Tiếng Việt", callback_data="setlang_vi"),
                InlineKeyboardButton("🇬🇧 English", callback_data="setlang_en"),
            ]]),
        )
        return
    context.user_data["language_return"] = source
    await update.message.reply_text(
        t(update.effective_user.id, "language_prompt"), parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🇻🇳 Tiếng Việt", callback_data="setlang_vi"),
            InlineKeyboardButton("🇬🇧 English", callback_data="setlang_en"),
        ]]),
    )


async def handle_set_language(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    lang = query.data.removeprefix("setlang_")
    if lang not in i18n.LANGS:
        await query.answer()
        return
    set_user_lang(query.from_user.id, lang)
    await query.answer(i18n.get_text(lang, "language_updated"))
    source = context.user_data.pop("language_return", "home")
    if source == "menu":
        text, keyboard = await build_menu_screen(query.from_user.id, refresh=False)
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        return
    balance = db.get_user_balance(query.from_user.id)
    await query.edit_message_text(
        render_home_text(query.from_user.id, query.from_user.first_name, balance),
        parse_mode="HTML", reply_markup=build_home_keyboard(query.from_user.id, balance),
    )


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hiển thị menu sản phẩm."""
    db.add_user(update.effective_user.id)
    user_id = update.effective_user.id
    msg = await update.message.reply_text(t(user_id, "loading_products"))

    try:
        text, keyboard = await build_menu_screen(user_id, refresh=False)
        await msg.edit_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception as e:
        logger.error(f"cmd_menu error: {e}")
        await msg.edit_text(t(user_id, "products_unavailable"))


async def handle_product_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Khi khách chọn sản phẩm."""
    query = update.callback_query
    await query.answer()

    product_key = query.data.replace("prod_", "")
    lang = user_lang(query.from_user.id)
    if db.is_product_hidden(product_key):
        await edit_navigation_message(query, t(query.from_user.id, "product_not_for_sale"))
        return
    
    # Xóa dữ liệu cũ để tránh lẫn giá từ sản phẩm trước
    context.user_data.pop("selected_product", None)
    context.user_data.pop("product_info", None)
    context.user_data.pop("sell_price", None)
    
    context.user_data["selected_product"] = product_key

    # Lấy thông tin sản phẩm (async — không block event loop)
    products, _ = get_products_cached()
    if not products or product_key not in products:
        await edit_navigation_message(query, t(query.from_user.id, "product_missing"))
        return

    # Clone info để KHÔNG mutate cache
    info = dict(products[product_key])
    info["name"] = product_display_name(product_key, info, lang)
    
    # Luôn tính giá bán từ nguồn chính xác nhất
    sell_price = get_sell_price(product_key, info["price"], info.get("is_custom_local", False))

    context.user_data["product_info"] = info
    context.user_data["sell_price"] = sell_price

    # Check stock
    if info["stock"] == 0:
        _, _, cid = classify_product(product_key, info)
        await edit_navigation_message(
            query,
            t(query.from_user.id, "product_out_of_stock", name=info['name']),
            parse_mode="Markdown",
            reply_markup=build_product_back_keyboard(query.from_user.id, cid),
        )
        return

    if info["stock"] == -1:
        _, _, cid = classify_product(product_key, info)
        await edit_navigation_message(
            query,
            t(query.from_user.id, "product_updating", name=info['name']),
            parse_mode="Markdown",
            reply_markup=build_product_back_keyboard(query.from_user.id, cid),
        )
        return

    # Chọn số lượng
    max_qty = min(info["stock"], 10)
    qty_buttons = []
    row = []
    for i in range(1, max_qty + 1):
        # Lưu ID sản phẩm vào callback_data để tránh lỗi phiên khi bot restart
        row.append(InlineKeyboardButton(str(i), callback_data=f"qty_{i}_{product_key}"))
        if len(row) == 5:
            qty_buttons.append(row)
            row = []
    if row:
        qty_buttons.append(row)

    _, _, cid = classify_product(product_key, info)
    qty_buttons.append([
        ui_btn("back", callback_data=f"viewcat_{cid}", user_id=query.from_user.id), ui_btn("home", callback_data="back_start", user_id=query.from_user.id)
    ])

    # Nếu là slot_gpt_team, thông báo cần email
    note = ""
    if product_key == "slot_gpt_team":
        note = "\n⚠️ <i>Sản phẩm này cần cung cấp email sau khi thanh toán</i>"

    # Hiển thị mô tả: chỉ custom hoặc API, KHÔNG tự sinh
    desc = product_display_desc(product_key, info, lang)
    
    desc_block = ""
    if desc:
        desc_block = f"\n<blockquote>{escape_html(desc)}</blockquote>\n"
    
    # Chỉ hiển thị "Nhận tự động" nếu sản phẩm THẬT SỰ có kho auto-delivery
    if db.has_custom_accounts_enabled(product_key):
        desc_block += "\n⚡ <i>Nhận tự động sau thanh toán</i>\n"
    
    # Icon danh mục (custom emoji nếu có)
    _, icon, cid_for_icon = classify_product(product_key, info)
    cat_icon = fmt_icon(cid_for_icon, icon)
    pname = escape_html(info['name'])
    if lang == "en":
        if db.has_custom_accounts_enabled(product_key):
            desc_block = f"\n<blockquote>{escape_html(desc)}</blockquote>\n" if desc else ""
            desc_block += t(query.from_user.id, "product_auto_delivery")
        await edit_navigation_message(
            query,
            t(query.from_user.id, "product_detail", icon=cat_icon, name=pname,
              price=product_display_price(product_key, sell_price, lang), stock=info["stock"], description=desc_block,
              note=t(query.from_user.id, "product_email_note") if product_key == "slot_gpt_team" else ""),
            parse_mode="HTML", disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup(qty_buttons),
        )
        return
    
    await edit_navigation_message(
        query,
        f"{cat_icon} <b>{pname}</b>\n\n"
        f"💰 Giá: <b>{format_money(sell_price)}</b> / cái\n"
        f"📦 Kho: <b>{info['stock']}</b> còn lại\n"
        f"{desc_block}{note}\n"
        f"👇 Chọn số lượng muốn mua:",
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup(qty_buttons)
    )


async def handle_qty_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Khi khách chọn số lượng."""
    query = update.callback_query
    await query.answer()
    lang = user_lang(query.from_user.id)

    # Format mới: qty_SỐ_LƯỢNG_MÃ_SẢN_PHẨM
    parts = query.data.split("_")
    qty = int(parts[1])
    product_key = "_".join(parts[2:]) if len(parts) > 2 else context.user_data.get("selected_product")
    
    if not product_key:
        await query.edit_message_text(t(query.from_user.id, "session_error"))
        return
    if db.is_product_hidden(product_key):
        await edit_navigation_message(query, t(query.from_user.id, "product_not_for_sale"))
        return

    # Lấy thông tin sản phẩm từ cache (instant, không block event loop)
    products, _ = get_products_cached()
    if not products or product_key not in products:
        await query.edit_message_text(t(query.from_user.id, "product_missing"))
        return
    
    # Clone info để không mutate cache
    info = dict(products[product_key])
    info["name"] = product_display_name(product_key, info, lang)
    
    # Luôn tính giá bán từ nguồn chính xác nhất (custom_prices hoặc markup)
    sell_price = get_sell_price(product_key, info['price'], info.get('is_custom_local', False))

    # Kiểm tra tồn kho trước khi tạo đơn
    if info["stock"] <= 0:
        _, _, cid = classify_product(product_key, info)
        await query.edit_message_text(
            t(query.from_user.id, "stock_error", name=info['name']),
            parse_mode="Markdown",
            reply_markup=build_product_back_keyboard(query.from_user.id, cid),
        )
        return

    if qty > info["stock"]:
        qty = info["stock"]  # Giới hạn số lượng theo kho thực tế

    total = sell_price * qty
    order_code = generate_order_code()

    # Lưu pending order
    order = {
        "order_code": order_code,
        "user_id": query.from_user.id,
        "username": query.from_user.username or query.from_user.first_name,
        "product_key": product_key,
        "product_name": info.get("name", product_key),
        "qty": qty,
        "sell_price": sell_price,
        "total": total,
        "status": "pending",
        "created_at": datetime.now().isoformat(),
        "paid_at": None,
        "items": [],
        "is_custom_local": info.get("is_custom_local", False)
    }

    # Phân loại yêu cầu gửi email hay giao hàng tự động
    if product_key == "slot_gpt_team":
        order["needs_email"] = True
        order["emails"] = []
    elif info.get("is_custom_local", False):
        if db.has_custom_accounts_enabled(product_key):
            order["needs_email"] = False
            order["is_auto_delivered"] = True
        else:
            order["needs_email"] = True
            order["emails"] = []

    db.save_order(order_code, order)
    context.user_data["current_order"] = order_code

    # Kiểm tra số dư ví
    user_balance = db.get_user_balance(query.from_user.id)

    # Hiển thị chọn phương thức thanh toán (CHƯA tạo QR)
    buttons = []
    
    if user_balance >= total:
        buttons.append([InlineKeyboardButton(
            t(query.from_user.id, "btn_pay_wallet", amount=format_money(total)),
            callback_data=f"paywallet_{order_code}"
        )])
    elif user_balance > 0:
        remain = total - user_balance
        buttons.append([InlineKeyboardButton(
            t(query.from_user.id, "btn_pay_partial", balance=format_money(user_balance), amount=format_money(remain)),
            callback_data=f"paypartial_{order_code}"
        )])
    
    buttons.append([InlineKeyboardButton(
        t(query.from_user.id, "btn_pay_bank", amount=format_money(total)),
        callback_data=f"paybank_{order_code}"
    )])
    if CRYPTO_ENABLED:
        estimated_usdt = estimate_order_usdt(order)
        buttons.append([InlineKeyboardButton(
            t(query.from_user.id, "btn_pay_crypto", amount=format_usdt(estimated_usdt)),
            callback_data=f"paycrypto_{order_code}"
        )])
    buttons.append([InlineKeyboardButton(t(query.from_user.id, "btn_cancel"), callback_data=f"cancel_{order_code}")])

    wallet_line = ""
    if user_balance > 0:
        wallet_line = t(query.from_user.id, "wallet_balance", balance=format_money(user_balance))

    text = t(
        query.from_user.id,
        "order_payment",
        order_code=order_code,
        product=escape_html(info.get('name', product_key)),
        qty=qty,
        price=product_display_price(product_key, sell_price, lang),
        total=product_display_price(product_key, sell_price, lang, qty),
        wallet=wallet_line,
    ) + t(query.from_user.id, "order_expiry")

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

    # Auto cancel sau 5 phút
    asyncio.create_task(auto_cancel_order(context, order_code, query.from_user.id, ORDER_TIMEOUT_SECONDS))


async def handle_back_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Quay lại menu chính — hiển thị lại danh mục."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    text, keyboard = await build_menu_screen(user_id, refresh=False)
    await edit_navigation_message(query, text, parse_mode="HTML", reply_markup=keyboard)


async def cmd_myorders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xem lịch sử đơn hàng."""
    user_id = update.effective_user.id
    text, keyboard = build_orders_screen(user_id)
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)


async def handle_category_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not context.user_data.pop("_skip_query_answer", False):
        await query.answer()
    data = query.data
    
    if data in ("open_menu", "reload_menu"):
        text, keyboard = await build_menu_screen(
            query.from_user.id, refresh=(data == "reload_menu")
        )
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
        return
        
    if data == "btn_myorders":
        user_id = update.effective_user.id
        text, keyboard = build_orders_screen(user_id)
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
        return

    cat_id = data.replace("viewcat_", "")
    products, _ = get_products_cached()
    if not products:
        await query.edit_message_text(t(query.from_user.id, "products_unavailable"))
        return
        
    buttons = []
    for key, info in products.items():
        stock = info.get("stock", 0)
        
        # KHÔNG hiển thị sản phẩm bị ẩn cho khách, HOẶC đã hết tồn kho
        if db.is_product_hidden(key) or stock == 0:
            continue
            
        _, _, c_id = classify_product(key, info)
        if c_id == cat_id:
            sell_price = get_sell_price(key, info['price'], info.get('is_custom_local', False))
            status = "🔄" if stock == -1 else f"✅{stock}"
            dname = product_display_name(key, info, user_lang(query.from_user.id))
            
            # Phân biệt nguồn API (Chỉ cho Admin)
            api_tag = ""
            if is_admin(update.effective_user.id):
                api_source = info.get("api_source", "CTV")
                api_tag = f"[{api_source}] " if not info.get("is_custom_local") else "[TỰ BÁN] "
            
            display_price = product_display_price(key, sell_price, user_lang(query.from_user.id), include_vnd=False)
            buttons.append([InlineKeyboardButton(f"{api_tag}{dname} | {display_price} | {status}", callback_data=f"prod_{key}")])
               
    buttons.append([
        ui_btn("back", t(query.from_user.id, "btn_back_categories"), callback_data="back_menu", user_id=query.from_user.id),
        ui_btn("home", callback_data="back_start", user_id=query.from_user.id),
    ])
    
    # Lấy tên + icon danh mục
    all_cats = get_all_categories_merged()
    cat_name, cat_emoji = all_cats.get(cat_id, [t(query.from_user.id, "category_products"), "🛒"])
    if cat_id == "khac":
        cat_name = t(query.from_user.id, "category_other")
    cat_icon_html = fmt_icon(cat_id, cat_emoji)
    
    await query.edit_message_text(
        t(query.from_user.id, "category_panel", icon=cat_icon_html, name=escape_html(cat_name)),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def handle_wallet_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hiển thị ví người dùng."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    user_info = db.get_user(user_id)
    balance = user_info.get("balance", 0)
    total_deposited = user_info.get("total_deposited", 0)
    total_spent = user_info.get("total_spent", 0)
    referral_earnings = user_info.get("referral_earnings", 0)
    
    text = t(user_id, "wallet_home", balance=format_money(balance), deposited=format_money(total_deposited), spent=format_money(total_spent), earnings=format_money(referral_earnings))
    
    buttons = [
        [ui_btn("deposit", t(user_id, "btn_deposit"), callback_data="deposit_start", user_id=user_id)],
        [ui_btn("referral", t(user_id, "btn_invite"), callback_data="referral_home", user_id=user_id), ui_btn("buy", callback_data="open_menu", user_id=user_id)],
        [ui_btn("home", callback_data="back_start", user_id=user_id)],
    ]
    
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))


async def handle_deposit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tạo lệnh nạp tiền."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    dep_code = db.create_deposit(user_id)
    min_deposit = db.get_setting("min_deposit", 5000)
    
    qr_url = generate_qr_url(0, dep_code)
    
    text = t(user_id, "deposit_page", bank=escape_html(BANK_NAME), account=escape_html(BANK_ACCOUNT_NUMBER), account_name=escape_html(BANK_ACCOUNT_NAME), code=dep_code, minimum=format_money(min_deposit), qr_url=qr_url)
    
    buttons = [
        [ui_btn("wallet", t(user_id, "btn_balance"), callback_data="wallet_home", user_id=user_id), ui_btn("home", callback_data="back_start", user_id=user_id)],
    ]
    
    await query.edit_message_text(
        text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
        disable_web_page_preview=False
    )


async def handle_referral_home(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hiển thị trang giới thiệu."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    bot_username = get_bot_username() or (await context.bot.get_me()).username
    ref_link = f"https://t.me/{bot_username}?start=ref_{user_id}"
    
    stats = db.get_referral_stats(user_id)
    reward = db.get_setting("referral_reward", 1000)
    new_user_rw = db.get_setting("referral_new_user_reward", 500)
    ref_enabled = db.get_setting("referral_enabled", True)
    
    status = t(user_id, "ref_active" if ref_enabled else "ref_paused")
    
    new_user_line = ""
    if new_user_rw > 0:
        new_user_line = t(user_id, "ref_friend_reward", amount=format_money(new_user_rw))
    
    text = t(user_id, "referral_page", link=ref_link, reward=format_money(reward), friend_reward=new_user_line, status=status, count=stats['referral_count'], earnings=format_money(stats['referral_earnings']))
    
    share_text = t(user_id, "ref_share")
    share_url = "https://t.me/share/url?" + urlencode({
        "url": ref_link,
        "text": share_text,
    })
    
    buttons = [
        [ui_btn("share", t(user_id, "btn_share"), url=share_url, user_id=user_id)],
        [
            ui_btn("wallet", callback_data="wallet_home", user_id=user_id),
            ui_btn("buy", callback_data="open_menu", user_id=user_id),
        ],
        [ui_btn("home", callback_data="back_start", user_id=user_id)],
    ]
    
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))


async def handle_back_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Quay lại màn hình /start."""
    query = update.callback_query
    await query.answer()
    user = query.from_user
    user_id = user.id
    
    balance = db.get_user_balance(user_id)
    
    text = render_home_text(user_id, user.first_name, balance)
    await edit_navigation_message(
        query,
        text,
        parse_mode="HTML",
        reply_markup=build_home_keyboard(user_id, balance),
    )


async def handle_noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý nút category separator."""
    query = update.callback_query
    await query.answer()
