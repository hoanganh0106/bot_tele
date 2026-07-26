# Implement — Tích hợp nguồn hàng Hypervin

> Làm theo thứ tự. Mỗi bước có cách kiểm tra riêng. Repo làm việc: thư mục gốc `d:\Du_an\bot_tele`; sau khi xong sync sang repo lồng `bot_tele/` (bước 8).

## Bước 0 — Chốt response đơn thành công (BẮT BUỘC trước khi code bước 4)

- [ ] Nạp ví Hypervin ≥ 10.000đ (link PayOS đã tạo sẵn invoice 10k, hoặc tạo mới qua `POST /api/wallet/topup`).
- [ ] Gọi thử 1 đơn thật bằng curl:
  ```bash
  curl -s -X POST -H "X-API-Key: $HYPERVIN_API_KEY" -H "Content-Type: application/json" \
    -d '{"product_id":"cc_7d","quantity":1}' https://hypervin.xyz/api/orders
  ```
- [ ] Dán response thành công vào `design.md` mục 1 (thay dòng "Response THÀNH CÔNG chưa biết"), xác định: field chứa list tài khoản, field mã đơn, field số tiền đã trừ.
- Kiểm tra: `POST /api/wallet/balance` thấy số dư giảm đúng 10.000đ.

## Bước 1 — Client `hypervin_client.py` (file mới ở gốc repo)

- [ ] Class `HypervinApi(base_url, api_key)` theo pattern `ctv_api.py`: `requests.Session`, header mặc định `X-API-Key` + `Content-Type: application/json`, `Retry(total=3, backoff_factor=0.5, allowed_methods=["GET"])`.
- [ ] `get_products() -> dict | None`: GET `/api/products`, validate `success=True` và `products` là list; convert về `{f"hv_{p['id']}": {"name": p["name"].strip(), "price": p["price"], "stock": p["stock"], "api_source": "HYPERVIN"}}`. Lỗi/timeout/shape sai → log + return None.
- [ ] `get_balance() -> int | None`: POST `/api/wallet/balance` body `{}`.
- [ ] `create_order(product_id, qty) -> dict`: POST `/api/orders` body `{"product_id": ..., "quantity": ...}`, timeout 30s, KHÔNG retry; map response (theo bước 0) về `{success, items: list[str], total_charged: int, api_order_code: str, error: str|None}`; map lỗi mạng/timeout/JSON hỏng thành `{success: False, error: "..."}` tiếng Việt như `CTVApi.buy`.
- Kiểm tra: script tay `python -c "from hypervin_client import HypervinApi; c=HypervinApi('https://hypervin.xyz','<key>'); print(len(c.get_products() or {}), c.get_balance())"` in ra ~40 và số dư.

## Bước 2 — Config + runtime

- [ ] `config.env.example`: thêm khối `# ---------- HYPERVIN API (ĐỐI TÁC 3) ----------` với `HYPERVIN_API_URL=https://hypervin.xyz`, `HYPERVIN_API_KEY=YOUR_HYPERVIN_API_KEY`, `HYPERVIN_LOW_BALANCE_ALERT=50000`.
- [ ] `core/config.py`: load 3 biến (int cho ngưỡng).
- [ ] `core/runtime.py`: theo pattern Binance (dòng 20–26 hiện tại):
  ```python
  HYPERVIN_ENABLED = bool(HYPERVIN_API_KEY) and not HYPERVIN_API_KEY.upper().startswith("YOUR_")
  hypervin = HypervinApi(HYPERVIN_API_URL, HYPERVIN_API_KEY) if HYPERVIN_ENABLED else None
  ```
  kèm `logger.warning` khi tắt.
- Kiểm tra: `python tools/smoke_import.py` (hoặc import bot) chạy sạch cả khi key trống lẫn khi có key.

## Bước 3 — Gộp sản phẩm (`core/products.py`)

- [ ] Thêm `"HYPERVIN": {"failures": 0, "last_fail": 0, "cooldown": 60}` vào `_circuit_breaker`.
- [ ] Viết `_fetch_api2()` giống `_fetch_api1`: nếu `hypervin is None` → `({}, None)` ngay; check `_is_circuit_open("HYPERVIN")`; gọi `hypervin.get_products()` + `hypervin.get_balance()`; `_record_api_result("HYPERVIN", products is not None)`.
- [ ] Cache số dư: module-level `_hv_balance` + helper `get_hypervin_balance() -> int | None`.
- [ ] `_do_refresh_products()`: submit song song `_fetch_api1`/`_fetch_api2` vào `_api_executor`, merge CTV → Hypervin → custom (giữ nguyên phần custom hiện có). Tuple trả về `(products, balance)` GIỮ NGUYÊN nghĩa balance = CTV (design D3).
- [ ] `jobs.py` (~dòng 580, log pre-warm): thêm đếm `api2_count` theo `api_source == "HYPERVIN"` vào log.
- Kiểm tra: chạy bot local (`python bot.py`), log `Cache pre-warmed: N products (API1: x, HYPERVIN: y, Custom: z)`; `/menu` → danh mục "Khác" có sản phẩm Hypervin, giá = giá API + 10.000đ; tắt key → y = 0, không lỗi.

## Bước 4 — Định tuyến giao hàng (`handlers/payment.py`)

- [ ] Import `hypervin` từ `core.runtime`.
- [ ] Tại khối "Gọi API đối tác (CTV) mua hàng" (~dòng 685–691): định tuyến theo `api_source` như design D4; nhánh `HYPERVIN` mà `hypervin is None` → `result = {"success": False, "error": "Nguồn Hypervin đang tắt"}`.
- [ ] Đổi log `Calling CTV API` thành log kèm tên nguồn.
- [ ] Không sửa gì phía sau khối này (giao items / báo admin / profit dùng chung).
- Kiểm tra: đơn thật `hv_cc_7d` qua bot (ví đã nạp) — khách nhận tài khoản, admin thấy lãi = markup; và 1 đơn khi ví hết tiền — đơn failed + admin được báo hoàn tiền.

## Bước 5 — Số dư admin + cảnh báo (`handlers/admin.py`, `jobs.py`, `i18n.py`)

- [ ] Màn admin đang hiện số dư CTV: thêm dòng `Hypervin: {số dư}` (đọc `get_hypervin_balance()`, hiện "—" nếu None/tắt).
- [ ] `jobs.py` `_periodic_product_refresh`: sau refresh, check ngưỡng `HYPERVIN_LOW_BALANCE_ALERT` (0 = tắt), notify admin với cờ chống spam (chỉ báo lại sau khi hồi trên ngưỡng).
- [ ] Chuỗi mới thêm đủ VI/EN vào `i18n.py`.
- Kiểm tra: set ngưỡng > số dư hiện tại → nhận đúng 1 cảnh báo, không lặp mỗi 90s.

## Bước 6 — Unit test

- [ ] `tests/test_hypervin_client.py` theo mẫu `bot_tele/tests/test_binance_client.py` (mock `requests`): get_products convert đúng + prefix `hv_`; shape sai → None; create_order map lỗi "Insufficient balance"/"Product not found"/timeout → `{success: False}`; response thành công (fixture từ bước 0) → normalize đúng.
- Kiểm tra: `python -m pytest tests/test_hypervin_client.py -q` pass; chạy thêm toàn bộ test có sẵn không vỡ.

## Bước 7 — Rà soát chéo

- [ ] Grep `get_all_products_merged\(\)|get_products_cached\(\)` — xác nhận không chỗ nào hiểu balance là "tổng 2 nguồn".
- [ ] Grep `api\.buy\(` — chỉ còn đường CTV trong nhánh else của payment.py.
- [ ] Xác nhận `handlers/customer.py:494` hiện tag `[HYPERVIN]` (tự chạy, chỉ xem lại).

## Bước 8 — Sync repo lồng + deploy EC2

- [ ] Copy các file đổi/mới sang `bot_tele/` (repo lồng), commit repo con TRƯỚC, rồi commit gitlink ở repo cha (quy trình nested-git đã dùng).
- [ ] EC2: thêm `HYPERVIN_API_URL`, `HYPERVIN_API_KEY`, `HYPERVIN_LOW_BALANCE_ALERT` vào config.env tại `/home/ubuntu/ctv-bot/`; chạy `update.sh` (nhớ gotcha `unset GITHUB_TOKEN` nếu pull lỗi credentials).
- [ ] Xem log service: dòng `Cache pre-warmed` có HYPERVIN > 0; bấm thử menu.
- Rollback nếu sự cố: xóa `HYPERVIN_API_KEY` trong config.env + restart service (không cần revert code).

## Ghi chú

- KHÔNG commit API key thật vào bất kỳ file nào (kể cả task artifacts). Key chỉ nằm trong config.env local/EC2.
- Sản phẩm test rẻ nhất: `cc_7d` — CapCut Pro 7 ngày, 10.000đ.
