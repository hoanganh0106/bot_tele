# Design — Fix revenue stats and customer spend accuracy

## Quyết định kiến trúc

### D1 — Bỏ hoàn toàn cơ chế archive, không phải "đọc thêm archive"

Hai hướng đã cân nhắc:

| Hướng | Đánh giá |
|---|---|
| Giữ archive, mọi chỗ đọc thêm `bot_data_archive.json` | ❌ Phải sửa 3 chỗ (`get_stats`, `find_user_orders_by_query`, `get_user_orders`), mỗi lần mở panel parse thêm 1 file, và vẫn phải maintain 2 nguồn mãi mãi |
| **Bỏ archive, chỉ xoá đơn rác** | ✅ **Chọn** |

Lý do chọn: docstring `cleanup_old_orders` nói mục đích là *"Giữ DB nhẹ → `find_order_by_content`
nhanh hơn"*, nhưng thực tế `bot_data.json` = **182 KB / 49 đơn**. Sau khi xoá 66 đơn rác
(20 `failed` + 46 `cancelled*`), DB chỉ còn **62 đơn paid** — **nhẹ hơn hiện tại** dù đã merge
79 đơn từ archive về. Việc archive là tối ưu hoá không cần thiết, và nó là nguyên nhân gốc của
cả 3 triệu chứng (doanh thu sai, tra cứu khách sai, khách mất lịch sử).

**Hệ quả quan trọng:** R1 + R5 được giải quyết **cùng lúc bởi một thay đổi**, không cần sửa
`get_user_orders` hay `find_user_orders_by_query` để đọc archive.

### D2 — Vẫn thêm counter lũy kế, dù đã bỏ archive

Counter `data["stats"]` không phải để bù cho archive (D1 đã lo), mà là **lưới an toàn dài hạn**:

- Tốc độ hiện tại ~90 đơn/tháng → ~1.100 đơn/năm. Sau 2-3 năm file ~4 MB.
- `Database._write()` ghi lại **toàn bộ** file mỗi lần → 4 MB/write sẽ thành vấn đề.
- Khi đó nếu muốn bật lại archive/prune, `get_stats()` vẫn đúng mà **không phải sửa lần nữa**.

Nghĩa là counter là nguồn sự thật cho *số tổng*, còn `orders` là nguồn cho *chi tiết đơn*.

### D3 — Một điểm ghi duy nhất: `complete_order_payment`

`complete_order_payment` (`bot_tele/database.py:453`) là **cửa duy nhất** mọi luồng thanh toán đi
qua để chuyển đơn sang `paid` / `paid_waiting_email` — wallet, bank (SePay), crypto (Binance),
partial, custom_local auto, custom_local manual, API đối tác. Xác minh: mọi `"status": "paid"`
trong `handlers/payment.py` (dòng 618, 645, 682, 790) đều gọi qua hàm này.

→ Đặt việc cập nhật `data["stats"]` **và** `users[uid]["total_spent"]` vào **bên trong** hàm này,
trong cùng `self.lock`, cùng một `_write`. Không thể lệch, không thể lọt luồng nào.

Xoá 4 điểm tăng `total_spent` rải rác hiện tại: `database.py:422, 508, 555, 631`.

### D4 — Idempotency bằng cờ trên chính order

`complete_order_payment` có thể được gọi lại cho cùng một đơn (retry job, recover sau restart,
`_process_paid_order_locked` nhận `status == "failed"`). Nếu cộng counter mù thì mỗi lần retry
sẽ cộng trùng.

→ Dùng cờ `order["stats_counted"] = True`. Chỉ cộng khi cờ chưa có. Cùng cơ chế cho hoàn tiền:
`order["spent_reverted"] = True`.

Đây là pattern đã có trong codebase: `wallet_payment_confirmed`, `wallet_refunded`,
`crypto_payment_confirmed` đều làm vậy.

## Contracts

### Helper mới — `bot_tele/core/helpers.py`

```python
REVENUE_STATUSES = ("paid", "paid_waiting_email")
JUNK_STATUSES    = ("cancelled", "cancelled_timeout")

def order_revenue(order: dict) -> int:
    """Số tiền khách thực trả. Đơn bank_partial có `total` bị ghi đè thành
    `remaining`, số gốc nằm ở `original_total`."""
    return int(order.get("original_total") or order.get("total") or 0)

def order_cost(order: dict) -> int:
    return int(order.get("cost") or 0)
```

Lý do đặt ở `core/helpers.py`: đã có tiền lệ — `helpers.py:72` dùng đúng công thức
`original_total or total` cho quy đổi USDT, và `core/screens.py:171` cũng vậy. Ba chỗ đang
copy-paste cùng logic; helper này gom lại.

### Schema mới trong DB

```jsonc
{
  "stats": {                        // MỚI — counter lũy kế
    "lifetime_revenue": 0,
    "lifetime_cost": 0,
    "lifetime_paid_orders": 0,
    "lifetime_refunded": 0
  },
  "orders": {
    "BOT...": {
      "stats_counted": true,        // MỚI — cờ idempotency cho counter
      "spent_reverted": true,       // MỚI — cờ idempotency cho hoàn tiền
      "cost": 0                     // MỚI với custom_local (tường minh)
    }
  }
}
```

Tương thích ngược: `data.setdefault("stats", {...})` trong `_read`/migration; đơn cũ không có
`stats_counted` sẽ được backfill xử lý một lần rồi đánh cờ.

### API thay đổi

| Hàm | Thay đổi |
|---|---|
| `Database.complete_order_payment(code, updates)` | Thêm: nếu status mới ∈ `REVENUE_STATUSES` và chưa `stats_counted` → cộng `stats` + `users[uid].total_spent`, set cờ |
| `Database.get_stats()` | Đọc `data["stats"]` cho 3 số tiền; vẫn scan `orders` cho các số đếm theo status |
| `Database.deduct_balance(user_id, amount)` | **Bỏ** dòng tăng `total_spent` (`database.py:1515`) |
| `Database.add_balance(user_id, amount, reason)` | **Bỏ** nhánh `reason == "refund"` trừ `total_spent` (`database.py:1500`) — chuyển vào hàm revert riêng |
| `Database.revert_order_spend(order_code)` | **MỚI** — trừ `total_spent` + `stats` đúng một lần, set `spent_reverted` |
| `Database.cleanup_old_orders(days)` | **Thay bằng** `purge_junk_orders()` — xoá thật, không archive |
| `Database.purge_junk_orders()` | **MỚI** — trả về `dict` các đơn đã xoá để caller log |

### Luồng dữ liệu sau khi sửa

```
Khách thanh toán (wallet | bank | crypto | partial)
        │
        ▼
complete_order_payment(code, {"status": "paid", ...})   ◄── CỬA DUY NHẤT
        │  [trong 1 lock, 1 write]
        ├─ order.update(updates)
        ├─ if status in REVENUE_STATUSES and not order["stats_counted"]:
        │     stats.lifetime_revenue      += order_revenue(order)
        │     stats.lifetime_cost         += order_cost(order)
        │     stats.lifetime_paid_orders  += 1
        │     users[uid].total_spent      += order_revenue(order)
        │     order["stats_counted"] = True
        └─ _write(immediate=True)
                │
                ├──► get_stats()            → đọc stats.*            → panel admin
                ├──► users[uid].total_spent → ví khách / khách gần đây / tra cứu khách
                └──► orders                 → lịch sử đơn khách (chi tiết)

Hoàn tiền  ──► revert_order_spend(code)  ──► trừ stats + total_spent, set spent_reverted
```

## Tradeoffs & rủi ro

### T1 — Xoá đơn `failed` làm mất dấu 3.86M nghĩa vụ hoàn tiền
Chủ bot đã xác nhận đã xử lý tay hết. Giảm thiểu: `purge_junk_orders` **log ra file**
`data/purged_orders.log` (mã đơn, user_id, tiền, status, error, created_at) trước khi xoá —
rẻ, và nếu sau này có khách khiếu nại thì vẫn tra được.

### T2 — Grace 24h cho `failed` là ràng buộc chức năng, không phải con số tuỳ ý
`get_retryable_orders(max_age_minutes=30)` cần đơn `failed` còn sống. `_process_paid_order_locked`
nhận `status == "failed"` để cứu đơn tay. Nếu ai đó "tối ưu" grace xuống 1h thì vẫn an toàn về
retry, nhưng admin mất cửa sổ xử lý tay. **24h là mức tối thiểu hợp lý, đừng giảm.**

### T3 — Counter có thể lệch khỏi `orders` nếu sửa DB bằng tay
Sau khi bỏ archive, `sum(order_revenue(o) for o in paid)` phải == `stats.lifetime_revenue`.
Giảm thiểu: thêm log cảnh báo khi khởi động nếu hai số lệch > 0 (chỉ log, không tự sửa —
tự sửa sẽ che mất bug thật).

### T4 — `paid_waiting_email` tính vào doanh thu dù chưa giao hàng
Đúng về mặt tiền (khách đã trả), nhưng nếu đơn kẹt mãi thì doanh thu ghi nhận cho đơn chưa giao.
Hiện chỉ có 2 đơn. Chấp nhận, và panel có dòng `📧 Chờ email` riêng để nhìn ra.

### T5 — Không có transaction thật, JSON file DB
Nếu crash giữa `order.update` và `_write` thì mất cả hai (vì cùng 1 write) — an toàn.
Nhưng nếu crash **sau** `_write` thì counter đã cộng, đơn đã paid — vẫn nhất quán. OK.

## Rollout / rollback

**Rollout** — thứ tự bắt buộc (chi tiết ở `implement.md`):
1. Backup ra ngoài server.
2. Vá bảo vệ dữ liệu (archive atomic + rotation) — deploy được độc lập, không đổi hành vi.
3. Sửa code (helper, `complete_order_payment`, `get_stats`, `purge_junk_orders`, UI).
4. **Stop bot** → chạy `merge_archive.py` → `purge_junk.py` → `backfill_stats.py` → **start bot**.
5. Đối chiếu panel với AC1.

**Rollback:** mọi script migration chỉ ghi `bot_data.json`. Rollback = stop bot,
`cp` bản backup trước migration về, `git revert`, start bot. `bot_data_archive.json` được rename
thành `.migrated` chứ **không xoá** → merge có thể chạy lại.

## Files sẽ sửa

Tất cả trong repo lồng `bot_tele/`:

| File | Nội dung |
|---|---|
| `core/helpers.py` | + `REVENUE_STATUSES`, `JUNK_STATUSES`, `order_revenue()`, `order_cost()` |
| `database.py:453` | `complete_order_payment` — cộng counter + `total_spent`, cờ `stats_counted` |
| `database.py:1263` | `cleanup_old_orders` → `purge_junk_orders` (xoá thật) |
| `database.py:1296-1307` | Archive ghi atomic, abort khi đọc lỗi (trước khi bỏ archive) |
| `database.py:1325` | `get_stats` — đọc counter, thêm `paid_waiting_email` |
| `database.py:1500` | `add_balance` — bỏ nhánh `refund` trừ `total_spent` |
| `database.py:1515` | `deduct_balance` — bỏ tăng `total_spent` |
| `database.py:422,508,555,631` | Bỏ 4 điểm tăng `total_spent` rải rác |
| `database.py` (mới) | `revert_order_spend()` |
| `handlers/payment.py:618,645,682` | + `"cost": 0` cho custom_local |
| `handlers/payment.py:556` | `product_hidden_refund` → gọi `revert_order_spend` |
| `handlers/payment.py:818` | Dùng `order_revenue()` |
| `handlers/admin.py:436-447` | + dòng `📧 Chờ email`, `💔 Lỗi` |
| `handlers/text_input.py:667` | Dùng `total_spent` từ counter, không tính lại |
| `core/screens.py:155-178` | Phân trang, bỏ `[:10]` và `text[:4000]` |
| `jobs.py:643` | `cleanup_old_orders(days=7)` → `purge_junk_orders()` |
| `jobs.py:687-691` | Rotation sort theo mtime |
| `tools/merge_archive.py` | **MỚI** |
| `tools/purge_junk.py` | **MỚI** |
| `tools/backfill_stats.py` | **MỚI** |
| `tests/test_revenue_stats.py` | **MỚI** |
