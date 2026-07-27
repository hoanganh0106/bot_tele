# Fix revenue stats and customer spend accuracy

## Goal

Panel admin "📊 Doanh thu" phải gom **đủ doanh thu từ trước tới nay**, và "tổng chi tiêu" của
khách phải đúng ở mọi chỗ hiển thị. Đồng thời dọn sạch đơn rác (`failed`, `cancelled`,
`cancelled_timeout`) khỏi DB.

> Toàn bộ code cần sửa nằm trong repo git lồng `bot_tele/`. Các file trùng tên ở thư mục gốc
> (`d:\Du_an\bot_tele\database.py`, `handlers/`, `core/`) là **bản copy cũ, không deploy** —
> sửa ở đó là code không lên EC2.

## Hiện trạng (đo trên production 2026-07-27)

Panel admin đang hiện **2.187.000đ** trong khi doanh thu thật là **8.475.000đ** (thiếu 74%).

| Nguồn | Đơn | paid | Doanh thu | Cost |
|---|---|---|---|---|
| `bot_data.json` (live) | 49 | 13 | 2.187.000 | 1.126.000 |
| `bot_data_archive.json` | 79 | 47 | 6.288.000 | 3.613.000 |
| **Gộp (dedup, 0 overlap)** | **128** | **60 + 2 `paid_waiting_email`** | **8.475.000** | **4.739.000** |

- Khoảng thời gian: 2026-04-18 → 2026-07-27. Đơn theo tháng: `04: 4 · 05: 3 · 06: 30 · 07: 91`.
- Đã quét cả 20 file trong `backups/`: **đóng góp 0 đơn mới** → `live + archive` là toàn bộ
  lịch sử, chưa từng bị wipe. Backfill khôi phục được 100%.
- **23 khách đã mua thành công, nhưng chỉ 6 khách có `total_spent > 0`** (219/225 user bằng 0).
  Tổng counter = 9.946.000đ, *cao hơn* doanh thu thật 8.475.000đ. Counter sai theo **cả 3 hướng**:
  - **Mất trắng 17/23 khách** — 29/62 đơn paid trả trực tiếp qua SePay (`payment_method: bank`),
    không đi qua ví nên không điểm nào tăng counter. VD khách `427734870` mua 735.000đ bằng
    4 lần chuyển khoản → counter = 0.
  - **Thiếu một phần** ở 3 khách — đơn có cả ví + bank chỉ tính phần ví
    (`6759865504`: mua 360.000, counter 130.000).
  - **Phồng** ở 3 khách — `5846376104` counter 9.497.000 / mua thật 4.582.000 (+4.915.000);
    `5333398836` counter 155.000 mà **chưa mua thành công đơn nào**. Cơ chế: ví bị trừ →
    `total_spent` tăng → API đối tác hết số dư → đơn `failed` → admin hoàn tay bằng "➕ Cộng ví"
    (`reason="admin_add"` không trừ counter) → counter kẹt vĩnh viễn. Cộng thêm `deduct_balance`
    tăng counter khi admin bấm "➖ Trừ ví".
  - Lưu ý: 9.497.000/9.946.000 (95%) tổng counter đến từ **một** account bị phồng — con số tổng
    nhìn "gần đúng" chỉ là trùng hợp.
- Phân bố 62 đơn paid: `payment_method` = bank 23 / wallet 14 / None (đơn cũ) 25;
  `payment_source` = wallet 33 / sepay 29.
- **20/20** đơn paid thiếu `cost` đều là `is_custom_local: True`. Không đơn API nào thiếu.
- **46** đơn `cancelled`/`cancelled_timeout`: **0** đơn có dấu vết thanh toán
  (`paid_at`/`wallet_paid`/`crypto_txid`).
- **20** đơn `failed`, tổng 3.860.000đ, tất cả cùng lỗi `"Không đủ số dư. Cần X đ"`
  (số dư **nhà cung cấp** hết, không phải khách). Chủ bot đã xác nhận **đã xử lý tay hết → cho xoá**.
  - **Nhưng 4/20 đơn trả bằng ví** (`wallet_payment_confirmed: True`, **không** có
    `wallet_refunded`), tổng **860.000đ**, đều của khách `5846376104` ngày 26/07:
    `BOT1784953670BCD31F` 300k, `BOT1784953708B0A99F` 300k, `BOT1785060685ECF0F9` 130k,
    `BOT17850607091FD9B2` 130k. Tiền vẫn nằm trong sổ ví của bot và **bản ghi đơn là bằng
    chứng duy nhất** → xoá là mất dấu nghĩa vụ hoàn tiền. Xem R2.

## Nguyên nhân gốc

1. **`cleanup_old_orders(days=7)` chạy mỗi lần bot khởi động** (`bot_tele/jobs.py:643`) đẩy đơn
   `paid`/`cancelled`/`cancelled_timeout` cũ hơn 7 ngày sang `bot_data_archive.json` rồi
   `del orders[code]`. Cả `get_stats()`, `find_user_orders_by_query()` và `get_user_orders()`
   đều **chỉ đọc `orders` sống** → mất 74% doanh thu, khách mất lịch sử.
2. **`total_spent` có 2 nguồn số liệu khác nhau** cho cùng một khái niệm — counter
   `users[uid]["total_spent"]` (ví khách, "khách gần đây") vs tính lại từ orders (tra cứu khách).
   Hai nguồn không bao giờ khớp, và cả hai đều sai.
3. **Counter chỉ tăng khi tiền ra khỏi ví** → khách chuyển khoản/crypto trực tiếp không được tính.
4. **`deduct_balance()` tăng `total_spent`** → admin bấm "➖ Trừ ví" làm phồng "đã chi" của khách.
5. **Đơn `bank_partial` bị tính thiếu**: `start_partial_wallet_payment` ghi đè
   `order["total"] = remaining`, số gốc lưu ở `original_total`. `get_stats` vẫn cộng `total`.
6. **`cost` không được ghi cho nhánh "hàng tự bán"** (`custom_local`) → lợi nhuận thổi phồng.
7. **`paid_waiting_email` bị bỏ sót** khỏi mọi phép đếm dù đã thu tiền.
8. **Đơn `failed`/`paid_waiting_email` không bao giờ được dọn** → tích tụ từ tháng 4,
   chiếm 22/49 đơn live (45%).

## Requirements

### R1 — Doanh thu gom đủ từ trước tới nay
- Panel "📊 Doanh thu" phải hiện tổng của **toàn bộ** đơn thành công kể từ đơn đầu tiên,
  không phụ thuộc số đơn còn lưu trong DB và không tụt sau mỗi lần restart.
- Doanh thu = tổng `original_total` (fallback `total`) của đơn có status ∈
  `{paid, paid_waiting_email}`, trừ phần đã hoàn.
- Merge 79 đơn từ `bot_data_archive.json` về DB chính (chỉ `paid`/`paid_waiting_email`).

### R2 — Xoá đơn rác
- `cancelled`, `cancelled_timeout`: **xoá vĩnh viễn**, không archive. Grace 1h.
- `failed`: **xoá vĩnh viễn** sau grace **24h**.
  - Grace 24h là bắt buộc, không phải tuỳ chọn: `_retry_failed_orders` dùng
    `get_retryable_orders(max_age_minutes=30)` và `_process_paid_order_locked` nhận
    `status == "failed"` để cứu đơn. Xoá ngay sẽ phá đường cứu đơn của khách đã trả tiền.
  - **Ngoại lệ bắt buộc**: đơn `failed` còn giữ tiền ví khách chưa hoàn
    (`wallet_payment_confirmed` hoặc `wallet_paid > 0`, mà chưa có `wallet_refunded`/
    `refund_credited`) thì **KHÔNG xoá ở bất kỳ tuổi nào** — kể cả trong migration.
    Giữ lại + cảnh báo admin cho tới khi hoàn tiền. Đơn trả bank/crypto không có nghĩa vụ
    hoàn nằm trong sổ của bot nên vẫn xoá theo grace 24h, dấu vết ở `purged_orders.log`.
- `paid`, `paid_waiting_email`: **giữ vĩnh viễn**.
- Trước khi xoá phải log danh sách mã đơn + số tiền để có dấu vết. Log phải ghi **trước**
  khi xoá; không ghi được log thì **huỷ việc xoá**, không được xoá "im lặng".
  Log phải chứa cả dấu vết thanh toán (`paid_at`, `payment_method`, `payment_source`,
  `wallet_paid`, `wallet_refunded`, `refund_credited`) để còn hoàn tiền được.

### R3 — Vốn hàng tự bán = 0đ
- 3 nhánh `custom_local` ghi `"cost": 0` **tường minh** (không để thiếu field).
  Có field = "đã xác nhận không vốn"; thiếu field = "chưa biết" → phải phân biệt được để
  sau này phát hiện đơn API mất `cost` thật.

### R4 — `total_spent` đúng ở mọi chỗ
- Một nguồn sự thật duy nhất, dùng chung ở ví khách, "khách gần đây", và tra cứu khách.
- Tính cho **mọi** phương thức thanh toán: ví / bank / crypto / partial.
- Admin cộng/trừ ví **không** làm thay đổi `total_spent`.
- Hoàn tiền trừ `total_spent` đúng **một lần** (idempotent).
- **Cộng tiền hoàn vào ví cũng phải đúng một lần.** `_process_paid_order_locked` nhận
  `status == "failed"` nên nhánh hoàn tiền chạy lại được (admin bấm "✅ Xác nhận thanh toán"
  lần 2, tiền vào muộn khớp đơn `failed`, recover sau restart, retry job) — mỗi lần chạy lại
  mà không có cờ là một lần cộng ví nữa.

### R5 — Lịch sử đơn của khách
- Khách xem được toàn bộ đơn mua thành công, không giới hạn 7 ngày.
- Bỏ giới hạn cứng 10 đơn → phân trang (khách nhiều nhất có 48 đơn).
- Bỏ `text[:4000]` cắt cứng giữa dòng → cắt theo số đơn.

### R6 — Bảo vệ dữ liệu
- Backup `bot_data.json` + `bot_data_archive.json` + `backups/` ra ngoài server trước khi chạy gì.
- `bot_data_archive.json` đang giữ 74% doanh thu mà ghi **không atomic** và **reset `{}` khi
  đọc lỗi** → phải vá trước khi thao tác.
- Rotation backup đang xoá sai file → phải vá.

## Constraints

- Không đổi format DB kiểu breaking; mọi field mới phải có default an toàn cho đơn cũ.
- Mọi thay đổi `total_spent` / counter phải nằm **trong cùng một `self.lock`** với thay đổi
  order tương ứng (tránh lệch khi crash giữa hai bước).
- Script migration/backfill phải **idempotent** — chạy 2 lần ra cùng kết quả.
- Bot phải **stop** khi chạy migration; backup trước.
- Không phá luồng cứu đơn: `recover_confirmed_crypto_orders`, `recover_confirmed_wallet_orders`,
  `_retry_failed_orders`, `_process_paid_order_locked` (nhận `failed`).

## Out of scope

- Không truy thu / không hoàn tiền lại cho 20 đơn `failed` (chủ bot đã xử lý tay).
- Không thêm giá nhập cho sản phẩm tự bán (chốt = 0đ).
- Không dựng lại UI panel admin, chỉ thêm dòng cho khớp tổng.
- Không sửa i18n/bilingual.

## Acceptance Criteria

- [ ] **AC1** — Panel "📊 Doanh thu" hiện: Tổng thu **8.475.000**, Tổng gốc **4.739.000**,
      Lợi nhuận **3.736.000**, đơn thành công **62**.
- [ ] **AC2** — Restart bot 2 lần liên tiếp, ba số ở AC1 **không đổi**.
- [ ] **AC3** — `Tổng đơn` = tổng các dòng con hiển thị (thêm dòng `📧 Chờ email` và `💔 Lỗi`).
- [ ] **AC4** — Sau khi dọn rác: 0 đơn `cancelled`/`cancelled_timeout` trong DB;
      0 đơn `failed` cũ hơn 24h **trừ 4 đơn còn nợ tiền ví** (860.000đ, xem AC17);
      62 đơn `paid`/`paid_waiting_email` còn nguyên.
- [ ] **AC5** — Đơn `failed` mới tạo 20 phút **không** bị xoá (retry vẫn chạy được);
      đơn `failed` 25h bị xoá.
- [ ] **AC6** — 20 đơn `custom_local` có `cost == 0` **tồn tại như một field**, không phải thiếu field.
- [ ] **AC7** — `sum(users[*].total_spent)` == **8.475.000** (giảm 1.471.000 so với 9.946.000 hiện tại).
- [ ] **AC8** — Số khách có `total_spent > 0` tăng từ **6** lên **23** (đúng số khách đã mua thành công).
- [ ] **AC8b** — Kiểm 3 mốc cụ thể sau backfill:
      `427734870` (trả bank 100%) → `total_spent == 735_000` (hiện 0);
      `5846376104` (bị phồng) → `total_spent == 4_582_000` (hiện 9.497.000);
      `5333398836` (chưa mua gì) → `total_spent == 0` (hiện 155.000).
- [ ] **AC9** — Admin bấm "➕ Cộng ví" / "➖ Trừ ví" → `total_spent` của khách **không đổi**.
- [ ] **AC10** — Gọi hàm hoàn tiền 2 lần cho cùng một đơn → `total_spent` chỉ giảm 1 lần.
- [ ] **AC11** — Đơn `bank_partial` (ví + bank) tính doanh thu = `original_total`, không phải `remaining`.
- [ ] **AC12** — Tra cứu khách và ví khách hiện **cùng một con số** "đã chi" cho cùng một khách.
- [ ] **AC13** — Khách `5846376104` (48 đơn) bấm "📋 Lịch sử" xem được hết đơn mua thành công qua phân trang.
- [ ] **AC14** — Chạy `backfill_stats.py` hai lần → kết quả giống nhau (idempotent).
- [ ] **AC15** — `bot_data_archive.json` ghi atomic; mô phỏng đọc lỗi → **abort**, không ghi đè rỗng.
- [ ] **AC16** — Đơn sản phẩm ẩn bị xử lý lại (2 lần `process_paid_order`) → ví khách chỉ
      được cộng hoàn **một lần**; lần 2 chỉ log, không cộng, không nhắn lại khách.
      Nhánh hủy/timeout sau đó cũng không cộng thêm phần ví (`wallet_refunded` đã set).
- [ ] **AC17** — Đơn `failed` còn nợ tiền ví khách **không bị xoá** bởi cả
      `purge_junk_orders` (runtime) và `tools/purge_junk.py` (migration), ở mọi tuổi đơn;
      admin nhận cảnh báo lúc khởi động kèm tổng tiền còn nợ. Sau migration: 4 đơn / 860.000đ.
- [ ] **AC18** — `purge_junk_orders` ghi `purged_orders.log` **trước** khi xoá; nếu không ghi
      được log thì không xoá đơn nào.
