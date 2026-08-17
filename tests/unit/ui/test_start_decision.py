import pytest

from tiewtrade.ui.start_decision import StartCourse, course_before_start


def test_nothing_open_makes_the_session_the_form_describes() -> None:
    """There is no Position to lose, so Start is only "make this Session"."""
    assert (
        course_before_start(
            position_open=False,
            sells_on_start=True,
            form_matches_session=False,
        )
        is StartCourse.REPLACE_SESSION
    )


def test_a_position_at_its_target_is_asked_about_before_anything_else() -> None:
    """Starting sells it at once, which outranks what the form happens to say."""
    for form_matches in (True, False):
        assert (
            course_before_start(
                position_open=True,
                sells_on_start=True,
                form_matches_session=form_matches,
            )
            is StartCourse.ASK_ABOUT_THE_SALE
        )


def test_an_untouched_form_runs_the_session_on_without_a_question() -> None:
    """Nothing was asked for and nothing is at its target: nothing to ask.

    A dialog here would be about nothing, which is what teaches a hand to
    dismiss the dialogs that are about something.
    """
    assert (
        course_before_start(
            position_open=True,
            sells_on_start=False,
            form_matches_session=True,
        )
        is StartCourse.RESUME_SESSION
    )


def test_an_edited_form_is_asked_about_because_it_closes_the_position() -> None:
    """A different Session cannot be made while this one holds a Position."""
    assert (
        course_before_start(
            position_open=True,
            sells_on_start=False,
            form_matches_session=False,
        )
        is StartCourse.ASK_ABOUT_REPLACING
    )


@pytest.mark.parametrize("sells_on_start", [True, False])
@pytest.mark.parametrize("form_matches_session", [True, False])
def test_a_question_is_put_only_where_the_answer_changes_something(
    sells_on_start: bool, form_matches_session: bool
) -> None:
    """Every course that asks has a real fork behind it, and no other does."""
    course = course_before_start(
        position_open=True,
        sells_on_start=sells_on_start,
        form_matches_session=form_matches_session,
    )
    asks = course in (StartCourse.ASK_ABOUT_THE_SALE, StartCourse.ASK_ABOUT_REPLACING)

    assert asks == (sells_on_start or not form_matches_session)
