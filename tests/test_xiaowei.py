import unittest

from xiaowei_proxy_manager.backends.xiaowei import XiaoweiClient, XiaoweiResponse


class FakeXiaoweiClient(XiaoweiClient):
    def request(self, payload, *, check=True):
        self.last_payload = payload
        return XiaoweiResponse(
            code=10000,
            message="SUCCESS",
            data=[
                {
                    "serial": "ea85356a",
                    "onlySerial": "fixed-serial",
                    "model": "Redmi Note 3",
                    "name": "phone 1",
                    "mode": 0,
                    "status": "online",
                    "intranetIp": "192.168.111.143",
                }
            ],
        )


class XiaoweiClientTests(unittest.TestCase):
    def test_list_devices_parses_xiaowei_response(self):
        client = FakeXiaoweiClient()
        devices = client.list_devices()
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].serial, "ea85356a")
        self.assertEqual(devices[0].state, "device")
        self.assertIn("fixed-serial", devices[0].details)
        self.assertEqual(client.last_payload, {"action": "list"})


if __name__ == "__main__":
    unittest.main()

