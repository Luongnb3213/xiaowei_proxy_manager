from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .proxy import Proxy, ProxyParseError


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class StateStore:
    """Small JSON state store used for one-step or multi-step rollback."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: dict[str, Any] = {
            "version": 2,
            "devices": {},
            "gateway": {"devices": {}},
            "proxy_pool": {"items": [], "cursor": 0},
        }
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Không đọc được state file {self.path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"State file {self.path} không phải JSON object.")
        self.data = payload
        self.data.setdefault("version", 2)
        self.data.setdefault("devices", {})
        self.data.setdefault("gateway", {})
        self.data["gateway"].setdefault("devices", {})
        self.data.setdefault("proxy_pool", {})
        self.data["proxy_pool"].setdefault("items", [])
        self.data["proxy_pool"].setdefault("cursor", 0)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(self.data, ensure_ascii=False, indent=2) + "\n"
        fd, temp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def record_change(
        self,
        serial: str,
        *,
        before: str | None,
        after: str | None,
        label: str = "",
    ) -> None:
        device = self.data.setdefault("devices", {}).setdefault(
            serial,
            {"current": None, "history": []},
        )
        device.setdefault("history", [])
        device["current"] = after
        device["history"].append(
            {
                "at": utc_now(),
                "before": before,
                "after": after,
                "label": label,
            }
        )
        # Keep the file bounded while retaining enough operational history.
        device["history"] = device["history"][-50:]

    def set_gateway_mapping(
        self,
        serial: str,
        *,
        local_port: int,
        bind_host: str,
        advertised_host: str,
        upstream: Proxy | None,
        record_history: bool = True,
    ) -> None:
        gateway_devices = self.data.setdefault("gateway", {}).setdefault("devices", {})
        current = gateway_devices.get(serial)
        upstream_raw = upstream.raw if upstream else None
        if not isinstance(current, dict):
            current = {
                "local_port": local_port,
                "bind_host": bind_host,
                "advertised_host": advertised_host,
                "upstream": upstream_raw,
                "history": [],
            }
            gateway_devices[serial] = current
            return

        previous_raw = current.get("upstream")
        if record_history and previous_raw != upstream_raw:
            history = current.setdefault("history", [])
            history.append(
                {
                    "at": utc_now(),
                    "before": previous_raw,
                    "after": upstream_raw,
                }
            )
            current["history"] = history[-50:]
        current["local_port"] = local_port
        current["bind_host"] = bind_host
        current["advertised_host"] = advertised_host
        current["upstream"] = upstream_raw

    def gateway_mapping(self, serial: str) -> dict[str, Any] | None:
        value = self.data.get("gateway", {}).get("devices", {}).get(serial)
        return value if isinstance(value, dict) else None

    def gateway_mappings(self) -> dict[str, dict[str, Any]]:
        values = self.data.get("gateway", {}).get("devices", {})
        if not isinstance(values, dict):
            return {}
        return {
            str(serial): value
            for serial, value in values.items()
            if isinstance(value, dict)
        }

    def gateway_upstream(self, serial: str) -> Proxy | None:
        mapping = self.gateway_mapping(serial)
        if not mapping:
            return None
        raw = mapping.get("upstream")
        if not raw:
            return None
        if not isinstance(raw, str):
            raise RuntimeError(f"Gateway state của {serial} có upstream không hợp lệ.")
        try:
            return Proxy.from_raw(raw)
        except ProxyParseError as exc:
            raise RuntimeError(f"Gateway state của {serial} có upstream không hợp lệ.") from exc

    def gateway_rollback_target(self, serial: str) -> Proxy | None:
        mapping = self.gateway_mapping(serial)
        if not mapping:
            return None
        history = mapping.get("history", [])
        if not isinstance(history, list) or not history:
            return None
        previous = history[-1].get("before")
        if not previous:
            return None
        if not isinstance(previous, str):
            raise RuntimeError(f"Gateway history của {serial} có upstream không hợp lệ.")
        try:
            return Proxy.from_raw(previous)
        except ProxyParseError as exc:
            raise RuntimeError(f"Gateway history của {serial} có upstream không hợp lệ.") from exc

    def remove_gateway_mapping(self, serial: str) -> None:
        devices = self.data.setdefault("gateway", {}).setdefault("devices", {})
        devices.pop(serial, None)

    def set_proxy_pool(self, proxies: list[Proxy], *, append: bool = False, source: str = "") -> dict[str, Any]:
        pool = self.data.setdefault("proxy_pool", {})
        existing = pool.get("items", []) if append else []
        items: list[dict[str, Any]] = [
            item for item in existing
            if isinstance(item, dict) and isinstance(item.get("raw"), str)
        ]
        seen = {str(item["raw"]) for item in items}
        added = 0
        for proxy in proxies:
            if proxy.raw in seen:
                continue
            items.append({"raw": proxy.raw, "source": source, "last_used_by": None, "last_used_at": None})
            seen.add(proxy.raw)
            added += 1
        pool["items"] = items
        if not append:
            pool["cursor"] = 0
        elif items:
            pool["cursor"] = int(pool.get("cursor", 0) or 0) % len(items)
        else:
            pool["cursor"] = 0
        return {"count": len(items), "added": added}

    def proxy_pool_summary(self) -> dict[str, Any]:
        pool = self.data.setdefault("proxy_pool", {})
        items = [
            item for item in pool.get("items", [])
            if isinstance(item, dict) and isinstance(item.get("raw"), str)
        ]
        return {
            "count": len(items),
            "cursor": int(pool.get("cursor", 0) or 0),
            "items": [
                {
                    "proxy": Proxy.from_raw(str(item["raw"])).redacted,
                    "source": item.get("source") or "",
                    "last_used_by": item.get("last_used_by"),
                    "last_used_at": item.get("last_used_at"),
                }
                for item in items
            ],
        }

    def next_proxy_from_pool(
        self,
        *,
        avoid_raw: set[str] | None = None,
        mark_used_by: str | None = None,
        advance: bool = True,
    ) -> Proxy | None:
        pool = self.data.setdefault("proxy_pool", {})
        items = [
            item for item in pool.get("items", [])
            if isinstance(item, dict) and isinstance(item.get("raw"), str)
        ]
        if not items:
            return None
        avoid = avoid_raw or set()
        cursor = int(pool.get("cursor", 0) or 0) % len(items)
        selected_index = cursor
        for offset in range(len(items)):
            index = (cursor + offset) % len(items)
            raw = str(items[index]["raw"])
            if raw not in avoid:
                selected_index = index
                break
        selected = items[selected_index]
        proxy = Proxy.from_raw(str(selected["raw"]))
        if advance:
            selected["last_used_by"] = mark_used_by
            selected["last_used_at"] = utc_now()
            pool["items"] = items
            pool["cursor"] = (selected_index + 1) % len(items)
        return proxy

    def current(self, serial: str) -> str | None:
        value = self.data.get("devices", {}).get(serial, {}).get("current")
        return value if isinstance(value, str) and value else None

    def rollback_target(self, serial: str) -> str | None:
        """Return the previous endpoint.

        ``None`` means there is no history. An empty string means the recorded
        previous state was explicitly "no proxy", which is distinct from no
        history and must trigger a clear operation.
        """
        history = self.data.get("devices", {}).get(serial, {}).get("history", [])
        if not history:
            return None
        previous = history[-1].get("before")
        if previous is None:
            return ""
        return previous if isinstance(previous, str) else ""
