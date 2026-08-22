from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from tiewtrade.desktop_logging import PACKAGE_LOGGER, configure_desktop_logging


@pytest.fixture(autouse=True)
def restore_package_logger() -> Iterator[None]:
    """Logging is process-wide, so a test that configures it puts it back."""
    logger = logging.getLogger(PACKAGE_LOGGER)
    handlers = list(logger.handlers)
    level = logger.level
    try:
        yield
    finally:
        for handler in list(logger.handlers):
            if handler not in handlers:
                logger.removeHandler(handler)
                handler.close()
        logger.setLevel(level)


def test_events_reach_a_file_in_the_named_directory(tmp_path: Path) -> None:
    log_path = configure_desktop_logging(tmp_path / "TiewTrade")

    logging.getLogger("tiewtrade.market_data.runtime").warning("market_data.stale")

    assert log_path == tmp_path / "TiewTrade" / "tiewtrade.log"
    line = log_path.read_text(encoding="utf-8").splitlines()[-1]
    assert "WARNING" in line
    assert "tiewtrade.market_data.runtime" in line
    assert "market_data.stale" in line


def test_structured_event_fields_are_written_beside_the_event_name(
    tmp_path: Path,
) -> None:
    """The fields are the whole point: a reconnect says which attempt it is.

    MarketDataRuntimeLog states everything it knows through `extra`, and a
    formatter that renders only the message throws all of it away — leaving
    a file that says a backfill failed without saying what it asked for.
    """
    log_path = configure_desktop_logging(tmp_path)

    logging.getLogger("tiewtrade.market_data.runtime").error(
        "market_data.backfill.failed",
        extra={
            "event_name": "market_data.backfill.failed",
            "symbol": "BTCUSDT",
            "reason": "source_error",
            "failure_kind": None,
        },
    )

    line = log_path.read_text(encoding="utf-8").splitlines()[-1]
    assert "reason=source_error" in line
    assert "symbol=BTCUSDT" in line
    assert "failure_kind=None" in line


def test_no_file_is_opened_before_the_first_record(tmp_path: Path) -> None:
    """Composing the desktop must not touch the disk; only running it may."""
    directory = tmp_path / "TiewTrade"

    log_path = configure_desktop_logging(directory)

    assert not directory.exists()

    logging.getLogger("tiewtrade").info("first")

    assert log_path.exists()


def test_configuring_twice_leaves_one_handler(tmp_path: Path) -> None:
    configure_desktop_logging(tmp_path)
    configure_desktop_logging(tmp_path)

    logging.getLogger("tiewtrade").info("once")

    logger = logging.getLogger(PACKAGE_LOGGER)
    assert len(logger.handlers) == 1
    assert (tmp_path / "tiewtrade.log").read_text(encoding="utf-8").count("once") == 1


def test_info_events_are_kept(tmp_path: Path) -> None:
    log_path = configure_desktop_logging(tmp_path)

    logging.getLogger("tiewtrade.market_data.runtime").info(
        "market_data.backfill.completed"
    )

    assert "market_data.backfill.completed" in log_path.read_text(encoding="utf-8")
