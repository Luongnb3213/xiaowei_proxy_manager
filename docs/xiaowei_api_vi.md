# Xiaowei Android Casting API - bản dịch ghi chú kỹ thuật

Nguồn chính: https://www.xiaowei.xin/help/70/234  
Manual: `70` - 效卫安卓投屏, nhóm `8. API文档`.

Tài liệu này được dịch và rút gọn để agent khác có thể implement adapter
Xiaowei cho `xiaowei_proxy_manager`. Các article liên quan:

- `234`: 8.1.1. 接口文档说明 - mô tả protocol chung.
- `349`: 8.1.3. API接口功能如何使用？ - cách test API.
- `350`: API接口无法使用怎么办？ - troubleshooting.
- `351`: 4.2 接口文档 - danh sách API.
- `25`: 8.2.1. `list` - lấy danh sách thiết bị.
- `28`: 8.2.3. `adb` - chạy lệnh ADB đầy đủ.
- `252`: 8.2.25. `adb_shell` - chạy phần sau `adb shell`.

## Endpoint

Xiaowei mở WebSocket local tại:

```text
ws://127.0.0.1:22222/
```

Client gửi JSON qua WebSocket và nhận JSON phản hồi.

## Cấu Trúc Request Chung

Theo docs, request và response đều là JSON. Trừ khi từng API ghi khác, tham số
request là kiểu `string`.

```json
{
  "action": "pushEvent",
  "devices": "all",
  "data": {
    "type": "2"
  }
}
```

Tham số chung:

| Field | Type | Bắt buộc | Ví dụ | Ghi chú |
|---|---:|---:|---|---|
| `action` | string | Có | `"pushEvent"` | Loại sự kiện/API, không phân biệt hoa thường. |
| `devices` | string | Có | `"all"` hoặc `"xxx,xxx"` | Serial thiết bị. `all` là tất cả. Nhiều thiết bị cách nhau bằng dấu phẩy tiếng Anh. Nếu không có serial, có thể truyền dạng `IP:port`. Serial xem trong danh sách thiết bị hoặc qua API `list`. |
| `data` | JSON | Không | `{...}` | Object tham số riêng của từng API. |

Response chung:

```json
{
  "code": 10000,
  "message": "SUCCESS",
  "data": null
}
```

| Field | Type | Ví dụ | Ghi chú |
|---|---:|---|---|
| `code` | int | `10000` | Mã phản hồi. |
| `message` | string | `"SUCCESS"` | Nội dung phản hồi. |
| `data` | JSON/null | `null` | Có dữ liệu thì là JSON, không có thì `null`. |

Mã chung:

| Code | Ý nghĩa |
|---:|---|
| `10000` | Request thành công. |
| `10001` | Request thất bại. |

## Cách Test API

Docs đề xuất dùng công cụ WebSocket online, ví dụ `https://wstool.js.org/`:

1. Mở công cụ test WebSocket trên máy đang chạy Xiaowei.
2. Kết nối tới `ws://127.0.0.1:22222/`.
3. Khi WebSocket báo `opened`, gửi JSON API.
4. Có thể gửi một lần hoặc định kỳ.

Khi implement bằng Python, dùng thư viện WebSocket client để gửi JSON tương tự.

## Danh Sách API

Nguồn: article `351` - `4.2 接口文档`.

| STT | API | `action` | Mô tả |
|---:|---|---|---|
| 1 | Lấy danh sách thiết bị | `list` | Lấy các thiết bị hiện đang kết nối. |
| 2 | Cập nhật tên và số thứ tự thiết bị | `updateDevices` | Sửa tên và thứ tự hiển thị của thiết bị. |
| 3 | Chạy lệnh ADB | `adb` | Chạy command ADB đầy đủ. |
| 4 | Chụp màn hình | `screen` | Chụp frame hiện tại của phone, mặc định lưu ở `D:\Pictures`. |
| 5 | Điều khiển màn hình | `pointerEvent` | Touch/move/scroll/swipe: `0` down, `1` up, `2` move, `4` wheel up, `5` wheel down, `6` swipe up, `7` swipe down, `8` swipe left, `9` swipe right. |
| 6 | Phím tắt điện thoại | `pushEvent` | `1` recent tasks, `2` home, `3` back. |
| 7 | Gửi vào clipboard | `writeClipBoard` | Gửi text vào clipboard của phone. |
| 8 | Upload file | `uploadFile` | Đẩy file từ máy tính vào phone. |
| 9 | Tải file về máy tính | `pullFile` | Kéo file từ phone về máy tính, mặc định lưu ở `D:\Downloads`. |
| 10 | Danh sách app | `apkList` | Lấy danh sách app đã cài trên phone. |
| 11 | Cài APK | `installApk` | Cài app vào phone. |
| 12 | Gỡ APK | `uninstallApk` | Gỡ app khỏi phone. |
| 13 | Mở app | `startApk` | Khởi chạy app trên phone. |
| 14 | Dừng app | `stopApk` | Tắt app trên phone. |
| 15 | Danh sách bàn phím | `imeList` | Lấy danh sách IME đang cài. |
| 16 | Cài bàn phím casting | `installInputIme` | Cài IME của Xiaowei, thường phone tự cài khi kết nối. |
| 17 | Chọn bàn phím | `selectIme` | Chọn IME hiện tại. |
| 18 | Nhập chữ | `inputText` | Nhập text, yêu cầu màn hình đang focus input và IME Xiaowei đang được chọn. |
| 19 | Lấy tất cả tag | `getTags` | Lấy toàn bộ nhãn/nhóm. |
| 20 | Tạo tag | `addTag` | Tạo nhãn/nhóm. |
| 21 | Sửa tag | `updateTag` | Đổi tên nhãn/nhóm. |
| 22 | Xóa tag | `removeTag` | Xóa nhãn/nhóm. |
| 23 | Thêm thiết bị vào tag | `addtagdevice` | Gán device vào nhóm tag. |
| 24 | Xóa thiết bị khỏi tag | `removeTagDevice` | Bỏ device khỏi nhóm tag. |
| 25 | Chạy lệnh ADB shell | `adb_shell` | Chạy command sau `adb shell`, không cần ghi `adb shell`. |
| 26 | Lấy danh sách action | chưa đọc chi tiết | Article `380`. |
| 27 | Chạy action | chưa đọc chi tiết | Article `382`. |
| 28 | Dừng action | chưa đọc chi tiết | Article `381`. |
| 29 | Lấy danh sách task | chưa đọc chi tiết | Article `379`. |
| 30 | Chạy task | chưa đọc chi tiết | Article `378`. |
| 31 | Dừng task | chưa đọc chi tiết | Article `377`. |

## API `list` - Lấy Danh Sách Thiết Bị

Article: `25`, URL tham khảo: `https://www.xiaowei.xin/help/70/25`

Mục đích: lấy tất cả thiết bị hiện đang kết nối với Xiaowei.

Request:

```json
{
  "action": "list"
}
```

Response `data` là array device. Các field đáng chú ý:

| Field | Type | Ghi chú |
|---|---:|---|
| `width` | int | Chiều rộng màn hình hiển thị/casting. |
| `height` | int | Chiều cao màn hình hiển thị/casting. |
| `serial` | string | Serial theo mode kết nối hiện tại. Với Wi-Fi/OTG/accessibility, field này có thể là IP. |
| `model` | string | Model phone. |
| `sort` | int | Số thứ tự phone trong Xiaowei. |
| `name` | string | Tên tùy chỉnh của phone. |
| `onlySerial` | string | Serial cố định duy nhất của phone, không đổi theo mode kết nối. Nên dùng field này để lưu mapping lâu dài nếu ổn định trên máy thật. |
| `hide` | bool | Trạng thái ẩn/đóng màn hình. |
| `mode` | int | Mode kết nối. |
| `status` | string | Trạng thái online/offline. |
| `connectTime` | int | Timestamp kết nối. |
| `intranetIp` | string | IP nội bộ. |
| `sourceWidth` | int | Chiều rộng thật của màn hình phone. |
| `sourceHeight` | int | Chiều cao thật của màn hình phone. |

Mode kết nối:

| Mode | Ý nghĩa |
|---:|---|
| `0` | USB |
| `1` | Wi-Fi |
| `2` | OTG |
| `3` | Accessibility/no-accessibility mode trong bản dịch: chế độ không USB debugging |
| `10` | Cloud real device |
| `11` | Cloud phone |
| `12` | Cloud phone |

Ví dụ response rút gọn:

```json
{
  "code": 10000,
  "message": "SUCCESS",
  "data": [
    {
      "serial": "ea85356a",
      "onlySerial": "ea85356a",
      "model": "Redmi Note 3",
      "name": "Redmi Note 33",
      "mode": 0,
      "status": "online",
      "intranetIp": "192.168.111.143"
    }
  ]
}
```

Gợi ý implement: adapter nên gọi `list`, rồi map device theo `serial`,
`onlySerial`, `name`, `sort`, `intranetIp`. Với proxy tool, nên log cả
`serial` và `onlySerial` để tránh nhầm phone khi mode kết nối thay đổi.

## API `adb` - Chạy Lệnh ADB Đầy Đủ

Article: `28`, URL tham khảo: `https://www.xiaowei.xin/help/70/28`

Mục đích: chạy command ADB đầy đủ qua Xiaowei.

Request params:

| Field | Type | Bắt buộc | Ví dụ | Ghi chú |
|---|---:|---:|---|---|
| `action` | string | Có | `"adb"` | Không phân biệt hoa thường. |
| `devices` | string | Có | `"all"` hoặc `"xxx,xxx"` | Serial thiết bị, nhiều serial cách nhau bằng dấu phẩy. |
| `data.command` | string | Có | `"adb exec-out ip addr show wlan0"` | Truyền command ADB đầy đủ. Nếu path có khoảng trắng, bọc bằng dấu nháy kép. |

Ví dụ:

```json
{
  "action": "adb",
  "devices": "all",
  "data": {
    "command": "adb exec-out ip addr show wlan0"
  }
}
```

Response thành công có dạng:

```json
{
  "code": 10000,
  "message": "adb命令已执行",
  "data": {
    "988ed8344551435141": "output..."
  }
}
```

Ghi chú: `data` là object map từ serial sang output command. Dùng action này
khi cần command không chỉ nằm trong `adb shell`.

## API `adb_shell` - Chạy Phần Sau `adb shell`

Article: `252`, URL tham khảo: `https://www.xiaowei.xin/help/70/252`

Mục đích: chạy lệnh shell trên phone. Chỉ truyền phần sau `adb shell`.

Request params:

| Field | Type | Bắt buộc | Ví dụ | Ghi chú |
|---|---:|---:|---|---|
| `action` | string | Có | `"adb_shell"` | Không phân biệt hoa thường. |
| `devices` | string | Có | `"all"` hoặc `"xxx,xxx"` | Serial thiết bị. |
| `data.command` | string | Có | `"input keyevent KEYCODE_VOLUME_UP"` | Chỉ truyền command sau `adb shell`. |

Ví dụ docs:

```json
{
  "action": "adb_shell",
  "devices": "all",
  "data": {
    "command": "am start -a android.intent.action.VIEW -d https://xiaowei.run"
  }
}
```

Response thành công:

```json
{
  "code": 10000,
  "message": "adb_shell命令已执行",
  "data": null
}
```

## Dùng Cho Tool Proxy

Vì Xiaowei có `adb_shell`, tool proxy có thể gọi qua WebSocket thay vì gọi
binary `adb` trực tiếp trên máy:

Đọc proxy hiện tại:

```json
{
  "action": "adb_shell",
  "devices": "SERIAL",
  "data": {
    "command": "settings get global http_proxy"
  }
}
```

Đặt Android global HTTP proxy endpoint:

```json
{
  "action": "adb_shell",
  "devices": "SERIAL",
  "data": {
    "command": "settings put global http_proxy HOST:PORT"
  }
}
```

Xóa proxy:

```json
{
  "action": "adb_shell",
  "devices": "SERIAL",
  "data": {
    "command": "settings put global http_proxy :0"
  }
}
```

Quan trọng: đường ADB/global setting này vẫn chỉ áp dụng được `HOST:PORT`. Proxy
dạng `host:port:username:password` cần một trong các hướng sau để dùng đủ auth:

- Xiaowei có API/proxy manager riêng ngoài docs này.
- Cài/chạy app proxy trên phone rồi cấu hình bằng intent/file/ADB.
- Dùng local relay không auth trên phone/network, relay đó mới đi ra proxy auth.

Do đó adapter Xiaowei đầu tiên nên hỗ trợ:

1. `list_devices()` qua action `list`.
2. `shell(serial, command)` qua action `adb_shell`.
3. `get_global_proxy(serial)` bằng `settings get global http_proxy`.
4. `set_global_proxy_endpoint(serial, host_port)` bằng `settings put global http_proxy`.
5. Sau khi có cơ chế auth thật, thêm `set_authenticated_proxy(serial, proxy)`.

## Troubleshooting

Article: `350`, URL tham khảo: `https://www.xiaowei.xin/help/70/350`

Các nguyên nhân API không chạy:

- `devices` điền sai.
- Xiaowei client đang bật safe mode hoặc accessibility mode khiến USB debugging
  bị đóng, nên không chạy được command API.
- Tên tham số, format tham số hoặc thông tin thiết bị trong request sai.
- Phiên bản Xiaowei quá thấp.

Cách xử lý:

- Nếu phone kết nối bằng USB, `devices` nên là serial.
- Nếu phone kết nối bằng Wi-Fi hoặc OTG, `devices` nên là `IP:port`.
- Kiểm tra lại `devices`, tên field, format JSON và thông tin device.
- Cập nhật Xiaowei lên bản mới, reconnect device rồi thử lại.

