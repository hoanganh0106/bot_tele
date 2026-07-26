# Design — Optimize system logic and processing speed

## Phạm vi thay đổi

| File | Thay đổi |
|---|---|
| `database.py` | Writer thread nền cho flush; compact JSON; giữ nguyên API public của class `Database` |
| `core/products.py` | Sửa `_fetch_api1` (khôi phục thân hàm); thêm setter `set_api_cache()` để module khác cập nhật cache an toàn |
| `jobs.py` | Dùng `set_api_cache()` thay cho `global _api_cache`; rà race giữa các job |
| `binance_client.py` | `requests.Session` + `HTTPAdapter` pooling (giống `ctv_api.py`, `hypervin_client.py`) |
| `handlers/payment.py` | Chỉ vá nếu audit race/double-click phát hiện lỗ hổng (đọc trước khi sửa) |

## 1. Database writer thread (R1)

Hiện trạng: `_write(immediate=True)` gọi `_flush_to_disk()` đồng bộ trong thread gọi (event loop của bot) — `json.dump(indent=2)` toàn bộ DB mỗi lần lưu đơn.

Thiết kế mới:

```
Database.__init__:
    self._flush_event = threading.Event()
    self._stop = False
    self._writer = threading.Thread(target=self._writer_loop, daemon=True)
    self._writer.start()

_write(data, immediate):
    cache = data; idx_version += 1; _pending_write = True
    if immediate: self._flush_event.set()          # đánh thức writer ngay
    else:        (giữ debounce 2s như cũ — timer chỉ set event)

_writer_loop():
    while not stop:
        _flush_event.wait(); _flush_event.clear()
        with self.lock: snapshot = json.dumps(self._cache, ensure_ascii=False, separators=(",", ":"))
        atomic write snapshot → file (ngoài lock)

flush():  # shutdown — vẫn đồng bộ
    set event + join/flush trực tiếp dưới lock
```

Quyết định:
- **Snapshot bằng `json.dumps` dưới lock, ghi file ngoài lock** — tránh writer ghi dở trong lúc handler mutate dict (hiện `json.dump` stream trực tiếp từ cache, có nguy cơ `RuntimeError: dict changed during iteration` nếu 2 thread — webhook thread + bot loop — cùng chạm; bug tiềm ẩn sẵn có, thiết kế mới loại bỏ luôn).
- **Compact separators** thay `indent=2`: file nhỏ hơn ~2-3 lần, dump nhanh hơn; đọc lại tương thích mọi định dạng JSON.
- Debounce timer giữ nguyên cho non-critical, nhưng callback chỉ `_flush_event.set()` (không dump trong timer thread).
- Cửa sổ mất dữ liệu: writer thức dậy ngay khi có event → thực tế < vài chục ms; chấp nhận theo PRD (≤1s).
- `_flush_to_disk` fallback (ghi thẳng không atomic) giữ nguyên hành vi khi lỗi.

## 2. Product cache (bug fixes)

- Khôi phục thân `_fetch_api1`:
  ```python
  def _fetch_api1():
      if _is_circuit_open("CTV"):
          return None, 0
      try:
          products, balance = api.get_stock()
          _record_api_result("CTV", products is not None)
          return products, balance
      except Exception as e:
          _record_api_result("CTV", False)
          logger.error(f"API 1 fetch error: {e}")
          return None, 0
  ```
  và `get_hypervin_balance()` chỉ còn `return _hv_balance["value"]`.
- Thêm vào `core/products.py`:
  ```python
  def set_api_cache(products, balance):
      global _api_cache
      _api_cache = {"data": (products, balance),
                    "expiry": time.time() + API_CACHE_TTL,
                    "stale_expiry": time.time() + API_STALE_TTL}
  ```
  `jobs.py` (periodic + post_init) gọi setter này; bỏ `from core.products import _api_cache` và `global _api_cache` trong jobs.

## 3. Binance Session (R3)

Trong `BinanceClient.__init__`: tạo `requests.Session()`, mount `HTTPAdapter(pool_connections=2, pool_maxsize=4)` — cùng pattern `ctv_api.py`. `_signed_request` dùng `self.session.request(...)`. Không retry tự động (poller đã có backoff 418/429 ở jobs.py).

## 4. Race audit (R2) — checklist khi thực thi

- `process_paid_order` (handlers/payment.py): xác nhận có claim atomic (vd. `claim_order_for_payment` / chuyển status pending→processing dưới lock) trước khi giao hàng; nếu retry job + payment processor cùng gọi thì lần 2 phải bị từ chối.
- Nút bấm double-click: `handle_pay_wallet`, `handle_paid_button`… xác nhận dùng claim/`answerCallbackQuery` guard.
- `_retry_failed_orders`: xác nhận không retry đơn đang được xử lý (status phải là `failed` tại thời điểm claim).
- Kết quả audit ghi vào `implement.md` (mục đánh dấu) — chỉ sửa khi có lỗ hổng thật, kèm test.

## Tương thích & rollback

- Không đổi schema JSON; file cũ (indent) đọc bình thường; sau lần ghi đầu sẽ thành compact — rollback code cũ vẫn đọc được (json.load không quan tâm format).
- API public của `Database` không đổi → handlers không phải sửa.
- Rollback: revert commit; không cần migrate dữ liệu.

## Rủi ro

- Writer thread chết ngầm → thêm try/except quanh vòng lặp, log error, không để thread thoát.
- Đảm bảo `flush()` shutdown hoạt động cả khi writer đang bận (join có timeout + flush trực tiếp).
- Test trên Windows (dev) lẫn Linux (EC2): atomic `os.replace` hoạt động cả 2.
