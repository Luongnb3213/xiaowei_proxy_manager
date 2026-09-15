from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DEFAULT_CONFIG = Path(__file__).resolve().parent / "config.json"

DEFAULT_CONFIG_DATA: dict[str, dict[str, Any]] = {
    "api": {
        "host": "127.0.0.1",
        "port": 8765,
    },
    "gateway": {
        "bind_host": "0.0.0.0",
        "advertised_host": "",
        "start_port": 10001,
        "end_port": 11000,
        "connect_timeout": 20,
        "idle_timeout": 300,
    },
}


def load_config(path: str | Path = DEFAULT_CONFIG) -> dict[str, Any]:
    config_path = Path(path)
    try:
        with config_path.open(encoding="utf-8") as handle:
            loaded = json.load(handle)
    except FileNotFoundError:
        loaded = {}
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Không đọc được config {config_path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ValueError("config.json phải là JSON object.")

    config: dict[str, Any] = {
        section: dict(values)
        for section, values in DEFAULT_CONFIG_DATA.items()
    }
    for section, values in loaded.items():
        if section not in config:
            config[section] = values
            continue
        if not isinstance(values, dict):
            raise ValueError(f"config.json cần object '{section}'.")
        config[section].update(values)

    _validate_config(config)
    return config


def gateway_kwargs(config: dict[str, Any]) -> dict[str, Any]:
    gateway = config.get("gateway")
    if not isinstance(gateway, dict):
        raise ValueError("config.json cần object 'gateway'.")
    return {
        "bind_host": str(gateway.get("bind_host") or "0.0.0.0"),
        "advertised_host": str(gateway.get("advertised_host") or "") or None,
        "start_port": int(gateway.get("start_port", 10001)),
        "end_port": int(gateway.get("end_port", 11000)),
        "connect_timeout": float(gateway.get("connect_timeout", 20)),
        "idle_timeout": float(gateway.get("idle_timeout", 300)),
    }


def api_kwargs(config: dict[str, Any]) -> dict[str, Any]:
    api = config.get("api")
    if not isinstance(api, dict):
        raise ValueError("config.json cần object 'api'.")
    return {
        "host": str(api.get("host") or "127.0.0.1"),
        "port": int(api.get("port", 8765)),
    }


def config_value(args: Any, config: dict[str, Any], section: str, key: str) -> Any:
    attr = key if section == "api" else f"{section}_{key}"
    override = getattr(args, attr, None)
    return override if override is not None else config[section][key]


def _validate_config(config: dict[str, Any]) -> None:
    required = {
        "api": ("host", "port"),
        "gateway": ("bind_host", "advertised_host", "start_port", "end_port"),
    }
    for section, keys in required.items():
        if not isinstance(config.get(section), dict):
            raise ValueError(f"config.json cần object '{section}'.")
        for key in keys:
            if key not in config[section]:
                raise ValueError(f"config.json thiếu '{section}.{key}'.")
    api_kwargs(config)
    gateway_kwargs(config)
