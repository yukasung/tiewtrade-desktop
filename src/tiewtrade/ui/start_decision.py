"""What pressing Start means, and which question it is worth putting first.

Start is one button with four meanings, because what is on screen decides
between them: a Session may already be running, a Position may be open, that
Position may already be at its target, and the form may or may not still
describe the Session it was filled from.

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
    # Nothing was asked for and nothing is at its target: run the Session on.
    RESUME_SESSION = "resume_session"
    # Starting sells at once, which is the decision actually being made.
    ASK_ABOUT_THE_SALE = "ask_about_the_sale"
    # A different Session was asked for, and one cannot be made beside an
    # open Position.
    ASK_ABOUT_REPLACING = "ask_about_replacing"


def course_before_start(
    *,
    position_open: bool,
    sells_on_start: bool,
    form_matches_session: bool,
) -> StartCourse:
    """Which of the four a Start is, in the order the answers rule each other.

    A question is put only where the answer changes something. A dialog about
    nothing is not free: it is what teaches a hand to dismiss the dialogs that
    are about something, and the one dismissed here closes a Position at the
    market price.
    """
    if not position_open:
        return StartCourse.REPLACE_SESSION
    if sells_on_start:
        return StartCourse.ASK_ABOUT_THE_SALE
    if form_matches_session:
        return StartCourse.RESUME_SESSION
    return StartCourse.ASK_ABOUT_REPLACING
