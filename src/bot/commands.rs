//! Telegram BotCommands enum and command handlers.

use std::sync::Arc;
use teloxide::prelude::*;
use teloxide::types::ParseMode;
use teloxide::utils::command::BotCommands;

use crate::config::Config;
use crate::db::DbStore;
use crate::error::AppError;
use crate::i18n::{self, keys};
use crate::services::CatalogService;
use super::access_control::is_admin;
use super::helpers::format_money;
use super::screens;
use super::state::StateManager;

#[derive(BotCommands, Clone, Debug)]
#[command(rename_rule = "lowercase", description = "These commands are supported:")]
pub enum Command {
    #[command(description = "Bắt đầu")]
    Start(String),
    #[command(description = "Hướng dẫn sử dụng")]
    Help,
    #[command(description = "Xem sản phẩm và mua hàng")]
    Menu,
    #[command(description = "Lịch sử đơn hàng")]
    MyOrders,
    #[command(description = "Đổi ngôn ngữ")]
    Language,
    #[command(description = "Trang quản trị Admin")]
    Admin,
    #[command(description = "Lấy custom emoji ID")]
    GetEmoji,
    #[command(description = "Cập nhật tỷ giá USDT/VND")]
    SetRate(String),
}

/// Parse referral user ID from /start arguments (supports `ref_123456` or `123456`).
pub fn parse_referral_id(payload: &str, current_user_id: i64) -> Option<i64> {
    let trimmed = payload.trim();
    if trimmed.is_empty() {
        return None;
    }
    let token = trimmed.strip_prefix("ref_").unwrap_or(trimmed);
    if let Ok(id) = token.parse::<i64>() {
        if id > 0 && id != current_user_id {
            return Some(id);
        }
    }
    None
}

/// Handle incoming Bot commands.
pub async fn handle_command(
    bot: Bot,
    msg: Message,
    cmd: Command,
    db: Arc<DbStore>,
    config: Arc<Config>,
    catalog: Arc<dyn CatalogService>,
    state_mgr: Arc<StateManager>,
) -> Result<(), AppError> {
    let user = match msg.from() {
        Some(u) => u,
        None => return Ok(()),
    };
    let user_id = user.id.0 as i64;
    let username = user.username.as_deref();
    let first_name = user.first_name.as_str();

    match cmd {
        Command::Start(payload) => {
            let referred_by = parse_referral_id(&payload, user_id);
            let (is_new, referral_credited, new_user_reward) = register_user(
                &db,
                user_id,
                username,
                Some(first_name),
                referred_by,
            );

            // Notify referrer if commission was credited
            if referral_credited && referred_by.is_some() {
                let ref_id = referred_by.unwrap();
                let ref_lang = i18n::get_user_lang(ref_id, |uid| {
                    db.get_user(uid).map(|u| u.lang)
                });
                let reward = db.read_data(|d| d.settings.referral_reward);
                let ref_balance = db.get_user_balance(ref_id);

                let notify_text = i18n::get_text(
                    ref_lang.as_str(),
                    keys::REFERRAL_CREDITED,
                    &[
                        ("name", first_name),
                        ("reward", &format_money(reward)),
                        ("balance", &format_money(ref_balance)),
                    ],
                );
                let _ = bot.send_message(ChatId(ref_id), notify_text)
                    .parse_mode(ParseMode::Markdown)
                    .await;
            }

            // Genuinely new user -> Language selection prompt
            if is_new {
                let mut prompt = i18n::get_text("vi", keys::LANGUAGE_PROMPT, &[]);
                if new_user_reward > 0 {
                    prompt.push_str(&format!(
                        "\n\n🎁 Quà chào mừng **+{}** đã cộng vào ví!\n🎁 Welcome bonus **+{}** added to your wallet!",
                        format_money(new_user_reward),
                        format_money(new_user_reward)
                    ));
                }
                let keyboard = screens::build_language_keyboard();
                bot.send_message(msg.chat.id, prompt)
                    .parse_mode(ParseMode::Markdown)
                    .reply_markup(keyboard)
                    .await?;
                return Ok(());
            }

            // Returning user -> Home Screen
            let balance = db.get_user_balance(user_id);
            let admin_flag = is_admin(&config, user_id);
            let home_text = screens::render_home_text(&db, user_id, Some(first_name), balance, admin_flag);
            let home_kb = screens::build_home_keyboard(&db, user_id, balance, admin_flag);

            bot.send_message(msg.chat.id, home_text)
                .parse_mode(ParseMode::Html)
                .reply_markup(home_kb)
                .await?;
        }

        Command::Help => {
            let lang = i18n::get_user_lang(user_id, |uid| db.get_user(uid).map(|u| u.lang));
            let help_text = i18n::get_text(lang.as_str(), keys::HELP, &[]);
            bot.send_message(msg.chat.id, help_text)
                .parse_mode(ParseMode::Markdown)
                .await?;
        }

        Command::Menu => {
            let balance = db.get_user_balance(user_id);
            let admin_flag = is_admin(&config, user_id);
            let (menu_text, menu_kb) = screens::build_menu_screen(
                catalog.as_ref(),
                &db,
                user_id,
                balance,
                admin_flag,
                false,
            ).await?;

            bot.send_message(msg.chat.id, menu_text)
                .parse_mode(ParseMode::Html)
                .reply_markup(menu_kb)
                .await?;
        }

        Command::MyOrders => {
            let (orders_text, orders_kb) = screens::build_orders_screen(&db, user_id, 0, 8);
            bot.send_message(msg.chat.id, orders_text)
                .parse_mode(ParseMode::Markdown)
                .reply_markup(orders_kb)
                .await?;
        }

        Command::Language => {
            state_mgr.set_nav_return(user_id, "home");
            let lang = i18n::get_user_lang(user_id, |uid| db.get_user(uid).map(|u| u.lang));
            let prompt = i18n::get_text(lang.as_str(), keys::LANGUAGE_PROMPT, &[]);
            bot.send_message(msg.chat.id, prompt)
                .parse_mode(ParseMode::Markdown)
                .reply_markup(screens::build_language_keyboard())
                .await?;
        }

        Command::Admin => {
            if !is_admin(&config, user_id) {
                bot.send_message(msg.chat.id, "⛔ Tính năng chỉ dành cho Admin.").await?;
                return Ok(());
            }
            let (dash_text, dash_kb) = screens::build_admin_dashboard();
            bot.send_message(msg.chat.id, dash_text)
                .parse_mode(ParseMode::Html)
                .reply_markup(dash_kb)
                .await?;
        }

        Command::GetEmoji => {
            if !is_admin(&config, user_id) {
                bot.send_message(msg.chat.id, "⛔ Chỉ Admin mới dùng được lệnh này.").await?;
                return Ok(());
            }
            let target_msg = msg.reply_to_message().unwrap_or(&msg);
            let mut emoji_found = Vec::new();

            if let Some(entities) = target_msg.entities() {
                for entity in entities {
                    if let teloxide::types::MessageEntityKind::CustomEmoji { custom_emoji_id } = &entity.kind {
                        let text = target_msg.text().unwrap_or_default();
                        let sub = text.chars().skip(entity.offset).take(entity.length).collect::<String>();
                        emoji_found.push((sub, custom_emoji_id.clone()));
                    }
                }
            }

            if emoji_found.is_empty() {
                let guide = "📌 **HƯỚNG DẪN LẤY CUSTOM EMOJI ID**\n━━━━━━━━━━━━━━━━━━\n\n**Cách 1:** Gửi một custom emoji vào chat, rồi reply tin nhắn đó bằng `/getemoji`\n\n**Cách 2:** Gửi custom emoji cùng với lệnh\n\n💡 _Bạn cần Telegram Premium để tìm và gửi custom emoji._";
                bot.send_message(msg.chat.id, guide).parse_mode(ParseMode::Markdown).await?;
            } else {
                let mut out = "🎨 **CUSTOM EMOJI ĐÃ TÌM THẤY**\n━━━━━━━━━━━━━━━━━━\n\n".to_string();
                for (emoji_text, eid) in emoji_found {
                    out.push_str(&format!("• {} → ID: `{}`\n", emoji_text, eid));
                }
                out.push_str("\n📋 Để gắn emoji vào danh mục, vào:\n**Admin → Quản lý sản phẩm → 🎨 Đổi Icon danh mục**");
                bot.send_message(msg.chat.id, out).parse_mode(ParseMode::Markdown).await?;
            }
        }

        Command::SetRate(arg) => {
            if !is_admin(&config, user_id) {
                bot.send_message(msg.chat.id, "⛔ Chỉ Admin mới dùng được lệnh này.").await?;
                return Ok(());
            }
            let clean = arg.replace(',', "").replace('.', "").trim().to_string();
            if clean.is_empty() {
                let current_rate = db.read_data(|d| d.settings.usdt_vnd_rate).unwrap_or(26500);
                bot.send_message(
                    msg.chat.id,
                    format!("Tỷ giá hiện tại: **{} VND/USDT**\nCách dùng: `/setrate 26800`", format_money(current_rate)),
                ).parse_mode(ParseMode::Markdown).await?;
                return Ok(());
            }

            match clean.parse::<i64>() {
                Ok(rate) if (10_000..=100_000).contains(&rate) => {
                    db.update_settings(|s| s.usdt_vnd_rate = Some(rate), true);
                    bot.send_message(msg.chat.id, format!("✅ Đã cập nhật tỷ giá: **{} VND/USDT**", format_money(rate)))
                        .parse_mode(ParseMode::Markdown)
                        .await?;
                }
                _ => {
                    bot.send_message(msg.chat.id, "❌ Tỷ giá phải nằm trong khoảng 10.000–100.000 VND/USDT.").await?;
                }
            }
        }
    }

    Ok(())
}

/// Helper function to register user and process referral bonuses atomically.
pub fn register_user(
    db: &DbStore,
    user_id: i64,
    username: Option<&str>,
    first_name: Option<&str>,
    referred_by: Option<i64>,
) -> (bool, bool, i64) {
    db.write_data(|data| {
        let uid = user_id.to_string();
        let is_new = !data.users.contains_key(&uid);

        if is_new {
            let mut record = crate::db::UserRecord::default();
            record.referred_by = referred_by;
            record.joined_at = chrono::Local::now().to_rfc3339();
            data.users.insert(uid.clone(), record);
        }

        let user_ref = data.users.get_mut(&uid).unwrap();
        user_ref.username = username.map(|s| s.to_string());
        user_ref.first_name = first_name.map(|s| s.to_string());

        if !data.user_list.contains(&user_id) {
            data.user_list.push(user_id);
        }

        let mut referral_credited = false;
        let mut new_user_reward = 0i64;

        if is_new && referred_by.is_some() {
            let ref_id = referred_by.unwrap();
            let ref_uid = ref_id.to_string();
            let ref_enabled = data.settings.referral_enabled;

            if ref_enabled && ref_id != user_id && data.users.contains_key(&ref_uid) {
                let reward = data.settings.referral_reward;
                let new_user_bonus = data.settings.referral_new_user_reward;

                // Credit referrer
                let referrer = data.users.get_mut(&ref_uid).unwrap();
                referrer.balance += reward;
                referrer.referral_count += 1;
                referrer.referral_earnings += reward;

                // Credit new user welcome bonus
                if new_user_bonus > 0 {
                    let new_u = data.users.get_mut(&uid).unwrap();
                    new_u.balance += new_user_bonus;
                    new_user_reward = new_user_bonus;
                }
                referral_credited = true;
            }
        }

        (is_new, referral_credited, new_user_reward)
    }, true)
}
