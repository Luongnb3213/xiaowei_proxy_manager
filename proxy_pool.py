from __future__ import annotations

import csv
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from .proxy import Proxy, ProxyParseError, parse_proxy


def load_proxy_file(path: str | Path) -> list[Proxy]:
    proxy_path = Path(path)
    if not proxy_path.exists():
        raise ValueError(f"Không tìm thấy file proxy: {proxy_path}")
    if proxy_path.suffix.lower() == ".xlsx":
        return parse_proxy_rows(_xlsx_rows(proxy_path))
    return parse_proxy_rows(_csv_or_text_rows(proxy_path))


def parse_proxy_rows(rows: list[list[str]]) -> list[Proxy]:
    cleaned = [
        [cell.strip() for cell in row]
        for row in rows
        if any(cell.strip() for cell in row)
    ]
    if not cleaned:
        return []

    first = [_normalize_header(cell) for cell in cleaned[0]]
    has_named_header = any(
        value in {"proxy", "proxy_string", "upstream", "host", "server"}
        for value in first
    )
    data_rows = cleaned[1:] if has_named_header else cleaned
    proxies: list[Proxy] = []
    seen: set[str] = set()

    if has_named_header:
        proxy_index = _first_index(first, ("proxy", "proxy_string", "upstream"))
        host_index = _first_index(first, ("host", "server", "ip"))
        port_index = _first_index(first, ("port",))
        user_index = _first_index(first, ("username", "user", "account"))
        password_index = _first_index(first, ("password", "pass", "pwd"))
        for row_number, row in enumerate(data_rows, start=2):
            raw = ""
            if proxy_index is not None and proxy_index < len(row):
                raw = row[proxy_index]
            elif None not in (host_index, port_index, user_index, password_index):
                raw = ":".join(
                    _row_value(row, index)
                    for index in (host_index, port_index, user_index, password_index)
                )
            _append_proxy(proxies, seen, raw, row_number)
        return proxies

    for row_number, row in enumerate(data_rows, start=1):
        raw = ""
        if len(row) == 1:
            raw = row[0]
        elif len(row) >= 4:
            raw = ":".join(row[:4])
        _append_proxy(proxies, seen, raw, row_number)
    return proxies


def parse_proxy_text(text: str) -> list[Proxy]:
    rows = [
        [line.strip()]
        for line in str(text or "").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    return parse_proxy_rows(rows)


def _csv_or_text_rows(path: Path) -> list[list[str]]:
    content = path.read_text(encoding="utf-8-sig")
    sample = content[:2048]
    delimiter = ","
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
    except csv.Error:
        pass
    return list(csv.reader(content.splitlines(), delimiter=delimiter))


def _xlsx_rows(path: Path) -> list[list[str]]:
    with zipfile.ZipFile(path) as archive:
        shared_strings = _shared_strings(archive)
        sheet_name = _first_sheet_name(archive)
        root = ElementTree.fromstring(archive.read(sheet_name))

    namespace = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rows: list[list[str]] = []
    for row in root.findall(".//s:sheetData/s:row", namespace):
        values: list[str] = []
        for cell in row.findall("s:c", namespace):
            values.append(_cell_value(cell, shared_strings, namespace))
        rows.append(values)
    return rows


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    namespace = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    values: list[str] = []
    for item in root.findall("s:si", namespace):
        values.append("".join(text.text or "" for text in item.findall(".//s:t", namespace)))
    return values


def _first_sheet_name(archive: zipfile.ZipFile) -> str:
    for name in archive.namelist():
        if name.startswith("xl/worksheets/") and name.endswith(".xml"):
            return name
    raise ValueError("File .xlsx không có worksheet.")


def _cell_value(cell: ElementTree.Element, shared_strings: list[str], namespace: dict[str, str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(text.text or "" for text in cell.findall(".//s:t", namespace)).strip()
    value = cell.find("s:v", namespace)
    if value is None or value.text is None:
        return ""
    if cell_type == "s":
        index = int(value.text)
        return shared_strings[index] if 0 <= index < len(shared_strings) else ""
    return value.text.strip()


def _append_proxy(proxies: list[Proxy], seen: set[str], raw: str, row_number: int) -> None:
    raw = raw.strip()
    if not raw or raw.startswith("#"):
        return
    try:
        proxy = parse_proxy(raw)
    except ProxyParseError as exc:
        raise ValueError(f"Dòng {row_number}: {exc}") from exc
    if proxy.raw in seen:
        return
    seen.add(proxy.raw)
    proxies.append(proxy)


def _normalize_header(value: str) -> str:
    return value.strip().lower().replace(" ", "_").replace("-", "_")


def _first_index(headers: list[str], names: tuple[str, ...]) -> int | None:
    for name in names:
        if name in headers:
            return headers.index(name)
    return None


def _row_value(row: list[str], index: int | None) -> str:
    if index is None or index >= len(row):
        return ""
    return row[index]
