# Implement: Chặn mua/giao hàng sản phẩm đã ẩn

> Thứ tự dưới đây đi từ ít rủi ro → nhạy cảm nhất (đụng tới tiền).
> Mỗi bước xong phải chạy validation trước khi sang bước sau.

## Checklist

### Bước 0 — Rà soát trước khi code
- [ ] Rà mọi nơi tiêu thụ `order["status"]` (build_orders_screen, admin stats,
      auto_cancel_order, webhook) để chốt: dùng status `refunded` mới hay
      `failed` + `refund_reason` (xem design.md mục [4]).
- [ ] Xác nhận luồng hủy đơn hiện tại (`handle_cancel_order`) có hoàn/không
      hoàn stock để checkpoint [3] tái dùng đúng.
- [ ] Xác nhận cơ chế cộng ví (deposit/balance) trong `database.py` dùng được
      cho refund mà không phá thống kê `total_deposited`.

### Bước 1 — i18n
- [ ] Thêm key VI+EN: `product_not_for_sale`,
      `order_canceled_product_hidden`, `order_refunded_product_hidden`
      vào `i18n.py`.

### Bước 2 — Checkpoint [1] + [2] (customer.py, không đụng tiền)
- [ ] `handle_product_select`: check hidden ngay sau parse `product_key`,
      trước check stock.
- [ ] `handle_qty_select`: check hidden sau parse `product_key`, trước khi
      tạo order.

### Bước 3 — Checkpoint [3] (payment.py, trước khi nhận tiền)
- [ ] Viết helper `_reject_if_hidden(query, order)` — hủy đơn + báo khách.
- [ ] Gắn vào đầu: `handle_pay_wallet`, `handle_pay_partial`,
      `handle_pay_bank`, `handle_pay_crypto`, `handle_paid_button`.

### Bước 4 — Checkpoint [4] (process_paid_order, tiền đã vào)
- [ ] Check hidden ngay sau load order, trước MỌI nhánh giao hàng.
- [ ] Refund vào ví khách + set status atomic qua `complete_order_payment`.
- [ ] Notify admin (`_notify_all_admins`) + báo khách kèm số tiền hoàn.

### Bước 5 — Đồng bộ hai cây code
- [ ] Áp các thay đổi tương tự vào repo lồng `bot_tele/`
      (customer.py / payment.py / i18n.py bên trong `bot_tele/`).
- [ ] Commit repo con trước, rồi cập nhật gitlink ở repo cha.

## Validation

```bash
# Syntax check cả hai cây
python -m py_compile handlers/customer.py handlers/payment.py i18n.py
python -m py_compile bot_tele/handlers/customer.py bot_tele/handlers/payment.py bot_tele/i18n.py
```

Test tay theo acceptance criteria trong prd.md (bot test hoặc EC2 staging):
1. Ẩn sản phẩm → bấm nút cũ ở màn danh mục → phải bị chặn.
2. Ẩn khi khách đang ở màn chọn số lượng → bấm số lượng → không có đơn mới.
3. Ẩn khi có đơn pending → bấm thanh toán ví → đơn hủy, ví không đổi.
4. Ẩn sau khi hiện QR, giả lập webhook tiền về → không giao hàng, tiền vào ví,
   admin nhận notify.
5. Kiểm tra cả VI và EN.
6. Regression: sản phẩm không ẩn mua bình thường end-to-end.

## Review gate

- Sau Bước 3 và Bước 4: tự review diff, đặc biệt thứ tự
  check-hidden ↔ trừ tiền ↔ set status (không được có cửa sổ trừ tiền
  trước khi check).
- Chạy `trellis-check` trước khi commit.

## Rollback

- Mỗi bước là thay đổi cộng thêm (guard đầu hàm) → rollback bằng revert
  commit; không có migration dữ liệu.
