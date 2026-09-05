"""What the toolbar says this installation is, and what it says by default.

The whole title is the trader's, not a remark appended to a fixed name: an
installation looking after someone else's Bot may have no reason to say
"TiewTrade" at all, and one running on a VPS beside four others has every
reason to say something else. So the application's own name is only where
the title starts — the value a Settings page offers when nothing has been
stored, and the value the toolbar falls back to when the title is emptied.

Shared by the page the title is typed on and the toolbar that wears it,
which may not import each other: the toolbar is built around the page.
"""

DEFAULT_BOT_TITLE = "TiewTrade"
"""The name of the application, which is what an untitled installation is."""


def bot_title_or_default(stored: str) -> str:
    """What to show for a stored title, which may never have been set."""
    return stored.strip() or DEFAULT_BOT_TITLE
