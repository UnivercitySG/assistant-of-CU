"""Tests for the SQLite persistence layer."""

from datetime import datetime, timedelta, timezone

import pytest

from bot.database import Database
from bot.models import Survey


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    yield database
    database.close()


# -- groups ----------------------------------------------------------------


def test_group_upsert_and_list(db):
    db.upsert_group(-100, "Math 101")
    db.upsert_group(-200, "Physics")
    db.upsert_group(-100, "Math 101 (renamed)")  # update keeps a single row
    groups = db.list_groups()
    assert {g.chat_id for g in groups} == {-100, -200}
    assert db.get_group(-100).title == "Math 101 (renamed)"


def test_group_remove(db):
    db.upsert_group(-100, "Math")
    db.remove_group(-100)
    assert db.get_group(-100) is None
    assert db.list_groups() == []


# -- surveys ---------------------------------------------------------------


def test_save_assigns_id_and_roundtrips(db):
    deadline = datetime(2026, 6, 20, 18, 0, tzinfo=timezone.utc)
    survey = Survey(
        group_id=-100123,
        title="Course feedback",
        link="https://forms.gle/abc",
        deadline=deadline,
        reminder_offsets=[1440, 60],
        created_by=42,
    )
    saved = db.save_survey(survey)
    assert saved.id is not None

    loaded = db.get_survey(saved.id)
    assert loaded.group_id == -100123
    assert loaded.title == "Course feedback"
    assert loaded.link == "https://forms.gle/abc"
    assert loaded.deadline == deadline
    assert loaded.reminder_offsets == [1440, 60]
    assert loaded.created_by == 42
    assert loaded.is_sent is False
    assert loaded.created_at is not None


def test_get_missing_returns_none(db):
    assert db.get_survey(999) is None


def test_update_existing_survey(db):
    survey = db.save_survey(Survey(group_id=1, title="A", link="https://a"))
    survey.title = "B"
    survey.is_sent = True
    db.save_survey(survey)
    loaded = db.get_survey(survey.id)
    assert loaded.title == "B"
    assert loaded.is_sent is True
    assert len(db.list_all_surveys()) == 1


def test_multiple_surveys_per_group(db):
    db.save_survey(Survey(group_id=1, title="a", link="https://a"))
    db.save_survey(Survey(group_id=1, title="b", link="https://b"))
    db.save_survey(Survey(group_id=2, title="c", link="https://c"))
    assert len(db.list_surveys_for_group(1)) == 2
    assert len(db.list_surveys_for_group(2)) == 1
    assert len(db.list_all_surveys()) == 3


def test_delete(db):
    survey = db.save_survey(Survey(group_id=7, title="x", link="https://x"))
    assert db.delete_survey(survey.id) is True
    assert db.get_survey(survey.id) is None
    assert db.delete_survey(survey.id) is False


# -- model behaviour -------------------------------------------------------


def test_is_complete_property():
    assert Survey(group_id=1).is_complete is False
    assert Survey(group_id=1, title="t").is_complete is False
    assert Survey(group_id=1, title="t", link="https://x").is_complete is True


def test_deadline_passed():
    past = datetime(2000, 1, 1, tzinfo=timezone.utc)
    future = datetime(2999, 1, 1, tzinfo=timezone.utc)
    assert Survey(group_id=1, deadline=past).deadline_passed() is True
    assert Survey(group_id=1, deadline=future).deadline_passed() is False
    assert Survey(group_id=1, deadline=None).deadline_passed() is False


def test_is_active():
    future = datetime.now(timezone.utc) + timedelta(days=1)
    past = datetime.now(timezone.utc) - timedelta(days=1)
    base = dict(group_id=1, title="t", link="https://x")
    # Active: sent, complete, future (or no) deadline.
    assert Survey(**base, is_sent=True, deadline=future).is_active is True
    assert Survey(**base, is_sent=True, deadline=None).is_active is True
    # Inactive: not sent, or past deadline, or incomplete.
    assert Survey(**base, is_sent=False, deadline=future).is_active is False
    assert Survey(**base, is_sent=True, deadline=past).is_active is False
    assert Survey(group_id=1, is_sent=True).is_active is False
