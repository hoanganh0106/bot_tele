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

# 4. Pull code mới
cd "$PROJECT_DIR"
echo "📥 Pulling code mới..."
git pull origin main || {
    echo "⚠️ Git pull conflict, force reset..."
    git fetch origin main
    git reset --hard origin/main
}

# Dọn file data cũ trong git (đã chuyển ra DATA_DIR)
git rm -f data/bot_data.json 2>/dev/null || true
rm -f data/bot_data.json 2>/dev/null || true

# 5. Cài dependencies mới (dùng python -m pip để an toàn hơn)
echo "📦 Cập nhật dependencies..."
if [ -f "$PROJECT_DIR/venv/bin/python" ]; then
    "$PROJECT_DIR/venv/bin/python" -m pip install -r requirements.txt -q
else
    echo "⚠️ Không tìm thấy venv tại $PROJECT_DIR/venv. Bỏ qua cài đặt pip."
fi

# 6. Đảm bảo config.env KHÔNG bị ghi đè
if [ ! -f "config.env" ]; then
    echo "⚠️ config.env bị thiếu! Khôi phục từ git..."
    git checkout config.env 2>/dev/null || echo "❌ Không tìm thấy config.env trong git"
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
