import subprocess
import unittest

from xiaowei_proxy_manager.adb import AdbClient


class FakeRunner:
    def __init__(self):
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append(command)
        if command[1:] == ["devices", "-l"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    "List of devices attached\n"
                    "phone-1\tdevice product:x model:BoxPhone device:box\n"
                    "phone-2\tunauthorized usb:1-1\n"
                ),
                stderr="",
            )
        if command[-5:] == ["shell", "settings", "get", "global", "http_proxy"]:
            return subprocess.CompletedProcess(command, 0, stdout="host:1234\n", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")


class AdbClientTests(unittest.TestCase):
    def test_list_devices_parses_states(self):
        runner = FakeRunner()
        devices = AdbClient(runner=runner).list_devices()
        self.assertEqual([device.serial for device in devices], ["phone-1", "phone-2"])
        self.assertTrue(devices[0].usable)
        self.assertFalse(devices[1].usable)

    def test_get_proxy_normalizes_output(self):
        runner = FakeRunner()
        self.assertEqual(AdbClient(runner=runner).get_global_proxy("phone-1"), "host:1234")


if __name__ == "__main__":
    unittest.main()

