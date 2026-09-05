from pathlib import Path

import pytest

from tiewtrade.integrations.preferences.bot_title import (
    BOT_TITLE_FILE_NAME,
    MAX_BOT_TITLE_LENGTH,
    BotTitleError,
    read_bot_title,
    save_bot_title,
)


def test_an_untitled_installation_reads_as_no_title(tmp_path: Path) -> None:
    assert read_bot_title(tmp_path) == ""


def test_a_saved_title_is_read_back(tmp_path: Path) -> None:
    save_bot_title(tmp_path, "Khun Somchai")

    assert read_bot_title(tmp_path) == "Khun Somchai"


def test_the_title_outlives_the_process_that_wrote_it(tmp_path: Path) -> None:
    """A remark written once is read at every launch afterwards."""
    save_bot_title(tmp_path, "VPS-02")

    assert (tmp_path / BOT_TITLE_FILE_NAME).read_text(encoding="utf-8") == "VPS-02\n"


def test_the_directory_is_made_when_nothing_has_been_stored_there_yet(
    tmp_path: Path,
) -> None:
    """Settings can be saved before the database has been created."""
    directory = tmp_path / "not-there-yet"

    save_bot_title(directory, "Khun Somchai")

    assert read_bot_title(directory) == "Khun Somchai"


def test_a_title_is_kept_to_one_line_of_a_length_a_toolbar_can_hold(
    tmp_path: Path,
) -> None:
    save_bot_title(tmp_path, "  Khun\n Somchai  " + "x" * MAX_BOT_TITLE_LENGTH)

    title = read_bot_title(tmp_path)
    assert len(title) == MAX_BOT_TITLE_LENGTH
    assert title.startswith("Khun Somchai x")


def test_emptying_the_title_takes_the_file_away(tmp_path: Path) -> None:
    save_bot_title(tmp_path, "Khun Somchai")

    save_bot_title(tmp_path, "   ")

    assert read_bot_title(tmp_path) == ""
    assert not (tmp_path / BOT_TITLE_FILE_NAME).exists()


def test_a_file_that_will_not_answer_reads_as_no_title(tmp_path: Path) -> None:
    """A title nobody can read is a plain toolbar, not a window that fails."""
    unreadable = tmp_path / BOT_TITLE_FILE_NAME
    unreadable.mkdir()

    assert read_bot_title(tmp_path) == ""


def test_a_title_that_cannot_be_kept_says_so(tmp_path: Path) -> None:
    directory = tmp_path / "taken"
    directory.write_text("not a directory", encoding="utf-8")

    with pytest.raises(BotTitleError):
        save_bot_title(directory, "Khun Somchai")
