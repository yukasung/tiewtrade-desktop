import pytest

from tiewtrade.ui.start_decision import StartCourse, course_before_start


def test_nothing_open_makes_the_session_the_form_describes() -> None:
    """With nothing held, Start is the plain thing it looks like."""
    assert (
        course_before_start(
            position_open=False,
            sells_on_start=True,
            sale_is_unknown=False,
        )
        is StartCourse.REPLACE_SESSION
    )


def test_a_position_wholly_at_its_target_is_asked_about_as_a_sale() -> None:
    """Running on would book every Entry at a price the market has left."""
    assert (
        course_before_start(
            position_open=True,
            sells_on_start=True,
            sale_is_unknown=False,
        )
        is StartCourse.ASK_ABOUT_THE_SALE
    )


def test_a_position_short_of_its_target_is_still_asked_about() -> None:
    """Resuming trades nothing, and is still done to an open Position.

    Passing without a question here read as the window having missed the
    Position altogether — the one press that hands it back to a Bot said
    nothing about it.
    """
    assert (
        course_before_start(
            position_open=True,
            sells_on_start=False,
            sale_is_unknown=False,
        )
        is StartCourse.ASK_ABOUT_THE_POSITION
    )


def test_a_sale_that_cannot_be_read_is_not_read_as_a_sale_of_everything() -> None:
    """Not knowing what a Start sells is not knowing that it sells the lot.

    The sale course withholds Start Bot, which leaves closing at the market
    price as the only way forward. Taking it on a guess is how a window that
    had simply not been told a price yet came to offer selling the whole
    Position and nothing else.
    """
    assert (
        course_before_start(
            position_open=True,
            sells_on_start=True,
            sale_is_unknown=True,
        )
        is StartCourse.ASK_ABOUT_THE_POSITION
    )


@pytest.mark.parametrize("sells_on_start", [True, False])
@pytest.mark.parametrize("sale_is_unknown", [True, False])
def test_a_question_is_put_for_every_position_and_no_other_start(
    sells_on_start: bool,
    sale_is_unknown: bool,
) -> None:
    """Every Start that meets a Position asks, and no other Start does.

    A dialog about nothing is not free: it is what teaches a hand to dismiss
    the dialogs that are about something, and the ones dismissed here close a
    Position at the market price.
    """
    asking = (StartCourse.ASK_ABOUT_THE_SALE, StartCourse.ASK_ABOUT_THE_POSITION)
    for position_open in (True, False):
        course = course_before_start(
            position_open=position_open,
            sells_on_start=sells_on_start,
            sale_is_unknown=sale_is_unknown,
        )
        assert (course in asking) is position_open
