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

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
