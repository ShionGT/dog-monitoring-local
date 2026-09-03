"""Central logging configuration.

Uses Python's :mod:`logging` module. The application must never rely on
``print()`` for diagnostic output (see Project spec, section 25). A small custom
filter (:class:`SecretRedactor`) is installed so that webhook URLs, secrets and
passwords that might accidentally reach a log line are redacted.
"""
from __future__ import annotations

import logging
import os
import re
import sys

_DEFAULT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DEFAULT_DATEFMT = "%Y-%m-%d %H:%M:%S"

# Patterns whose values we never want to see in logs.
_SECRET_PATTERNS = [
    # Discord webhook URLs contain a long token; redact the whole URL.
    re.compile(r"https?://(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks/\S+", re.IGNORECASE),
    # generic secret-looking env values passed through messages
]
# A generic "KEY=value" pattern used to mask secret-ish assignments.
_KEY_VALUE = re.compile(
    r"((secret_key|admin_password|discord_webhook_url|token|password)\s*[=:]\s*)(\S+)",
    re.IGNORECASE,
)


class SecretRedactor(logging.Filter):
    """Remove secrets that might appear in a log record's message."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003 (mirror logging API)
        try:
            msg = record.getMessage()
        except Exception:  # pragma: no cover - defensive
            return True
        for pat in _SECRET_PATTERNS:
            msg = pat.sub("[REDACTED]", msg)
        msg = _KEY_VALUE.sub(r"\1[REDACTED]", msg)
        record.msg = msg
        record.args = ()
        return True


def configure_logging(level: str | int | None = None, stream=None) -> logging.Logger:
    """Configure the root logger once and return the app logger.

    Idempotent: repeated calls just re-apply the level without stacking handlers.
    """
    # Resolve the level to an int, tolerating string names and None.
    if level is None:
        level = os.environ.get("LOG_LEVEL", "INFO")
    resolved: int = logging.getLevelName(level) if isinstance(level, str) else int(level)

    root = logging.getLogger()
    already = any(isinstance(h, logging.StreamHandler) for h in root.handlers)
    if not already:
        handler = logging.StreamHandler(stream or sys.stdout)
        handler.setFormatter(logging.Formatter(_DEFAULT_FORMAT, _DEFAULT_DATEFMT))
        handler.addFilter(SecretRedactor())
        root.addHandler(handler)
    root.setLevel(resolved)
    return logging.getLogger("dog_monitoring")


def getLogger(name: str = "dog_monitoring") -> logging.Logger:
    """Convenience accessor: configure the root logger if never done, then
    return a namespaced child logger. Idempotent and safe to call from any
    module at import time.
    """
    # Ensure the root is configured at least once; if no handler exists yet,
    # wire up the default one so logs are visible.
    if not logging.getLogger().handlers:
        configure_logging()
    return logging.getLogger(name)
