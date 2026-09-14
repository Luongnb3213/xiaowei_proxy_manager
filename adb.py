from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Sequence


class AdbError(RuntimeError):
    """An ADB command failed or returned an unusable result."""


@dataclass(frozen=True)
class Device:
    serial: str
    state: str
    details: str = ""

    @property
    def usable(self) -> bool:
        return self.state == "device"


class AdbClient:
    def __init__(
        self,
        adb_path: str | None = None,
        *,
        timeout: float = 20,
        runner=None,
    ):
        self.adb_path = adb_path or os.environ.get("ADB_PATH", "adb")
        self.timeout = timeout
        self._runner = runner or subprocess.run

    def _run(self, args: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        command = [self.adb_path, *[str(arg) for arg in args]]
        try:
            result = self._runner(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise AdbError(
                f"Không tìm thấy ADB tại {self.adb_path!r}. "
                "Cài Android platform-tools hoặc truyền --adb."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise AdbError(f"ADB timeout sau {self.timeout:g}s: {' '.join(command)}") from exc

        if check and result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise AdbError(
                f"ADB lỗi ({result.returncode}): {' '.join(command)}"
                + (f" | {detail}" if detail else "")
            )
        return result

    def list_devices(self) -> list[Device]:
        result = self._run(["devices", "-l"])
        devices: list[Device] = []
        for raw_line in result.stdout.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("List of devices attached"):
                continue
            fields = line.split()
            if len(fields) < 2:
                continue
            devices.append(Device(serial=fields[0], state=fields[1], details=" ".join(fields[2:])))
        return devices

    def shell(self, serial: str, *command: str, check: bool = True) -> str:
        if not serial.strip():
            raise ValueError("Serial thiết bị không được trống.")
        result = self._run(["-s", serial, "shell", *command], check=check)
        return (result.stdout or "").strip()

    def get_global_proxy(self, serial: str) -> str | None:
        """Read Android Settings.Global http_proxy.

        Android commonly returns ``:0``, ``null`` or ``none`` when no proxy is
        configured. All of those are normalized to ``None``.
        """
        value = self.shell(serial, "settings", "get", "global", "http_proxy").strip()
        if not value or value.lower() in {"null", "none", ":0", "0.0.0.0:0"}:
            return None
        return value

    def set_global_proxy(self, serial: str, endpoint: str) -> str | None:
        if not endpoint.strip():
            raise ValueError("Proxy endpoint không được trống.")
        self.shell(serial, "settings", "put", "global", "http_proxy", endpoint)
        return self.get_global_proxy(serial)

    def clear_global_proxy(self, serial: str) -> str | None:
        # :0 is supported by a wider range of Android builds than `settings
        # delete`, and is the conventional ADB way to clear this setting.
        self.shell(serial, "settings", "put", "global", "http_proxy", ":0")
        return self.get_global_proxy(serial)

    def get_model(self, serial: str) -> dict[str, str]:
        props = {
            "manufacturer": "ro.product.manufacturer",
            "model": "ro.product.model",
            "android": "ro.build.version.release",
            "device": "ro.product.device",
        }
        result: dict[str, str] = {}
        for key, prop in props.items():
            result[key] = self.shell(serial, "getprop", prop, check=False)
        return result

