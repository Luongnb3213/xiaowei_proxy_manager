import io
import json
import logging
import re
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from xiaowei_proxy_manager.api import (
    ProxyApiServer,
    ProxyManagerService,
    _log_json,
    _redact_proxy_text,
)
from xiaowei_proxy_manager.backends.adb import Device
from xiaowei_proxy_manager.core.config import logging_kwargs
from xiaowei_proxy_manager.core.logging_config import (
    LOGGER_NAME,
    get_logger,
    log_file_path,
    setup_logging,
)
from xiaowei_proxy_manager.core.state import StateStore


SECRET = "sup3r-s3cret-pw"

# 2026-09-15 21:30:12.345 | INFO     | xiaowei_proxy_manager.api | ...
LOG_LINE = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} \| "
    r"(?:DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+\| "
    r"xiaowei_proxy_manager(?:\.\w+)* \| .+$"
)


class FakeClient:
    def __init__(self):
        self.devices = [Device(serial="phone-1", state="device", details="model:Box")]
        self.proxies = {"phone-1": None}

    def list_devices(self):
        return list(self.devices)

    def get_global_proxy(self, serial):
        return self.proxies.get(serial)

    def set_global_proxy(self, serial, endpoint):
        self.proxies[serial] = endpoint
        return endpoint

    def clear_global_proxy(self, serial):
        self.proxies[serial] = None
        return None

    def get_model(self, serial):
        return {"manufacturer": "Test", "model": "Box", "android": "14", "device": "box"}


def _configured_handlers() -> list[logging.Handler]:
    """Handler thật sự ghi log — NullHandler mặc định của package không tính."""
    return [
        handler
        for handler in logging.getLogger(LOGGER_NAME).handlers
        if not isinstance(handler, logging.NullHandler)
    ]


def _detach_handlers() -> None:
    logger = logging.getLogger(LOGGER_NAME)
    for handler in _configured_handlers():
        logger.removeHandler(handler)
        handler.close()


class SetupLoggingTests(unittest.TestCase):
    def tearDown(self):
        _detach_handlers()

    def test_creates_directory_and_writes_in_standard_format(self):
        with tempfile.TemporaryDirectory() as directory:
            log_dir = Path(directory) / "nested" / "logs"
            setup_logging(
                level="DEBUG",
                directory=log_dir,
                filename="app.log",
                console=False,
                force=True,
            )
            get_logger("probe").info("xin chào %s", "log")
            _detach_handlers()

            path = log_file_path(log_dir, "app.log")
            self.assertTrue(path.exists())
            lines = [
                line
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertTrue(lines)
            for line in lines:
                self.assertRegex(line, LOG_LINE)
            self.assertIn("xiaowei_proxy_manager.probe | xin chào log", lines[-1])

    def test_setup_is_idempotent_unless_forced(self):
        with tempfile.TemporaryDirectory() as directory:
            kwargs = {"directory": directory, "console": False}
            logger = setup_logging(level="INFO", **kwargs)
            self.assertEqual(len(_configured_handlers()), 1)

            # Gọi lại (CLI rồi GUI trong cùng process) không được nhân đôi handler,
            # nếu không mỗi dòng log sẽ bị ghi hai lần.
            again = setup_logging(level="DEBUG", **kwargs)
            self.assertIs(again, logger)
            self.assertEqual(len(_configured_handlers()), 1)
            self.assertEqual(again.level, logging.DEBUG)

            setup_logging(level="INFO", force=True, **kwargs)
            self.assertEqual(len(_configured_handlers()), 1)
            _detach_handlers()

    def test_console_stays_one_line_while_file_keeps_the_traceback(self):
        stream = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            log_dir = Path(directory) / "logs"
            setup_logging(
                level="INFO",
                directory=log_dir,
                filename="app.log",
                console=True,
                force=True,
            )
            for handler in _configured_handlers():
                if isinstance(handler, logging.StreamHandler) and not isinstance(
                    handler, logging.FileHandler
                ):
                    handler.setStream(stream)
            try:
                raise ValueError("adb chết rồi")
            except ValueError as exc:
                get_logger("cli").error("CLI lỗi: %s", exc, exc_info=exc)
            _detach_handlers()

            console = stream.getvalue()
            on_disk = log_file_path(log_dir, "app.log").read_text(encoding="utf-8")

        self.assertIn("CLI lỗi: adb chết rồi", console)
        self.assertNotIn("Traceback", console)
        self.assertEqual(len([line for line in console.splitlines() if line.strip()]), 1)

        # File log là chỗ để điều tra, nên traceback phải còn nguyên ở đó.
        self.assertIn("CLI lỗi: adb chết rồi", on_disk)
        self.assertIn("Traceback (most recent call last)", on_disk)
        self.assertIn("ValueError: adb chết rồi", on_disk)

    def test_config_defaults_are_usable_without_logging_section(self):
        kwargs = logging_kwargs({"logging": {}})
        self.assertEqual(kwargs["level"], "INFO")
        self.assertEqual(kwargs["filename"], "proxy_manager.log")
        self.assertTrue(Path(kwargs["directory"]).is_absolute())


class RedactionTests(unittest.TestCase):
    def test_proxy_string_keeps_endpoint_and_user_only(self):
        self.assertEqual(
            _redact_proxy_text(f"proxy.example:8080:user:{SECRET}"),
            "proxy.example:8080:user:***",
        )

    def test_endpoint_without_credentials_is_kept_readable(self):
        self.assertEqual(_redact_proxy_text("proxy.example:8080"), "proxy.example:8080")
        self.assertEqual(_redact_proxy_text("[2001:db8::1]:8080"), "[2001:db8::1]:8080")

    def test_unparseable_value_with_credentials_is_hidden_entirely(self):
        # Không parse được nghĩa là không biết đoạn nào là mật khẩu.
        self.assertEqual(_redact_proxy_text("a:b:c"), "***")

    def test_nested_payload_is_redacted(self):
        text = _log_json(
            {
                "serial": "phone-1",
                "password": SECRET,
                "proxies": [f"a.example:1:u:{SECRET}", f"b.example:2:u:{SECRET}"],
                "nested": {"token": SECRET, "dry_run": True},
            }
        )
        self.assertNotIn(SECRET, text)
        self.assertIn("a.example:1:u:***", text)
        self.assertIn("phone-1", text)
        self.assertIn('"dry_run": true', text)

    def test_long_value_is_truncated(self):
        text = _log_json({"blob": "x" * 5000})
        self.assertLess(len(text), 1700)
        self.assertIn("cắt bớt", text)


class ApiAccessLogTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.log_dir = Path(self.temp_dir.name) / "logs"
        setup_logging(
            level="DEBUG",
            directory=self.log_dir,
            filename="api.log",
            console=False,
            force=True,
        )
        self.log_path = log_file_path(self.log_dir, "api.log")

        state = StateStore(Path(self.temp_dir.name) / "state.json")
        self.client = FakeClient()
        self.server = ProxyApiServer(
            ("127.0.0.1", 0),
            ProxyManagerService(self.client, state, backend="test"),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        _detach_handlers()
        self.temp_dir.cleanup()

    def request(self, method, path, payload=None):
        body = None
        headers = {}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            with urlopen(request, timeout=2) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def log_text(self):
        for handler in logging.getLogger(LOGGER_NAME).handlers:
            handler.flush()
        return self.log_path.read_text(encoding="utf-8")

    def wait_for(self, needle, timeout=3.0):
        """Dòng '<--' được ghi sau khi response đã bay về client, nên phải chờ."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            text = self.log_text()
            if needle in text:
                return text
            time.sleep(0.02)
        self.fail(f"Không thấy {needle!r} trong log:\n{self.log_text()}")

    def test_request_response_and_payload_share_one_request_id(self):
        status, _ = self.request(
            "POST",
            "/api/v1/proxy/apply",
            {
                "serial": "phone-1",
                "proxy": f"proxy.example:8080:user:{SECRET}",
                "allow_auth_unsupported": True,
            },
        )
        self.assertEqual(status, 200)
        text = self.wait_for("<-- 200 OK")

        start = re.search(r"\[([0-9a-f]{8})\] --> POST /api/v1/proxy/apply", text)
        self.assertIsNotNone(start, text)
        request_id = start.group(1)
        for marker in ("payload", "result", "<-- 200 OK"):
            self.assertIn(f"[{request_id}] {marker}", text)

        finish = re.search(rf"\[{request_id}\] <-- 200 OK ([\d.]+)ms (\d+)B", text)
        self.assertIsNotNone(finish, text)
        self.assertGreater(int(finish.group(2)), 0)

        for line in text.splitlines():
            if line.strip():
                self.assertRegex(line, LOG_LINE)

    def test_password_never_reaches_the_log_file(self):
        status, _ = self.request(
            "POST",
            "/api/v1/proxy/apply",
            {
                "serial": "phone-1",
                "proxy": f"proxy.example:8080:user:{SECRET}",
                "allow_auth_unsupported": True,
            },
        )
        self.assertEqual(status, 200)
        text = self.wait_for("<-- 200 OK")
        self.assertNotIn(SECRET, text)
        self.assertIn("proxy.example:8080:user:***", text)

    def test_pool_import_text_is_redacted_line_by_line(self):
        status, _ = self.request(
            "POST",
            "/api/v1/proxy/pool",
            {"text": f"a.example:1:u1:{SECRET}\nb.example:2:u2:{SECRET}"},
        )
        self.assertEqual(status, 200)
        text = self.wait_for("<-- 200 OK")
        self.assertNotIn(SECRET, text)
        self.assertIn("a.example:1:u1:***", text)
        self.assertIn("b.example:2:u2:***", text)

    def test_client_error_is_logged_as_warning_with_error_code(self):
        status, _ = self.request(
            "POST",
            "/api/v1/proxy/apply",
            {"serial": "phone-1", "proxy": "invalid"},
        )
        self.assertEqual(status, 422)
        text = self.wait_for("<-- 422")
        self.assertIn("invalid_proxy", text)
        self.assertRegex(text, r"WARNING\s+\| xiaowei_proxy_manager\.api \| \[[0-9a-f]{8}\] invalid_proxy")

    def test_get_request_is_logged_with_method_and_path(self):
        status, _ = self.request("GET", "/api/v1/devices?include_model=false")
        self.assertEqual(status, 200)
        text = self.wait_for("<-- 200 OK")
        self.assertIn("--> GET /api/v1/devices?include_model=false from 127.0.0.1:", text)


if __name__ == "__main__":
    unittest.main()
