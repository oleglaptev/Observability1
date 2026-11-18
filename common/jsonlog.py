# Observability/common/jsonlog.py
import json
import sys
from datetime import datetime, timezone

_service = "unknown"


def init(service: str) -> None:
    """Вызывается один раз при старте: 'api' или 'worker'."""
    global _service
    _service = service


def log_event(event: str, level: str = "INFO", **fields) -> None:
    """Одна запись = один JSON-объект в stdout."""
    record = {
        "timestamp": datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "level": level,
        "service": _service,
        "event": event,
    }
    record.update({k: v for k, v in fields.items() if v is not None})
    sys.stdout.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()