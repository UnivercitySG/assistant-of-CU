"""Tests for the SQLite persistence layer."""

from datetime import datetime, timezone

import pytest

from bot.database import Database
from bot.models import Survey


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    yield database
    database.close()


def test_save_and_get_roundtrip(db):
    deadline = datetime(2026, 6, 20, 18, 0, tzinfo=timezone.utc)
    survey = Survey(
        chat_id=-100123,
        title="Course feedback",
        link="https://forms.gle/abc",
        deadline=deadline,
        reminder_offsets=[1440, 60],
        created_by=42,
    )
    db.save_survey(survey)

    loaded = db.get_survey(-100123)
    assert loaded is not None
    assert loaded.title == "Course feedback"
    assert loaded.link == "https://forms.gle/abc"
    assert loaded.deadline == deadline
    assert loaded.reminder_offsets == [1440, 60]
    assert loaded.created_by == 42
    assert loaded.is_sent is False
    assert loaded.created_at is not None


def test_get_missing_returns_none(db):
    assert db.get_survey(999) is None


def test_save_is_upsert(db):
    db.save_survey(Survey(chat_id=1, title="A", link="https://a"))
    db.save_survey(Survey(chat_id=1, title="B", link="https://b", is_sent=True))
    loaded = db.get_survey(1)
    assert loaded.title == "B"
    assert loaded.is_sent is True
    assert len(db.list_surveys()) == 1


def test_delete(db):
    db.save_survey(Survey(chat_id=7, title="x", link="https://x"))
    assert db.delete_survey(7) is True
    assert db.get_survey(7) is None
    assert db.delete_survey(7) is False


def test_list_surveys(db):
    db.save_survey(Survey(chat_id=1, title="a", link="https://a"))
    db.save_survey(Survey(chat_id=2, title="b", link="https://b"))
    assert {s.chat_id for s in db.list_surveys()} == {1, 2}


def test_is_complete_property():
    assert Survey(chat_id=1).is_complete is False
    assert Survey(chat_id=1, title="t").is_complete is False
    assert Survey(chat_id=1, title="t", link="https://x").is_complete is True


def test_deadline_passed():
    past = datetime(2000, 1, 1, tzinfo=timezone.utc)
    future = datetime(2999, 1, 1, tzinfo=timezone.utc)
    assert Survey(chat_id=1, deadline=past).deadline_passed() is True
    assert Survey(chat_id=1, deadline=future).deadline_passed() is False
    assert Survey(chat_id=1, deadline=None).deadline_passed() is False
