"""One JSON object per log line, so the hosting log viewer can be searched by field."""

import json
import logging
import sys
import threading
from collections import deque
from datetime import UTC, datetime
from typing import Any


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            entry.update(fields)
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


class RingBufferHandler(logging.Handler):
    """Keeps the last `capacity` log entries in memory for GET /ops/logs."""

    def __init__(self, capacity: int = 2000) -> None:
        super().__init__()
        self.setFormatter(JsonFormatter())
        self._entries: deque[dict[str, Any]] = deque(maxlen=capacity)
        self._entries_lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            entry = json.loads(self.format(record))
        except Exception:
            self.handleError(record)
            return
        with self._entries_lock:
            self._entries.append(entry)

    def tail(self, limit: int) -> list[dict[str, Any]]:
        with self._entries_lock:
            return list(self._entries)[-limit:] if limit > 0 else []


def ring_buffer() -> RingBufferHandler | None:
    return next((h for h in logging.getLogger().handlers if isinstance(h, RingBufferHandler)), None)


def configure(level: str) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler, RingBufferHandler()]
    root.setLevel(level)
    # uvicorn's own access log duplicates our request log line
    logging.getLogger("uvicorn.access").disabled = True
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers[:] = []
        logging.getLogger(name).propagate = True
    logging.getLogger("httpx").setLevel(logging.WARNING)
