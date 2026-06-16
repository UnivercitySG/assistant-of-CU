"""Tests for parsing/formatting helpers."""

from datetime import timezone
from zoneinfo import ZoneInfo

import pytest

from bot.utils import (
    ParseError,
    format_offset,
    is_valid_url,
    parse_deadline,
    parse_reminder_offsets,
)

UTC = ZoneInfo("UTC")


def test_parse_deadline_iso_in_utc():
    dt = parse_deadline("2026-06-20 18:00", UTC)
    assert dt.tzinfo == timezone.utc
    assert (dt.year, dt.month, dt.day, dt.hour, dt.minute) == (2026, 6, 20, 18, 0)


def test_parse_deadline_converts_local_to_utc():
    tz = ZoneInfo("Asia/Almaty")  # UTC+5, no DST
    dt = parse_deadline("2026-06-20 18:00", tz)
    assert dt.hour == 13  # 18:00 local -> 13:00 UTC
    assert dt.tzinfo == timezone.utc


def test_parse_deadline_date_only_is_midnight():
    dt = parse_deadline("2026-06-20", UTC)
    assert (dt.hour, dt.minute) == (0, 0)


def test_parse_deadline_alternate_format():
    dt = parse_deadline("20.06.2026 09:30", UTC)
    assert (dt.day, dt.month, dt.hour, dt.minute) == (20, 6, 9, 30)


def test_parse_deadline_invalid():
    with pytest.raises(ParseError):
        parse_deadline("not a date", UTC)


def test_parse_reminder_offsets_sorted_desc_and_unique():
    assert parse_reminder_offsets("30m, 2h, 1d, 2h") == [1440, 120, 30]


def test_parse_reminder_offsets_units():
    assert parse_reminder_offsets("1w") == [60 * 24 * 7]
    assert parse_reminder_offsets("90 minutes") == [90]


def test_parse_reminder_offsets_invalid_unit():
    with pytest.raises(ParseError):
        parse_reminder_offsets("5 fortnights")


def test_parse_reminder_offsets_empty():
    with pytest.raises(ParseError):
        parse_reminder_offsets("   ")


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://forms.gle/abc", True),
        ("http://example.com/x", True),
        ("ftp://example.com", False),
        ("example.com", False),
        ("", False),
    ],
)
def test_is_valid_url(url, expected):
    assert is_valid_url(url) is expected


def test_format_offset():
    assert format_offset(1440) == "1d"
    assert format_offset(90) == "1h 30m"
    assert format_offset(60 * 24 * 7 + 60) == "1w 1h"
    assert format_offset(0) == "0m"
