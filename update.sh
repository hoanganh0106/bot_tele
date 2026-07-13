#!/bin/bash
# ============================================
# SCRIPT CẬP NHẬT CODE AN TOÀN — KHÔNG MẤT DỮ LIỆU
# ============================================
# Chạy trên EC2: bash update.sh
# Script này:
#   1. Backup database trước khi pull
#   2. Pull code mới
#   3. Đảm bảo database không bị ghi đè
#   4. Restart bot

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

echo "🔄 Bắt đầu cập nhật bot..."

# 1. Tạo thư mục data ngoài git (nếu chưa có)
mkdir -p "$DATA_DIR"
mkdir -p "$BACKUP_DIR"

# 2. Di chuyển data cũ ra ngoài git (nếu đang nằm trong project)
if [ -f "$PROJECT_DIR/data/bot_data.json" ] && [ ! -f "$DB_FILE" ]; then
    echo "📦 Phát hiện database cũ trong project, di chuyển ra ngoài git..."
    cp "$PROJECT_DIR/data/bot_data.json" "$DB_FILE"
    echo "✅ Đã di chuyển database sang: $DB_FILE"
fi

# 3. Backup database trước khi pull
if [ -f "$DB_FILE" ]; then
    TIMESTAMP=$(date +%Y%m%d_%H%M%S)
    BACKUP_FILE="$BACKUP_DIR/bot_data_before_update_${TIMESTAMP}.json"
    cp "$DB_FILE" "$BACKUP_FILE"
    echo "💾 Đã backup database: $BACKUP_FILE"
    
    # Giữ tối đa 30 bản backup
    ls -t "$BACKUP_DIR"/bot_data_*.json 2>/dev/null | tail -n +31 | xargs rm -f 2>/dev/null || true
fi

# 4. Pull code mới từ git repository hiện tại và copy sang PROJECT_DIR
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
if [ -d "$SCRIPT_DIR/.git" ] || git -C "$SCRIPT_DIR" rev-parse --is-inside-work-tree &>/dev/null; then
    cd "$SCRIPT_DIR"
    echo "📥 Pulling code mới tại $SCRIPT_DIR..."
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
    
    # Dọn file data cũ trong git (nếu có)
    git rm -f data/bot_data.json 2>/dev/null || true
    rm -f data/bot_data.json 2>/dev/null || true
else
    echo "⚠️ Thư mục hiện tại ($SCRIPT_DIR) không phải là git repository. Bỏ qua git pull."
fi

# Sao chép code mới từ SCRIPT_DIR sang PROJECT_DIR
if [ "$SCRIPT_DIR" != "$PROJECT_DIR" ]; then
    echo "🚚 Đang sao chép code mới sang $PROJECT_DIR..."
    FILES_TO_COPY=("bot.py" "jobs.py" "test_binance.py" "ctv_api.py" "database.py" "binance_client.py" "i18n.py" "sepay_server.py" "config.env.example" "requirements.txt" "ctv-bot.service")
    for file in "${FILES_TO_COPY[@]}"; do
        if [ -f "$SCRIPT_DIR/$file" ]; then
            cp "$SCRIPT_DIR/$file" "$PROJECT_DIR/"
            echo "   -> Đã copy $file"
        fi
    done

    for dir in core handlers; do
        if [ -d "$SCRIPT_DIR/$dir" ]; then
            rm -rf "$PROJECT_DIR/$dir"
            cp -r "$SCRIPT_DIR/$dir" "$PROJECT_DIR/"
            echo "   -> Đã copy $dir/"
        fi
    done
    rm -rf "$PROJECT_DIR/__pycache__" "$PROJECT_DIR/core/__pycache__" "$PROJECT_DIR/handlers/__pycache__"
    
    # Chỉ copy config.env nếu bên PROJECT_DIR chưa có
    if [ -f "$SCRIPT_DIR/config.env" ] && [ ! -f "$PROJECT_DIR/config.env" ]; then
        cp "$SCRIPT_DIR/config.env" "$PROJECT_DIR/"
        echo "   -> Đã copy config.env"
    fi
fi

cd "$PROJECT_DIR"

# 5. Cài dependencies mới
echo "📦 Cập nhật dependencies..."
if [ -f "$PROJECT_DIR/venv/bin/python" ]; then
    "$PROJECT_DIR/venv/bin/python" -m pip install -r requirements.txt -q
else
    echo "⚠️ Không tìm thấy venv tại $PROJECT_DIR/venv. Bỏ qua cài đặt pip."
fi

# 6. Đảm bảo config.env không bị thiếu
if [ ! -f "config.env" ]; then
    if [ -f "config.env.example" ]; then
        echo "📝 Tạo config.env từ config.env.example..."
        cp config.env.example config.env
        echo "⚠️ CẢNH BÁO: Vui lòng sửa config.env tại $PROJECT_DIR với thông tin cấu hình thực tế của bạn!"
    else
        echo "❌ config.env bị thiếu!"
    fi
fi

# 7. Đảm bảo biến DATA_DIR được set trong service
if ! grep -q "DATA_DIR" /etc/systemd/system/ctv-bot.service 2>/dev/null; then
    echo "⚙️ Cập nhật systemd service với DATA_DIR..."
    sudo sed -i "/^Environment=PYTHONUNBUFFERED=1/a Environment=DATA_DIR=$DATA_DIR" /etc/systemd/system/ctv-bot.service
    sudo systemctl daemon-reload
fi

# 8. Restart bot
echo "🔄 Restarting bot..."
sudo systemctl restart ctv-bot

# 9. Đợi 3 giây và kiểm tra
sleep 3
if sudo systemctl is-active --quiet ctv-bot; then
    echo ""
    echo "✅ ============================================"
    echo "✅ CẬP NHẬT THÀNH CÔNG!"
    echo "✅ ============================================"
    echo ""
    echo "📂 Database: $DB_FILE"
    echo "💾 Backup: $BACKUP_FILE"
    echo ""
    echo "📌 Xem logs: sudo journalctl -u ctv-bot -f --no-pager -n 50"
else
    echo ""
    echo "❌ Bot không khởi động được! Kiểm tra logs:"
    echo "   sudo journalctl -u ctv-bot -n 30 --no-pager"
fi
