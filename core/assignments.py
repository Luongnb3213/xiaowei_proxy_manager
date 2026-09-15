from __future__ import annotations

import csv
from pathlib import Path

from .proxy import Proxy, ProxyParseError, parse_proxy


def load_assignments(path: str | Path) -> dict[str, Proxy]:
    """Load CSV rows with `serial,proxy` columns."""
    csv_path = Path(path)
    try:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise ValueError("CSV thiếu header. Cần tối thiểu serial,proxy.")
            fields = {field.strip().lower() for field in reader.fieldnames if field}
            serial_field = next((f for f in ("serial", "device", "device_id") if f in fields), None)
            proxy_field = next((f for f in ("proxy", "proxy_string") if f in fields), None)
            if not serial_field or not proxy_field:
                raise ValueError("CSV cần hai cột `serial` và `proxy`.")

            assignments: dict[str, Proxy] = {}
            for row_number, row in enumerate(reader, start=2):
                normalized = {
                    str(key or "").strip().lower(): str(value or "").strip()
                    for key, value in row.items()
                }
                serial = normalized.get(serial_field, "")
                raw_proxy = normalized.get(proxy_field, "")
                if not serial and not raw_proxy:
                    continue
                if not serial:
                    raise ValueError(f"Dòng {row_number}: thiếu serial.")
                try:
                    assignments[serial] = parse_proxy(raw_proxy)
                except ProxyParseError as exc:
                    raise ValueError(f"Dòng {row_number}: {exc}") from exc
            return assignments
    except FileNotFoundError as exc:
        raise ValueError(f"Không tìm thấy file mapping: {csv_path}") from exc

