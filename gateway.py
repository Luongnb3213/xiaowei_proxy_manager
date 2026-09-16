from __future__ import annotations

import base64
import os
import select
import socket
import socketserver
import threading
import time
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

from .core.proxy import Proxy
from .core.state import StateStore


class GatewayError(RuntimeError):
    """The local gateway could not create or use a mapping."""


class GatewayRelayError(GatewayError):
    """A local client could not be relayed to the upstream proxy."""


@dataclass
class GatewayMapping:
    device_id: str
    local_port: int
    bind_host: str
    advertised_host: str
    upstream: Proxy | None = None
    server: "_GatewayServer | None" = None
    thread: threading.Thread | None = None
    last_error: str | None = None

    @property
    def endpoint(self) -> str:
        return f"{self.advertised_host}:{self.local_port}"

    @property
    def running(self) -> bool:
        return self.server is not None and self.thread is not None and self.thread.is_alive()

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "local_port": self.local_port,
            "bind_host": self.bind_host,
            "advertised_host": self.advertised_host,
            "endpoint": self.endpoint,
            "upstream": self.upstream.redacted if self.upstream else None,
            "running": self.running,
            "last_error": self.last_error,
        }


class LocalProxyGateway:
    """Stable per-device HTTP proxy listeners with authenticated HTTP upstreams."""

    def __init__(
        self,
        state: StateStore,
        *,
        bind_host: str = "0.0.0.0",
        advertised_host: str | None = None,
        start_port: int = 10001,
        end_port: int = 11000,
        connect_timeout: float = 20,
        idle_timeout: float = 300,
    ):
        if not bind_host:
            raise ValueError("Gateway bind host không được trống.")
        if start_port < 1 or end_port > 65535 or start_port > end_port:
            raise ValueError("Gateway port range không hợp lệ.")
        self.state = state
        self.bind_host = bind_host
        self.advertised_host = advertised_host or detect_advertised_host()
        self.start_port = start_port
        self.end_port = end_port
        self.connect_timeout = connect_timeout
        self.idle_timeout = idle_timeout
        self._lock = threading.RLock()
        self._mappings: dict[str, GatewayMapping] = {}
        self._closed = False
        self._load_state()

    @classmethod
    def from_env(cls, state: StateStore) -> "LocalProxyGateway":
        advertised = os.environ.get("LOCAL_PROXY_ADVERTISED_HOST") or None
        return cls(
            state,
            bind_host=os.environ.get("LOCAL_PROXY_BIND_HOST", "0.0.0.0"),
            advertised_host=advertised,
            start_port=_env_int("LOCAL_PROXY_START_PORT", 10001),
            end_port=_env_int("LOCAL_PROXY_END_PORT", 11000),
            connect_timeout=_env_float("LOCAL_PROXY_CONNECT_TIMEOUT", 20),
            idle_timeout=_env_float("LOCAL_PROXY_IDLE_TIMEOUT", 300),
        )

    def start(self) -> None:
        """Restore persisted ports and start listeners for persisted mappings."""
        with self._lock:
            if self._closed:
                raise GatewayError("Gateway đã đóng.")
            for mapping in self._mappings.values():
                if mapping.server is not None:
                    continue
                try:
                    self._start_listener_locked(mapping)
                except OSError as exc:
                    mapping.last_error = f"Không bind được port {mapping.local_port}: {exc}"

    def close(self) -> None:
        with self._lock:
            self._closed = True
            listeners = [
                (mapping.server, mapping.thread)
                for mapping in self._mappings.values()
                if mapping.server is not None
            ]
            for mapping in self._mappings.values():
                mapping.server = None
                mapping.thread = None
        for server, thread in listeners:
            self._close_listener(server, thread)

    def ensure_mapping(self, device_id: str) -> GatewayMapping:
        device_id = _require_device_id(device_id)
        with self._lock:
            self._ensure_open()
            mapping = self._mappings.get(device_id)
            if mapping is None:
                mapping = self._new_mapping_locked(device_id)
                self.state.set_gateway_mapping(
                    device_id,
                    local_port=mapping.local_port,
                    bind_host=mapping.bind_host,
                    advertised_host=mapping.advertised_host,
                    upstream=None,
                    record_history=False,
                )
                self.state.save()
            elif mapping.server is None:
                try:
                    self._start_listener_locked(mapping)
                except OSError as exc:
                    mapping.last_error = f"Không bind được port {mapping.local_port}: {exc}"
                    raise GatewayError(mapping.last_error) from exc
            return mapping

    def update_upstream(self, device_id: str, upstream: Proxy) -> GatewayMapping:
        device_id = _require_device_id(device_id)
        with self._lock:
            mapping = self.ensure_mapping(device_id)
            mapping.upstream = upstream
            mapping.last_error = None
            if mapping.server is None:
                try:
                    self._start_listener_locked(mapping)
                except OSError as exc:
                    mapping.last_error = f"Không bind được port {mapping.local_port}: {exc}"
                    raise GatewayError(mapping.last_error) from exc
            self.state.set_gateway_mapping(
                device_id,
                local_port=mapping.local_port,
                bind_host=mapping.bind_host,
                advertised_host=mapping.advertised_host,
                upstream=upstream,
            )
            self.state.save()
            return mapping

    def rollback_upstream(self, device_id: str) -> GatewayMapping:
        target = self.state.gateway_rollback_target(device_id)
        if target is None:
            raise GatewayError(f"Thiết bị {device_id} chưa có upstream history.")
        return self.update_upstream(device_id, target)

    def mapping(self, device_id: str) -> GatewayMapping | None:
        with self._lock:
            return self._mappings.get(device_id)

    def mappings(self) -> list[GatewayMapping]:
        with self._lock:
            return list(self._mappings.values())

    def set_advertised_host(self, host: str) -> None:
        """Change the endpoint host shown to Android while keeping listeners."""
        host = str(host or "").strip()
        if not host:
            raise ValueError("Gateway advertised host không được trống.")
        with self._lock:
            self.advertised_host = host
            for mapping in self._mappings.values():
                mapping.advertised_host = host
                self.state.set_gateway_mapping(
                    mapping.device_id,
                    local_port=mapping.local_port,
                    bind_host=mapping.bind_host,
                    advertised_host=host,
                    upstream=mapping.upstream,
                    record_history=False,
                )
            self.state.save()

    def remove_mapping(self, device_id: str, *, delete_state: bool = True) -> None:
        device_id = _require_device_id(device_id)
        with self._lock:
            mapping = self._mappings.pop(device_id, None)
            if delete_state:
                self.state.remove_gateway_mapping(device_id)
                self.state.save()
        if mapping and mapping.server is not None:
            self._close_listener(mapping.server, mapping.thread)

    def reconcile(self, active_device_ids: set[str]) -> list[str]:
        """Remove mappings explicitly absent from the supplied device inventory."""
        with self._lock:
            removed = [
                device_id
                for device_id in self._mappings
                if device_id not in active_device_ids
            ]
        for device_id in removed:
            self.remove_mapping(device_id)
        return removed

    def _load_state(self) -> None:
        for device_id, value in self.state.gateway_mappings().items():
            try:
                local_port = int(value["local_port"])
                bind_host = str(value.get("bind_host") or self.bind_host)
                advertised_host = str(value.get("advertised_host") or self.advertised_host)
                raw_upstream = value.get("upstream")
                upstream = Proxy.from_raw(raw_upstream) if raw_upstream else None
                self._mappings[device_id] = GatewayMapping(
                    device_id=device_id,
                    local_port=local_port,
                    bind_host=bind_host,
                    advertised_host=advertised_host,
                    upstream=upstream,
                )
            except (KeyError, TypeError, ValueError, GatewayError) as exc:
                self._mappings[device_id] = GatewayMapping(
                    device_id=device_id,
                    local_port=int(value.get("local_port", 0) or 0),
                    bind_host=self.bind_host,
                    advertised_host=self.advertised_host,
                    last_error=f"Gateway state không hợp lệ: {exc}",
                )

    def _new_mapping_locked(self, device_id: str) -> GatewayMapping:
        used = {mapping.local_port for mapping in self._mappings.values()}
        for port in range(self.start_port, self.end_port + 1):
            if port in used:
                continue
            mapping = GatewayMapping(
                device_id=device_id,
                local_port=port,
                bind_host=self.bind_host,
                advertised_host=self.advertised_host,
            )
            try:
                self._start_listener_locked(mapping)
            except OSError:
                continue
            self._mappings[device_id] = mapping
            return mapping
        raise GatewayError(
            f"Không còn port gateway khả dụng trong range "
            f"{self.start_port}-{self.end_port}."
        )

    def _start_listener_locked(self, mapping: GatewayMapping) -> None:
        if mapping.server is not None:
            return
        if not 1 <= mapping.local_port <= 65535:
            raise GatewayError(f"Local port không hợp lệ: {mapping.local_port}.")
        if _port_accepts_connections(mapping.advertised_host, mapping.local_port):
            raise OSError(f"Port {mapping.local_port} đang có listener trên {mapping.advertised_host}.")
        server = _GatewayServer(
            (mapping.bind_host, mapping.local_port),
            self,
            mapping.device_id,
        )
        thread = threading.Thread(
            target=server.serve_forever,
            name=f"proxy-gateway-{mapping.device_id}",
            daemon=True,
        )
        mapping.server = server
        mapping.thread = thread
        thread.start()

    def _close_listener(
        self,
        server: "_GatewayServer | None",
        thread: threading.Thread | None,
    ) -> None:
        if server is None:
            return
        try:
            server.shutdown()
        finally:
            server.server_close()
        if thread and thread is not threading.current_thread():
            thread.join(timeout=2)

    def _ensure_open(self) -> None:
        if self._closed:
            raise GatewayError("Gateway đã đóng.")

    def _upstream_for(self, device_id: str) -> Proxy | None:
        with self._lock:
            mapping = self._mappings.get(device_id)
            return mapping.upstream if mapping else None

    def _connect_upstream(self, upstream: Proxy) -> socket.socket:
        try:
            connection = socket.create_connection(
                (upstream.host, upstream.port),
                timeout=self.connect_timeout,
            )
            connection.settimeout(self.connect_timeout)
            return connection
        except OSError as exc:
            raise GatewayRelayError("Không kết nối được upstream proxy.") from exc


class _GatewayServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = False
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        gateway: LocalProxyGateway,
        device_id: str,
    ):
        self.gateway = gateway
        self.device_id = device_id
        super().__init__(server_address, _GatewayRequestHandler)


class _GatewayRequestHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        client = self.request
        client.settimeout(self.server.gateway.connect_timeout)
        upstream_config = self.server.gateway._upstream_for(self.server.device_id)
        if upstream_config is None:
            _send_error(client, 503, "Local gateway chưa có upstream.")
            return
        try:
            _handle_client(
                client,
                upstream_config,
                connect_timeout=self.server.gateway.connect_timeout,
                idle_timeout=self.server.gateway.idle_timeout,
            )
        except GatewayRelayError:
            _send_error(client, 502, "Upstream proxy không khả dụng.")
        except (OSError, ValueError):
            _send_error(client, 400, "Proxy request không hợp lệ.")


class _BufferedSocket:
    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.buffer = bytearray()

    def read_until(self, marker: bytes, limit: int) -> bytes:
        while marker not in self.buffer:
            if len(self.buffer) > limit:
                raise GatewayRelayError("Proxy headers quá lớn.")
            chunk = self.sock.recv(8192)
            if not chunk:
                raise GatewayRelayError("Client đóng kết nối giữa request.")
            self.buffer.extend(chunk)
        index = self.buffer.index(marker) + len(marker)
        result = bytes(self.buffer[:index])
        del self.buffer[:index]
        return result

    def read_exact(self, length: int) -> bytes:
        while len(self.buffer) < length:
            chunk = self.sock.recv(min(65536, length - len(self.buffer)))
            if not chunk:
                raise GatewayRelayError("Client đóng kết nối giữa request body.")
            self.buffer.extend(chunk)
        result = bytes(self.buffer[:length])
        del self.buffer[:length]
        return result

    def read_line(self, limit: int = 8192) -> bytes:
        return self.read_until(b"\r\n", limit)


def _handle_client(
    client: socket.socket,
    upstream: Proxy,
    *,
    connect_timeout: float,
    idle_timeout: float,
) -> None:
    reader = _BufferedSocket(client)
    request_head = reader.read_until(b"\r\n\r\n", 64 * 1024)
    method, target, version, headers = _parse_headers(request_head)
    if method.upper() == "CONNECT":
        _handle_connect(
            client,
            target,
            upstream,
            initial_payload=bytes(reader.buffer),
            connect_timeout=connect_timeout,
            idle_timeout=idle_timeout,
        )
        return

    upstream_socket = _connect_for_proxy(upstream, connect_timeout)
    try:
        rewritten = _build_upstream_request(method, target, version, headers, upstream)
        upstream_socket.sendall(rewritten)
        _forward_request_body(reader, upstream_socket, headers)
        upstream_socket.shutdown(socket.SHUT_WR)
        _forward_http_response(upstream_socket, client)
    finally:
        upstream_socket.close()


def _handle_connect(
    client: socket.socket,
    target: str,
    upstream: Proxy,
    *,
    initial_payload: bytes,
    connect_timeout: float,
    idle_timeout: float,
) -> None:
    host, port = _split_target(target, default_port=443)
    upstream_socket = _connect_for_proxy(upstream, connect_timeout)
    try:
        auth = _proxy_authorization(upstream)
        connect_request = (
            f"CONNECT {host}:{port} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Proxy-Authorization: {auth}\r\n"
            "Connection: keep-alive\r\n"
            "\r\n"
        ).encode("ascii")
        upstream_socket.sendall(connect_request)
        response = _read_headers_from_socket(upstream_socket, 64 * 1024)
        client.sendall(response)
        status = _status_code(response)
        if 200 <= status < 300:
            if initial_payload:
                upstream_socket.sendall(initial_payload)
            _bidirectional_copy(client, upstream_socket, idle_timeout=idle_timeout)
    finally:
        upstream_socket.close()


def _connect_for_proxy(upstream: Proxy, timeout: float) -> socket.socket:
    try:
        connection = socket.create_connection((upstream.host, upstream.port), timeout=timeout)
        connection.settimeout(timeout)
        return connection
    except OSError as exc:
        raise GatewayRelayError("Không kết nối được upstream proxy.") from exc


def _build_upstream_request(
    method: str,
    target: str,
    version: str,
    headers: list[tuple[str, str]],
    upstream: Proxy,
) -> bytes:
    header_map = {name.lower(): value for name, value in headers}
    if target.startswith("/"):
        host = header_map.get("host")
        if not host:
            raise GatewayRelayError("HTTP request relative-form thiếu Host.")
        target = f"http://{host}{target}"

    output = [(name, value) for name, value in headers if name.lower() not in {
        "proxy-authorization",
        "proxy-connection",
        "connection",
    }]
    output.append(("Proxy-Authorization", _proxy_authorization(upstream)))
    output.append(("Connection", "close"))
    lines = [f"{method} {target} {version}"]
    lines.extend(f"{name}: {value}" for name, value in output)
    return ("\r\n".join(lines) + "\r\n\r\n").encode("iso-8859-1")

def _proxy_authorization(upstream: Proxy) -> str:
    encoded = base64.b64encode(
        f"{upstream.username}:{upstream.password}".encode("utf-8")
    ).decode("ascii")
    return f"Basic {encoded}"


def _forward_request_body(
    reader: _BufferedSocket,
    upstream_socket: socket.socket,
    headers: list[tuple[str, str]],
) -> None:
    header_map = {name.lower(): value.strip() for name, value in headers}
    content_length = header_map.get("content-length")
    if content_length is not None:
        try:
            remaining = int(content_length)
        except ValueError as exc:
            raise GatewayRelayError("Content-Length không hợp lệ.") from exc
        if remaining < 0:
            raise GatewayRelayError("Content-Length không hợp lệ.")
        while remaining:
            chunk = reader.read_exact(min(65536, remaining))
            upstream_socket.sendall(chunk)
            remaining -= len(chunk)
        return

    transfer_encoding = header_map.get("transfer-encoding", "").lower()
    if "chunked" in transfer_encoding:
        while True:
            size_line = reader.read_line()
            upstream_socket.sendall(size_line)
            size_text = size_line[:-2].split(b";", 1)[0].strip()
            try:
                size = int(size_text, 16)
            except ValueError as exc:
                raise GatewayRelayError("Chunked body không hợp lệ.") from exc
            data = reader.read_exact(size + 2)
            upstream_socket.sendall(data)
            if size == 0:
                while True:
                    trailer = reader.read_line()
                    upstream_socket.sendall(trailer)
                    if trailer == b"\r\n":
                        return


def _forward_http_response(upstream: socket.socket, client: socket.socket) -> None:
    response_head = _read_headers_from_socket(upstream, 64 * 1024)
    client.sendall(response_head)
    _version, status, headers = _parse_response_headers(response_head)
    header_map = {name.lower(): value.strip() for name, value in headers}
    if status in {204, 304} or 100 <= status < 200:
        return
    content_length = header_map.get("content-length")
    if content_length is not None:
        try:
            remaining = int(content_length)
        except ValueError as exc:
            raise GatewayRelayError("Upstream Content-Length không hợp lệ.") from exc
        while remaining:
            chunk = upstream.recv(min(65536, remaining))
            if not chunk:
                raise GatewayRelayError("Upstream đóng trước khi đủ response body.")
            client.sendall(chunk)
            remaining -= len(chunk)
        return
    if "chunked" in header_map.get("transfer-encoding", "").lower():
        _copy_chunked_response(upstream, client)
        return
    _copy_until_close(upstream, client)


def _copy_chunked_response(upstream: socket.socket, client: socket.socket) -> None:
    reader = _BufferedSocket(upstream)
    while True:
        line = reader.read_line()
        client.sendall(line)
        size_text = line[:-2].split(b";", 1)[0].strip()
        try:
            size = int(size_text, 16)
        except ValueError as exc:
            raise GatewayRelayError("Upstream chunked response không hợp lệ.") from exc
        data = reader.read_exact(size + 2)
        client.sendall(data)
        if size == 0:
            while True:
                trailer = reader.read_line()
                client.sendall(trailer)
                if trailer == b"\r\n":
                    return


def _copy_until_close(source: socket.socket, destination: socket.socket) -> None:
    while True:
        try:
            chunk = source.recv(65536)
        except socket.timeout:
            return
        if not chunk:
            return
        destination.sendall(chunk)


def _bidirectional_copy(
    left: socket.socket,
    right: socket.socket,
    *,
    idle_timeout: float,
) -> None:
    sockets = [left, right]
    last_activity = time.monotonic()
    while sockets:
        remaining = idle_timeout - (time.monotonic() - last_activity)
        if remaining <= 0:
            return
        readable, _writable, _exceptional = select.select(sockets, [], sockets, min(remaining, 1.0))
        if not readable:
            continue
        for source in readable:
            destination = right if source is left else left
            try:
                data = source.recv(65536)
            except OSError:
                return
            if not data:
                try:
                    destination.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                sockets.remove(source)
                continue
            destination.sendall(data)
            last_activity = time.monotonic()


def _read_headers_from_socket(sock: socket.socket, limit: int) -> bytes:
    data = bytearray()
    while b"\r\n\r\n" not in data:
        if len(data) > limit:
            raise GatewayRelayError("Proxy headers quá lớn.")
        chunk = sock.recv(1)
        if not chunk:
            raise GatewayRelayError("Upstream đóng kết nối giữa response headers.")
        data.extend(chunk)
    return bytes(data)


def _parse_headers(raw: bytes) -> tuple[str, str, str, list[tuple[str, str]]]:
    lines = raw.decode("iso-8859-1").split("\r\n")
    if not lines or len(lines[0].split(" ", 2)) != 3:
        raise GatewayRelayError("Request line không hợp lệ.")
    method, target, version = lines[0].split(" ", 2)
    if not version.startswith("HTTP/"):
        raise GatewayRelayError("HTTP version không hợp lệ.")
    headers = _parse_header_lines(lines[1:-2])
    return method, target, version, headers


def _parse_response_headers(raw: bytes) -> tuple[str, int, list[tuple[str, str]]]:
    lines = raw.decode("iso-8859-1").split("\r\n")
    parts = lines[0].split(" ", 2)
    if len(parts) < 2:
        raise GatewayRelayError("Response line không hợp lệ.")
    try:
        status = int(parts[1])
    except ValueError as exc:
        raise GatewayRelayError("Response status không hợp lệ.") from exc
    return parts[0], status, _parse_header_lines(lines[1:-2])


def _parse_header_lines(lines: list[str]) -> list[tuple[str, str]]:
    headers: list[tuple[str, str]] = []
    for line in lines:
        if not line:
            continue
        if ":" not in line:
            raise GatewayRelayError("HTTP header không hợp lệ.")
        name, value = line.split(":", 1)
        headers.append((name.strip(), value.strip()))
    return headers


def _split_target(target: str, *, default_port: int) -> tuple[str, int]:
    if not target or any(char.isspace() for char in target):
        raise GatewayRelayError("CONNECT target không hợp lệ.")
    if target.startswith("["):
        closing = target.find("]")
        if closing < 0:
            raise GatewayRelayError("CONNECT IPv6 target không hợp lệ.")
        host = target[1:closing]
        port_text = target[closing + 2 :] if target[closing + 1 : closing + 2] == ":" else ""
    else:
        host, separator, port_text = target.rpartition(":")
        if not separator:
            host, port_text = target, str(default_port)
    try:
        port = int(port_text or default_port)
    except ValueError as exc:
        raise GatewayRelayError("CONNECT port không hợp lệ.") from exc
    if not host or not 1 <= port <= 65535:
        raise GatewayRelayError("CONNECT target không hợp lệ.")
    return host, port


def _status_code(response: bytes) -> int:
    try:
        return int(response.decode("iso-8859-1").split(" ", 2)[1])
    except (IndexError, ValueError) as exc:
        raise GatewayRelayError("Upstream response không hợp lệ.") from exc


def _send_error(sock: socket.socket, status: int, message: str) -> None:
    body = f"{status} {message}\n".encode("utf-8")
    try:
        reason = HTTPStatus(status).phrase
    except ValueError:
        reason = "Error"
    response = (
        f"HTTP/1.1 {status} {reason}\r\n"
        "Connection: close\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Content-Type: text/plain; charset=utf-8\r\n"
        "\r\n"
    ).encode("ascii") + body
    try:
        sock.sendall(response)
    except OSError:
        pass


def detect_advertised_host() -> str:
    """Find the local IPv4 address used for outbound LAN routing."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return str(sock.getsockname()[0])
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def _port_accepts_connections(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except OSError:
        return False


def _require_device_id(device_id: str) -> str:
    value = str(device_id or "").strip()
    if not value:
        raise ValueError("Device id không được trống.")
    return value


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} phải là số nguyên.") from exc


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} phải là số.") from exc
