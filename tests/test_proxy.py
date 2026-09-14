import unittest

from xiaowei_proxy_manager.proxy import ProxyParseError, parse_proxy


class ProxyParserTests(unittest.TestCase):
    def test_parse_four_part_proxy(self):
        proxy = parse_proxy("host.example:1234:user:pass")
        self.assertEqual(proxy.host, "host.example")
        self.assertEqual(proxy.port, 1234)
        self.assertEqual(proxy.endpoint, "host.example:1234")
        self.assertEqual(proxy.redacted, "host.example:1234:user:***")

    def test_password_can_contain_colon(self):
        proxy = parse_proxy("host:1:user:pa:ss")
        self.assertEqual(proxy.password, "pa:ss")

    def test_bracketed_ipv6(self):
        proxy = parse_proxy("[2001:db8::1]:8080:user:pass")
        self.assertEqual(proxy.host, "2001:db8::1")
        self.assertEqual(proxy.endpoint, "[2001:db8::1]:8080")

    def test_rejects_invalid_shape(self):
        with self.assertRaises(ProxyParseError):
            parse_proxy("host:1234")

    def test_rejects_invalid_port(self):
        with self.assertRaises(ProxyParseError):
            parse_proxy("host:70000:user:pass")


if __name__ == "__main__":
    unittest.main()

