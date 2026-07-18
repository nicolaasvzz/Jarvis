"""Tests for the logging module."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from jarvis.config.schema import LoggingConfig
from jarvis.logging import get_logger, log_context, setup_logging


@pytest.fixture(autouse=True)
def _reset_jarvis_logger() -> Any:
    """Remove handlers after each test so state never leaks between tests."""
    yield
    logger = logging.getLogger("jarvis")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


def configure(tmp_path: Path, **overrides: Any) -> Path:
    config = LoggingConfig(directory=tmp_path, console=False, **overrides)
    return setup_logging(config)


def read_json_lines(log_file: Path) -> list[dict[str, Any]]:
    for handler in logging.getLogger("jarvis").handlers:
        handler.flush()
    lines = log_file.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def test_records_are_written_as_parseable_json(tmp_path: Path) -> None:
    log_file = configure(tmp_path)
    logger = get_logger("test")
    logger.info("hello %s", "world", extra={"action": "greet"})

    (record,) = read_json_lines(log_file)
    assert record["message"] == "hello world"
    assert record["level"] == "INFO"
    assert record["logger"] == "jarvis.test"
    assert record["extra"]["action"] == "greet"
    assert "timestamp" in record


def test_context_fields_are_attached_and_scoped(tmp_path: Path) -> None:
    log_file = configure(tmp_path)
    logger = get_logger("test")

    with log_context(task_id="task-1"):
        logger.info("inside")
        with log_context(tool="file_manager"):
            logger.info("nested")
    logger.info("outside")

    inside, nested, outside = read_json_lines(log_file)
    assert inside["context"] == {"task_id": "task-1"}
    assert nested["context"] == {"task_id": "task-1", "tool": "file_manager"}
    assert "context" not in outside


def test_exceptions_include_traceback(tmp_path: Path) -> None:
    log_file = configure(tmp_path)
    logger = get_logger("test")

    try:
        raise ValueError("boom")
    except ValueError:
        logger.exception("task failed")

    (record,) = read_json_lines(log_file)
    assert record["level"] == "ERROR"
    assert "ValueError: boom" in record["exception"]


def test_level_filtering(tmp_path: Path) -> None:
    log_file = configure(tmp_path, level="WARNING")
    logger = get_logger("test")
    logger.info("dropped")
    logger.warning("kept")

    (record,) = read_json_lines(log_file)
    assert record["message"] == "kept"


def test_setup_is_idempotent(tmp_path: Path) -> None:
    configure(tmp_path)
    log_file = configure(tmp_path)  # reconfigure — must not duplicate handlers
    get_logger("test").info("once")

    records = read_json_lines(log_file)
    assert len(records) == 1


def test_get_logger_namespacing() -> None:
    assert get_logger("planner").name == "jarvis.planner"
    assert get_logger("jarvis.planner").name == "jarvis.planner"
    assert get_logger("jarvis").name == "jarvis"
