from __future__ import annotations

import queue
import shutil
import sys
import threading
import tkinter as tk
from ipaddress import ip_address
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable
from urllib.request import ProxyHandler, build_opener

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
    from xiaowei_proxy_manager.backends.adb import AdbClient, Device
    from xiaowei_proxy_manager.api import ProxyApiServer, ProxyManagerService
    from xiaowei_proxy_manager.core.config import (
        DEFAULT_CONFIG,
        api_kwargs,
        gateway_kwargs,
        load_config,
        logging_kwargs,
    )
    from xiaowei_proxy_manager.core.logging_config import (
        get_logger,
        setup_logging,
    )
    from xiaowei_proxy_manager.gateway import LocalProxyGateway
    from xiaowei_proxy_manager.core.proxy import ProxyParseError, parse_proxy
    from xiaowei_proxy_manager.core.state import StateStore
    from xiaowei_proxy_manager.backends.xiaowei import XiaoweiClient
else:
    from ..backends.adb import AdbClient, Device
    from ..api import ProxyApiServer, ProxyManagerService
    from ..core.config import (
        DEFAULT_CONFIG,
        api_kwargs,
        gateway_kwargs,
        load_config,
        logging_kwargs,
    )
    from ..core.logging_config import get_logger, setup_logging
    from ..gateway import LocalProxyGateway
    from ..core.proxy import ProxyParseError, parse_proxy
    from ..core.state import StateStore
    from ..backends.xiaowei import XiaoweiClient


DEFAULT_STATE = Path(__file__).resolve().parent.parent / "data" / "state.json"

LOGGER = get_logger("gui")


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
        self.selected_serial = ""
        self.state = StateStore(state_path)
        self.config_path = config_path
        self.config = load_config(config_path)
        self.gateway = LocalProxyGateway(self.state, **gateway_kwargs(self.config))

        self.backend_var = tk.StringVar(value=backend)
        self.adb_var = tk.StringVar(value=adb_path or _find_adb_path())
        self.xiaowei_var = tk.StringVar(value=xiaowei_url)
        self.host_var = tk.StringVar()
        self.port_var = tk.StringVar()
        self.user_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.dry_run_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Sẵn sàng")
        self._busy_count = 0
        self._busy_title = ""
        self._busy_seconds = 0
        self._busy_timer: str | None = None
        self._action_buttons: list[ttk.Button] = []
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
        self.root.bind_all("<Button-1>", self._clear_selection_on_background_click, add="+")
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
        self._action_button(top, "Refresh devices", self.refresh_devices).grid(row=0, column=6, padx=(8, 0))
        self._action_button(top, "Browse ADB", self.browse_adb).grid(row=0, column=7, padx=(8, 0))
        ttk.Label(top, textvariable=self.gateway_var).grid(row=1, column=0, columnspan=8, sticky="w", pady=(8, 0))
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
        self.tree.bind("<Button-1>", self._select_clicked_row)
        self.tree.bind("<<TreeviewSelect>>", self._remember_selection)
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
        self._action_button(form, "Import list", self.import_proxy_pool).grid(row=1, column=5, padx=5, pady=(8, 0))
        self._action_button(form, "Rotate proxy", self.rotate_proxy).grid(row=1, column=6, padx=5, pady=(8, 0))
        self._action_button(form, "Apply proxy", self.apply_proxy).grid(row=1, column=7, padx=5, pady=(8, 0))
        self._action_button(form, "Clear proxy", self.clear_proxy).grid(row=2, column=7, padx=5, pady=(8, 0))

        bottom = ttk.Frame(self.root, padding=(10, 0, 10, 10))
        bottom.pack(fill="both")
        status_row = ttk.Frame(bottom)
        status_row.pack(fill="x")
        ttk.Label(status_row, textvariable=self.status_var).pack(side="left")
        self.progress = ttk.Progressbar(status_row, mode="indeterminate", length=150)
        self.log = tk.Text(bottom, height=7, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True, pady=(4, 0))

    def _action_button(self, parent: tk.Misc, text: str, command: Callable[[], None]) -> ttk.Button:
        button = ttk.Button(parent, text=text, command=command)
        self._action_buttons.append(button)
        return button

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
        if self._busy_timer is not None:
            self.root.after_cancel(self._busy_timer)
            self._busy_timer = None
        if self.api_server is not None:
            self.api_server.shutdown()
            self.api_server.server_close()
        if self.api_thread is not None:
            self.api_thread.join(timeout=2)
        self.gateway.close()
        self.root.destroy()

    def browse_adb(self) -> None:
        path = filedialog.askopenfilename(
            title="Chọn adb.exe",
            filetypes=(("ADB", "adb.exe"), ("Executable", "*.exe"), ("All files", "*.*")),
        )
        if not path:
            return
        self.adb_var.set(path)
        self._log(f"ADB path: {path}")
        self.refresh_devices()

    def _run_async(
        self,
        title: str,
        operation: Callable[[], object],
        *,
        show_error_popup: bool = True,
    ) -> None:
        self._begin_busy(title)

        def worker() -> None:
            try:
                self.events.put(("result", operation()))
            except Exception as exc:
                self.events.put(("error", (str(exc), show_error_popup)))

        threading.Thread(target=worker, daemon=True).start()

    def _begin_busy(self, title: str) -> None:
        self._busy_count += 1
        self._busy_title = title
        self._busy_seconds = 0
        self.status_var.set(title)
        if self._busy_count == 1:
            self.progress.pack(side="left", padx=(10, 0))
            self.progress.start(12)
            for button in self._action_buttons:
                button.state(["disabled"])
            self.root.configure(cursor="watch")
        self._schedule_busy_tick()

    def _schedule_busy_tick(self) -> None:
        if self._busy_timer is not None:
            self.root.after_cancel(self._busy_timer)
        self._busy_timer = self.root.after(1000, self._tick_busy)

    def _tick_busy(self) -> None:
        self._busy_timer = None
        if self._busy_count <= 0:
            return
        self._busy_seconds += 1
        self.status_var.set(f"{self._busy_title} ({self._busy_seconds}s)")
        self._schedule_busy_tick()

    def _end_busy(self) -> None:
        self._busy_count = max(0, self._busy_count - 1)
        if self._busy_count:
            return
        if self._busy_timer is not None:
            self.root.after_cancel(self._busy_timer)
            self._busy_timer = None
        self.progress.stop()
        self.progress.pack_forget()
        for button in self._action_buttons:
            button.state(["!disabled"])
        self.root.configure(cursor="")

    def refresh_devices(self, *, show_error_popup: bool = True) -> None:
        def operation():
            client = self._client()
            rows = []
            for device in client.list_devices():
                model = client.get_model(device.serial) if device.usable else {}
                proxy = client.get_global_proxy(device.serial) if device.usable else None
                mapping = self.gateway.mapping(device.serial)
                if device.usable and mapping:
                    public_ip = self._gateway_public_ip(mapping.to_dict())
                elif device.usable:
                    public_ip = self._device_public_ip(client, device.serial)
                else:
                    public_ip = ""
                rows.append((device, model, proxy, public_ip, mapping.to_dict() if mapping else None))
            return rows

        self._run_async(
            "Đang đọc thiết bị và IP...",
            operation,
            show_error_popup=show_error_popup,
        )

    def _gateway_public_ip(self, mapping: dict[str, object]) -> str:
        port = mapping.get("local_port")
        if not port:
            return "(chưa có gateway)"
        proxy_url = f"http://127.0.0.1:{int(port)}"
        opener = build_opener(ProxyHandler({"http": proxy_url, "https": proxy_url}))
        try:
            value = opener.open("https://api.ipify.org", timeout=20).read().decode("utf-8").strip()
        except Exception as exc:
            return f"(không đọc được IP: {exc.__class__.__name__})"
        if _is_ip_address(value):
            return value
        return "(IP không hợp lệ)"

    def _device_public_ip(self, client, serial: str) -> str:
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
            if not value:
                continue
            candidate = value.splitlines()[-1].strip()
            if _is_ip_address(candidate):
                return candidate
            lowered = value.lower()
            if "not found" in lowered or "inaccessible" in lowered or "unknown command" in lowered:
                continue
        return "(thiếu curl/wget)"

    def _selected_serial(self) -> str:
        selected = self.tree.selection()
        if not selected:
            raise ValueError("Hãy chọn một thiết bị.")
        return self.tree.item(selected[0], "values")[0]

    def _selected_serial_or_all(self, title: str, message: str) -> tuple[list[str] | None, bool]:
        selected = self.tree.selection()
        if selected:
            return [self.tree.item(selected[0], "values")[0]], False
        if not messagebox.askyesno(title, message):
            return None, True
        return [], True

    def _online_serials(self, service: ProxyManagerService) -> list[str]:
        serials = [
            row["serial"]
            for row in service.list_devices()
            if row["usable"]
        ]
        if not serials:
            raise RuntimeError("Không có thiết bị online.")
        return serials

    def apply_proxy(self) -> None:
        try:
            proxy = parse_proxy(":".join((
                self.host_var.get(), self.port_var.get(),
                self.user_var.get(), self.password_var.get(),
            )))
            selected_serials, all_devices = self._selected_serial_or_all(
                "Apply proxy toàn bộ?",
                "Không có thiết bị nào được chọn. Áp dụng proxy này cho toàn bộ thiết bị online?",
            )
            if selected_serials is None:
                return
        except ProxyParseError as exc:
            messagebox.showerror("Proxy không hợp lệ", str(exc))
            return

        def operation():
            service = self._service()
            serials = selected_serials or self._online_serials(service)
            if self.dry_run_var.get():
                result = service.apply(proxy, serials, dry_run=True)
            else:
                result = service.apply(proxy, serials)
            failed = [row for row in result["results"] if not row.get("ok")]
            if failed:
                raise RuntimeError(_format_failures(failed, "Apply thất bại."))
            prefix = "DRY-RUN " if self.dry_run_var.get() else "OK "
            if all_devices:
                return f"{prefix}apply proxy cho toàn bộ {len(serials)} thiết bị | upstream {proxy.redacted}"
            row = result["results"][0]
            endpoint = row.get("android_after") or row.get("local_endpoint") or "(gateway port mới)"
            return f"{serials[0]}: {prefix}Android -> {endpoint}; upstream {proxy.redacted}"

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
        selected_serials, all_devices = self._selected_serial_or_all(
            "Rotate proxy toàn bộ?",
            "Không có thiết bị nào được chọn. Random proxy cho toàn bộ thiết bị online?",
        )
        if selected_serials is None:
            return

        def operation():
            service = self._service()
            serials = selected_serials or self._online_serials(service)
            result = service.rotate_from_pool(serials, dry_run=self.dry_run_var.get())
            failed = [row for row in result["results"] if not row.get("ok")]
            if failed:
                raise RuntimeError(_format_failures(failed, "Rotate thất bại."))
            prefix = "DRY-RUN " if self.dry_run_var.get() else "OK "
            if all_devices:
                return f"{prefix}rotate proxy cho toàn bộ {len(serials)} thiết bị"
            row = result["results"][0]
            endpoint = row.get("local_endpoint") or "(gateway port mới)"
            android_after = row.get("android_after") or endpoint
            return (
                f"{serials[0]}: {prefix}rotate -> {row.get('upstream')} | "
                f"Android {row.get('android_before') or '(none)'} -> {android_after}"
            )

        self._run_async("Đang xoay proxy...", operation)

    def clear_proxy(self) -> None:
        try:
            serial = self._selected_serial()
        except ValueError as exc:
            if not messagebox.askyesno(
                "Clear all proxy?",
                f"{exc}\n\nKhông có thiết bị nào được chọn. Clear proxy toàn bộ thiết bị online?",
            ):
                return
            serial = ""

        def operation():
            service = self._service()
            if serial:
                serials = [serial]
            else:
                serials = self._online_serials(service)
            result = service.clear(serials, dry_run=self.dry_run_var.get())
            failed = [row for row in result["results"] if not row.get("ok")]
            if failed:
                raise RuntimeError(_format_failures(failed, "Clear thất bại."))
            prefix = "DRY-RUN " if self.dry_run_var.get() else "OK "
            if serial:
                row = result["results"][0]
                return f"{serial}: {prefix}{row.get('before') or '(none)'} -> (none)"
            return f"{prefix}clear proxy toàn bộ {len(serials)} thiết bị"

        self._run_async("Đang xóa proxy...", operation)

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "result":
                    if isinstance(payload, list):
                        self._render_devices(payload)
                        self.status_var.set(f"Đã đọc {len(payload)} thiết bị")
                        self._end_busy()
                    else:
                        self._log(str(payload))
                        self.pool_var.set(self._pool_label())
                        self.status_var.set("Hoàn tất")
                        # Start the follow-up refresh before releasing this
                        # operation so the indicator never blinks off between
                        # the two.
                        self.refresh_devices()
                        self._end_busy()
                else:
                    message, show_error_popup = payload
                    self._log(f"LỖI: {message}")
                    self.status_var.set("Có lỗi")
                    self._end_busy()
                    if show_error_popup:
                        messagebox.showerror("Lỗi", str(message))
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
            item = self.tree.insert("", "end", values=(device.serial, device.state, model_text, proxy_text, public_ip))
            if device.serial == self.selected_serial:
                self.tree.selection_set(item)
                self.tree.focus(item)

    def _log(self, text: str) -> None:
        # Mọi dòng người dùng thấy trong ô log cũng phải nằm trong file log,
        # nếu không thì lúc báo lỗi chẳng có gì để đối chiếu.
        LOGGER.info("%s", text)
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

    def _select_clicked_row(self, event) -> None:
        row = self.tree.identify_row(event.y)
        if not row:
            self.selected_serial = ""
            self.tree.selection_remove(self.tree.selection())
            return
        self.root.after_idle(lambda: self._force_select_row(row))

    def _force_select_row(self, row: str) -> None:
        if not self.tree.exists(row):
            return
        self.tree.selection_set(row)
        self.tree.focus(row)
        values = self.tree.item(row, "values")
        self.selected_serial = values[0] if values else ""

    def _remember_selection(self, _event) -> None:
        selected = self.tree.selection()
        if not selected:
            self.selected_serial = ""
            return
        values = self.tree.item(selected[0], "values")
        self.selected_serial = values[0] if values else ""

    def _clear_selection_on_background_click(self, event) -> None:
        widget = event.widget
        if isinstance(widget, str):
            # Tk dựng dropdown của combobox (và ruột các hộp thoại) bằng code
            # Tcl, nên Tkinter không tra ra object Python và để nguyên đường
            # dẫn dạng chuỗi. Click vào những widget đó không phải click nền.
            try:
                widget = self.root.nametowidget(widget)
            except KeyError:
                return
        if widget is self.tree:
            return
        widget_class = widget.winfo_class()
        interactive_classes = {
            "Button",
            "TButton",
            "Entry",
            "TEntry",
            "TCombobox",
            "TCheckbutton",
            "Text",
            "Scrollbar",
            "TScrollbar",
        }
        if widget_class in interactive_classes:
            return
        self.selected_serial = ""
        self.tree.selection_remove(self.tree.selection())


def _format_failures(rows: list[dict[str, object]], fallback: str) -> str:
    return "; ".join(
        f"{row.get('serial')}: {row.get('error', fallback)}"
        for row in rows
    )


def _is_ip_address(value: str) -> bool:
    try:
        ip_address(value)
        return True
    except ValueError:
        return False


def _find_adb_path() -> str:
    detected = shutil.which("adb")
    if detected:
        return detected
    candidates = (
        Path("C:/Program Files (x86)/xiaowei_android/tools/adb.exe"),
        Path("C:/Program Files/xiaowei_android/tools/adb.exe"),
        Path.home() / "AppData" / "Local" / "Android" / "Sdk" / "platform-tools" / "adb.exe",
        Path("C:/Android/platform-tools/adb.exe"),
        Path("C:/platform-tools/adb.exe"),
    )
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return "adb"


def launch_gui(
    *,
    adb_path: str | None = None,
    backend: str = "adb",
    xiaowei_url: str = "ws://127.0.0.1:22222/",
    state_path: str = str(DEFAULT_STATE),
    config_path: str = str(DEFAULT_CONFIG),
) -> int:
    try:
        setup_logging(**logging_kwargs(load_config(config_path)))
    except (ValueError, OSError):
        setup_logging(**logging_kwargs({"logging": {}}))
    LOGGER.info("GUI khởi động: backend=%s state=%s", backend, state_path)
    root = tk.Tk()
    app = ProxyManagerApp(
        root,
        adb_path=adb_path,
        backend=backend,
        xiaowei_url=xiaowei_url,
        state_path=state_path,
        config_path=config_path,
    )
    root.after(150, lambda: app.refresh_devices(show_error_popup=False))
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(launch_gui())
