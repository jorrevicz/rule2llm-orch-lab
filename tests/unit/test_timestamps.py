import re
from datetime import UTC, datetime, timedelta, timezone

import pytest

from shared.timestamps import elapsed_ms, parse_iso, to_iso, utc_now_iso

ISO_UTC_MS = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z"


def test_utc_now_iso_has_canonical_format():
    assert re.fullmatch(ISO_UTC_MS, utc_now_iso())


def test_to_iso_truncates_to_milliseconds():
    moment = datetime(2026, 8, 27, 12, 0, 0, 123456, tzinfo=UTC)

    assert to_iso(moment) == "2026-08-27T12:00:00.123Z"


def test_to_iso_converts_other_offsets_to_utc():
    moment = datetime(2026, 8, 27, 9, 0, 0, tzinfo=timezone(timedelta(hours=-3)))

    assert to_iso(moment) == "2026-08-27T12:00:00.000Z"


def test_to_iso_rejects_naive_datetime():
    with pytest.raises(ValueError):
        to_iso(datetime(2026, 8, 27, 12, 0, 0))


def test_parse_iso_round_trip():
    value = "2026-08-27T12:00:02.142Z"

    assert to_iso(parse_iso(value)) == value


def test_parse_iso_rejects_missing_timezone():
    with pytest.raises(ValueError):
        parse_iso("2026-08-27T12:00:02.142")


def test_elapsed_ms():
    start = parse_iso("2026-08-27T12:00:00.000Z")
    end = parse_iso("2026-08-27T12:00:02.054Z")

    assert elapsed_ms(start, end) == pytest.approx(2054.0)
