# Thuê số Telegram

- Nút **📱 Thuê số** nằm ở trang chủ `/start` và menu `/menu`.
- Khách chọn **Nhận số**, xác nhận **4.000đ**, rồi bấm **Lấy OTP**.
- Thanh toán bằng ví. Bot giữ 4.000đ trước khi gọi API; nhận số thành công thì ghi nhận đơn đã thanh toán. Không nhận được số thì hoàn tiền. Lấy OTP không thu thêm phí.
- Nếu bot bị ngắt khi đang cấp số, khoản giữ ví chưa hoàn tất được hoàn một lần khi khởi động lại. Bot không tự gọi lại API cấp số.
- Giá áp dụng cho việc cấp số. Nút **Đổi số khác** và **Hủy** kiểm tra lỗi trực tiếp qua `/get-otp` trước khi hoàn tiền; xem điều kiện bên dưới.
- Đơn thuê số có trong lịch sử mua hàng và doanh thu. API mẫu không cung cấp giá vốn; giá vốn chưa được ghi nhận.

## Đổi tên nút

Admin gửi:

```text
/setphonename SIM Telegram
```

Tên dài tối đa 64 ký tự, lưu trong database và giữ sau khi khởi động lại. Có hướng dẫn trong **Admin → Giao diện → Đổi tên nút thuê số**.

Khôi phục tên mặc định:

```text
/setphonename reset
```

Mở lại `/start` hoặc `/menu` để cập nhật nút trên tin nhắn mới.

## Hủy và đổi số lỗi

- Chỉ lỗi chính xác `Message not found or Archived for another partner` trong trường `message` hoặc `error` của JSON (ở cấp ngoài hoặc trong `data`) mới được xét hoàn. Chấp nhận phản hồi HTTP 200/400/404; timeout, lỗi máy chủ, dữ liệu lạ và OTP đang chờ không cho phép hoàn.
- Chờ ít nhất 60 giây sau khi cấp số, rồi nhấn **Hủy** hoặc **Đổi số khác**. Bot kiểm tra API với số đã lưu của chính người dùng, không nhận số do callback tự truyền lên.
- Lần đầu API báo lỗi, bot yêu cầu chờ 10 giây rồi nhấn lại trong 2 phút. Lần hai phải tiếp tục trả đúng lỗi. Nếu trả trạng thái chờ hoặc lỗi kết nối thì phải xác minh lại từ đầu.
- Nếu bot đã thấy OTP ở bất kỳ lần kiểm tra nào, quyền hoàn tiền bị khóa và được lưu qua khởi động lại. Các đơn tạo trước khi có cơ chế theo dõi này cần admin kiểm tra thủ công.
- **Hủy** hợp lệ: hoàn đúng 4.000đ vào ví một lần, đóng số hiện tại và điều chỉnh doanh thu/chi tiêu.
- **Đổi số khác** hợp lệ: hoàn đơn lỗi rồi thuê số mới bằng 4.000đ trong ví. Nếu thuê mới thất bại, tiền nằm trong ví. Nếu bot ngắt giữa hai bước, không tự cấp số lại; khoản chưa hoàn tất được hoàn khi khởi động.
- Không giới hạn số lần hoàn tiền cho các đơn khác nhau đủ điều kiện. Mỗi đơn chỉ hoàn một lần. Kiểm tra lỗi cách nhau ít nhất 10 giây; xử lý OTP/hủy/đổi của cùng người dùng được tuần tự hóa để tránh hoàn tiền hoặc đổi số trùng.
- Nút **Thuê số khác** là mua thêm số với giá 4.000đ như trước; không phải đổi miễn phí và không tự hoàn đơn cũ.

API báo trạng thái tin nhắn, không chứng minh tuyệt đối số chưa từng được dùng bên ngoài bot. Hai lần xác minh, ghi nhận OTP và chặn hoàn trùng giúp giảm lạm dụng; chưa có API thu hồi số nên bot không thể bảo đảm nhà cung cấp vô hiệu hóa số đã hủy.

## Cấu hình API

Trong `config.env`, có thể đổi địa chỉ khi đường hầm Cloudflare thay đổi:

```dotenv
PHONE_RENTAL_API_URL=https://cultural-webshots-track-say.trycloudflare.com
```

Khởi động lại bot sau khi cập nhật code hoặc biến môi trường. `update.sh` hiện có sao chép cả thư mục `core` và `handlers`, chứa các module mới.

API cấp số dùng `GET /get_phone`, theo mẫu `get_phone.json`: `data.phone` và `data.prefix`. Bot giữ nguyên `phone` khi gửi `GET /get-otp?phone=...`; hiển thị riêng mã quốc gia để tránh tự ghép sai số.

Bộ đọc OTP hỗ trợ `data.otp`, `data.code`, `otp`, `code`; giá trị null/rỗng hoặc trạng thái `pending`/`waiting` được xem là chưa có mã. Phản hồi chưa nhận diện được sẽ báo lỗi định dạng thay vì hiển thị nhầm mã. Phản hồi OTP thành công từ API thật chưa được xác minh; cần mẫu JSON hoặc cho phép kiểm tra số thử trước khi đưa vào sử dụng thực tế.

## Kiểm thử

Các kiểm thử nằm trong `tests/test_phone_rental.py`: chống trừ tiền lặp, số dư thiếu, hoàn tiền, phục hồi sau khởi động, lưu doanh thu, quyền đổi tên, giới hạn tần suất và quyền truy cập OTP theo người dùng. Các kiểm thử API dùng dữ liệu giả; không gửi tin Telegram hoặc thuê số thật.
