# Xiaowei Proxy Manager (Local Proxy Gateway)

Tool này nằm độc lập với `REG_PBANDAI`. Tool giao tiếp trực tiếp với phone qua
ADB hoặc qua adapter WebSocket cục bộ của Xiaowei. Residential proxy được dùng
ở host machine thông qua Local Proxy Gateway; Android chỉ nhận một endpoint
`HOST_LAN_IP:LOCAL_PORT`.

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

### Cấu hình gateway

Gateway listener mặc định bind `0.0.0.0` để Android Box trong LAN kết nối được.
UI và lệnh `serve` đều tự đọc [config.json](config.json) trong package. Chỉnh
một lần nếu cần đổi IP hoặc port:

```json
{
  "api": {"host": "127.0.0.1", "port": 8765},
  "gateway": {
    "bind_host": "0.0.0.0",
    "advertised_host": "",
    "start_port": 10001,
    "end_port": 11000,
    "connect_timeout": 20,
    "idle_timeout": 300
  },
  "logging": {
    "level": "INFO",
    "dir": "logs",
    "file": "proxy_manager.log",
    "max_bytes": 5242880,
    "backup_count": 5,
    "console": true
  }
}
```

`gateway.advertised_host` để trống thì chương trình tự chọn IPv4; nếu máy có
nhiều card mạng/VPN, điền IP LAN mà Android kết nối được, ví dụ `192.168.1.50`.
`gateway.bind_host` là địa chỉ listener bind, còn `api.host` chỉ dành cho REST
API. Có thể dùng file khác với `gui --config /duong/dan/config.json` hoặc
`serve --config /duong/dan/config.json`.

Gateway hiện giả định upstream là **HTTP proxy**. HTTPS của website vẫn được
hỗ trợ bằng HTTP `CONNECT` tunnel; gateway không MITM, không giải mã TLS và
không cần cài CA trên Android. Chuỗi bạn đưa:

```text
residential.byteproxies.io:8888:pool-basic-cc-jp-city-kyoto-sid-79099880-ttl-30:YOUR_PASSWORD
```

được xử lý như HTTP proxy có Basic authentication. Format
`HOST:PORT:USER:PASS` tự nó không cho biết proxy server có yêu cầu TLS khi kết
nối tới chính proxy hay không; nếu nhà cung cấp yêu cầu `https://proxy-host`,
cần bổ sung protocol riêng sau. Không gửi credential này cho Android.

### Log

CLI, GUI và REST API cùng ghi vào `logs/proxy_manager.log` (xoay vòng 5 MB × 5
file, UTF-8). Mỗi dòng theo format:

```text
2026-09-15 22:47:03.105 | INFO     | xiaowei_proxy_manager.api | [5713542c] --> POST /api/v1/proxy/apply from 127.0.0.1:63397 body=105B
2026-09-15 22:47:03.105 | INFO     | xiaowei_proxy_manager.api | [5713542c] payload {"serial": "phone-1", "proxy": "proxy.example:8080:user:***", "allow_auth_unsupported": true}
2026-09-15 22:47:03.120 | INFO     | xiaowei_proxy_manager.api | [5713542c] result {"mode": "legacy_endpoint", "results": [...], "ok": true}
2026-09-15 22:47:03.120 | INFO     | xiaowei_proxy_manager.api | [5713542c] <-- 200 OK 15.4ms 189B
```

Mỗi request có một `[request-id]` 8 ký tự nối dòng `-->`, `payload`, `result`
và `<--` lại với nhau, kèm thời gian xử lý và số byte. Request lỗi 4xx ghi mức
`WARNING`, lỗi 5xx ghi `ERROR` kèm traceback.

**Mật khẩu proxy không bao giờ được ghi ra log**: mọi field `password`,
`proxy`, `proxies`, `upstream`, `text` đều bị che thành `host:port:user:***`
trước khi ghi. Console chỉ in một dòng cho mỗi bản ghi; traceback đầy đủ nằm
trong file.

Đổi `logging.level` sang `DEBUG` (hoặc chạy `--log-level DEBUG`) để thêm access
log gốc của HTTP server. Đặt `logging.console` = `false` nếu không muốn log ra
màn hình. Thư mục `logs/` đã nằm trong `.gitignore`.

### REST API local

Chạy API khi cần service khác gọi vào proxy manager:

```bash
python -m xiaowei_proxy_manager serve
```

API điều khiển mặc định bind tại `127.0.0.1` và không yêu cầu token.

Health check:

```bash
curl http://127.0.0.1:8765/health
curl http://127.0.0.1:8765/api/v1/devices
curl http://127.0.0.1:8765/api/v1/status
curl http://127.0.0.1:8765/api/v1/gateway
curl http://127.0.0.1:8765/api/v1/gateway/reconcile
```

Gán upstream lần đầu cho một thiết bị. API sẽ cấp local port, khởi động
listener và cấu hình Android thành `gateway.advertised_host:local_port`:

```bash
curl -X POST http://127.0.0.1:8765/api/v1/proxy/apply \
  -H 'Content-Type: application/json' \
  -d '{
    "serial": "box01-phone01",
    "proxy": "residential.byteproxies.io:8888:pool-basic-cc-jp-city-kyoto-sid-79099880-ttl-30:YOUR_PASSWORD"
  }'
```

Đổi residential proxy của cùng thiết bị, Android không bị cấu hình lại:

```bash
curl -X POST http://127.0.0.1:8765/api/v1/devices/box01-phone01/upstream \
  -H 'Content-Type: application/json' \
  -d '{
    "proxy": "residential.byteproxies.io:8888:pool-basic-cc-jp-city-kyoto-sid-79099999-ttl-30:new_password"
  }'
```

Local endpoint của `box01-phone01` vẫn giữ nguyên. Có thể dùng endpoint bulk:

```bash
curl -X POST http://127.0.0.1:8765/api/v1/proxy/upstream \
  -H 'Content-Type: application/json' \
  -d '{
    "serials": ["box01-phone01", "box01-phone02"],
    "proxy": "residential.byteproxies.io:8888:user:password"
  }'
```

Các endpoint chính:

| Method | Endpoint | Mục đích |
|---|---|---|
| `GET` | `/health` hoặc `/api/v1/health` | Kiểm tra API đang chạy |
| `GET` | `/api/v1/devices` | Danh sách thiết bị |
| `GET` | `/api/v1/status` | Proxy thực tế và proxy đã ghi trong state |
| `GET` | `/api/v1/gateway` | Tất cả device → local port → upstream đã mask |
| `GET` | `/api/v1/gateway/reconcile` | Xóa mapping của device không còn trong inventory |
| `GET` | `/api/v1/proxy/pool` | Danh sách proxy pool đã mask |
| `GET` | `/api/v1/devices/{serial}/gateway` | Gateway mapping một thiết bị |
| `GET` | `/api/v1/devices/{serial}/proxy` | Status một thiết bị |
| `POST` | `/api/v1/proxy/apply` | Gán gateway/upstream cho một hoặc nhiều thiết bị |
| `POST` | `/api/v1/devices/{serial}/proxy` | Gán gateway cho một thiết bị |
| `POST` | `/api/v1/devices/{serial}/gateway` | Alias của apply gateway |
| `POST` | `/api/v1/proxy/upstream` | Đổi upstream cho nhiều thiết bị |
| `POST` | `/api/v1/devices/{serial}/upstream` | Đổi upstream cho một thiết bị |
| `POST` | `/api/v1/proxy/pool` | Import proxy pool từ JSON text/list/path |
| `POST` | `/api/v1/proxy/rotate` | Xoay proxy từ pool cho nhiều thiết bị |
| `POST` | `/api/v1/devices/{serial}/proxy/rotate` | Xoay proxy từ pool cho một thiết bị |
| `POST` | `/api/v1/proxy/clear` | Clear bulk |
| `DELETE` | `/api/v1/devices/{serial}/proxy` | Clear một thiết bị |
| `DELETE` | `/api/v1/devices/{serial}/gateway` | Clear Android và dừng listener |
| `POST` | `/api/v1/proxy/rollback` | Rollback bulk |
| `POST` | `/api/v1/devices/{serial}/proxy/rollback` | Rollback một thiết bị |

Bulk request nhận `serial`, `serials` hoặc `all: true`. Ví dụ clear toàn bộ:

```bash
curl -X POST http://127.0.0.1:8765/api/v1/proxy/clear \
  -H 'Content-Type: application/json' \
  -d '{"all": true}'
```

Response chỉ trả upstream dạng mask, ví dụ
`residential.byteproxies.io:8888:user:***`. Password không xuất hiện trong log
hoặc API response. State local được đặt quyền `0600` trên Unix vì gateway cần
restore upstream sau restart; trên Windows cần bảo vệ thư mục state bằng
quyền user/service account tương ứng.

Gateway không giới hạn subnet theo yêu cầu hiện tại. Vì vậy mọi máy có thể
reach được các port gateway đều có thể dùng chúng như proxy; hãy dùng firewall
OS/LAN nếu host nằm trên network không tin cậy.

### Windows và macOS

Trên Windows PowerShell:

```powershell
py -m xiaowei_proxy_manager --backend xiaowei serve
```

Trên macOS:

```bash
python3 -m xiaowei_proxy_manager serve
```

Logic gateway dùng Python socket/threading chuẩn nên không phụ thuộc
PowerShell, `netsh` hoặc lệnh mạng riêng của macOS.

### Giao diện Tkinter

Mở UI từ thư mục cha của package:

```powershell
cd C:\Users\ADMIN\github
python -m xiaowei_proxy_manager gui
```

UI hiển thị serial, trạng thái, model, proxy global hiện tại và IP public đọc
từ chính thiết bị sau khi proxy được áp dụng. Nút `Refresh devices` đọc lại
toàn bộ các giá trị này. Nếu Android không có `curl` hoặc `wget`, cột IP sẽ
hiện `(không đọc được)`.

Khi bấm `Apply proxy`, UI sẽ tạo hoặc dùng lại local gateway port cố định cho
thiết bị đã chọn, set Android về `gateway.advertised_host:local_port`, rồi giữ
credential của upstream proxy trên máy chạy UI. Bấm `Apply proxy` lần sau với
upstream mới sẽ thay proxy phía sau mà endpoint trên Android vẫn giữ nguyên.

Khi chạy backend `xiaowei`, UI dùng `127.0.0.1:local_port` trên Android và tự
tạo `adb reverse` cho từng thiết bị. Cách này không phụ thuộc việc Wi-Fi/AP có
chặn kết nối từ điện thoại về máy tính hay không.
UI cũng tự mở REST API local theo `api.host/api.port` trong `config.json`, nên
service khác có thể gọi API trong lúc app đang mở mà không cần chạy thêm
`serve`.

Nút `Import list` nhận file `.txt`, `.csv` hoặc `.xlsx`. File có thể có một cột
`proxy` chứa `host:port:username:password`, hoặc bốn cột `host`, `port`,
`username`, `password`. Nút `Rotate proxy` bốc random proxy trong pool và chỉ
né các proxy đang được thiết bị khác giữ trong gateway mapping. Khi thiết bị
`Clear proxy`, `Rollback` sang proxy khác hoặc mapping bị gỡ, proxy cũ được mở
lại để thiết bị khác có thể dùng.

Proxy đầu vào có dạng bắt buộc:

```text
host:port:username:password
```

Ví dụ chạy thử:

```bash
python -m xiaowei_proxy_manager gui
```

Khi Xiaowei đang mở trên máy Windows, dùng backend Xiaowei:

```bash
python -m xiaowei_proxy_manager --backend xiaowei devices

python -m xiaowei_proxy_manager --backend xiaowei apply \
  --serial ea85356a \
  --proxy 10.0.0.5:8080:my_user:my_pass \
  --dry-run
```

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

Lệnh CSV/CLI cũ vẫn giữ để tương thích, nhưng flow Local Proxy Gateway đầy đủ
được điều khiển qua process `serve` và REST API. Android cần giữ listener
gateway đang chạy; vì vậy không nên dùng CLI apply cũ để vận hành production
thay cho API gateway.

## Rollback

Mỗi lần apply/clear được ghi vào `data/state.json` (file được tạo quyền
`0600` khi hệ điều hành hỗ trợ). History Android chỉ giữ endpoint/nhãn đã mask;
gateway mapping lưu upstream credential tại host để có thể restore sau restart.
Credential không được trả qua API hoặc log.

```bash
python -m xiaowei_proxy_manager rollback --serial box01-phone01
python -m xiaowei_proxy_manager clear --all
```

## Kiểm thử

Từ thư mục cha của package (ví dụ `C:\Users\ADMIN\github`), chạy:

```bash
python -m unittest discover -s xiaowei_proxy_manager/tests -v
```
