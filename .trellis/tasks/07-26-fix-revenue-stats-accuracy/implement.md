# Implement — Fix revenue stats and customer spend accuracy

Đọc `prd.md` → `design.md` trước. Checklist dưới đây theo **thứ tự bắt buộc**; các Gate là điểm
dừng để review/rollback.

## Bối cảnh môi trường (đọc kỹ trước khi sửa file nào)

- **Code phải sửa nằm trong repo git lồng `bot_tele/`.** Thư mục gốc `d:\Du_an\bot_tele\` có bản
  copy cũ của `database.py`, `handlers/`, `core/` (thiếu feature crypto watermark) — **sửa ở đó
  là code không lên EC2**.
- GitHub `main` = repo lồng `bot_tele/`. Commit repo con trước, rồi cập nhật gitlink ở repo cha.
- Deploy: SSH `ubuntu@54.169.17.170` (key `bottele2.pem`) → `bash update.sh`.
  Nếu gặp lỗi `invalid credentials` thì `unset GITHUB_TOKEN` trước.
- Code chạy ở `/home/ubuntu/ctv-bot/`, **data ở `/home/ubuntu/ctv-bot-data/`** (khác thư mục code).
- `update.sh:88` chỉ copy danh sách file cố định + 2 thư mục `core handlers`. **Không copy `tools/`.**
  → Vì vậy 3 script migration phải **standalone stdlib** (chỉ `json`/`os`/`sys`/`shutil`,
  không import `database.py`), nhận đường dẫn DATA_DIR qua argv, và chạy trực tiếp từ
  `~/bot_tele/tools/` trên EC2. Không cần sửa `update.sh` manifest.

## Phase A — Bảo vệ dữ liệu *(làm trước tiên, deploy được độc lập)*

- [ ] **A1** Backup ra ngoài server:
  ```bash
  scp -i bottele2.pem ubuntu@54.169.17.170:/home/ubuntu/ctv-bot-data/bot_data.json ./backup_pre_fix/
  scp -i bottele2.pem ubuntu@54.169.17.170:/home/ubuntu/ctv-bot-data/bot_data_archive.json ./backup_pre_fix/
  scp -r -i bottele2.pem ubuntu@54.169.17.170:/home/ubuntu/ctv-bot-data/backups ./backup_pre_fix/
  ```
  Kiểm: `bot_data_archive.json` phải parse được và có **79** key.

- [ ] **A2** `bot_tele/database.py:1296-1307` — sửa ghi archive:
  - Ghi atomic: `tmp` + `os.replace` (copy pattern từ `_write()` ở `database.py:124-131`).
  - Đọc lỗi → **raise/abort**, KHÔNG `existing_archive = {}`. Chuỗi hiện tại
    (đọc lỗi → coi như rỗng → ghi đè không atomic) có thể wipe toàn bộ 79 đơn.
  - *Giữ hàm archive ở phase này* — Phase C mới bỏ. Mục đích A2 là không mất data trong lúc đang làm.

- [ ] **A3** `bot_tele/jobs.py:687-691` — rotation backup sort theo **mtime** thay vì tên.
  Bug hiện tại: `sorted(reverse=True)` theo tên khiến `bot_data_before_update_*` luôn đứng trước
  `bot_data_<timestamp>.json` (vì `'b' > '2'`) → backup khởi động **luôn bị xoá đầu tiên**.
  Server hiện có đúng 20 file `before_update` và **0** file backup khởi động.

- [ ] **A4** Test + deploy Phase A. Không đổi hành vi nghiệp vụ nào → deploy an toàn ngay.

> **Gate A** — Xác nhận trên server: `bot_data_archive.json` vẫn 79 đơn, restart bot 1 lần thì
> thấy file `backups/bot_data_<timestamp>.json` mới xuất hiện và **không** bị xoá.
> *Rollback point: `git revert` + start bot. Chưa có thay đổi dữ liệu.*

## Phase B — Helper + sửa công thức tính tiền

- [ ] **B1** `bot_tele/core/helpers.py` — thêm:
  ```python
  REVENUE_STATUSES = ("paid", "paid_waiting_email")
  JUNK_STATUSES    = ("cancelled", "cancelled_timeout")

  def order_revenue(order: dict) -> int:
      return int(order.get("original_total") or order.get("total") or 0)

  def order_cost(order: dict) -> int:
      return int(order.get("cost") or 0)
  ```
  Rồi refactor 2 chỗ đang copy-paste cùng logic dùng helper: `core/helpers.py:72`,
  `core/screens.py:171`.

- [ ] **B2** `bot_tele/handlers/payment.py` — thêm `"cost": 0` **tường minh** vào 3 nhánh
  custom_local: dòng **618** (auto hết kho), **645** (auto giao thành công), **682** (manual).
  Không dùng default ngầm — cần phân biệt "đã xác nhận không vốn" với "chưa biết".

- [ ] **B3** `bot_tele/handlers/payment.py:818` — `profit` dùng `order_revenue(order)` thay
  `order["total"]` (đơn `bank_partial` đang bị tính thiếu phần ví).

- [ ] **B4** `bot_tele/database.py:1325` `get_stats()`:
  - 3 số tiền đọc từ `data["stats"]` (counter, tạo ở B5).
  - Số đếm theo status vẫn scan `orders`, thêm `paid_waiting_email` vào nhóm doanh thu.
  - Trả thêm `waiting_email_orders`.

- [ ] **B5** `bot_tele/database.py:453` `complete_order_payment()` — điểm ghi duy nhất:
  ```
  trong cùng self.lock, trước _write:
    if updates.get("status") in REVENUE_STATUSES and not order.get("stats_counted"):
        stats = data.setdefault("stats", {...})
        stats["lifetime_revenue"]     += order_revenue(order)
        stats["lifetime_cost"]        += order_cost(order)
        stats["lifetime_paid_orders"] += 1
        users[str(order["user_id"])]["total_spent"] += order_revenue(order)
        order["stats_counted"] = True
  ```
  Cờ `stats_counted` là **bắt buộc** — hàm này bị gọi lại bởi retry job, recover-after-restart,
  và `_process_paid_order_locked` (nhận `status == "failed"`).

- [ ] **B6** `bot_tele/database.py` — thêm `revert_order_spend(order_code)`: trừ
  `stats.lifetime_revenue` / `lifetime_cost` / `lifetime_paid_orders`, trừ `total_spent`,
  cộng `stats.lifetime_refunded`, set `order["spent_reverted"] = True`. No-op nếu cờ đã có.

- [ ] **B7** Xoá các điểm ghi `total_spent` cũ:
  - `database.py:1515` `deduct_balance` — bỏ tăng (admin "➖ Trừ ví" đang làm phồng số của khách).
  - `database.py:1500` `add_balance` nhánh `reason == "refund"` — bỏ trừ, chuyển sang B6.
  - `database.py:422, 508, 555, 631` — bỏ cả 4 điểm tăng rải rác.

- [ ] **B8** `bot_tele/handlers/payment.py:556` — `product_hidden_refund` gọi `revert_order_spend`
  (hiện tại `reason="product_hidden_refund"` không khớp nhánh `"refund"` nên **không trừ gì**).
  - Cộng ví phải qua `db.credit_order_refund_once()` (cờ `refund_credited` trong cùng
    `self.lock`), **không** dùng `add_balance` trực tiếp: hàm này chạy lại được cho đơn
    `failed` → `add_balance` không có cờ sẽ cộng ví nhiều lần. Đã hoàn rồi thì chỉ log,
    không nhắn lại khách/admin.

- [ ] **B9** `bot_tele/handlers/admin.py:436-447` — thêm dòng `📧 Chờ email` và `💔 Lỗi` để
  `Tổng đơn` = tổng các dòng con.

- [ ] **B10** `bot_tele/handlers/text_input.py:667` — dùng `total_spent` từ counter thay vì
  `sum(o.get("total") ...)`, để tra cứu khách và ví khách hiện cùng một con số (AC12).

> **Gate B** — Chạy test, chưa deploy. `pytest` phải xanh. Chưa chạm dữ liệu production.
> *Rollback point: `git revert`.*

## Phase C — Bỏ archive, chuyển sang xoá đơn rác

- [ ] **C1** `bot_tele/database.py:1263` — thay `cleanup_old_orders(days)` bằng
  `purge_junk_orders(junk_grace_hours=1, failed_grace_hours=24)`:

  | Status | Hành động |
  |---|---|
  | `cancelled`, `cancelled_timeout` | xoá nếu cũ hơn **1h** |
  | `failed` | xoá nếu cũ hơn **24h** |
  | `paid`, `paid_waiting_email` | **giữ vĩnh viễn** |
  | `pending`, `processing` | không chạm (đã có `_cleanup_stale_orders`) |

  - **Không xoá đơn `failed` còn nợ tiền ví khách** ở bất kỳ tuổi nào —
    `core/order_values.holds_unrefunded_wallet_money()`. Giữ lại + `logger.warning`.
  - Trả về `dict` các đơn đã xoá để caller ghi log; nhận `log_path` để tự ghi log
    **trước** khi xoá (không ghi được log → không xoá).
  - Thêm `get_unrefunded_failed_orders()` để startup nhắc admin hoàn tiền.
  - **Grace 24h cho `failed` là ràng buộc chức năng, không phải con số tuỳ ý** —
    `get_retryable_orders(max_age_minutes=30)` và `_process_paid_order_locked` (nhận `failed`)
    cần đơn còn sống. Đừng giảm.
  - Xoá hẳn phần ghi `bot_data_archive.json`.

- [ ] **C2** `bot_tele/jobs.py:643` — `db.cleanup_old_orders(days=7)` →
  `db.purge_junk_orders(log_path=<DATA_DIR>/purged_orders.log)`. Log gồm mã đơn, user_id,
  `order_revenue`, status, error, created_at **và dấu vết thanh toán** (`paid_at`,
  `payment_method`, `payment_source`, `wallet_paid`, `wallet_refunded`, `refund_credited`).
  Sau đó gọi `get_unrefunded_failed_orders()` → `_notify_all_admins` nhắc hoàn tiền.

- [ ] **C3** `bot_tele/core/screens.py:155-178` `build_orders_screen`:
  - Phân trang (khách nhiều nhất có **48** đơn) — bỏ `[:10]` cứng.
  - Bỏ `text[:4000]` cắt cứng → cắt theo **số đơn/trang** để không mất đơn giữa dòng.
  - Cân nhắc ẩn/gộp đơn hủy khỏi màn hình khách (sau C1 thì đơn hủy đã bị xoá, nên chủ yếu là
    phòng cho đơn hủy mới).

- [ ] **C4** Cảnh báo lệch (T3 trong design): lúc khởi động, nếu
  `sum(order_revenue(o) for o in paid) != stats.lifetime_revenue` thì **log warning**.
  Chỉ log, **không tự sửa** — tự sửa sẽ che mất bug thật.

> **Gate C** — `pytest` xanh, review toàn bộ diff. **Chưa deploy, chưa chạy migration.**

## Phase D — Migration trên production

3 script standalone trong `bot_tele/tools/` (stdlib only, nhận DATA_DIR qua argv, không import
`database.py` — xem lý do ở "Bối cảnh môi trường"). **Tất cả phải idempotent.**

- [ ] **D1** `tools/merge_archive.py` — nhập `bot_data_archive.json` → `bot_data.json`:
  - Chỉ lấy status ∈ `REVENUE_STATUSES`. Dedup theo mã đơn (đã xác minh **0 overlap**).
  - Xong thì `mv bot_data_archive.json bot_data_archive.json.migrated` (**không xoá**).
  - In: số đơn nhập, số bỏ qua, tổng doanh thu nhập vào.

- [ ] **D2** `tools/purge_junk.py` — xoá `failed` + `cancelled*` khỏi `bot_data.json`:
  - **In danh sách trước khi xoá** và ghi `purged_orders.log`.
  - **Giữ lại** đơn `failed` còn nợ tiền ví khách (bản sao stdlib của
    `holds_unrefunded_wallet_money`) → in dòng `KEEP ...` cho từng đơn.
  - Kỳ vọng (đo lại 27/07 trên bản backup production): xoá **30** đơn / 10.023.000đ,
    **giữ 4** đơn nợ ví 860.000đ. (Con số 66 trong bản kế hoạch đầu là tính cả `cancelled`
    trong archive — nhưng `merge_archive` chỉ nhập status doanh thu nên chúng không vào DB.)

- [ ] **D3** `tools/backfill_stats.py` — recompute từ đầu:
  - `data["stats"]` từ toàn bộ đơn `REVENUE_STATUSES`.
  - `users[*]["total_spent"]` — **reset về 0 rồi cộng lại** từ orders (không cộng dồn lên số cũ,
    vì số cũ đang phồng 1.471.000đ do admin trừ ví).
  - Đánh `stats_counted = True` cho mọi đơn đã tính.
  - Idempotent: chạy lần 2 phải ra **y hệt**.

- [ ] **D4** Thực thi trên EC2 — thứ tự bắt buộc:
  ```bash
  sudo systemctl stop ctv-bot
  cp /home/ubuntu/ctv-bot-data/bot_data.json /home/ubuntu/ctv-bot-data/backups/bot_data_pre_migration.json
  cd ~/bot_tele
  python3 tools/merge_archive.py  /home/ubuntu/ctv-bot-data
  python3 tools/purge_junk.py     /home/ubuntu/ctv-bot-data
  python3 tools/backfill_stats.py /home/ubuntu/ctv-bot-data
  python3 tools/backfill_stats.py /home/ubuntu/ctv-bot-data   # chạy lần 2 — phải ra y hệt
  sudo systemctl start ctv-bot
  ```

> **Gate D** — Đối chiếu số liệu ngay sau migration, **trước khi** báo xong:
> ```
> lifetime_revenue       == 8_475_000
> lifetime_cost          == 4_739_000
> lifetime_paid_orders   == 62
> sum(total_spent)       == 8_475_000     (giảm 1_471_000 so với 9_946_000)
> len(orders)            == 66 = 62 doanh thu + 4 failed nợ ví (+ pending/processing đang chạy)
> đơn cancelled*         == 0
> đơn failed             == 4 (đúng 4 đơn nợ ví 860_000; mọi đơn failed khác đã xoá)
> ```
> *Rollback point: `systemctl stop ctv-bot` → `cp backups/bot_data_pre_migration.json bot_data.json`
> → `git revert` → start. `bot_data_archive.json.migrated` vẫn còn nên merge chạy lại được.*

## Phase E — Test & verify

- [ ] **E1** `bot_tele/tests/test_revenue_stats.py` — mỗi case map thẳng tới một AC:

  | Test | AC |
  |---|---|
  | `bank_partial` order → revenue = `original_total`, không phải `remaining` | AC11 |
  | `complete_order_payment` gọi 2 lần cùng đơn → counter chỉ cộng 1 lần | AC2 |
  | `paid_waiting_email` được tính vào doanh thu | AC1 |
  | custom_local order → `"cost" in order` và `== 0` | AC6 |
  | `deduct_balance` → `total_spent` không đổi | AC9 |
  | `revert_order_spend` gọi 2 lần → chỉ trừ 1 lần | AC10 |
  | `failed` 20 phút tuổi → `purge_junk_orders` **không** xoá | AC5 |
  | `failed` 25h → bị xoá; `paid` 90 ngày → **không** bị xoá | AC4, AC5 |
  | `backfill_stats` chạy 2 lần → kết quả giống nhau | AC14 |
  | archive đọc lỗi → abort, không ghi đè rỗng | AC15 |
  | `credit_order_refund_once` 2 lần → chỉ cộng ví 1 lần | AC16 |
  | `process_paid_order` 2 lần cho đơn sản phẩm ẩn → 1 lần cộng ví | AC16 |
  | sau khi hoàn, `refund_order_wallet_if_needed` trả `(0, balance)` | AC16 |
  | `failed` 25h còn nợ ví → **không** xoá; đã `wallet_refunded`/`refund_credited` → xoá | AC17 |
  | `tools/purge_junk.py` giữ đơn failed nợ ví, trả `retained` | AC17 |
  | log ghi trước khi xoá; không ghi được log → không xoá đơn nào | AC18 |

- [ ] **E2** `cd bot_tele && python -m pytest -q` — toàn bộ suite xanh (không chỉ test mới).
- [ ] **E3** `cd bot_tele && python tools/smoke_import.py` — index/order path không bị vỡ.
- [ ] **E4** Verify tay trên bot thật:
  - Panel "📊 Doanh thu" → khớp AC1, `Tổng đơn` = tổng dòng con (AC3).
  - `systemctl restart ctv-bot` **2 lần** → số không đổi (AC2).
  - Tra cứu khách `5846376104` → "đã chi" khớp với ví khách (AC12).
  - Khách bấm "📋 Lịch sử" → phân trang xem đủ đơn (AC13).
  - Admin "➕ Cộng ví" 10.000đ cho 1 khách test → `total_spent` **không đổi** (AC9).

> **Gate E** — Tick hết 15 AC trong `prd.md` rồi mới commit + báo xong.

## Rollback tổng

| Đã tới phase | Cách rollback |
|---|---|
| A / B / C (chưa migration) | `git revert` + restart bot. Dữ liệu chưa bị chạm. |
| D (đã migration) | stop bot → `cp backups/bot_data_pre_migration.json bot_data.json` → `mv bot_data_archive.json.migrated bot_data_archive.json` → `git revert` → start bot |

## Lưu ý cho người code

- Đừng "tối ưu" grace 24h của `failed` xuống thấp hơn — xem T2 trong `design.md`.
- Đừng để `purge_junk_orders` xoá `paid_waiting_email` (hiện có 2 đơn, khách đã trả tiền).
- Mọi cập nhật counter phải nằm **trong cùng `self.lock` + cùng `_write`** với thay đổi order.
- `users` dict key là **string** (`str(user_id)`), không phải int — xem `_migrate_users`.
- Sau khi sửa xong: commit repo lồng `bot_tele/` trước, rồi cập nhật gitlink ở repo cha.
