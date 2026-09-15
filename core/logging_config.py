"""Cấu hình logging dùng chung cho CLI, GUI và REST API."""

from __future__ import annotations

import copy
import logging
import logging.handlers
import sys
from pathlib import Path

LOGGER_NAME = "xiaowei_proxy_manager"
LOG_FORMAT = "%(asctime)s.%(msecs)03d | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
DEFAULT_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
DEFAULT_LOG_FILE = "proxy_manager.log"

# Nếu không ai gọi setup_logging() (ví dụ import package trong test), logging sẽ
# dùng lastResort handler và in WARNING trần ra stderr. NullHandler chặn việc đó
# theo đúng khuyến nghị cho thư viện: im lặng cho tới khi app tự cấu hình log.
logging.getLogger(LOGGER_NAME).addHandler(logging.NullHandler())


class _SafeStreamHandler(logging.StreamHandler):
    """Console Windows thường là cp1252 nên không vẽ được tiếng Việt.

    Thay vì để UnicodeEncodeError làm hỏng cả dòng log, ghi bản đã escape.
    File log vẫn luôn là UTF-8 đầy đủ.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            super().emit(record)
        except UnicodeEncodeError:
            encoding = getattr(self.stream, "encoding", None) or "ascii"
            text = self.format(record).encode(encoding, "backslashreplace").decode(encoding)
            self.stream.write(text + self.terminator)
            self.flush()


class _OneLineFormatter(logging.Formatter):
    """Console chỉ cần một dòng; traceback đầy đủ đã nằm trong file log.

    Phải copy record thay vì sửa tại chỗ: cùng một LogRecord được truyền qua mọi
    handler, xóa exc_info trên bản gốc là file handler mất luôn traceback.
    """

    def format(self, record: logging.LogRecord) -> str:
        if record.exc_info or record.exc_text or record.stack_info:
            record = copy.copy(record)
            record.exc_info = None
            record.exc_text = None
            record.stack_info = None
        return super().format(record)


def get_logger(name: str | None = None) -> logging.Logger:
    """Trả logger con của package, ví dụ get_logger("api")."""
    if not name:
        return logging.getLogger(LOGGER_NAME)
    return logging.getLogger(f"{LOGGER_NAME}.{name}")


def setup_logging(
    *,
    level: str | int = "INFO",
    directory: str | Path = DEFAULT_LOG_DIR,
    filename: str = DEFAULT_LOG_FILE,
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 5,
    console: bool = True,
    force: bool = False,
) -> logging.Logger:
    """Gắn handler cho logger gốc của package. Gọi nhiều lần cũng an toàn."""
    logger = logging.getLogger(LOGGER_NAME)
    # NullHandler ở trên không tính là "đã cấu hình", nếu không thì lần gọi đầu
    # tiên sẽ tưởng xong rồi và chẳng bao giờ gắn được file handler.
    configured = [
        handler
        for handler in logger.handlers
        if not isinstance(handler, logging.NullHandler)
    ]
    if configured and not force:
        logger.setLevel(_coerce_level(level))
        return logger
    for handler in configured:
        logger.removeHandler(handler)
        handler.close()

    logger.setLevel(_coerce_level(level))
    logger.propagate = False
    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    log_dir = Path(directory)
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / filename,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    if console:
        stream_handler = _SafeStreamHandler(sys.stderr)
        stream_handler.setFormatter(_OneLineFormatter(LOG_FORMAT, datefmt=DATE_FORMAT))
        logger.addHandler(stream_handler)

    logger.debug("Logging sẵn sàng: %s (level=%s)", log_dir / filename, logging.getLevelName(logger.level))
    return logger


def log_file_path(
    directory: str | Path = DEFAULT_LOG_DIR,
    filename: str = DEFAULT_LOG_FILE,
) -> Path:
    return Path(directory) / filename


def _coerce_level(level: str | int) -> int:
    if isinstance(level, int):
        return level
    resolved = logging.getLevelName(str(level).upper())
    if not isinstance(resolved, int):
        raise ValueError(f"Log level không hợp lệ: {level}")
    return resolved
