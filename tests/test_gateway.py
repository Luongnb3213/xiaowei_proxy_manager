import base64
import socket
import socketserver
import tempfile
import threading
import unittest
from pathlib import Path

from xiaowei_proxy_manager.gateway import LocalProxyGateway
from xiaowei_proxy_manager.core.proxy import parse_proxy
from xiaowei_proxy_manager.core.state import StateStore


def _read_headers(sock):
    data = bytearray()
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(1)
        if not chunk:
            break
        data.extend(chunk)
    return bytes(data)


class MockProxyHandler(socketserver.BaseRequestHandler):
    def handle(self):
        headers = _read_headers(self.request)
        self.server.seen.append(headers)
        first_line = headers.split(b"\r\n", 1)[0].decode("ascii")
        if first_line.startswith("CONNECT "):
            self.request.sendall(
                b"HTTP/1.1 200 Connection Established\r\n"
                b"Connection: keep-alive\r\n\r\n"
            )
            payload = self.request.recv(4096)
            if payload:
                self.request.sendall(payload)
            return

        body = b"mock-http-response"
        self.request.sendall(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n"
            b"Connection: close\r\n\r\n" + body
        )


class MockProxyServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address):
        self.seen = []
        super().__init__(server_address, MockProxyHandler)


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.upstream = MockProxyServer(("127.0.0.1", 0))
        self.upstream_thread = threading.Thread(
            target=self.upstream.serve_forever,
            daemon=True,
        )
        self.upstream_thread.start()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.state_path = Path(self.temp_dir.name) / "state.json"

    def tearDown(self):
        self.upstream.shutdown()
        self.upstream.server_close()
        self.upstream_thread.join(timeout=2)
        self.temp_dir.cleanup()

    def proxy(self, username="user", password="secret"):
        return parse_proxy(
            f"127.0.0.1:{self.upstream.server_address[1]}:{username}:{password}"
        )

    def test_http_forwarding_adds_upstream_auth_without_exposing_it(self):
        gateway = LocalProxyGateway(
            StateStore(self.state_path),
            advertised_host="127.0.0.1",
            start_port=19101,
            end_port=19101,
        )
        try:
            mapping = gateway.update_upstream("phone-1", self.proxy())
            with socket.create_connection(("127.0.0.1", mapping.local_port), timeout=2) as client:
                client.sendall(
                    b"GET http://example.test/path HTTP/1.1\r\n"
                    b"Host: example.test\r\n"
                    b"Connection: close\r\n\r\n"
                )
                chunks = []
                while True:
                    chunk = client.recv(4096)
                    if not chunk:
                        break
                    chunks.append(chunk)
                response = b"".join(chunks)
            self.assertIn(b"mock-http-response", response)
            self.assertIn(
                b"Proxy-Authorization: Basic "
                + base64.b64encode(b"user:secret"),
                self.upstream.seen[0],
            )
            self.assertNotIn(b"secret", str(mapping.to_dict()).encode())
        finally:
            gateway.close()

    def test_https_connect_is_tunneled_without_mitm(self):
        gateway = LocalProxyGateway(
            StateStore(self.state_path),
            advertised_host="127.0.0.1",
            start_port=19102,
            end_port=19102,
        )
        try:
            mapping = gateway.update_upstream("phone-1", self.proxy())
            with socket.create_connection(("127.0.0.1", mapping.local_port), timeout=2) as client:
                client.sendall(
                    b"CONNECT secure.example:443 HTTP/1.1\r\n"
                    b"Host: secure.example:443\r\n\r\n"
                    b"encrypted-test-bytes"
                )
                response = _read_headers(client)
                self.assertIn(b"200 Connection Established", response)
                self.assertEqual(client.recv(4096), b"encrypted-test-bytes")
        finally:
            gateway.close()

    def test_mapping_is_stable_across_upstream_rotation_and_restart(self):
        state = StateStore(self.state_path)
        gateway = LocalProxyGateway(
            state,
            advertised_host="127.0.0.1",
            start_port=19103,
            end_port=19103,
        )
        mapping = gateway.update_upstream("phone-1", self.proxy("first", "one"))
        endpoint = mapping.endpoint
        gateway.update_upstream("phone-1", self.proxy("second", "two"))
        self.assertEqual(gateway.mapping("phone-1").endpoint, endpoint)
        gateway.close()

        restored = LocalProxyGateway(
            StateStore(self.state_path),
            advertised_host="127.0.0.1",
            start_port=19103,
            end_port=19103,
        )
        try:
            restored.start()
            restored_mapping = restored.mapping("phone-1")
            self.assertEqual(restored_mapping.endpoint, endpoint)
            self.assertEqual(restored_mapping.upstream.username, "second")
            self.assertEqual(restored_mapping.upstream.password, "two")
        finally:
            restored.close()

    def test_port_collision_uses_next_available_port(self):
        occupied = socket.socket()
        occupied.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        occupied.bind(("127.0.0.1", 19104))
        occupied.listen()
        gateway = LocalProxyGateway(
            StateStore(self.state_path),
            advertised_host="127.0.0.1",
            start_port=19104,
            end_port=19105,
        )
        try:
            mapping = gateway.update_upstream("phone-1", self.proxy())
            self.assertEqual(mapping.local_port, 19105)
        finally:
            gateway.close()
            occupied.close()


if __name__ == "__main__":
    unittest.main()
