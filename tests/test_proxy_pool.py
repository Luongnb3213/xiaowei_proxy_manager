import tempfile
import unittest
import zipfile
from pathlib import Path

from xiaowei_proxy_manager.proxy_pool import load_proxy_file, parse_proxy_rows, parse_proxy_text


class ProxyPoolTests(unittest.TestCase):
    def test_parse_text_list_deduplicates(self):
        proxies = parse_proxy_text(
            """
            proxy1.example:8000:user:pass
            proxy1.example:8000:user:pass
            proxy2.example:8000:user:pass
            """
        )

        self.assertEqual([proxy.host for proxy in proxies], ["proxy1.example", "proxy2.example"])

    def test_parse_rows_with_host_columns(self):
        proxies = parse_proxy_rows(
            [
                ["host", "port", "username", "password"],
                ["proxy.example", "8888", "user", "pass"],
            ]
        )

        self.assertEqual(proxies[0].raw, "proxy.example:8888:user:pass")

    def test_load_xlsx_proxy_column(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "proxies.xlsx"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(
                    "xl/worksheets/sheet1.xml",
                    """<?xml version="1.0" encoding="UTF-8"?>
                    <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
                      <sheetData>
                        <row><c t="inlineStr"><is><t>proxy</t></is></c></row>
                        <row><c t="inlineStr"><is><t>proxy.example:8888:user:pass</t></is></c></row>
                      </sheetData>
                    </worksheet>
                    """,
                )

            proxies = load_proxy_file(path)

        self.assertEqual(len(proxies), 1)
        self.assertEqual(proxies[0].endpoint, "proxy.example:8888")


if __name__ == "__main__":
    unittest.main()
