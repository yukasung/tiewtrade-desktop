"""What pressing Start means, and which question it is worth putting first.

Start is one button with three meanings, because what is on screen decides
between them: a Position may be open, and that Position may already be at its
target.

The rule is worth one place of its own. Spread across the handlers that act on
it, it has twice been half-applied — once asking two questions in a row for one
decision, and once asking none where the answer sold a Position. Stated here it
can be read in full and tested without a window to click.
"""

from enum import StrEnum


class StartCourse(StrEnum):
    """What a Start does, once what is on screen has been read."""

    # Nothing is open to lose, so the form's values make a Session.
    REPLACE_SESSION = "replace_session"
    # Every open Entry is at its target, so running the Session on would sell
    # the lot there. Closing at the price now is the only trade offered.
    ASK_ABOUT_THE_SALE = "ask_about_the_sale"
    # A Position is open. Starting puts it back under a Bot, closing sells
    # it, and neither is something to do to it without asking.
    ASK_ABOUT_THE_POSITION = "ask_about_the_position"


def course_before_start(
    *,
    position_open: bool,
    sells_on_start: bool,
    leaves_entries_holding: bool,
) -> StartCourse:
    """Which of the three a Start is, in the order the answers rule each other.

    Every Start that meets an open Position asks, and the question names what
    its answers do to that Position. Resuming trades nothing, which is why it
    used to pass without one — but it is still the press that puts an open
    Position back under a Bot, and saying nothing about that read as the
    window having missed it.

    Only a Start that sells the *whole* Position at targets the price has left
    takes the course that will not offer to run the Session on. Where one
    Entry has reached its target and another has not, refusing to start made
    closing at the market price the one way forward — and that sells the Entry
    still holding along with it.

    What the form says decides nothing here. With a question put either way,
    an edited form changes only which answer discards it.
    """
    if not position_open:
        return StartCourse.REPLACE_SESSION
    if sells_on_start and not leaves_entries_holding:
        return StartCourse.ASK_ABOUT_THE_SALE
    return StartCourse.ASK_ABOUT_THE_POSITION
