from __future__ import annotations

import base64
import hashlib
import json
import os
import shlex
import socket
import ssl
import struct
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .adb import Device


class XiaoweiError(RuntimeError):
    """A Xiaowei WebSocket API call failed."""


@dataclass(frozen=True)
class XiaoweiResponse:
    code: int
    message: str
    data: Any

    @property
    def ok(self) -> bool:
        return self.code == 10000


class SimpleWebSocket:
    """Small blocking WebSocket client for Xiaowei's local JSON API.

    It supports text frames, ping/pong and close frames. This is deliberately
    narrow: enough for ``ws://127.0.0.1:22222/`` without pulling a dependency.
    """

    GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

    def __init__(self, url: str, *, timeout: float = 20):
        self.url = url
        self.timeout = timeout
        self.sock: socket.socket | ssl.SSLSocket | None = None

    def __enter__(self) -> "SimpleWebSocket":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def connect(self) -> None:
        parsed = urlsplit(self.url)
        if parsed.scheme not in {"ws", "wss"}:
            raise XiaoweiError(f"URL WebSocket không hợp lệ: {self.url}")
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query

        raw_sock = socket.create_connection((host, port), timeout=self.timeout)
        raw_sock.settimeout(self.timeout)
        if parsed.scheme == "wss":
            sock: socket.socket | ssl.SSLSocket = ssl.create_default_context().wrap_socket(
                raw_sock,
                server_hostname=host,
            )
        else:
            sock = raw_sock

        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        sock.sendall(request.encode("ascii"))
        response = self._recv_http_response(sock)
        accept = base64.b64encode(hashlib.sha1((key + self.GUID).encode("ascii")).digest()).decode(
            "ascii"
        )
        if " 101 " not in response.split("\r\n", 1)[0]:
            raise XiaoweiError(f"Xiaowei WebSocket handshake thất bại: {response.splitlines()[0]}")
        if f"sec-websocket-accept: {accept.lower()}" not in response.lower():
            raise XiaoweiError("Xiaowei WebSocket handshake thiếu Sec-WebSocket-Accept hợp lệ.")
        self.sock = sock

    @staticmethod
    def _recv_http_response(sock: socket.socket | ssl.SSLSocket) -> str:
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        return data.decode("iso-8859-1", errors="replace")

    def send_json(self, payload: dict[str, Any]) -> None:
        self.send_text(json.dumps(payload, ensure_ascii=False))

    def recv_json(self) -> dict[str, Any]:
        message = self.recv_text()
        try:
            payload = json.loads(message)
        except json.JSONDecodeError as exc:
            raise XiaoweiError(f"Xiaowei trả về JSON không hợp lệ: {message[:200]}") from exc
        if not isinstance(payload, dict):
            raise XiaoweiError("Xiaowei response không phải JSON object.")
        return payload

    def send_text(self, text: str) -> None:
        payload = text.encode("utf-8")
        self._send_frame(0x1, payload)

    def recv_text(self) -> str:
        chunks: list[bytes] = []
        while True:
            fin, opcode, payload = self._recv_frame()
            if opcode == 0x8:
                raise XiaoweiError("Xiaowei đã đóng WebSocket trước khi trả response.")
            if opcode == 0x9:
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode in (0x1, 0x0):
                chunks.append(payload)
                if fin:
                    return b"".join(chunks).decode("utf-8", errors="replace")
            else:
                raise XiaoweiError(f"WebSocket opcode không hỗ trợ: {opcode}")

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        if self.sock is None:
            raise XiaoweiError("WebSocket chưa kết nối.")
        header = bytearray([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length <= 0xFFFF:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        mask = os.urandom(4)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        self.sock.sendall(bytes(header) + mask + masked)

    def _recv_frame(self) -> tuple[bool, int, bytes]:
        if self.sock is None:
            raise XiaoweiError("WebSocket chưa kết nối.")
        first = self._recv_exact(2)
        fin = bool(first[0] & 0x80)
        opcode = first[0] & 0x0F
        masked = bool(first[1] & 0x80)
        length = first[1] & 0x7F
        if length == 126:
            length = struct.unpack("!H", self._recv_exact(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", self._recv_exact(8))[0]
        mask = self._recv_exact(4) if masked else b""
        payload = self._recv_exact(length) if length else b""
        if masked:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        return fin, opcode, payload

    def _recv_exact(self, length: int) -> bytes:
        if self.sock is None:
            raise XiaoweiError("WebSocket chưa kết nối.")
        chunks: list[bytes] = []
        remaining = length
        while remaining:
            chunk = self.sock.recv(remaining)
            if not chunk:
                raise XiaoweiError("WebSocket đóng giữa chừng.")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def close(self) -> None:
        if self.sock is None:
            return
        try:
            self._send_frame(0x8, b"")
        except Exception:
            pass
        try:
            self.sock.close()
        finally:
            self.sock = None


class XiaoweiClient:
    def __init__(self, url: str = "ws://127.0.0.1:22222/", *, timeout: float = 20):
        self.url = url
        self.timeout = timeout

    def request(self, payload: dict[str, Any], *, check: bool = True) -> XiaoweiResponse:
        with SimpleWebSocket(self.url, timeout=self.timeout) as ws:
            ws.send_json(payload)
            raw = ws.recv_json()
        response = XiaoweiResponse(
            code=int(raw.get("code", 0)),
            message=str(raw.get("message") or raw.get("msg") or ""),
            data=raw.get("data"),
        )
        if check and not response.ok:
            raise XiaoweiError(f"Xiaowei API lỗi {response.code}: {response.message}")
        return response

    def list_devices(self) -> list[Device]:
        response = self.request({"action": "list"})
        if not response.data:
            return []
        if not isinstance(response.data, list):
            raise XiaoweiError("Response `list` không trả về array.")
        devices: list[Device] = []
        for item in response.data:
            if not isinstance(item, dict):
                continue
            serial = str(item.get("serial") or item.get("onlySerial") or "").strip()
            if not serial:
                continue
            status = str(item.get("status") or "online").lower()
            state = "device" if status in {"online", "device"} else status
            details = " ".join(
                part
                for part in (
                    f"name:{item.get('name')}" if item.get("name") else "",
                    f"model:{item.get('model')}" if item.get("model") else "",
                    f"onlySerial:{item.get('onlySerial')}" if item.get("onlySerial") else "",
                    f"mode:{item.get('mode')}" if item.get("mode") is not None else "",
                    f"intranetIp:{item.get('intranetIp')}" if item.get("intranetIp") else "",
                )
                if part
            )
            devices.append(Device(serial=serial, state=state, details=details))
        return devices

    def shell(self, serial: str, *command: str, check: bool = True) -> str:
        command_text = " ".join(shlex.quote(str(part)) for part in command)
        response = self.request(
            {
                "action": "adb",
                "devices": serial,
                "data": {"command": f"adb shell {command_text}"},
            },
            check=check,
        )
        return self._extract_output(response.data, serial).strip()

    def shell_no_output(self, serial: str, command: str, *, check: bool = True) -> None:
        self.request(
            {
                "action": "adb_shell",
                "devices": serial,
                "data": {"command": command},
            },
            check=check,
        )

    @staticmethod
    def _extract_output(data: Any, serial: str) -> str:
        if data is None:
            return ""
        if isinstance(data, dict):
            if serial in data:
                return str(data[serial])
            if data:
                return str(next(iter(data.values())))
            return ""
        return str(data)

    def get_global_proxy(self, serial: str) -> str | None:
        value = self.shell(serial, "settings", "get", "global", "http_proxy").strip()
        if not value or value.lower() in {"null", "none", ":0", "0.0.0.0:0"}:
            return None
        return value

    def set_global_proxy(self, serial: str, endpoint: str) -> str | None:
        self.shell_no_output(serial, f"settings put global http_proxy {endpoint}")
        return self.get_global_proxy(serial)

    def clear_global_proxy(self, serial: str) -> str | None:
        self.shell_no_output(serial, "settings put global http_proxy :0")
        return self.get_global_proxy(serial)

    def reverse_port(self, serial: str, port: int) -> None:
        """Create an ADB reverse tunnel from phone localhost to this host."""
        if port < 1 or port > 65535:
            raise ValueError("Port reverse không hợp lệ.")
        response = self.request(
            {
                "action": "adb",
                "devices": serial,
                "data": {"command": f"adb -s {serial} reverse tcp:{port} tcp:{port}"},
            }
        )
        # Xiaowei normally returns the command output in data; a successful
        # API response is sufficient because `adb reverse` is otherwise silent.
        _ = response

    def get_model(self, serial: str) -> dict[str, str]:
        return {
            "manufacturer": self.shell(serial, "getprop", "ro.product.manufacturer", check=False),
            "model": self.shell(serial, "getprop", "ro.product.model", check=False),
            "android": self.shell(serial, "getprop", "ro.build.version.release", check=False),
            "device": self.shell(serial, "getprop", "ro.product.device", check=False),
        }

