"""
utils/logger.py

Centralised logging setup for the entire PhD Shortlist Builder pipeline.
Every module imports get_logger() from here — never configures logging itself.

Log format: timestamp | level | module | message
Outputs to:  logs/pipeline_<run_id>.log  AND  stdout
"""

import logging
import sys
from pathlib import Path
from datetime import datetime


_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
_LOG_DIR.mkdir(exist_ok=True)

# One log file per pipeline run, named by timestamp
_RUN_TIMESTAMP = datetime.now().strftime("%Y%m%d_%H%M%S")
_LOG_FILE = _LOG_DIR / f"pipeline_{_RUN_TIMESTAMP}.log"

_FORMATTER = logging.Formatter(
    fmt="%(asctime)s | %(levelname)-8s | %(name)-30s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

_file_handler = logging.FileHandler(_LOG_FILE, encoding="utf-8")
_file_handler.setFormatter(_FORMATTER)
_file_handler.setLevel(logging.DEBUG)

_console_handler = logging.StreamHandler(sys.stdout)
_console_handler.setFormatter(_FORMATTER)
_console_handler.setLevel(logging.INFO)

# Root logger — all child loggers inherit handlers
_root = logging.getLogger("phd_builder")
_root.setLevel(logging.DEBUG)
if not _root.handlers:
    _root.addHandler(_file_handler)
    _root.addHandler(_console_handler)


def get_logger(name: str) -> logging.Logger:
    """
    Return a child logger scoped to the given module name.

    Usage:
        from utils.logger import get_logger
        log = get_logger(__name__)
        log.info("Stage 1 started")
    """
    return logging.getLogger(f"phd_builder.{name}")


def get_log_file_path() -> Path:
    """Return the path to the current run's log file (useful for printing at end of run)."""
    return _LOG_FILE
