# Optimize system logic and processing speed

## Goal

Tối ưu toàn diện bot Telegram bán hàng (CTV bot): tốc độ phản hồi nút bấm, độ đúng đắn của logic đơn hàng/thanh toán, và tốc độ gọi API ngoài — đồng thời sửa 2 bug đã phát hiện trong lúc khảo sát.

## Bugs phát hiện (bắt buộc sửa)

1. **`core/products.py` — `_fetch_api1` mất thân hàm**: khối `try: api.get_stock()` bị lạc xuống sau `return` của `get_hypervin_balance` (dead code, dòng ~108-115). Khi circuit breaker đóng (bình thường), `_fetch_api1` trả `None` → `_do_refresh_products` unpack `TypeError` → sản phẩm CTV không load được.
2. **`jobs.py:101-108` — `global _api_cache` gán nhầm biến module**: `_periodic_product_refresh` rebind `jobs._api_cache` (đã import by-value) thay vì cập nhật cache của `core.products` → refresh định kỳ 30s vô tác dụng; chỉ `post_init` (dùng `.update()`) là có tác dụng.

## Requirements

### R1 — Tốc độ phản hồi (DB write không chặn event loop)
- Ghi DB "immediate" hiện serialize toàn bộ JSON (indent=2) đồng bộ ngay trong handler async → chặn event loop mỗi lần có đơn/nạp tiền. Chuyển sang writer thread nền: handler chỉ đánh dấu dirty, thread nền dump + atomic replace.
- Bỏ `indent=2` khi ghi file chính (file máy tự quản lý; backup vẫn giữ nguyên bản copy).
- Cửa sổ mất dữ liệu khi crash phải ≤ ~1 giây cho dữ liệu critical (orders/payments); `flush()` khi shutdown vẫn đồng bộ.

### R2 — Logic đơn hàng & thanh toán
- Rà soát race: `_payment_processor`, `poll_binance_deposits`, `_retry_failed_orders` có thể đụng cùng 1 đơn — xác nhận `process_paid_order` claim đơn atomic; vá nếu thiếu.
- Rà double-click nút thanh toán (paid/paybank/paywallet…) — xác nhận có claim/guard.
- Sửa 2 bug ở mục trên.

### R3 — Tốc độ gọi API ngoài
- `binance_client.py`: dùng `requests.Session` (connection pooling) thay vì `requests.request` trần — giảm TLS handshake mỗi lần poll.
- Giữ nguyên hành vi API/chữ ký; không đổi contract.

### Ràng buộc
- KHÔNG migrate JSON → SQLite trong task này (rủi ro với hệ thống đang chạy thật; ghi nhận làm task sau nếu cần).
- Không đổi schema dữ liệu `bot_data.json` (tương thích ngược tuyệt đối — file đang chạy trên EC2).
- Không đổi hành vi nghiệp vụ (giá, luồng thanh toán, thông báo admin).
- Code chạy được trên Python hiện tại của EC2 (3.10+) và Windows dev.

## Acceptance Criteria

- [ ] `_fetch_api1` gọi được `api.get_stock()`; `_do_refresh_products` trả về products CTV khi API sống (test bằng mock).
- [ ] Refresh định kỳ trong `jobs.py` thực sự cập nhật cache mà `core.products.get_products_cached()` đọc (test bằng mock/inspect).
- [ ] Ghi DB immediate không còn thực hiện `json.dump` trong thread gọi (handler); dữ liệu vẫn ra đĩa ≤ ~1s; `flush()` shutdown đồng bộ và trọn vẹn.
- [ ] File DB ghi dạng compact (không indent); đọc lại tương thích file cũ có indent.
- [ ] `tools/bench_db.py` vẫn PASS correctness; đo thời gian ghi trước/sau cho thấy cải thiện.
- [ ] `BinanceClient` dùng Session; test hiện có (`test_binance.py`, `bot_tele/tests/test_binance_client.py` nếu áp dụng) pass.
- [ ] Toàn bộ pytest pass; `tools/smoke_import.py` pass.
- [ ] Không có thay đổi schema dữ liệu trong `bot_data.json`.
