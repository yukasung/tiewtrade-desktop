"""A connection notice a test can read, in place of one it would have to click."""


class RecordingConnectionNotice:
    """Stands in for the dialog, so a test reads what was said, not clicked."""

    def __init__(self) -> None:
        self.shown = 0
        self.hidden = 0

    def show(self) -> None:
        self.shown += 1

    def hide(self) -> None:
        self.hidden += 1
