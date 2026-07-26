# Design — Tích hợp nguồn hàng Hypervin

## 1. API contract đã xác minh (dò thực tế 2026-07-17, không có docs chính thức)

Base URL: `https://hypervin.xyz` (env `HYPERVIN_API_URL`). Xác thực: header `X-API-Key: sk_...` cho MỌI endpoint.

> ⚠️ BẪY: `GET /api/products` không có key (hoặc dùng `Authorization: Bearer`) vẫn trả 200 nhưng là format catalog CÔNG KHAI khác hẳn — stock nằm trong chuỗi tag tiếng Việt `"Còn hàng (55)"`, giá nằm trong `priceOptions[]`. TUYỆT ĐỐI dùng `X-API-Key` để nhận format đại lý bên dưới.

### GET /api/products (X-API-Key)
```json
{"success": true, "products": [
  {"id": "gmail_us", "name": " GMAIL US ...", "price": 69000, "group": "", "stock": 55, "format": ""}
]}
```
- ~40 sản phẩm, giá VND, `stock` là số nguyên. `id` là string (có cái dạng số như `"14"`).
- Khác CTV: `products` là **list** (CTV trả dict keyed theo product_key), và **không có field balance** trong response.

### POST /api/orders (X-API-Key, Content-Type: application/json)
- Body: `{"product_id": "cc_7d", "quantity": 3}` — snake_case; `productId`/`qty` bị từ chối.
- Trừ ví = giá niêm yết × quantity, **không chiết khấu đại lý** → lãi của bot = markup của mình.
- Lỗi (HTTP kèm `{"success": false, "error": "..."}`):
  - `"product_id is required"`
  - `"Product not found"`
  - `"Insufficient balance. Need: 30000, have: 0"`
- **Response THÀNH CÔNG chưa biết** (ví 0đ chưa test được). Việc đầu tiên khi implement: nạp ví ≥10k, gọi thử 1 đơn `cc_7d` (10.000đ) bằng curl, ghi lại response vào mục này rồi mới code `_normalize_order_result()`. Giả định hợp lý: có field chứa list tài khoản (`items`/`data`/`accounts`) — PHẢI xác nhận, không đoán.

### POST /api/wallet/balance (X-API-Key, body `{}`)
```json
{"success": true, "user_id": 5846376104, "balance": 0, "balance_formatted": "0đ"}
```

### POST /api/wallet/topup (X-API-Key) — ngoài phạm vi task, ghi để tham khảo
- Body `{"amount": 10000}` (min 10000 VND) → `{order_id, order_number, amount, checkout_url (PayOS), qr_code (VietQR), account_number, account_name, bin}`.

## 2. Kiến trúc & luồng dữ liệu

```
core/runtime.py          hypervin_client.py (MỚI)
  api  = CTVApi(...)       class HypervinApi
  hypervin = HypervinApi(...) | None   ← None nếu key trống (flag tắt)
        │
        ▼
core/products.py::_do_refresh_products()
  _fetch_api1() ──┐ (song song qua _api_executor)
  _fetch_api2() ──┤→ merge dict products {key → info}
                  │   CTV giữ key gốc; Hypervin key = "hv_" + id
                  │   info Hypervin: {name, price, stock, api_source: "HYPERVIN"}
                  └→ balances (xem quyết định D3)
        │
        ▼ (cache sẵn có: _api_cache, TTL 120s/stale 1800s — không đổi)
handlers/customer.py     → hiển thị: giá qua get_sell_price() (tự +markup), tag [HYPERVIN] tự chạy vì đọc api_source động
handlers/payment.py      → giao hàng: định tuyến theo api_source (xem D4)
jobs.py / handlers/admin.py → số dư + cảnh báo ngưỡng
```

## 3. Quyết định thiết kế

**D1 — Client mirror theo `CTVApi`** (`ctv_api.py`): `requests.Session` + `HTTPAdapter` retry (total=3, backoff 0.5, chỉ `allowed_methods=["GET"]`), timeout GET 5s / POST order 30s. API riêng của class:
- `get_products() -> dict | None` — trả về dict đã convert `{f"hv_{id}": {name, price, stock, api_source: "HYPERVIN"}}`, None khi lỗi (để circuit breaker đếm).
- `get_balance() -> int | None`
- `create_order(product_id: str, qty: int) -> dict` — nhận id ĐÃ BỎ prefix; trả dict đã chuẩn hóa `{success, items, total_charged, api_order_code, error}`.
Chuẩn hóa nằm TRONG client để payment.py không biết chi tiết Hypervin.

**D2 — Namespace key `hv_`**: dict products dùng chung không gian key với CTV và custom products; prefix tránh va chạm (CTV có key kiểu `slot_gpt_team`, Hypervin có `cc_7d`, `14`...). Mọi cơ chế theo key có sẵn (price_delta, custom_categories, custom_names, custom_stocks...) tự hoạt động với key mới. Khi gọi đơn: `product_key.removeprefix("hv_")`.

**D3 — Số dư: KHÔNG đổi chữ ký `(products, balance)`**. Lý do: tuple này được unpack ở ≥6 chỗ (products.py, jobs.py:97/573, admin, customer); đổi thành dict balances lan rộng và dễ sót. Thay vào đó:
- `balance` trong tuple giữ nguyên = số dư CTV (nghĩa hiện tại, không chỗ nào hiểu sai đi).
- Số dư Hypervin cache module-level trong `core/products.py`: `_hv_balance = {"value": None, "ts": 0}`, cập nhật bởi `_fetch_api2()` (gọi `get_balance()` cùng lượt refresh); admin/jobs đọc qua helper `get_hypervin_balance()`.

**D4 — Định tuyến giao hàng** trong `handlers/payment.py` (quanh dòng 685–691): thay khối gọi `api.buy(...)` bằng:
```python
info = products.get(product_key, {})
if info.get("api_source") == "HYPERVIN" and hypervin:
    result = await asyncio.to_thread(lambda: hypervin.create_order(product_key.removeprefix("hv_"), qty))
else:
    result = await asyncio.to_thread(lambda: api.buy(product_key, qty, emails=emails if emails else None))
```
Toàn bộ phần sau (complete_order_payment, gửi items cho khách, báo admin, profit = total − total_charged) giữ NGUYÊN nhờ format chuẩn hóa ở D1. Nhánh lỗi/thiếu sản phẩm giữ nguyên. Trường hợp `api_source == "HYPERVIN"` nhưng `hypervin is None` (admin vừa tắt key giữa chừng): trả failed với error rõ ràng, rơi vào nhánh hoàn tiền có sẵn.

**D5 — Circuit breaker & fetch song song**: thêm entry `"HYPERVIN"` vào `_circuit_breaker` (core/products.py) — cùng tham số CTV (threshold 3, cooldown 60→300s). `_do_refresh_products()` submit `_fetch_api1` và `_fetch_api2` cùng lúc vào `_api_executor` (pool 3 worker có sẵn), `.result(timeout=10)` từng future; lỗi/timeout một bên chỉ mất sản phẩm bên đó. Thứ tự merge: CTV → Hypervin → custom (custom đè cuối như hiện tại; prefix `hv_` bảo đảm CTV/Hypervin không đè nhau).

**D6 — Idempotency đặt đơn**: API không nhận order_code ngoài nên chống-mua-trùng dựa vào cơ chế có sẵn phía bot (đơn chuyển trạng thái atomic qua `db.complete_order_payment`, POST không retry). Ghi nhận: nếu timeout ĐÚNG LÚC POST order thì không biết đơn đã trừ ví chưa → xử lý như CTV hiện tại (đơn failed + admin check tay). Không làm phức tạp hơn ở phase này.

**D7 — Cảnh báo số dư thấp**: gắn vào vòng refresh sẵn có trong `jobs.py` (`_periodic_product_refresh`): sau mỗi refresh, nếu `get_hypervin_balance() < HYPERVIN_LOW_BALANCE_ALERT` (env, mặc định 50000; 0 = tắt) → notify admin, có cờ chống spam (chỉ báo lại khi số dư hồi lên trên ngưỡng rồi tụt xuống lại — pattern giống cảnh báo Binance poll fail).

## 4. Tương thích & rollback

- Key trống → `hypervin = None` → `_fetch_api2` trả rỗng ngay, không log lỗi ồn ào; hành vi bot giống 100% trước thay đổi.
- Không migration DB (price_deltas/custom_* nhận key `hv_*` tự nhiên).
- Rollback vận hành: xóa `HYPERVIN_API_KEY` khỏi config.env trên EC2 + restart. Sản phẩm `hv_*` biến khỏi menu; đơn `hv_*` đang pending sẽ rơi vào nhánh "sản phẩm không còn trên API" có sẵn (admin được báo hoàn tiền).

## 5. Rủi ro còn lại

| Rủi ro | Ứng phó |
|---|---|
| Response đơn thành công khác giả định | Bước 0 của implement.md: test đơn thật bằng curl TRƯỚC khi code normalize |
| Hypervin đổi format không báo (không docs) | Client validate shape (`success`, `products` là list...), lỗi shape → trả None → circuit breaker + log; bot không crash |
| Giá/stock đổi giữa lúc khách xem và lúc mua | Chấp nhận như CTV hiện tại (API từ chối nếu hết hàng → failed + hoàn tiền) |
| `id` sản phẩm Hypervin trùng nội bộ sau khi bỏ prefix | Không xảy ra: chỉ bỏ prefix khi gọi API Hypervin |
