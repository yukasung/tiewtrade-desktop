"""What a Qt test needs settled before the first QApplication is built.

CI runs these headless and the tests are written for that: they hover a
chart, tab through a filter row, and one of them names the platform it
expects outright. Run against a real window server instead — a developer's
own machine — and the window manager answers those differently, so seven
tests fail for where they are running rather than for what they cover.

Qt reads this when an application is constructed rather than when it is
imported, and pytest-qt builds one no earlier than the first test, so
setting it here is early enough.

setdefault rather than an assignment, so a developer who wants to watch a
real window can still ask for one.
"""

import logging
import os
from collections.abc import Iterator

import pytest

from tiewtrade.desktop_logging import HANDLER_NAME, PACKAGE_LOGGER

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def no_leftover_log_file_handler() -> Iterator[None]:
    """Logging outlives the test that configured it; the log file must not.

    A test that composes the desktop points this package's logger at a file
    under its own tmp_path. That handler would still be attached after the
    directory is gone, so every later test's events would be written into a
    path that no longer belongs to anything.
    """
    yield
    logger = logging.getLogger(PACKAGE_LOGGER)
    for handler in list(logger.handlers):
        if handler.name == HANDLER_NAME:
            logger.removeHandler(handler)
            handler.close()
