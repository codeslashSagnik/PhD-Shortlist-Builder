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


def _build_root_logger() -> logging.Logger:
    """Build or retrieve the root phd_builder logger with correct handlers."""
    root = logging.getLogger("phd_builder")
    root.setLevel(logging.DEBUG)

    # Check if a FileHandler already pointing to our log file exists
    has_file_handler = any(
        isinstance(h, logging.FileHandler) and h.baseFilename == str(_LOG_FILE.resolve())
        for h in root.handlers
    )
    has_console_handler = any(isinstance(h, logging.StreamHandler) for h in root.handlers)

    if not has_file_handler:
        fh = logging.FileHandler(_LOG_FILE, encoding="utf-8")
        fh.setFormatter(_FORMATTER)
        fh.setLevel(logging.DEBUG)
        root.addHandler(fh)

    if not has_console_handler:
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(_FORMATTER)
        ch.setLevel(logging.INFO)
        root.addHandler(ch)

    return root


_root = _build_root_logger()


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

