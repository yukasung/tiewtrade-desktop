import pytest

from tiewtrade.ui.start_decision import StartCourse, course_before_start


def test_nothing_open_makes_the_session_the_form_describes() -> None:
    """With nothing held, Start is the plain thing it looks like."""
    assert (
        course_before_start(
            position_open=False,
            sells_on_start=True,
            leaves_entries_holding=False,
        )
        is StartCourse.REPLACE_SESSION
    )


def test_a_position_wholly_at_its_target_is_asked_about_as_a_sale() -> None:
    """Running on would book every Entry at a price the market has left."""
    assert (
        course_before_start(
            position_open=True,
            sells_on_start=True,
            leaves_entries_holding=False,
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
            leaves_entries_holding=True,
        )
        is StartCourse.ASK_ABOUT_THE_POSITION
    )


def test_a_start_that_sells_only_part_of_it_still_offers_to_start() -> None:
    """One Entry at its target must not decide for the ones still holding.

    On the sale course the only answer that goes forward closes at the market
    price, which sells the Entries that had reached nothing along with the one
    that had. Leaving something behind is what keeps Start Bot on offer.
    """
    assert (
        course_before_start(
            position_open=True,
            sells_on_start=True,
            leaves_entries_holding=True,
        )
        is StartCourse.ASK_ABOUT_THE_POSITION
    )


@pytest.mark.parametrize("sells_on_start", [True, False])
@pytest.mark.parametrize("leaves_entries_holding", [True, False])
def test_a_question_is_put_for_every_position_and_no_other_start(
    sells_on_start: bool,
    leaves_entries_holding: bool,
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
            leaves_entries_holding=leaves_entries_holding,
        )
        assert (course in asking) is position_open
