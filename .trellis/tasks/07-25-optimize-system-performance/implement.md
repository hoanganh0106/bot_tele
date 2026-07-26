# Implement — Optimize system logic and processing speed

Thứ tự làm từ an toàn → phức tạp; mỗi bước có lệnh kiểm tra riêng. Chạy test bằng `python -m pytest` từ repo root (venv `.venv`).

## Bước 1 — Sửa bug `core/products.py::_fetch_api1` (bug fix, không hành vi mới)
- [x] Khôi phục thân hàm `_fetch_api1` (try/except gọi `api.get_stock()`), dọn dead code sau `return` trong `get_hypervin_balance`.
- Kiểm tra: `python tools/smoke_import.py`; viết test nhỏ mock `api.get_stock` → `_do_refresh_products()` trả products không raise.

## Bước 2 — Sửa bug cache refresh trong `jobs.py`
- [x] Thêm `set_api_cache(products, balance)` vào `core/products.py`.
- [x] `jobs.py::_periodic_product_refresh` và `post_init` dùng setter; xóa import `_api_cache` và mọi `global _api_cache` trong jobs.
- Kiểm tra: test mock — gọi setter rồi `get_products_cached()` phải trả dữ liệu mới.

## Bước 3 — `binance_client.py` dùng Session
- [x] Tạo `requests.Session` + `HTTPAdapter` trong `__init__`; `_signed_request` dùng `self.session.request`.
- Kiểm tra: `python -m pytest test_binance.py` (và `bot_tele/tests/test_binance_client.py` nếu chạy được từ root).

## Bước 4 — Database writer thread + compact JSON
- [x] Thêm `_flush_event`, `_writer_loop` (snapshot `json.dumps` dưới lock, ghi atomic ngoài lock), thread daemon khởi động trong `__init__`.
- [x] `_write(immediate=True)` → set event (không dump inline); debounce timer callback → set event.
- [x] `flush()` shutdown: cancel timer, dump + ghi đồng bộ dưới lock (không phụ thuộc writer thread).
- [x] Compact separators `(",", ":")`, bỏ `indent=2` (cả nhánh fallback).
- Kiểm tra:
  - `python tools/bench_db.py --orders 5000` → CORRECTNESS PASS.
  - Viết test: save_order → chờ ≤1s → file trên đĩa chứa order (writer hoạt động); flush() sau save → file chứa ngay.
  - Test đọc file cũ format indent → load OK.

## Bước 5 — Audit race đơn hàng/thanh toán (đọc trước, chỉ sửa khi có lỗ hổng)
- [x] Đọc `handlers/payment.py::process_paid_order` — xác nhận claim atomic pending→processing.
- [x] Xác nhận `_retry_failed_orders` không đụng đơn đang processing.
- [x] Xác nhận double-click guard ở `handle_pay_wallet` / `handle_paid_button`.
- [x] Ghi kết quả audit vào mục "Audit findings" bên dưới; vá + test nếu phát hiện lỗ hổng.

## Bước 6 — Kiểm tra toàn cục
- [x] `python -m pytest` toàn bộ.
- [x] `python tools/smoke_import.py`.
- [x] `python tools/bench_db.py` — so sánh trước/sau (ghi số liệu vào đây).
- [x] Rà `git diff` — không đổi schema, không đổi hành vi nghiệp vụ.

## Audit findings (điền khi thực hiện Bước 5)

- **LỖ HỔNG THẬT (đã vá):** `process_paid_order` check status không atomic ở đầu, gọi `api.buy`/`hypervin.create_order`, rồi mới chốt bằng `complete_order_payment` (atomic). 2 luồng đồng thời (retry job / payment processor / Binance poller / wallet handler) có thể cùng qua bước check → **mua 2 lần từ nhà cung cấp**. Vá: khóa `asyncio.Lock` per-order trong `handlers/payment.py` (`_fulfill_locks`) — hợp lệ vì mọi caller chạy chung 1 event loop; caller thứ 2 bị từ chối ngay (`lock.locked()`), không xếp hàng. Verify: script mô phỏng 2 call đồng thời → chỉ 1 lần fulfillment chạy.
- `handle_pay_wallet`: OK — đã có `claim_order_for_payment` (pending→processing atomic) chống double-click.
- `handle_cancel_order` / `auto_cancel_order` / `_cleanup_stale_orders`: OK — dùng `cancel_order_if_pending` atomic. Xóa 1 lệnh `release_usdt_amount` trùng lặp trong `handle_cancel_order`.
- Race lý thuyết còn lại (chấp nhận, xác suất cực thấp): auto-cancel đúng lúc fulfillment đang in-flight → `complete_order_payment` cho phép chuyển từ `cancelled_timeout` (cố ý, để phục hồi tiền vào muộn); trường hợp partial-wallet có thể vừa refund vừa giao. Các luồng phục hồi hiện có (`restore_refunded_wallet_for_order`, `claim_crypto_deposit`) đã xử lý các nhánh chính; không sửa thêm để tránh phá luồng phục hồi.

## Số liệu benchmark (điền ở Bước 6)

- DB 5.000 đơn: ghi inline cũ (indent=2, trong event loop) **52.3 ms/lần** → mới: caller chỉ **~0.001 ms** (set event), thread nền ghi compact **8.6 ms/lần**.
- Kích thước file DB: 969KB (indent) → 651KB (compact), giảm ~33%.
- `tools/bench_db.py --orders 5000`: CORRECTNESS PASS (index nhanh hơn scan 3.6x–184x, không đổi).
- Verify script: 6/6 PASS (durability ≤2s, compact + đọc file indent cũ OK, flush đồng bộ, `_fetch_api1` trả products, `set_api_cache` cập nhật đúng cache, chặn fulfillment trùng).
- `tools/smoke_import.py`: SMOKE PASS; `pytest bot_tele/tests`: 30 passed.

## Rollback

- Mỗi bước là thay đổi độc lập; revert từng file được. Không có migration dữ liệu.
