from __future__ import annotations

from dataclasses import dataclass


class ProxyParseError(ValueError):
    """Raised when a proxy is not in host:port:username:password format."""


@dataclass(frozen=True)
class Proxy:
    host: str
    port: int
    username: str
    password: str
    raw: str

    @property
    def endpoint(self) -> str:
        """The endpoint accepted by Android's global HTTP proxy setting."""
        host = self.host
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return f"{host}:{self.port}"

    @property
    def redacted(self) -> str:
        user = self.username or "no-user"
        return f"{self.endpoint}:{user}:***"

    def to_dict(self) -> dict[str, object]:
        return {
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "password": self.password,
            "raw": self.raw,
            "endpoint": self.endpoint,
        }


def parse_proxy(value: str) -> Proxy:
    """Parse host:port:username:password.

    ``split(..., 3)`` deliberately allows a colon in the password. IPv6 hosts
    should be written in brackets, e.g. ``[2001:db8::1]:8080:user:pass``.
    """
    raw = str(value or "").strip()
    if raw.startswith("["):
        closing = raw.find("]")
        if closing < 0 or closing + 1 >= len(raw) or raw[closing + 1] != ":":
            raise ProxyParseError(
                "IPv6 host phải có dạng [ipv6]:port:username:password."
            )
        host = raw[1:closing].strip()
        parts = [host, *raw[closing + 2 :].split(":", 2)]
    else:
        parts = raw.split(":", 3)
    if len(parts) != 4:
        raise ProxyParseError(
            "Proxy phải có dạng host:port:username:password "
            "(password có thể chứa dấu ':')."
        )

    host, port_text, username, password = (part.strip() for part in parts)
    if not host:
        raise ProxyParseError("Proxy thiếu host.")
    if any(char.isspace() for char in host):
        raise ProxyParseError("Host proxy không được chứa khoảng trắng.")
    try:
        port = int(port_text)
    except ValueError as exc:
        raise ProxyParseError(f"Port không hợp lệ: {port_text!r}.") from exc
    if not 1 <= port <= 65535:
        raise ProxyParseError("Port proxy phải nằm trong khoảng 1..65535.")
    if not username:
        raise ProxyParseError("Proxy thiếu username.")
    if not password:
        raise ProxyParseError("Proxy thiếu password.")

    return Proxy(
        host=host,
        port=port,
        username=username,
        password=password,
        raw=raw,
    )


def parse_proxy_lines(text: str) -> list[Proxy]:
    """Parse one proxy per line, ignoring blanks and comments."""
    proxies: list[Proxy] = []
    for line_number, line in enumerate(str(text or "").splitlines(), start=1):
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        try:
            proxies.append(parse_proxy(value))
        except ProxyParseError as exc:
            raise ProxyParseError(f"Dòng {line_number}: {exc}") from exc
    return proxies
