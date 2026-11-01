#!/bin/bash
# ============================================
# SCRIPT CẬP NHẬT CTV TELEGRAM BOT (RUST) AN TOÀN — KHÔNG MẤT DỮ LIỆU
# ============================================
# Chạy trên EC2 / VPS Ubuntu: bash update.sh
# Quy trình:
#   1. Sao lưu (backup) database trước khi cập nhật
#   2. Đồng bộ code mới từ Git / thư mục làm việc
#   3. Biên dịch release / cập nhật binary bot_tele
#   4. Cập nhật systemd service và khởi động lại dịch vụ
#   5. Xác nhận trạng thái bot hoạt động
# ============================================

set -e

PROJECT_DIR="/home/ubuntu/ctv-bot"
DATA_DIR="/home/ubuntu/ctv-bot-data"
DB_FILE="$DATA_DIR/bot_data.json"
BACKUP_DIR="$DATA_DIR/backups"

ensure_https_remote_for_token() {
    local remote_url
    remote_url="$(git config --get remote.origin.url || true)"

    if [[ "$remote_url" == git@github.com:* ]]; then
        local repo_path="${remote_url#git@github.com:}"
        local https_url="https://github.com/${repo_path}"

        echo "🔑 Đang đổi git remote sang HTTPS để dùng access token: $https_url"
        git remote set-url origin "$https_url"
    fi
}

echo "🔄 Bắt đầu cập nhật CTV Telegram Bot (Rust)..."

# 1. Khởi tạo thư mục dữ liệu an toàn
mkdir -p "$DATA_DIR"
mkdir -p "$BACKUP_DIR"

# 2. Di chuyển dữ liệu cũ nếu phát hiện nằm trong project
if [ -f "$PROJECT_DIR/data/bot_data.json" ] && [ ! -f "$DB_FILE" ]; then
    echo "📦 Di chuyển database cũ từ thư mục project sang: $DB_FILE"
    cp "$PROJECT_DIR/data/bot_data.json" "$DB_FILE"
fi

# 3. Sao lưu database trước khi cập nhật
BACKUP_FILE=""
if [ -f "$DB_FILE" ]; then
    TIMESTAMP=$(date +%Y%m%d_%H%M%S)
    BACKUP_FILE="$BACKUP_DIR/bot_data_before_update_${TIMESTAMP}.json"
    cp "$DB_FILE" "$BACKUP_FILE"
    echo "💾 Đã tạo bản sao lưu an toàn: $BACKUP_FILE"
    
    # Giữ tối đa 30 bản sao lưu gần nhất
    ls -t "$BACKUP_DIR"/bot_data_*.json 2>/dev/null | tail -n +31 | xargs rm -f 2>/dev/null || true
fi

# 4. Pull code mới nếu chạy trong git repository
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
if [ -d "$SCRIPT_DIR/.git" ] || git -C "$SCRIPT_DIR" rev-parse --is-inside-work-tree &>/dev/null; then
    cd "$SCRIPT_DIR"
    echo "📥 Đồng bộ mã nguồn mới nhất từ git..."
    GIT_TOKEN="${GITHUB_TOKEN:-${GH_TOKEN:-}}"
    GIT_TOKEN="$(printf '%s' "$GIT_TOKEN" | tr -d '\r\n')"
    if [ -n "$GIT_TOKEN" ]; then
        ensure_https_remote_for_token
        git -c credential.helper= -c http.extraHeader="Authorization: Bearer $GIT_TOKEN" pull origin main || {
            echo "⚠️ Git pull conflict, force reset..."
            git -c credential.helper= -c http.extraHeader="Authorization: Bearer $GIT_TOKEN" fetch origin main
            git reset --hard origin/main
        }
    else
        git pull origin main || {
            echo "⚠️ Git pull conflict, force reset..."
            git fetch origin main
            git reset --hard origin/main
        }
    fi
fi

# 5. Sao chép mã nguồn sang PROJECT_DIR (nếu SCRIPT_DIR khác PROJECT_DIR)
if [ "$SCRIPT_DIR" != "$PROJECT_DIR" ]; then
    echo "🚚 Đang sao chép code mới sang $PROJECT_DIR..."
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

    # Chỉ copy config.env nếu bên PROJECT_DIR chưa có
    if [ -f "$SCRIPT_DIR/config.env" ] && [ ! -f "$PROJECT_DIR/config.env" ]; then
        cp "$SCRIPT_DIR/config.env" "$PROJECT_DIR/"
        echo "   -> Đã copy config.env"
    fi
fi

cd "$PROJECT_DIR"

# 6. Cập nhật / Biên dịch binary bot_tele
export PATH="$HOME/.cargo/bin:$PATH"

if [ -f "$PROJECT_DIR/bot_tele" ] && [ "$PROJECT_DIR/bot_tele" -nt "$PROJECT_DIR/Cargo.toml" ]; then
    echo "⚡ Sử dụng binary bot_tele đã cập nhật."
    chmod +x "$PROJECT_DIR/bot_tele"
elif [ -f "$SCRIPT_DIR/target/release/bot_tele" ]; then
    echo "⚡ Sao chép binary bot_tele từ $SCRIPT_DIR/target/release/..."
    cp "$SCRIPT_DIR/target/release/bot_tele" "$PROJECT_DIR/bot_tele"
    chmod +x "$PROJECT_DIR/bot_tele"
elif command -v cargo &> /dev/null; then
    echo "🔨 Biên dịch phiên bản release tối ưu hóa..."
    cargo build --release --bin bot_tele
    cp "$PROJECT_DIR/target/release/bot_tele" "$PROJECT_DIR/bot_tele"
    chmod +x "$PROJECT_DIR/bot_tele"
else
    echo "❌ Không tìm thấy Rust toolchain (cargo) và không có binary bot_tele dựng sẵn!"
    exit 1
fi

# 7. Cập nhật systemd service
if [ -f "$PROJECT_DIR/ctv-bot.service" ]; then
    echo "⚙️ Đồng bộ systemd service..."
    sudo cp "$PROJECT_DIR/ctv-bot.service" /etc/systemd/system/ctv-bot.service
    sudo systemctl daemon-reload
fi

# 8. Khởi động lại dịch vụ bot
echo "🔄 Khởi động lại ctv-bot service..."
sudo systemctl restart ctv-bot

# 9. Đợi và kiểm tra tính sống còn của dịch vụ
sleep 3
if sudo systemctl is-active --quiet ctv-bot; then
    echo ""
    echo "============================================"
    echo "✅ CẬP NHẬT CTV TELEGRAM BOT THÀNH CÔNG!"
    echo "============================================"
    echo ""
    echo "📂 Dữ liệu DB: $DB_FILE"
    if [ -n "$BACKUP_FILE" ]; then
        echo "💾 Bản sao lưu: $BACKUP_FILE"
    fi
    echo ""
    echo "📌 Xem logs thời gian thực: sudo journalctl -u ctv-bot -f --no-pager -n 50"
else
    echo ""
    echo "❌ Lỗi: Bot không khởi động được sau khi cập nhật! Kiểm tra logs:"
    echo "   sudo journalctl -u ctv-bot -n 30 --no-pager"
    exit 1
fi
