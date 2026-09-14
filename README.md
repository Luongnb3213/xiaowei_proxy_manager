# Xiaowei Proxy Manager (ADB-first)

Tool này nằm độc lập với `REG_PBANDAI`. Bản đầu giao tiếp trực tiếp với các
phone qua ADB; adapter cho Xiaowei sẽ được thêm sau khi có tài liệu/API.

Docs Xiaowei đã dịch để agent khác đọc tiếp:

```text
docs/xiaowei_api_vi.md
```

## Cài đặt

Không cần package Python ngoài standard library. Cần Android platform-tools và
đảm bảo:

```bash
adb version
adb devices
```

Nếu `adb` không nằm trong `PATH`, dùng `--adb /duong/dan/toi/adb`.

## Lệnh

Chạy từ thư mục `TOOL_REG`:

```bash
python -m xiaowei_proxy_manager devices
python -m xiaowei_proxy_manager --backend xiaowei devices
python -m xiaowei_proxy_manager status --all
```

Proxy đầu vào có dạng bắt buộc:

```text
host:port:username:password
```

Ví dụ chạy thử:

```bash
python -m xiaowei_proxy_manager apply \
  --serial emulator-5554 \
  --proxy 10.0.0.5:8080:my_user:my_pass \
  --dry-run
```

Khi Xiaowei đang mở trên máy Windows, dùng backend Xiaowei:

```bash
python -m xiaowei_proxy_manager --backend xiaowei devices

python -m xiaowei_proxy_manager --backend xiaowei apply \
  --serial ea85356a \
  --proxy 10.0.0.5:8080:my_user:my_pass \
  --dry-run
```

## Giới hạn ADB hiện tại

`adb shell settings put global http_proxy` chỉ đặt được endpoint
`host:port`. Android global proxy không cung cấp chỗ để truyền
`username/password` bằng lệnh ADB này. Vì vậy tool mặc định **không ghi thay
đổi** cho proxy 4 phần để tránh báo thành công giả.

Sau khi bạn xác nhận muốn thử endpoint-only, có thể chạy:

```bash
python -m xiaowei_proxy_manager apply \
  --all \
  --proxy 10.0.0.5:8080:my_user:my_pass \
  --allow-auth-unsupported
```

Lệnh trên sẽ đặt `10.0.0.5:8080`, đồng thời cảnh báo rằng
`my_user/my_pass` chưa được Android sử dụng. Khi có docs Xiaowei, adapter native
sẽ dùng đủ bốn trường.

## CSV mapping

```csv
serial,proxy
box01-phone01,10.0.0.5:8080:user1:pass1
box01-phone02,10.0.0.6:8080:user2:pass2
```

```bash
python -m xiaowei_proxy_manager apply \
  --file sample_assignments.csv \
  --allow-auth-unsupported
```

## Rollback

Mỗi lần apply/clear được ghi vào `data/state.json` (file được tạo quyền
`0600` khi hệ điều hành hỗ trợ). Mật khẩu không được ghi vào log hoặc state;
state chỉ giữ endpoint và nhãn đã che mật khẩu.

```bash
python -m xiaowei_proxy_manager rollback --serial box01-phone01
python -m xiaowei_proxy_manager clear --all
```
