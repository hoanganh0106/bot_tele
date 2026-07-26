# PRD: Chặn mua/giao hàng sản phẩm đã ẩn (stale UI)

## Bối cảnh / Vấn đề

Khi khách đã mở UI và danh sách sản phẩm đang hiển thị, nếu admin ẩn sản phẩm
(`toggle_hidden_product` → `custom_hiddens` trong database), phía khách **không
reload** nên nút sản phẩm cũ vẫn bấm được. Hiện tại:

- Check `db.is_product_hidden(key)` **chỉ tồn tại ở bước render danh mục**
  (`handlers/customer.py` — `handle_category_click`, dòng ~482).
- `handle_product_select` (callback `prod_*`) chỉ check stock, KHÔNG check hidden.
- `handle_qty_select` (callback `qty_*` → tạo đơn hàng) KHÔNG check hidden.
- Toàn bộ `handlers/payment.py` (pay_wallet / pay_bank / pay_crypto / paid /
  pay_partial, và `process_paid_order` — bước giao hàng) KHÔNG check hidden.

→ Khách với UI cũ vẫn đi hết luồng: chọn sản phẩm → tạo đơn → thanh toán →
được giao hàng, dù admin đã ẩn.

## Mục tiêu

Sản phẩm đã bị admin ẩn thì khách **không thể tạo đơn mới và không thể thanh
toán đơn pending** cho sản phẩm đó, kể cả khi UI phía khách là màn hình cũ
(stale). Trải nghiệm khách phải rõ ràng: báo "sản phẩm không còn bán" + nút
quay lại menu, không im lặng nuốt lỗi.

## Yêu cầu chức năng

1. **Chặn tại bước chọn sản phẩm** (`handle_product_select`, callback `prod_*`):
   nếu `db.is_product_hidden(product_key)` → hiển thị thông báo sản phẩm
   không còn bán (song ngữ VI/EN qua i18n) + keyboard quay lại danh mục/menu.
2. **Chặn tại bước chọn số lượng / tạo đơn** (`handle_qty_select`, callback
   `qty_*`): check hidden TRƯỚC khi tạo order; nếu ẩn → thông báo tương tự,
   không tạo đơn.
3. **Chặn tại bước thanh toán** đối với đơn `pending` đã tạo trước khi admin ẩn:
   - `handle_pay_wallet`, `handle_pay_partial`: check hidden trước khi trừ ví;
     nếu ẩn → hủy đơn (status `canceled`), báo khách, KHÔNG trừ tiền.
   - `handle_pay_bank`, `handle_pay_crypto`, `handle_paid_button`: check hidden
     trước khi hiển thị QR/thông tin chuyển khoản; nếu ẩn → hủy đơn + báo khách
     để tránh khách chuyển tiền cho đơn không thể giao.
4. **Chốt chặn cuối tại giao hàng** (`process_paid_order`): nếu tiền ĐÃ VÀO
   (webhook SePay/crypto về sau khi admin ẩn) → KHÔNG giao hàng; xử lý theo
   chính sách hoàn tiền ở mục "Quyết định chính sách" bên dưới + thông báo
   admin và khách.
5. **Thông báo khách**: thêm i18n key mới (VI + EN) dạng
   "Sản phẩm này hiện không còn bán / This product is no longer available",
   dùng chung cho các điểm chặn.

## Quyết định chính sách (cần chốt khi implement)

- **Đơn đã trả tiền nhưng sản phẩm vừa bị ẩn** (chặn tại bước 4): mặc định đề
  xuất → cộng tiền vào **ví bot** của khách (refund wallet) + notify admin để
  xử lý tay nếu khách muốn rút. Không tự động chuyển khoản ngược.
- **Đơn pending khi admin ẩn**: không cần job quét chủ động; chặn lazy tại các
  checkpoint là đủ (đơn tự hết hạn sau 5 phút bởi `auto_cancel_order`).

## Ngoài phạm vi (out of scope)

- Không làm cơ chế push/edit message chủ động tới mọi khách đang mở UI cũ
  (tốn API call, không cần thiết vì đã chặn server-side).
- Không đụng tới luồng ẩn/hiện của admin (`toggle_hidden_product` giữ nguyên).
- Sản phẩm hết stock/stock=-1 đã có xử lý riêng, không thay đổi.

## Tiêu chí nghiệm thu (acceptance criteria)

- [ ] Admin ẩn sản phẩm X. Khách đang ở màn danh mục cũ bấm nút X →
      nhận thông báo "không còn bán", không vào được màn chọn số lượng.
- [ ] Khách đang ở màn chọn số lượng của X, admin ẩn X, khách bấm số lượng →
      nhận thông báo, KHÔNG có order mới trong database.
- [ ] Khách có đơn pending của X, admin ẩn X, khách bấm thanh toán ví →
      đơn bị hủy, số dư ví KHÔNG đổi, khách nhận thông báo.
- [ ] Khách có đơn pending của X đã hiện QR bank, admin ẩn X, tiền về qua
      webhook → KHÔNG giao hàng, tiền vào ví khách (theo chính sách đã chốt),
      admin nhận notify, khách nhận thông báo rõ ràng.
- [ ] Thông báo hiển thị đúng ngôn ngữ VI/EN theo `user_lang`.
- [ ] Sản phẩm KHÔNG bị ẩn: toàn bộ luồng mua bán hoạt động như cũ (regression).

## Ghi chú ràng buộc

- Code tồn tại ở HAI cây: repo gốc (`handlers/`, `core/`, …) và repo git lồng
  `bot_tele/` — sửa phải đồng bộ cả hai theo quy trình: commit repo con trước,
  rồi cập nhật gitlink ở repo cha.
- Deploy lên EC2 bằng `update.sh` (code chạy ở `/home/ubuntu/ctv-bot/`).
