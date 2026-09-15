from __future__ import annotations

import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlsplit

from .adb import AdbError, Device
from .gateway import GatewayError, LocalProxyGateway
from .proxy import Proxy, ProxyParseError, parse_proxy
from .proxy_pool import load_proxy_file, parse_proxy_text
from .state import StateStore
from .xiaowei import XiaoweiError


API_PREFIX = "/api/v1"


class ApiRequestError(ValueError):
    """Raised when an API request is missing or has invalid input."""


class ProxyManagerService:
    """Application service shared by CLI, GUI and local HTTP callers."""

    def __init__(
        self,
        client,
        state: StateStore,
        *,
        backend: str = "adb",
        gateway: LocalProxyGateway | None = None,
    ):
        self.client = client
        self.state = state
        self.backend = backend
        self.gateway = gateway
        self._lock = threading.RLock()

    def start(self) -> None:
        if self.gateway:
            self.gateway.start()

    def close(self) -> None:
        if self.gateway:
            self.gateway.close()

    def health(self) -> dict[str, Any]:
        return {
            "service": "xiaowei-proxy-manager",
            "version": "0.2.0",
            "backend": self.backend,
            "gateway": bool(self.gateway),
            "gateway_bind_host": self.gateway.bind_host if self.gateway else None,
            "gateway_advertised_host": self.gateway.advertised_host if self.gateway else None,
        }

    def list_devices(self, *, include_model: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            devices = self.client.list_devices()
            rows: list[dict[str, Any]] = []
            for device in devices:
                row = _device_to_dict(device)
                if include_model and device.usable:
                    row["model"] = self.client.get_model(device.serial)
                if self.gateway:
                    mapping = self.gateway.mapping(device.serial)
                    row["gateway"] = mapping.to_dict() if mapping else None
                rows.append(row)
            return rows

    def gateway_status(self, serial: str | None = None) -> list[dict[str, Any]]:
        if not self.gateway:
            return []
        if serial:
            mapping = self.gateway.mapping(serial)
            return [mapping.to_dict()] if mapping else []
        return [mapping.to_dict() for mapping in self.gateway.mappings()]

    def proxy_pool(self) -> dict[str, Any]:
        return self.state.proxy_pool_summary()

    def import_proxy_pool(
        self,
        proxies: list[Proxy],
        *,
        append: bool = False,
        source: str = "",
    ) -> dict[str, Any]:
        if not proxies:
            raise ApiRequestError("Danh sách proxy trống.")
        result = self.state.set_proxy_pool(proxies, append=append, source=source)
        self.state.save()
        result["items"] = self.state.proxy_pool_summary()["items"]
        return result

    def import_proxy_pool_from_file(
        self,
        path: str,
        *,
        append: bool = False,
    ) -> dict[str, Any]:
        return self.import_proxy_pool(
            load_proxy_file(path),
            append=append,
            source=path,
        )

    def reconcile_gateway(self) -> dict[str, Any]:
        if not self.gateway:
            raise ApiRequestError("Local Proxy Gateway chưa được khởi tạo.")
        active = {
            device.serial
            for device in self.client.list_devices()
            if device.usable
        }
        removed = self.gateway.reconcile(active)
        return {"removed": removed, "count": len(removed)}

    def status(self, serials: list[str]) -> list[dict[str, Any]]:
        with self._lock:
            devices = self._select_devices(serials)
            rows: list[dict[str, Any]] = []
            for device in devices:
                actual = self.client.get_global_proxy(device.serial)
                row: dict[str, Any] = {
                    "serial": device.serial,
                    "actual": actual,
                    "recorded": self.state.current(device.serial),
                }
                if self.gateway:
                    mapping = self.gateway.mapping(device.serial)
                    row["gateway"] = mapping.to_dict() if mapping else None
                rows.append(row)
            return rows

    def apply(
        self,
        proxy: Proxy,
        serials: list[str],
        *,
        allow_auth_unsupported: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Assign a gateway endpoint, or rotate its upstream without changing Android."""
        if not self.gateway:
            return self._legacy_apply(
                proxy,
                serials,
                allow_auth_unsupported=allow_auth_unsupported,
                dry_run=dry_run,
            )

        with self._lock:
            devices = self._select_devices(serials)
            results: list[dict[str, Any]] = []
            for device in devices:
                result: dict[str, Any] = {
                    "serial": device.serial,
                    "upstream": proxy.redacted,
                    "dry_run": dry_run,
                }
                try:
                    mapping = self.gateway.mapping(device.serial)
                    before = self.client.get_global_proxy(device.serial)
                    if dry_run:
                        result.update(
                            {
                                "local_endpoint": mapping.endpoint if mapping else None,
                                "android_before": before,
                                "android_after": mapping.endpoint if mapping else None,
                                "upstream_changed": True,
                                "ok": True,
                            }
                        )
                    else:
                        mapping = self.gateway.update_upstream(device.serial, proxy)
                        android_after = self._ensure_android_endpoint(
                            device.serial,
                            mapping.endpoint,
                            before=before,
                        )
                        result.update(
                            {
                                "local_endpoint": mapping.endpoint,
                                "android_before": before,
                                "android_after": android_after,
                                "upstream_changed": True,
                                "ok": True,
                            }
                        )
                except Exception as exc:
                    result["ok"] = False
                    result["error"] = _safe_error(exc)
                results.append(result)
            return {
                "mode": "local_gateway",
                "upstream": proxy.redacted,
                "dry_run": dry_run,
                "results": results,
                "ok": all(result["ok"] for result in results),
            }

    def update_upstream(
        self,
        proxy: Proxy,
        serials: list[str],
        *,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        if not self.gateway:
            raise ApiRequestError("Local Proxy Gateway chưa được khởi tạo.")
        with self._lock:
            devices = self._select_devices(serials)
            results: list[dict[str, Any]] = []
            for device in devices:
                result: dict[str, Any] = {
                    "serial": device.serial,
                    "upstream": proxy.redacted,
                    "dry_run": dry_run,
                }
                try:
                    mapping = self.gateway.mapping(device.serial)
                    before = self.client.get_global_proxy(device.serial)
                    if dry_run:
                        result.update(
                            {
                                "local_endpoint": mapping.endpoint if mapping else None,
                                "android_changed": False,
                                "ok": True,
                            }
                        )
                    else:
                        mapping = self.gateway.update_upstream(device.serial, proxy)
                        android_after = self._ensure_android_endpoint(
                            device.serial,
                            mapping.endpoint,
                            before=before,
                        )
                        result.update(
                            {
                                "local_endpoint": mapping.endpoint,
                                "android_before": before,
                                "android_after": android_after,
                                "android_changed": before != android_after,
                                "ok": True,
                            }
                        )
                except Exception as exc:
                    result["ok"] = False
                    result["error"] = _safe_error(exc)
                results.append(result)
            return {
                "mode": "local_gateway",
                "upstream": proxy.redacted,
                "dry_run": dry_run,
                "results": results,
                "ok": all(result["ok"] for result in results),
            }

    def rotate_from_pool(
        self,
        serials: list[str],
        *,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        if not self.gateway:
            raise ApiRequestError("Local Proxy Gateway chưa được khởi tạo.")
        with self._lock:
            devices = self._select_devices(serials)
            results: list[dict[str, Any]] = []
            for device in devices:
                result: dict[str, Any] = {"serial": device.serial, "dry_run": dry_run}
                try:
                    proxy = self._next_available_pool_proxy(
                        device.serial,
                        advance=not dry_run,
                    )
                    mapping = self.gateway.mapping(device.serial)
                    before = self.client.get_global_proxy(device.serial)
                    result["upstream"] = proxy.redacted
                    result["android_before"] = before
                    if dry_run:
                        result["local_endpoint"] = mapping.endpoint if mapping else None
                        result["ok"] = True
                    else:
                        mapping = self.gateway.update_upstream(device.serial, proxy)
                        android_after = self._ensure_android_endpoint(
                            device.serial,
                            mapping.endpoint,
                            before=before,
                        )
                        self.state.save()
                        result["local_endpoint"] = mapping.endpoint
                        result["android_after"] = android_after
                        result["ok"] = True
                except Exception as exc:
                    result["ok"] = False
                    result["error"] = _safe_error(exc)
                results.append(result)
            return {
                "mode": "local_gateway",
                "dry_run": dry_run,
                "results": results,
                "ok": all(result["ok"] for result in results),
            }

    def clear(self, serials: list[str], *, dry_run: bool = False) -> dict[str, Any]:
        with self._lock:
            devices = self._select_devices(serials)
            results: list[dict[str, Any]] = []
            for device in devices:
                result: dict[str, Any] = {"serial": device.serial, "dry_run": dry_run}
                try:
                    before = self.client.get_global_proxy(device.serial)
                    if dry_run:
                        result.update({"before": before, "after": None, "ok": True})
                        results.append(result)
                        continue
                    actual = self.client.clear_global_proxy(device.serial)
                    if actual is not None:
                        raise AdbError(f"Không xóa được proxy, vẫn còn {actual!r}")
                    self.state.record_change(
                        device.serial,
                        before=before,
                        after=None,
                        label="clear",
                    )
                    if self.gateway:
                        self.gateway.remove_mapping(device.serial)
                    result.update({"before": before, "after": None, "ok": True})
                except Exception as exc:
                    result["ok"] = False
                    result["error"] = _safe_error(exc)
                results.append(result)
            if not dry_run and any(result["ok"] for result in results):
                self.state.save()
            return {
                "dry_run": dry_run,
                "results": results,
                "ok": all(result["ok"] for result in results),
            }

    def rollback(self, serials: list[str], *, dry_run: bool = False) -> dict[str, Any]:
        if self.gateway:
            return self._gateway_rollback(serials, dry_run=dry_run)
        return self._legacy_rollback(serials, dry_run=dry_run)

    def _gateway_rollback(self, serials: list[str], *, dry_run: bool) -> dict[str, Any]:
        with self._lock:
            devices = self._select_devices(serials)
            results: list[dict[str, Any]] = []
            for device in devices:
                result: dict[str, Any] = {"serial": device.serial, "dry_run": dry_run}
                try:
                    target = self.state.gateway_rollback_target(device.serial)
                    if target is None:
                        raise ApiRequestError("Thiết bị chưa có upstream history để rollback.")
                    mapping = self.gateway.mapping(device.serial)
                    result["target_upstream"] = target.redacted
                    result["local_endpoint"] = mapping.endpoint if mapping else None
                    if dry_run:
                        result["ok"] = True
                    else:
                        before = self.client.get_global_proxy(device.serial)
                        mapping = self.gateway.rollback_upstream(device.serial)
                        android_after = self._ensure_android_endpoint(
                            device.serial,
                            mapping.endpoint,
                            before=before,
                        )
                        result["local_endpoint"] = mapping.endpoint
                        result["android_before"] = before
                        result["android_after"] = android_after
                        result["ok"] = True
                except Exception as exc:
                    result["ok"] = False
                    result["error"] = _safe_error(exc)
                results.append(result)
            return {
                "mode": "local_gateway",
                "dry_run": dry_run,
                "results": results,
                "ok": all(result["ok"] for result in results),
            }

    def _ensure_android_endpoint(
        self,
        serial: str,
        endpoint: str,
        *,
        before: str | None = None,
    ) -> str:
        actual_before = before if before is not None else self.client.get_global_proxy(serial)
        if actual_before == endpoint:
            return endpoint
        actual = self.client.set_global_proxy(serial, endpoint)
        if actual != endpoint:
            raise AdbError(
                f"{serial}: verify gateway endpoint thất bại "
                f"({actual!r} != {endpoint!r})"
            )
        self.state.record_change(
            serial,
            before=actual_before,
            after=actual,
            label=f"gateway:{endpoint}",
        )
        self.state.save()
        return actual

    def _next_available_pool_proxy(self, serial: str, *, advance: bool) -> Proxy:
        used = {
            str(value.get("upstream"))
            for device_serial, value in self.state.gateway_mappings().items()
            if device_serial != serial and value.get("upstream")
        }
        current = self.state.gateway_mapping(serial)
        if current and current.get("upstream"):
            used.add(str(current["upstream"]))
        proxy = self.state.next_proxy_from_pool(
            avoid_raw=used,
            mark_used_by=serial,
            advance=advance,
        )
        if proxy is None:
            raise ApiRequestError("Proxy pool đang trống. Hãy import list proxy trước.")
        return proxy

    def _legacy_apply(
        self,
        proxy: Proxy,
        serials: list[str],
        *,
        allow_auth_unsupported: bool,
        dry_run: bool,
    ) -> dict[str, Any]:
        if not allow_auth_unsupported and not dry_run:
            raise ApiRequestError(
                "Local Proxy Gateway chưa được khởi tạo và Android không nhận credential."
            )
        with self._lock:
            devices = self._select_devices(serials)
            results: list[dict[str, Any]] = []
            for device in devices:
                result = {
                    "serial": device.serial,
                    "endpoint": proxy.endpoint,
                    "dry_run": dry_run,
                }
                try:
                    before = self.client.get_global_proxy(device.serial)
                    if dry_run:
                        result.update({"before": before, "after": proxy.endpoint, "ok": True})
                    else:
                        actual = self.client.set_global_proxy(device.serial, proxy.endpoint)
                        if actual != proxy.endpoint:
                            raise AdbError(f"Verify thất bại: nhận {actual!r}, cần {proxy.endpoint!r}")
                        self.state.record_change(
                            device.serial,
                            before=before,
                            after=actual,
                            label=proxy.redacted,
                        )
                        result.update({"before": before, "after": actual, "ok": True})
                except Exception as exc:
                    result.update({"ok": False, "error": _safe_error(exc)})
                results.append(result)
            if not dry_run and any(result["ok"] for result in results):
                self.state.save()
            return {
                "mode": "legacy_endpoint",
                "results": results,
                "ok": all(result["ok"] for result in results),
            }

    def _legacy_rollback(self, serials: list[str], *, dry_run: bool) -> dict[str, Any]:
        with self._lock:
            devices = self._select_devices(serials)
            results: list[dict[str, Any]] = []
            for device in devices:
                result = {"serial": device.serial, "dry_run": dry_run}
                try:
                    target = self.state.rollback_target(device.serial)
                    if target is None:
                        raise ApiRequestError("Thiết bị chưa có lịch sử để rollback.")
                    before = self.client.get_global_proxy(device.serial)
                    if dry_run:
                        result.update({"before": before, "after": target or None, "ok": True})
                    else:
                        actual = (
                            self.client.clear_global_proxy(device.serial)
                            if target == ""
                            else self.client.set_global_proxy(device.serial, target)
                        )
                        expected = None if target == "" else target
                        if actual != expected:
                            raise AdbError(f"Rollback verify thất bại: {actual!r} != {expected!r}")
                        self.state.record_change(
                            device.serial,
                            before=before,
                            after=actual,
                            label="rollback",
                        )
                        result.update({"before": before, "after": actual, "ok": True})
                except Exception as exc:
                    result.update({"ok": False, "error": _safe_error(exc)})
                results.append(result)
            if not dry_run and any(result["ok"] for result in results):
                self.state.save()
            return {
                "mode": "legacy_endpoint",
                "results": results,
                "ok": all(result["ok"] for result in results),
            }

    def _select_devices(self, serials: list[str]) -> list[Device]:
        devices = [device for device in self.client.list_devices() if device.usable]
        by_serial = {device.serial: device for device in devices}
        missing = [serial for serial in serials if serial not in by_serial]
        if missing:
            raise ApiRequestError(
                "Không tìm thấy thiết bị ở trạng thái device: " + ", ".join(missing)
            )
        return [by_serial[serial] for serial in serials]


def _device_to_dict(device: Device) -> dict[str, Any]:
    return {
        "serial": device.serial,
        "state": device.state,
        "usable": device.usable,
        "details": device.details,
    }


class ProxyApiHandler(BaseHTTPRequestHandler):
    server: "ProxyApiServer"
    protocol_version = "HTTP/1.1"
    server_version = "XiaoweiProxyManager/0.2"

    def do_OPTIONS(self) -> None:
        self._send_json(HTTPStatus.NO_CONTENT, None)

    def do_GET(self) -> None:
        try:
            path, query = self._path_and_query()
            if path in {"/health", f"{API_PREFIX}/health"}:
                self._send_json(
                    HTTPStatus.OK,
                    {"ok": True, "data": self.server.service.health()},
                )
                return
            if path == f"{API_PREFIX}/gateway":
                self._send_json(
                    HTTPStatus.OK,
                    {"ok": True, "data": self.server.service.gateway_status()},
                )
                return
            if path == f"{API_PREFIX}/proxy/pool":
                self._send_json(
                    HTTPStatus.OK,
                    {"ok": True, "data": self.server.service.proxy_pool()},
                )
                return
            if path == f"{API_PREFIX}/gateway/reconcile":
                data = self.server.service.reconcile_gateway()
                self._send_json(HTTPStatus.OK, {"ok": True, "data": data})
                return
            if path == f"{API_PREFIX}/devices":
                include_model = _query_bool(query, "include_model", default=False)
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "ok": True,
                        "data": self.server.service.list_devices(include_model=include_model),
                    },
                )
                return
            if path == f"{API_PREFIX}/status":
                serials = _query_serials(query)
                if not serials:
                    serials = [
                        row["serial"]
                        for row in self.server.service.list_devices()
                        if row["usable"]
                    ]
                self._send_json(
                    HTTPStatus.OK,
                    {"ok": True, "data": self.server.service.status(serials)},
                )
                return

            device_id, action = _device_action_path(path)
            if device_id is not None:
                if action in {"gateway", "upstream"}:
                    data = self.server.service.gateway_status(device_id)
                    if not data:
                        raise ApiRequestError("Thiết bị chưa có Local Proxy Gateway mapping.")
                    self._send_json(HTTPStatus.OK, {"ok": True, "data": data[0]})
                    return
                if action == "proxy":
                    data = self.server.service.status([device_id])
                    self._send_json(HTTPStatus.OK, {"ok": True, "data": data[0]})
                    return
            raise ApiRequestError("Endpoint không tồn tại.")
        except Exception as exc:
            self._send_exception(exc)

    def do_POST(self) -> None:
        try:
            path, _query = self._path_and_query()
            payload = self._read_json_body()

            if path in {f"{API_PREFIX}/proxy/apply", f"{API_PREFIX}/gateway"}:
                data = self.server.service.apply(
                    _proxy_from_payload(payload),
                    self._resolve_target_serials(payload),
                    allow_auth_unsupported=_payload_bool(
                        payload,
                        "allow_auth_unsupported",
                        default=False,
                    ),
                    dry_run=_payload_bool(payload, "dry_run", default=False),
                )
                self._send_operation(data)
                return
            if path == f"{API_PREFIX}/proxy/upstream":
                data = self.server.service.update_upstream(
                    _proxy_from_payload(payload),
                    self._resolve_target_serials(payload),
                    dry_run=_payload_bool(payload, "dry_run", default=False),
                )
                self._send_operation(data)
                return
            if path == f"{API_PREFIX}/proxy/rotate":
                data = self.server.service.rotate_from_pool(
                    self._resolve_target_serials(payload),
                    dry_run=_payload_bool(payload, "dry_run", default=False),
                )
                self._send_operation(data)
                return
            if path == f"{API_PREFIX}/proxy/pool":
                data = self.server.service.import_proxy_pool(
                    _proxies_from_payload(payload),
                    append=_payload_bool(payload, "append", default=False),
                    source=str(payload.get("source") or payload.get("path") or "api"),
                )
                self._send_json(HTTPStatus.OK, {"ok": True, "data": data})
                return
            if path == f"{API_PREFIX}/proxy/clear":
                data = self.server.service.clear(
                    self._resolve_target_serials(payload),
                    dry_run=_payload_bool(payload, "dry_run", default=False),
                )
                self._send_operation(data)
                return
            if path == f"{API_PREFIX}/proxy/rollback":
                data = self.server.service.rollback(
                    self._resolve_target_serials(payload),
                    dry_run=_payload_bool(payload, "dry_run", default=False),
                )
                self._send_operation(data)
                return

            device_id, action = _device_action_path(path)
            if device_id is not None:
                if action in {"gateway", "proxy"}:
                    data = self.server.service.apply(
                        _proxy_from_payload(payload),
                        [device_id],
                        allow_auth_unsupported=_payload_bool(
                            payload,
                            "allow_auth_unsupported",
                            default=False,
                        ),
                        dry_run=_payload_bool(payload, "dry_run", default=False),
                    )
                elif action == "upstream":
                    data = self.server.service.update_upstream(
                        _proxy_from_payload(payload),
                        [device_id],
                        dry_run=_payload_bool(payload, "dry_run", default=False),
                    )
                elif action == "rollback":
                    data = self.server.service.rollback(
                        [device_id],
                        dry_run=_payload_bool(payload, "dry_run", default=False),
                    )
                elif action == "rotate":
                    data = self.server.service.rotate_from_pool(
                        [device_id],
                        dry_run=_payload_bool(payload, "dry_run", default=False),
                    )
                else:
                    raise ApiRequestError("Endpoint không tồn tại.")
                self._send_operation(data)
                return
            raise ApiRequestError("Endpoint không tồn tại.")
        except Exception as exc:
            self._send_exception(exc)

    def do_DELETE(self) -> None:
        try:
            path, _query = self._path_and_query()
            device_id, action = _device_action_path(path)
            if device_id is None or action not in {"proxy", "gateway"}:
                raise ApiRequestError(
                    "Xóa proxy cần dùng DELETE /api/v1/devices/{serial}/proxy."
                )
            data = self.server.service.clear([device_id], dry_run=False)
            self._send_operation(data)
        except Exception as exc:
            self._send_exception(exc)

    def _path_and_query(self) -> tuple[str, dict[str, list[str]]]:
        parsed = urlsplit(self.path)
        return parsed.path.rstrip("/") or "/", parse_qs(parsed.query, keep_blank_values=True)

    def _read_json_body(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length", "0")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ApiRequestError("Content-Length không hợp lệ.") from exc
        if length > 1_048_576:
            raise ApiRequestError("Request body vượt quá 1 MB.")
        if length == 0:
            return {}
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiRequestError("Body phải là JSON hợp lệ.") from exc
        if not isinstance(payload, dict):
            raise ApiRequestError("Body JSON phải là object.")
        return payload

    def _resolve_target_serials(self, payload: dict[str, Any]) -> list[str]:
        if _payload_bool(payload, "all", default=False):
            if "serial" in payload or "serials" in payload:
                raise ApiRequestError("Không dùng đồng thời all=true và serial/serials.")
            serials = [
                row["serial"]
                for row in self.server.service.list_devices()
                if row["usable"]
            ]
            if not serials:
                raise ApiRequestError("Không có thiết bị nào ở trạng thái device.")
            return serials
        return _target_serials(payload)

    def _send_operation(self, data: dict[str, Any]) -> None:
        self._send_json(HTTPStatus.OK, {"ok": data["ok"], "data": data})

    def _send_exception(self, exc: Exception) -> None:
        if isinstance(exc, ProxyParseError):
            status = HTTPStatus.UNPROCESSABLE_ENTITY
            code = "invalid_proxy"
        elif isinstance(exc, ApiRequestError):
            status = HTTPStatus.BAD_REQUEST
            code = "invalid_request"
        elif isinstance(exc, (AdbError, XiaoweiError, GatewayError)):
            status = HTTPStatus.BAD_GATEWAY
            code = "backend_error"
        else:
            status = HTTPStatus.INTERNAL_SERVER_ERROR
            code = "internal_error"
        self._send_json(
            status,
            {"ok": False, "error": {"code": code, "message": _safe_error(exc)}},
        )

    def _send_json(self, status: HTTPStatus, payload: Any) -> None:
        encoded = (
            b""
            if payload is None
            else json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.end_headers()
        if encoded:
            self.wfile.write(encoded)

    def log_message(self, format: str, *args: Any) -> None:
        return


class ProxyApiServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        service: ProxyManagerService,
    ):
        self.service = service
        super().__init__(server_address, ProxyApiHandler)


def serve_api(
    service: ProxyManagerService,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    ready: Callable[[ProxyApiServer], None] | None = None,
) -> int:
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("API chỉ được bind ở localhost (127.0.0.1 hoặc localhost).")
    server = ProxyApiServer((host, port), service)
    try:
        service.start()
        if ready:
            ready(server)
        print(f"API đang chạy tại http://{host}:{server.server_address[1]}")
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        service.close()
    return 0


def _proxy_from_payload(payload: dict[str, Any]) -> Proxy:
    value = payload.get("proxy", payload.get("upstream"))
    if value is None:
        fields = ("host", "port", "username", "password")
        if not all(field in payload for field in fields):
            raise ApiRequestError(
                "Cần proxy='host:port:username:password' "
                "hoặc đủ host, port, username, password."
            )
        value = ":".join(str(payload[field]) for field in fields)
    if not isinstance(value, str):
        raise ApiRequestError("Trường proxy/upstream phải là string.")
    return parse_proxy(value)


def _proxies_from_payload(payload: dict[str, Any]) -> list[Proxy]:
    if "path" in payload:
        path = payload["path"]
        if not isinstance(path, str) or not path.strip():
            raise ApiRequestError("Trường path phải là đường dẫn file.")
        return load_proxy_file(path)
    if "text" in payload:
        text = payload["text"]
        if not isinstance(text, str):
            raise ApiRequestError("Trường text phải là string.")
        return parse_proxy_text(text)
    raw = payload.get("proxies", payload.get("proxy"))
    if raw is None:
        raise ApiRequestError("Cần truyền proxies, text hoặc path.")
    if isinstance(raw, str):
        return parse_proxy_text(raw)
    if isinstance(raw, list):
        proxies: list[Proxy] = []
        for index, value in enumerate(raw, start=1):
            if not isinstance(value, str):
                raise ApiRequestError(f"proxies[{index}] phải là string.")
            try:
                proxies.append(parse_proxy(value))
            except ProxyParseError as exc:
                raise ProxyParseError(f"proxies[{index}]: {exc}") from exc
        return proxies
    raise ApiRequestError("proxies phải là string hoặc array string.")


def _target_serials(payload: dict[str, Any]) -> list[str]:
    raw = payload.get("serials", payload.get("serial"))
    if raw is None:
        raise ApiRequestError("Cần truyền serial hoặc serials, hoặc all=true.")
    if isinstance(raw, str):
        serials = [part.strip() for part in raw.split(",") if part.strip()]
    elif isinstance(raw, list):
        serials = [str(part).strip() for part in raw if str(part).strip()]
    else:
        raise ApiRequestError("serial/serials phải là string hoặc array.")
    if not serials:
        raise ApiRequestError("Danh sách serial không được trống.")
    return serials


def _query_serials(query: dict[str, list[str]]) -> list[str]:
    values = query.get("serial", []) + query.get("serials", [])
    return [
        part.strip()
        for value in values
        for part in value.split(",")
        if part.strip()
    ]


def _query_bool(query: dict[str, list[str]], key: str, *, default: bool) -> bool:
    values = query.get(key)
    if not values:
        return default
    return _parse_bool(values[-1], key)


def _payload_bool(payload: dict[str, Any], key: str, *, default: bool) -> bool:
    value = payload.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return _parse_bool(value, key)
    raise ApiRequestError(f"{key} phải là boolean.")


def _parse_bool(value: str, key: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ApiRequestError(f"{key} phải là boolean.")


def _device_action_path(path: str) -> tuple[str | None, str | None]:
    prefix = f"{API_PREFIX}/devices/"
    if not path.startswith(prefix):
        return None, None
    parts = path[len(prefix) :].split("/")
    if len(parts) == 2 and parts[1] in {"proxy", "gateway", "upstream"}:
        return unquote(parts[0]), parts[1]
    if len(parts) == 3 and parts[1] in {"proxy", "gateway"} and parts[2] == "rollback":
        return unquote(parts[0]), "rollback"
    if len(parts) == 3 and parts[1] in {"proxy", "gateway"} and parts[2] == "rotate":
        return unquote(parts[0]), "rotate"
    return None, None


def _safe_error(exc: Exception) -> str:
    return str(exc) or exc.__class__.__name__
