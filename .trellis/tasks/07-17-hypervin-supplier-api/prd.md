# Tích hợp API nguồn hàng Hypervin (hypervin.xyz)

## Goal

Thêm hypervin.xyz làm nguồn hàng thứ 2 (bên cạnh CTV) cho bot bán hàng Telegram: sản phẩm Hypervin hiện trên menu khách, bán được và giao tài khoản tự động sau thanh toán, admin theo dõi được số dư ví — mà không thay đổi hành vi hiện có của nguồn CTV.

## Bối cảnh

- Bot hiện có 1 nguồn hàng duy nhất (CTV qua `ctv_api.py`), khởi tạo cứng ở `core/runtime.py`, bước giao hàng trong `handlers/payment.py` gọi thẳng `api.buy()`.
- Hypervin KHÔNG có tài liệu API. Toàn bộ contract đã được dò thực tế ngày 2026-07-17 (xem `design.md` mục "API contract đã xác minh").
- Ví Hypervin đang 0đ; user sẽ nạp trước khi test đơn thật.

## Requirements

### R1 — Client Hypervin
- Module mới `hypervin_client.py` gọi được 3 endpoint: danh sách sản phẩm, đặt đơn, số dư ví.
- Xác thực bằng header `X-API-Key` (bắt buộc — header `Authorization: Bearer` trả về format catalog công khai sai lệch, xem design).
- Retry chỉ áp dụng cho GET; POST đặt đơn KHÔNG retry (tránh mua trùng).

### R2 — Cấu hình & feature flag
- 2 biến env mới: `HYPERVIN_API_URL`, `HYPERVIN_API_KEY` (thêm vào `config.env.example`, load ở `core/config.py`, khởi tạo client ở `core/runtime.py`).
- `HYPERVIN_API_KEY` trống hoặc chưa điền → nguồn Hypervin tắt hoàn toàn, bot chạy y như hiện tại (đây cũng là phương án rollback, giống pattern Binance).

### R3 — Sản phẩm trên menu
- Bật TẤT CẢ sản phẩm Hypervin (user tự chỉnh/ẩn/phân loại bằng quyền admin sau).
- Key sản phẩm prefix `hv_` để không đụng key CTV; tag `api_source: "HYPERVIN"`.
- Sản phẩm mới tự vào danh mục "Khác" (cơ chế `classify_product` có sẵn, không sửa).
- Giá bán = giá API + markup theo cơ chế CÓ SẴN `get_sell_price()` (mặc định +10.000đ, admin chỉnh delta từng sản phẩm) — KHÔNG code thêm logic giá.
- Một nguồn API chết không được kéo nguồn kia chết theo (fetch song song, circuit breaker riêng).

### R4 — Giao hàng sau thanh toán
- Đơn của sản phẩm Hypervin được định tuyến sang client Hypervin theo `api_source`; đơn CTV giữ nguyên đường cũ.
- Kết quả đơn Hypervin chuẩn hóa về format API 1 (`{success, items, total_charged, api_order_code, error}`) để tái dùng nguyên phần giao tài khoản cho khách / báo admin / tính lãi.
- Đơn lỗi (hết hàng, thiếu số dư ví...) → đơn chuyển `failed`, khách nhận thông báo lỗi chuẩn, admin được cảnh báo hoàn tiền — giống hành vi hiện tại với CTV.

### R5 — Số dư & cảnh báo
- Admin xem được số dư ví Hypervin (cạnh số dư CTV trong màn admin).
- Cảnh báo admin khi số dư Hypervin dưới ngưỡng (env `HYPERVIN_LOW_BALANCE_ALERT`, mặc định hợp lý, 0 = tắt).
- Chuỗi hiển thị mới có đủ VI/EN trong `i18n.py`.

### Ngoài phạm vi (làm sau)
- Lệnh admin nạp ví qua `POST /api/wallet/topup` (nạp thủ công qua web/PayOS trước).
- Markup % riêng cho nguồn Hypervin (dùng chung cơ chế delta hiện có).

## Acceptance Criteria

- [ ] Chưa điền `HYPERVIN_API_KEY`: bot khởi động và hoạt động y hệt trước khi có thay đổi (log cảnh báo nguồn Hypervin tắt).
- [ ] Điền key: log `Cache pre-warmed` hiện số sản phẩm HYPERVIN > 0; menu khách hiện sản phẩm Hypervin trong "Khác" với giá = giá API + 10.000đ.
- [ ] Admin đổi delta 1 sản phẩm `hv_*` → giá hiển thị đổi theo (cơ chế cũ hoạt động với key mới).
- [ ] Mua thật 1 đơn `hv_cc_7d` (CapCut 10k, ví đã nạp): khách nhận tài khoản, admin nhận thông báo lãi = markup, đơn `paid` trong DB với `api_order_code` của Hypervin.
- [ ] Giả lập Hypervin chết (sai URL): menu vẫn hiện sản phẩm CTV, circuit breaker HYPERVIN mở, không crash.
- [ ] Đặt đơn khi ví Hypervin 0đ: đơn `failed`, khách nhận thông báo lỗi, admin nhận cảnh báo hoàn tiền.
- [ ] Unit test client mới pass (`tests/test_hypervin_client.py`, mock HTTP).
- [ ] Code sync sang repo lồng `bot_tele/` và deploy EC2 qua `update.sh` thành công.
