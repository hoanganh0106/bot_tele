#!/bin/bash
# ============================================
# SCRIPT DEPLOY CTV TELEGRAM BOT (RUST) LÊN UBUNTU / EC2
# ============================================
# Chạy trên EC2 / VPS Ubuntu 22.04 / 24.04
# Usage: bash deploy.sh
# ============================================

set -e

echo "🚀 Bắt đầu deploy CTV Telegram Bot (Rust)..."

# 1. Cập nhật hệ thống & cài build dependencies
echo "📦 Cài đặt gói hệ thống và thư viện phụ trợ..."
sudo apt update && sudo apt install -y build-essential curl pkg-config libssl-dev git ufw

# 2. Cài đặt Rust toolchain (nếu chưa có)
if ! command -v cargo &> /dev/null; then
    echo "🦀 Không tìm thấy Rust toolchain. Tiến hành cài đặt qua rustup..."
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
    source "$HOME/.cargo/env"
else
    echo "🦀 Đã tìm thấy Rust toolchain: $(cargo --version)"
fi

# Đảm bảo PATH có cargo trong session hiện tại
export PATH="$HOME/.cargo/bin:$PATH"

# 3. Khởi tạo đường dẫn dự án & dữ liệu
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_DIR="/home/ubuntu/ctv-bot"
DATA_DIR="/home/ubuntu/ctv-bot-data"
BACKUP_DIR="$DATA_DIR/backups"

echo "📂 Tạo thư mục dự án và dữ liệu an toàn:"
echo "   -> Project: $PROJECT_DIR"
echo "   -> Data:    $DATA_DIR"
echo "   -> Backups: $BACKUP_DIR"

mkdir -p "$PROJECT_DIR"
mkdir -p "$DATA_DIR"
mkdir -p "$BACKUP_DIR"

# 4. Sao chép mã nguồn và cấu hình sang PROJECT_DIR (nếu chạy từ thư mục khác)
if [ "$SCRIPT_DIR" != "$PROJECT_DIR" ]; then
    echo "🚚 Đang sao chép mã nguồn từ $SCRIPT_DIR sang $PROJECT_DIR..."
    FILES_TO_COPY=("Cargo.toml" "Cargo.lock" "config.env.example" "ctv-bot.service" "deploy.sh" "update.sh" "bot_tele")
    for file in "${FILES_TO_COPY[@]}"; do
        if [ -f "$SCRIPT_DIR/$file" ]; then
            cp "$SCRIPT_DIR/$file" "$PROJECT_DIR/"
            echo "   -> Đã copy $file"
        fi
    done

    for dir in src tests; do
        if [ -d "$SCRIPT_DIR/$dir" ]; then
            rm -rf "$PROJECT_DIR/$dir"
            cp -r "$SCRIPT_DIR/$dir" "$PROJECT_DIR/"
            echo "   -> Đã copy $dir/"
        fi
    done

    # Chỉ copy config.env nếu PROJECT_DIR chưa có để bảo vệ cấu hình thực tế
    if [ -f "$SCRIPT_DIR/config.env" ] && [ ! -f "$PROJECT_DIR/config.env" ]; then
        cp "$SCRIPT_DIR/config.env" "$PROJECT_DIR/"
        echo "   -> Đã copy config.env"
    fi
fi

cd "$PROJECT_DIR"

# 5. Khởi tạo cấu hình config.env
if [ ! -f "$PROJECT_DIR/config.env" ]; then
    if [ -f "$PROJECT_DIR/config.env.example" ]; then
        echo "📝 Tạo config.env từ config.env.example..."
        cp "$PROJECT_DIR/config.env.example" "$PROJECT_DIR/config.env"
        echo "⚠️ CẢNH BÁO: Vui lòng kiểm tra và cập nhật config.env tại $PROJECT_DIR với thông tin thực tế!"
    else
        echo "❌ Thiếu file cấu hình config.env hoặc config.env.example!"
        exit 1
    fi
fi

# 6. Chuẩn bị binary thực thi bot_tele
if [ -f "$PROJECT_DIR/bot_tele" ]; then
    echo "⚡ Sử dụng binary bot_tele đã có sẵn."
    chmod +x "$PROJECT_DIR/bot_tele"
elif [ -f "$SCRIPT_DIR/target/release/bot_tele" ]; then
    echo "⚡ Sao chép binary bot_tele từ $SCRIPT_DIR/target/release/..."
    cp "$SCRIPT_DIR/target/release/bot_tele" "$PROJECT_DIR/bot_tele"
    chmod +x "$PROJECT_DIR/bot_tele"
else
    echo "🔨 Biên dịch phiên bản release tối ưu hóa..."
    cargo build --release --bin bot_tele
    cp "$PROJECT_DIR/target/release/bot_tele" "$PROJECT_DIR/bot_tele"
    chmod +x "$PROJECT_DIR/bot_tele"
fi

# 7. Cấu hình systemd service
echo "⚙️ Thiết lập systemd service /etc/systemd/system/ctv-bot.service..."
sudo cp "$PROJECT_DIR/ctv-bot.service" /etc/systemd/system/ctv-bot.service
sudo systemctl daemon-reload
sudo systemctl enable ctv-bot
sudo systemctl restart ctv-bot

# 8. Mở port Webhook SePay trên firewall (nếu có cấu hình ufw)
WEBHOOK_PORT=$(grep -E "^WEBHOOK_PORT=" "$PROJECT_DIR/config.env" 2>/dev/null | cut -d'=' -f2 | tr -d ' "\r' || echo "5000")
if [ -n "$WEBHOOK_PORT" ]; then
    echo "🔓 Mở port webhook $WEBHOOK_PORT trên ufw..."
    sudo ufw allow "$WEBHOOK_PORT/tcp" 2>/dev/null || true
fi

echo ""
echo "============================================"
echo "✅ DEPLOY RUST TELEGRAM BOT THÀNH CÔNG!"
echo "============================================"
echo ""
echo "📌 Kiểm tra trạng thái dịch vụ:"
echo "   sudo systemctl status ctv-bot"
echo ""
echo "📌 Xem logs thời gian thực:"
echo "   sudo journalctl -u ctv-bot -f"
echo ""
echo "📌 Khởi động lại bot:"
echo "   sudo systemctl restart ctv-bot"
echo ""
echo "📌 Đường dẫn dữ liệu bot: $DATA_DIR"
echo "📌 URL Webhook SePay:    http://YOUR_SERVER_IP:${WEBHOOK_PORT}/webhook/sepay"
echo ""
