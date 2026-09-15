import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from xiaowei_proxy_manager.backends.adb import Device
from xiaowei_proxy_manager.api import ProxyApiServer, ProxyManagerService
from xiaowei_proxy_manager.gateway import LocalProxyGateway
from xiaowei_proxy_manager.core.proxy import parse_proxy
from xiaowei_proxy_manager.core.state import StateStore


class FakeClient:
    def __init__(self):
        self.devices = [
            Device(serial="phone-1", state="device", details="model:Box"),
            Device(serial="offline-1", state="offline"),
        ]
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


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
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
        self.temp_dir.cleanup()

    def request(self, method, path, payload=None):
        body = None
        headers = {}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(
            self.base_url + path,
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=2) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    def test_health_and_devices_do_not_require_token(self):
        status, payload = self.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["backend"], "test")

        status, payload = self.request("GET", "/api/v1/devices")
        self.assertEqual(status, 200)
        self.assertEqual([row["serial"] for row in payload["data"]], ["phone-1", "offline-1"])

    def test_apply_status_clear_and_rollback(self):
        apply_status, apply_payload = self.request(
            "POST",
            "/api/v1/proxy/apply",
            {
                "serial": "phone-1",
                "proxy": "proxy.example:8080:user:secret",
                "allow_auth_unsupported": True,
            },
        )
        self.assertEqual(apply_status, 200)
        self.assertTrue(apply_payload["ok"])
        self.assertEqual(self.client.proxies["phone-1"], "proxy.example:8080")
        self.assertNotIn("secret", json.dumps(apply_payload))

        status, payload = self.request("GET", "/api/v1/devices/phone-1/proxy")
        self.assertEqual(status, 200)
        self.assertEqual(payload["data"]["actual"], "proxy.example:8080")

        status, payload = self.request("DELETE", "/api/v1/devices/phone-1/proxy")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertIsNone(self.client.proxies["phone-1"])

        status, payload = self.request(
            "POST",
            "/api/v1/devices/phone-1/proxy/rollback",
            {},
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(self.client.proxies["phone-1"], "proxy.example:8080")

    def test_invalid_apply_returns_structured_error(self):
        status, payload = self.request(
            "POST",
            "/api/v1/proxy/apply",
            {"serial": "phone-1", "proxy": "invalid"},
        )
        self.assertEqual(status, 422)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "invalid_proxy")

    def test_gateway_apply_and_upstream_rotation_keep_local_endpoint(self):
        gateway = LocalProxyGateway(
            self.server.service.state,
            advertised_host="127.0.0.1",
            start_port=19111,
            end_port=19111,
        )
        self.server.service.gateway = gateway
        try:
            status, payload = self.request(
                "POST",
                "/api/v1/proxy/apply",
                {
                    "serial": "phone-1",
                    "proxy": "first.example:8080:user:first-secret",
                },
            )
            self.assertEqual(status, 200)
            self.assertTrue(payload["ok"])
            endpoint = payload["data"]["results"][0]["local_endpoint"]
            self.assertEqual(self.client.proxies["phone-1"], endpoint)
            self.assertNotIn("first-secret", json.dumps(payload))

            status, payload = self.request(
                "POST",
                "/api/v1/devices/phone-1/upstream",
                {
                    "proxy": "second.example:8080:user:second-secret",
                },
            )
            self.assertEqual(status, 200)
            self.assertTrue(payload["ok"])
            self.assertEqual(
                payload["data"]["results"][0]["local_endpoint"],
                endpoint,
            )
            self.assertEqual(self.client.proxies["phone-1"], endpoint)
            self.assertNotIn("second-secret", json.dumps(payload))

            status, payload = self.request(
                "POST",
                "/api/v1/devices/phone-1/proxy/rollback",
                {},
            )
            self.assertEqual(status, 200)
            self.assertTrue(payload["ok"])
            self.assertEqual(
                payload["data"]["results"][0]["local_endpoint"],
                endpoint,
            )
            self.assertEqual(self.client.proxies["phone-1"], endpoint)
            self.assertNotIn("first-secret", json.dumps(payload))
        finally:
            gateway.close()

    def test_proxy_pool_rotate_endpoint_uses_next_proxy(self):
        gateway = LocalProxyGateway(
            self.server.service.state,
            advertised_host="127.0.0.1",
            start_port=19112,
            end_port=19112,
        )
        self.server.service.gateway = gateway
        try:
            status, payload = self.request(
                "POST",
                "/api/v1/proxy/pool",
                {
                    "proxies": [
                        "first.example:8080:user:first-secret",
                        "second.example:8080:user:second-secret",
                    ]
                },
            )
            self.assertEqual(status, 200)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["data"]["count"], 2)
            self.assertNotIn("first-secret", json.dumps(payload))

            status, payload = self.request(
                "POST",
                "/api/v1/devices/phone-1/proxy/rotate",
                {},
            )
            self.assertEqual(status, 200)
            self.assertTrue(payload["ok"])
            first_endpoint = payload["data"]["results"][0]["local_endpoint"]
            self.assertEqual(self.client.proxies["phone-1"], first_endpoint)
            self.assertRegex(
                json.dumps(payload),
                r"(first|second)\.example:8080:user:\*\*\*",
            )
            self.assertNotIn("first-secret", json.dumps(payload))
            self.assertNotIn("second-secret", json.dumps(payload))

            status, payload = self.request(
                "POST",
                "/api/v1/devices/phone-1/proxy/rotate",
                {},
            )
            self.assertEqual(status, 200)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["data"]["results"][0]["local_endpoint"], first_endpoint)
            self.assertEqual(self.client.proxies["phone-1"], first_endpoint)
            self.assertRegex(
                json.dumps(payload),
                r"(first|second)\.example:8080:user:\*\*\*",
            )
            self.assertNotIn("first-secret", json.dumps(payload))
            self.assertNotIn("second-secret", json.dumps(payload))
        finally:
            gateway.close()

    def test_pool_selection_only_locks_proxies_used_by_other_devices(self):
        state = self.server.service.state
        first = parse_proxy("first.example:8080:user:first-secret")
        second = parse_proxy("second.example:8080:user:second-secret")
        state.set_proxy_pool([first, second])
        state.set_gateway_mapping(
            "phone-1",
            local_port=19120,
            bind_host="127.0.0.1",
            advertised_host="127.0.0.1",
            upstream=first,
        )
        state.set_gateway_mapping(
            "phone-2",
            local_port=19121,
            bind_host="127.0.0.1",
            advertised_host="127.0.0.1",
            upstream=second,
        )

        selected = self.server.service._next_available_pool_proxy("phone-1", advance=False)
        self.assertEqual(selected.raw, first.raw)

        state.remove_gateway_mapping("phone-2")
        with patch("xiaowei_proxy_manager.core.state.random.choice", side_effect=lambda items: items[-1]):
            selected = self.server.service._next_available_pool_proxy("phone-1", advance=False)
        self.assertEqual(selected.raw, second.raw)


if __name__ == "__main__":
    unittest.main()
