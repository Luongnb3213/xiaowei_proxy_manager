from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

from .adb import AdbClient, AdbError, Device
from .proxy import ProxyParseError, parse_proxy
from .state import StateStore
from .xiaowei import XiaoweiClient


class ProxyManagerApp:
    def __init__(
        self,
        root: tk.Tk,
        *,
        adb_path: str | None = None,
        backend: str = "adb",
        xiaowei_url: str = "ws://127.0.0.1:22222/",
        state_path: str,
    ):
        self.root = root
        self.root.title("Xiaowei Proxy Manager")
        self.root.geometry("1120x680")
        self.root.minsize(900, 520)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.devices: dict[str, Device] = {}
        self.state = StateStore(state_path)

        self.backend_var = tk.StringVar(value=backend)
        self.adb_var = tk.StringVar(value=adb_path or "adb")
        self.xiaowei_var = tk.StringVar(value=xiaowei_url)
        self.host_var = tk.StringVar()
        self.port_var = tk.StringVar()
        self.user_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.allow_auth_var = tk.BooleanVar(value=False)
        self.dry_run_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Sẵn sàng")

        self._build_ui()
        self.root.after(100, self._poll_events)

    def _build_ui(self) -> None:
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill="x")
        ttk.Label(top, text="Backend").grid(row=0, column=0, sticky="w")
        backend = ttk.Combobox(
            top, textvariable=self.backend_var, values=("adb", "xiaowei"),
            state="readonly", width=12,
        )
        backend.grid(row=0, column=1, padx=(6, 14), sticky="w")
        ttk.Label(top, text="ADB path").grid(row=0, column=2, sticky="w")
        ttk.Entry(top, textvariable=self.adb_var, width=35).grid(row=0, column=3, padx=6, sticky="ew")
        ttk.Label(top, text="Xiaowei URL").grid(row=0, column=4, sticky="w")
        ttk.Entry(top, textvariable=self.xiaowei_var, width=32).grid(row=0, column=5, padx=6, sticky="ew")
        ttk.Button(top, text="Refresh devices", command=self.refresh_devices).grid(row=0, column=6, padx=(8, 0))
        top.columnconfigure(3, weight=1)
        top.columnconfigure(5, weight=1)

        table_frame = ttk.Frame(self.root, padding=(10, 0, 10, 8))
        table_frame.pack(fill="both", expand=True)
        columns = ("serial", "state", "model", "proxy", "public_ip")
        self.tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        headings = {
            "serial": "Serial", "state": "Trạng thái", "model": "Thiết bị",
            "proxy": "Proxy đang dùng", "public_ip": "IP sau proxy",
        }
        widths = {"serial": 145, "state": 95, "model": 250, "proxy": 210, "public_ip": 160}
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(column, width=widths[column], anchor="w")
        self.tree.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        scroll.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scroll.set)

        form = ttk.LabelFrame(self.root, text="Proxy", padding=10)
        form.pack(fill="x", padx=10, pady=(0, 8))
        fields = (
            ("Host", self.host_var), ("Port", self.port_var),
            ("Username", self.user_var), ("Password", self.password_var),
        )
        for index, (label, variable) in enumerate(fields):
            ttk.Label(form, text=label).grid(row=0, column=index * 2, sticky="w")
            entry = ttk.Entry(form, textvariable=variable, width=23, show="*" if label == "Password" else "")
            entry.grid(row=0, column=index * 2 + 1, padx=(5, 14), sticky="ew")
        for column in range(0, 8, 2):
            form.columnconfigure(column + 1, weight=1)
        ttk.Checkbutton(form, text="Cho phép endpoint-only", variable=self.allow_auth_var).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )
        ttk.Checkbutton(form, text="Dry run", variable=self.dry_run_var).grid(
            row=1, column=2, columnspan=2, sticky="w", pady=(8, 0)
        )
        ttk.Button(form, text="Apply proxy", command=self.apply_proxy).grid(row=1, column=5, padx=5, pady=(8, 0))
        ttk.Button(form, text="Rollback", command=self.rollback).grid(row=1, column=6, padx=5, pady=(8, 0))
        ttk.Button(form, text="Clear proxy", command=self.clear_proxy).grid(row=1, column=7, padx=5, pady=(8, 0))

        bottom = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        bottom.pack(fill="both")
        ttk.Label(bottom, textvariable=self.status_var).pack(anchor="w")
        self.log = tk.Text(bottom, height=7, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True, pady=(4, 0))

    def _client(self):
        if self.backend_var.get() == "xiaowei":
            return XiaoweiClient(self.xiaowei_var.get().strip())
        return AdbClient(self.adb_var.get().strip() or "adb")

    def _run_async(self, title: str, operation: Callable[[], object]) -> None:
        self.status_var.set(title)

        def worker() -> None:
            try:
                self.events.put(("result", operation()))
            except Exception as exc:
                self.events.put(("error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def refresh_devices(self) -> None:
        def operation():
            client = self._client()
            rows = []
            for device in client.list_devices():
                model = client.get_model(device.serial) if device.usable else {}
                proxy = client.get_global_proxy(device.serial) if device.usable else None
                public_ip = self._public_ip(client, device.serial) if device.usable else ""
                rows.append((device, model, proxy, public_ip))
            return rows

        self._run_async("Đang đọc thiết bị và IP...", operation)

    def _public_ip(self, client, serial: str) -> str:
        commands = (
            ("curl", "-fsS", "--max-time", "12", "https://api.ipify.org"),
            ("wget", "-qO-", "https://api.ipify.org"),
            ("toybox", "wget", "-qO-", "https://api.ipify.org"),
        )
        for command in commands:
            try:
                value = client.shell(serial, *command, check=False).strip()
            except Exception:
                continue
            if value and len(value) <= 64:
                return value.splitlines()[-1].strip()
        return "(không đọc được)"

    def _selected_serial(self) -> str:
        selected = self.tree.selection()
        if not selected:
            raise ValueError("Hãy chọn một thiết bị.")
        return self.tree.item(selected[0], "values")[0]

    def apply_proxy(self) -> None:
        try:
            serial = self._selected_serial()
            proxy = parse_proxy(":".join((
                self.host_var.get(), self.port_var.get(),
                self.user_var.get(), self.password_var.get(),
            )))
        except (ValueError, ProxyParseError) as exc:
            messagebox.showerror("Proxy không hợp lệ", str(exc))
            return

        def operation():
            client = self._client()
            before = client.get_global_proxy(serial)
            if self.dry_run_var.get():
                return f"{serial}: DRY-RUN {before or '(none)'} -> {proxy.endpoint}"
            if self.backend_var.get() == "adb" and not self.allow_auth_var.get():
                raise AdbError("ADB global proxy chỉ nhận host:port. Bật 'Cho phép endpoint-only' để tiếp tục.")
            actual = client.set_global_proxy(serial, proxy.endpoint)
            if actual != proxy.endpoint:
                raise AdbError(f"Verify thất bại: nhận {actual!r}, cần {proxy.endpoint!r}")
            self.state.record_change(serial, before=before, after=actual, label=proxy.redacted)
            self.state.save()
            return f"{serial}: OK {before or '(none)'} -> {actual}"

        self._run_async("Đang áp dụng proxy...", operation)

    def clear_proxy(self) -> None:
        try:
            serial = self._selected_serial()
        except ValueError as exc:
            messagebox.showerror("Chưa chọn thiết bị", str(exc))
            return

        def operation():
            client = self._client()
            before = client.get_global_proxy(serial)
            if self.dry_run_var.get():
                return f"{serial}: DRY-RUN {before or '(none)'} -> (none)"
            actual = client.clear_global_proxy(serial)
            if actual is not None:
                raise AdbError(f"Không xóa được proxy: {actual}")
            self.state.record_change(serial, before=before, after=None, label="clear")
            self.state.save()
            return f"{serial}: OK {before or '(none)'} -> (none)"

        self._run_async("Đang xóa proxy...", operation)

    def rollback(self) -> None:
        try:
            serial = self._selected_serial()
            target = self.state.rollback_target(serial)
            if target is None:
                raise ValueError("Thiết bị này chưa có lịch sử để rollback.")
        except ValueError as exc:
            messagebox.showerror("Không thể rollback", str(exc))
            return

        def operation():
            client = self._client()
            before = client.get_global_proxy(serial)
            if self.dry_run_var.get():
                return f"{serial}: DRY-RUN {before or '(none)'} -> {target or '(none)'}"
            actual = client.clear_global_proxy(serial) if target == "" else client.set_global_proxy(serial, target)
            expected = None if target == "" else target
            if actual != expected:
                raise AdbError(f"Rollback verify thất bại: {actual!r}")
            self.state.record_change(serial, before=before, after=actual, label="rollback")
            self.state.save()
            return f"{serial}: OK {before or '(none)'} -> {actual or '(none)'}"

        self._run_async("Đang rollback...", operation)

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "result":
                    if isinstance(payload, list):
                        self._render_devices(payload)
                        self.status_var.set(f"Đã đọc {len(payload)} thiết bị")
                    else:
                        self._log(str(payload))
                        self.status_var.set("Hoàn tất")
                        self.refresh_devices()
                else:
                    self._log(f"LỖI: {payload}")
                    self.status_var.set("Có lỗi")
                    messagebox.showerror("Lỗi", str(payload))
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

    def _render_devices(self, rows) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.devices.clear()
        for device, model, proxy, public_ip in rows:
            self.devices[device.serial] = device
            model_text = " ".join(filter(None, (model.get("manufacturer"), model.get("model")))) or device.details
            self.tree.insert("", "end", values=(device.serial, device.state, model_text, proxy or "(none)", public_ip))

    def _log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")


def launch_gui(
    *,
    adb_path: str | None = None,
    backend: str = "adb",
    xiaowei_url: str = "ws://127.0.0.1:22222/",
    state_path: str,
) -> int:
    root = tk.Tk()
    app = ProxyManagerApp(
        root,
        adb_path=adb_path,
        backend=backend,
        xiaowei_url=xiaowei_url,
        state_path=state_path,
    )
    root.after(150, app.refresh_devices)
    root.mainloop()
    return 0
