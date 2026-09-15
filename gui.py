from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from xiaowei_proxy_manager.adb import AdbClient, Device
    from xiaowei_proxy_manager.api import ProxyApiServer, ProxyManagerService
    from xiaowei_proxy_manager.config import (
        DEFAULT_CONFIG,
        api_kwargs,
        gateway_kwargs,
        load_config,
    )
    from xiaowei_proxy_manager.gateway import LocalProxyGateway
    from xiaowei_proxy_manager.proxy import ProxyParseError, parse_proxy
    from xiaowei_proxy_manager.state import StateStore
    from xiaowei_proxy_manager.xiaowei import XiaoweiClient
else:
    from .adb import AdbClient, Device
    from .api import ProxyApiServer, ProxyManagerService
    from .config import DEFAULT_CONFIG, api_kwargs, gateway_kwargs, load_config
    from .gateway import LocalProxyGateway
    from .proxy import ProxyParseError, parse_proxy
    from .state import StateStore
    from .xiaowei import XiaoweiClient


DEFAULT_STATE = Path(__file__).resolve().parent / "data" / "state.json"


class ProxyManagerApp:
    def __init__(
        self,
        root: tk.Tk,
        *,
        adb_path: str | None = None,
        backend: str = "adb",
        xiaowei_url: str = "ws://127.0.0.1:22222/",
        state_path: str = str(DEFAULT_STATE),
        config_path: str = str(DEFAULT_CONFIG),
    ):
        self.root = root
        self.root.title("Xiaowei Proxy Manager")
        self.root.geometry("1120x680")
        self.root.minsize(900, 520)
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.devices: dict[str, Device] = {}
        self.state = StateStore(state_path)
        self.config_path = config_path
        self.config = load_config(config_path)
        self.gateway = LocalProxyGateway(self.state, **gateway_kwargs(self.config))

        self.backend_var = tk.StringVar(value=backend)
        self.adb_var = tk.StringVar(value=adb_path or "adb")
        self.xiaowei_var = tk.StringVar(value=xiaowei_url)
        self.host_var = tk.StringVar()
        self.port_var = tk.StringVar()
        self.user_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.dry_run_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Sẵn sàng")
        self.pool_var = tk.StringVar(value=self._pool_label())
        self.api_server: ProxyApiServer | None = None
        self.api_thread: threading.Thread | None = None
        self.api_url: str | None = None
        self.api_error: str | None = None

        self.gateway.start()
        self._start_api_server()
        self.gateway_var = tk.StringVar(value=self._gateway_label())

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
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
        ttk.Label(top, textvariable=self.gateway_var).grid(row=1, column=0, columnspan=7, sticky="w", pady=(8, 0))
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
        widths = {"serial": 145, "state": 95, "model": 230, "proxy": 330, "public_ip": 150}
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(column, width=widths[column], anchor="w")
        self.tree.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        scroll.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scroll.set)

        form = ttk.LabelFrame(self.root, text="Upstream proxy", padding=10)
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
        ttk.Checkbutton(form, text="Dry run", variable=self.dry_run_var).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )
        ttk.Label(form, textvariable=self.pool_var).grid(row=1, column=2, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Button(form, text="Import list", command=self.import_proxy_pool).grid(row=1, column=5, padx=5, pady=(8, 0))
        ttk.Button(form, text="Rotate proxy", command=self.rotate_proxy).grid(row=1, column=6, padx=5, pady=(8, 0))
        ttk.Button(form, text="Apply proxy", command=self.apply_proxy).grid(row=1, column=7, padx=5, pady=(8, 0))
        ttk.Button(form, text="Rollback", command=self.rollback).grid(row=2, column=6, padx=5, pady=(8, 0))
        ttk.Button(form, text="Clear proxy", command=self.clear_proxy).grid(row=2, column=7, padx=5, pady=(8, 0))

        bottom = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        bottom.pack(fill="both")
        ttk.Label(bottom, textvariable=self.status_var).pack(anchor="w")
        self.log = tk.Text(bottom, height=7, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True, pady=(4, 0))

    def _client(self):
        if self.backend_var.get() == "xiaowei":
            return XiaoweiClient(self.xiaowei_var.get().strip())
        return AdbClient(self.adb_var.get().strip() or "adb")

    def _service(self) -> ProxyManagerService:
        return ProxyManagerService(
            self._client(),
            self.state,
            backend=self.backend_var.get(),
            gateway=self.gateway,
        )

    def _gateway_label(self) -> str:
        api_text = f"API: {self.api_url}" if self.api_url else f"API: lỗi mở port ({self.api_error})"
        return (
            f"Gateway: {self.gateway.bind_host} -> {self.gateway.advertised_host}, "
            f"ports {self.gateway.start_port}-{self.gateway.end_port}; {api_text}; "
            f"config {self.config_path}"
        )

    def _pool_label(self) -> str:
        count = self.state.proxy_pool_summary()["count"]
        return f"Proxy pool: {count} proxy"

    def _start_api_server(self) -> None:
        api = api_kwargs(self.config)
        host = api["host"]
        port = api["port"]
        if host not in {"127.0.0.1", "localhost"}:
            self.api_error = "api.host chỉ được là 127.0.0.1 hoặc localhost"
            return
        try:
            service = ProxyManagerService(
                self._client(),
                self.state,
                backend=self.backend_var.get(),
                gateway=self.gateway,
            )
            self.api_server = ProxyApiServer((host, port), service)
            self.api_thread = threading.Thread(
                target=self.api_server.serve_forever,
                name="proxy-manager-api-ui",
                daemon=True,
            )
            self.api_thread.start()
            bound_host, bound_port = self.api_server.server_address
            self.api_url = f"http://{bound_host}:{bound_port}"
        except OSError as exc:
            self.api_error = str(exc)

    def close(self) -> None:
        if self.api_server is not None:
            self.api_server.shutdown()
            self.api_server.server_close()
        if self.api_thread is not None:
            self.api_thread.join(timeout=2)
        self.gateway.close()
        self.root.destroy()

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
                mapping = self.gateway.mapping(device.serial)
                rows.append((device, model, proxy, public_ip, mapping.to_dict() if mapping else None))
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
            service = self._service()
            if self.dry_run_var.get():
                result = service.apply(proxy, [serial], dry_run=True)
                row = result["results"][0]
                if not row.get("ok"):
                    raise RuntimeError(row.get("error", "Apply thất bại."))
                endpoint = row.get("local_endpoint") or "(gateway port mới)"
                return f"{serial}: DRY-RUN Android -> {endpoint} | upstream {proxy.redacted}"
            result = service.apply(proxy, [serial])
            row = result["results"][0]
            if not row.get("ok"):
                raise RuntimeError(row.get("error", "Apply thất bại."))
            return (
                f"{serial}: OK Android {row.get('android_before') or '(none)'} "
                f"-> {row.get('android_after')}; upstream {proxy.redacted}"
            )

        self._run_async("Đang áp dụng proxy...", operation)

    def import_proxy_pool(self) -> None:
        path = filedialog.askopenfilename(
            title="Import proxy list",
            filetypes=(
                ("Proxy list", "*.csv *.txt *.xlsx"),
                ("CSV", "*.csv"),
                ("Excel", "*.xlsx"),
                ("Text", "*.txt"),
                ("All files", "*.*"),
            ),
        )
        if not path:
            return

        def operation():
            result = self._service().import_proxy_pool_from_file(path, append=False)
            return (
                f"Đã import {result['count']} proxy từ {Path(path).name} "
                f"({result['added']} proxy mới)"
            )

        self._run_async("Đang import proxy list...", operation)

    def rotate_proxy(self) -> None:
        try:
            serial = self._selected_serial()
        except ValueError as exc:
            messagebox.showerror("Chưa chọn thiết bị", str(exc))
            return

        def operation():
            result = self._service().rotate_from_pool([serial], dry_run=self.dry_run_var.get())
            row = result["results"][0]
            if not row.get("ok"):
                raise RuntimeError(row.get("error", "Rotate thất bại."))
            prefix = "DRY-RUN " if self.dry_run_var.get() else "OK "
            endpoint = row.get("local_endpoint") or "(gateway port mới)"
            android_after = row.get("android_after") or endpoint
            return (
                f"{serial}: {prefix}rotate -> {row.get('upstream')} | "
                f"Android {row.get('android_before') or '(none)'} -> {android_after}"
            )

        self._run_async("Đang xoay proxy...", operation)

    def clear_proxy(self) -> None:
        try:
            serial = self._selected_serial()
        except ValueError as exc:
            messagebox.showerror("Chưa chọn thiết bị", str(exc))
            return

        def operation():
            result = self._service().clear([serial], dry_run=self.dry_run_var.get())
            row = result["results"][0]
            if not row.get("ok"):
                raise RuntimeError(row.get("error", "Clear thất bại."))
            prefix = "DRY-RUN " if self.dry_run_var.get() else "OK "
            return f"{serial}: {prefix}{row.get('before') or '(none)'} -> (none)"

        self._run_async("Đang xóa proxy...", operation)

    def rollback(self) -> None:
        try:
            serial = self._selected_serial()
        except ValueError as exc:
            messagebox.showerror("Không thể rollback", str(exc))
            return

        def operation():
            result = self._service().rollback([serial], dry_run=self.dry_run_var.get())
            row = result["results"][0]
            if not row.get("ok"):
                raise RuntimeError(row.get("error", "Rollback thất bại."))
            prefix = "DRY-RUN " if self.dry_run_var.get() else "OK "
            target = row.get("target_upstream") or row.get("after") or "(none)"
            endpoint = row.get("local_endpoint")
            suffix = f" qua {endpoint}" if endpoint else ""
            return f"{serial}: {prefix}rollback -> {target}{suffix}"

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
                        self.pool_var.set(self._pool_label())
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
        for device, model, proxy, public_ip, gateway in rows:
            self.devices[device.serial] = device
            model_text = " ".join(filter(None, (model.get("manufacturer"), model.get("model")))) or device.details
            proxy_text = self._proxy_text(proxy, gateway)
            self.tree.insert("", "end", values=(device.serial, device.state, model_text, proxy_text, public_ip))

    def _log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _proxy_text(self, proxy: str | None, gateway: dict[str, object] | None) -> str:
        if not gateway:
            return proxy or "(none)"
        endpoint = str(gateway.get("endpoint") or "")
        upstream = str(gateway.get("upstream") or "(chưa có upstream)")
        running = "running" if gateway.get("running") else "stopped"
        if proxy == endpoint:
            return f"{endpoint} -> {upstream} [{running}]"
        return f"{proxy or '(none)'} | gateway {endpoint} -> {upstream} [{running}]"


def launch_gui(
    *,
    adb_path: str | None = None,
    backend: str = "adb",
    xiaowei_url: str = "ws://127.0.0.1:22222/",
    state_path: str = str(DEFAULT_STATE),
    config_path: str = str(DEFAULT_CONFIG),
) -> int:
    root = tk.Tk()
    app = ProxyManagerApp(
        root,
        adb_path=adb_path,
        backend=backend,
        xiaowei_url=xiaowei_url,
        state_path=state_path,
        config_path=config_path,
    )
    root.after(150, app.refresh_devices)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(launch_gui())
