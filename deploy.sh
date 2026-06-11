#!/bin/bash
# ============================================
# SCRIPT DEPLOY BOT BÁN CTV LÊN AWS EC2
# ============================================
# Chạy trên EC2 Ubuntu 22.04/24.04
# Usage: bash deploy.sh

set -e

echo "🚀 Bắt đầu deploy CTV Bot..."

# 1. Cập nhật hệ thống
echo "📦 Cập nhật hệ thống..."
sudo apt update && sudo apt upgrade -y

# 2. Cài Python 3.11+
echo "🐍 Cài đặt Python..."
sudo apt install -y python3 python3-pip python3-venv

# 3. Xác định thư mục chứa script và tạo thư mục project
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_DIR="/home/ubuntu/ctv-bot"

echo "📂 Tạo thư mục project: $PROJECT_DIR"
mkdir -p $PROJECT_DIR

# Tự động copy files từ thư mục chạy script vào PROJECT_DIR nếu khác nhau
if [ "$SCRIPT_DIR" != "$PROJECT_DIR" ]; then
    echo "🚚 Đang sao chép files từ $SCRIPT_DIR sang $PROJECT_DIR..."
    FILES_TO_COPY=("bot.py" "ctv_api.py" "database.py" "sepay_server.py" "config.env" "config.env.example" "requirements.txt" "ctv-bot.service")
    for file in "${FILES_TO_COPY[@]}"; do
        if [ -f "$SCRIPT_DIR/$file" ]; then
            cp "$SCRIPT_DIR/$file" "$PROJECT_DIR/"
            echo "   -> Đã copy $file"
        else
            echo "   ⚠️ Cảnh báo: Không tìm thấy $file tại $SCRIPT_DIR"
        fi
    done
fi

cd $PROJECT_DIR

# Tạo config.env từ template nếu chưa tồn tại
if [ ! -f "$PROJECT_DIR/config.env" ]; then
    if [ -f "$PROJECT_DIR/config.env.example" ]; then
        echo "📝 Tạo config.env từ config.env.example..."
        cp "$PROJECT_DIR/config.env.example" "$PROJECT_DIR/config.env"
        echo "⚠️ CẢNH BÁO: Vui lòng sửa config.env tại $PROJECT_DIR với thông tin cấu hình thực tế của bạn!"
    else
        echo "❌ Thiếu file cấu hình config.env hoặc config.env.example!"
        echo "   Vui lòng tạo file config.env tại $PROJECT_DIR trước khi tiếp tục."
        exit 1
    fi
fi

# 4. Tạo virtual environment
echo "🔧 Tạo virtual environment..."
python3 -m venv venv
source venv/bin/activate

# 5. Kiểm tra các file code bắt buộc sau khi copy
echo "📋 Kiểm tra files..."
REQUIRED_FILES=("bot.py" "ctv_api.py" "database.py" "sepay_server.py" "requirements.txt")
for file in "${REQUIRED_FILES[@]}"; do
    if [ ! -f "$file" ]; then
        echo "❌ Thiếu file: $file"
        echo "   Hãy copy tất cả files vào $PROJECT_DIR trước khi chạy script này!"
        exit 1
    fi
done

# 6. Cài dependencies
echo "📥 Cài đặt dependencies..."
pip install -r requirements.txt

# 7. Tạo thư mục data
mkdir -p data

# 8. Cấu hình systemd service
echo "⚙️ Cấu hình systemd service..."
sudo cp ctv-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable ctv-bot
sudo systemctl start ctv-bot

# 9. Mở port trên firewall (nếu dùng ufw)
echo "🔓 Mở port webhook..."
WEBHOOK_PORT=$(grep WEBHOOK_PORT config.env | cut -d'=' -f2)
sudo ufw allow $WEBHOOK_PORT/tcp 2>/dev/null || true

echo ""
echo "✅ ============================================"
echo "✅ DEPLOY THÀNH CÔNG!"
echo "✅ ============================================"
echo ""
echo "📌 Kiểm tra bot:"
echo "   sudo systemctl status ctv-bot"
echo ""
echo "📌 Xem logs:"
echo "   sudo journalctl -u ctv-bot -f"
echo ""
echo "📌 Restart bot:"
echo "   sudo systemctl restart ctv-bot"
echo ""
echo "📌 URL webhook SePay:"
echo "   http://YOUR_EC2_IP:${WEBHOOK_PORT}/sepay/webhook"
echo ""
echo "⚠️ QUAN TRỌNG:"
echo "   1. Sửa config.env với thông tin thật"
echo "   2. Mở port ${WEBHOOK_PORT} trong AWS Security Group"
echo "   3. Cấu hình webhook URL trên SePay dashboard"
echo "   4. Sau khi sửa config: sudo systemctl restart ctv-bot"
