"""What the OS window title says this installation is, and what it says by default.

The whole title is the trader's, not a remark appended to a fixed name: an
installation looking after someone else's Bot may have no reason to say
"TiewTrade" at all, and one running on a VPS beside four others has every
reason to say something else — that is what tells them apart in the OS
window list or dock/taskbar. So the application's own name is only where the
title starts — the value a Settings page offers when nothing has been
stored, and the value the window falls back to when the title is emptied.
The toolbar inside the window keeps naming the application, since a trader
already knows which window they are looking at once it is in front of them.

Shared by the page the title is typed on and the window that wears it, which
may not import each other: the window is built around the page.
"""

DEFAULT_BOT_TITLE = "TiewTrade"
"""The name of the application, which is what an untitled installation is."""


def bot_title_or_default(stored: str) -> str:
    """What to show for a stored title, which may never have been set."""
    return stored.strip() or DEFAULT_BOT_TITLE
