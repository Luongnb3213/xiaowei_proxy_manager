import json
import tempfile
import unittest
from pathlib import Path

from xiaowei_proxy_manager.config import api_kwargs, gateway_kwargs, load_config


class ConfigTests(unittest.TestCase):
    def test_load_config_merges_defaults(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "config.json"
            path.write_text(
                json.dumps(
                    {
                        "api": {"port": 9999},
                        "gateway": {"advertised_host": "192.168.1.50"},
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(path)

        self.assertEqual(api_kwargs(config)["host"], "127.0.0.1")
        self.assertEqual(api_kwargs(config)["port"], 9999)
        self.assertEqual(gateway_kwargs(config)["bind_host"], "0.0.0.0")
        self.assertEqual(gateway_kwargs(config)["advertised_host"], "192.168.1.50")
        self.assertEqual(gateway_kwargs(config)["start_port"], 10001)

    def test_missing_config_uses_defaults(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = load_config(Path(temp_dir) / "missing.json")

        self.assertEqual(api_kwargs(config)["port"], 8765)
        self.assertEqual(gateway_kwargs(config)["start_port"], 10001)


if __name__ == "__main__":
    unittest.main()
