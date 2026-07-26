# Design: Chặn mua/giao hàng sản phẩm đã ẩn

## Nguyên tắc chung

UI Telegram (inline keyboard) là **snapshot tĩnh** — không thể ép khách reload.
Do đó mọi quyết định bán/giao phải được **re-validate server-side tại thời điểm
callback**, không tin vào trạng thái lúc render. Đây là mô hình "check at use,
not at render".

Nguồn sự thật: `db.is_product_hidden(product_key)` (đọc `custom_hiddens` trong
database, đã có sẵn — không cần schema mới).

## Các checkpoint (defense in depth)

```
Khách bấm nút (UI cũ)
   │
   ▼
[1] handle_product_select  (prod_*)      → chặn sớm, UX tốt nhất
   ▼
[2] handle_qty_select      (qty_*)       → chặn trước khi TẠO ĐƠN
   ▼
[3] handlers/payment.py    (pay_*)       → chặn trước khi NHẬN TIỀN
   ▼
[4] process_paid_order     (webhook)     → chốt chặn cuối trước khi GIAO HÀNG
```

Lý do cần đủ 4 tầng: giữa mỗi bước khách có thể dừng lại nhiều phút (màn hình
cũ), admin có thể ẩn sản phẩm ở bất kỳ thời điểm nào giữa hai bước liên tiếp.

### [1] `handle_product_select` (handlers/customer.py ~dòng 188)

Ngay sau khi parse `product_key` và trước khi build màn chọn số lượng:

```python
if db.is_product_hidden(product_key):
    → edit_navigation_message(t(user_id, "product_not_for_sale", name=...),
      reply_markup=build_product_back_keyboard(...))
    → return
```

Đặt check TRƯỚC check stock (sản phẩm ẩn thì stock không còn ý nghĩa).

### [2] `handle_qty_select` (handlers/customer.py ~dòng 305)

Sau khi parse `product_key` từ callback_data, trước khi tạo order + trước khi
`asyncio.create_task(auto_cancel_order(...))`. Cùng thông báo với [1].

### [3] handlers/payment.py — các handler thanh toán

Helper chung (đặt trong payment.py hoặc core/helpers.py):

```python
async def _reject_if_hidden(query, order) -> bool:
    """True nếu đơn bị chặn vì sản phẩm đã ẩn. Hủy đơn + báo khách."""
```

- Hủy đơn: set status `canceled` (tái dùng đường đi của `handle_cancel_order`
  để hoàn slot stock nếu luồng hủy hiện tại có làm việc đó — xem lại khi code).
- Gọi ở đầu: `handle_pay_wallet`, `handle_pay_partial` (trước khi trừ ví),
  `handle_pay_bank`, `handle_pay_crypto` (trước khi hiện QR),
  `handle_paid_button` (trước khi xác nhận đã chuyển).

### [4] `process_paid_order` (handlers/payment.py ~dòng 468)

Đây là điểm NHẠY CẢM nhất: tiền đã vào (webhook SePay hoặc crypto).

- Check `db.is_product_hidden(product_key)` ngay sau khi load order và trước
  mọi nhánh giao hàng (kể cả nhánh `paid_waiting_email` và auto-delivery).
- Nếu ẩn:
  - Dùng `db.complete_order_payment(order_code, {"status": "refunded", ...})`
    (atomic — cùng cơ chế chống race hiện có) để đơn không bị xử lý 2 lần.
  - Hoàn tiền vào ví bot của khách (`db` đã có cơ chế balance — cộng đúng số
    tiền đơn, ghi log/transaction nếu hệ thống có).
  - Notify tất cả admin qua `_notify_all_admins` (mẫu giống cảnh báo
    "AUTO GIAO HÀNG HẾT KHO" hiện có, ~dòng 541).
  - Báo khách bằng i18n key mới, nêu rõ tiền đã vào ví.
- Nếu status `refunded` chưa tồn tại trong hệ thống trạng thái đơn, cân nhắc
  tái dùng `failed` + flag `refund_reason: "product_hidden"` để không phá các
  màn hình lọc theo status (build_orders_screen, thống kê admin). Chốt khi
  implement sau khi rà nơi tiêu thụ status.

## i18n

Thêm key mới vào `i18n.py` (cả VI và EN), ví dụ:

- `product_not_for_sale`: "😔 Sản phẩm <b>{name}</b> hiện không còn bán." /
  "😔 Product <b>{name}</b> is no longer available."
- `order_canceled_product_hidden`: dùng cho checkpoint [3].
- `order_refunded_product_hidden`: dùng cho checkpoint [4], nêu số tiền đã
  hoàn vào ví.

Tái dùng phong cách/format của các key hiện có (`product_out_of_stock`,
`product_missing`).

## Tradeoff / lựa chọn đã loại

- **Push edit message tới mọi khách khi admin ẩn**: loại — không track được
  message_id của mọi khách, tốn rate-limit Telegram, và vẫn có race window.
  Server-side re-validation là đủ và rẻ.
- **Job quét đơn pending khi admin ẩn**: loại — đơn pending tự hết hạn 5 phút
  (`auto_cancel_order`), checkpoint [3]/[4] đã che hết đường tiền đi.

## Tương thích / rollout

- Không đổi schema database (dùng field/cơ chế sẵn có).
- Không đổi hành vi cho sản phẩm không ẩn → rollback = revert commit.
- Áp dụng đồng thời cho cả hai cây code (repo gốc + repo lồng `bot_tele/`).
