from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class StateStore:
    """Small JSON state store used for one-step or multi-step rollback."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data: dict[str, Any] = {"version": 1, "devices": {}}
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
        self.data.setdefault("version", 1)
        self.data.setdefault("devices", {})

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
