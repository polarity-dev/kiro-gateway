"""
Tests for the kiro-tracker scheduled dispatcher (scripts/send_usage_scheduled.py).

Focus: the time-band scheduling logic that decides when a usage snapshot is due.
The dispatcher splits the day into three contiguous bands:

    night     : 18:00-06:59  (wraps past midnight)
    morning   : 07:00-11:59
    afternoon : 12:00-17:59

The night band straddles midnight and is keyed by its *band date* (the calendar
date on which it started at 18:00) so the 18:00-23:59 and 00:00-06:59 halves
share a single idempotency marker and never double-send across midnight.

These tests exercise band classification, band-date derivation, and the
per-band-date marker helpers. They are pure-logic tests: no network, no
subprocess, no real tracker API.
"""

from __future__ import annotations

import importlib.util
from datetime import date, datetime
from pathlib import Path

import pytest

# Load the dispatcher module directly from scripts/ by path, so the test does
# not depend on scripts/ being importable on sys.path.
_DISPATCHER_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "send_usage_scheduled.py"
)
_spec = importlib.util.spec_from_file_location("send_usage_scheduled", _DISPATCHER_PATH)
dispatcher = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dispatcher)


class TestCurrentSlotClassification:
    """Verify _current_slot maps every hour of the day to the right band."""

    @pytest.mark.parametrize(
        "hour,expected",
        [
            # night: 18:00-23:59 and 00:00-06:59
            (18, "night"),
            (19, "night"),
            (23, "night"),
            (0, "night"),
            (3, "night"),
            (6, "night"),
            # morning: 07:00-11:59
            (7, "morning"),
            (9, "morning"),
            (11, "morning"),
            # afternoon: 12:00-17:59
            (12, "afternoon"),
            (15, "afternoon"),
            (17, "afternoon"),
        ],
    )
    def test_each_hour_maps_to_expected_band(self, hour: int, expected: str) -> None:
        """
        What it does: classifies a representative minute in every hour.
        Purpose: the three bands must partition the full 24h with no gaps.
        """
        now = datetime(2026, 10, 1, hour, 30)
        assert dispatcher._current_slot(now).name == expected

    def test_boundary_minutes_are_exact(self) -> None:
        """
        What it does: checks the exact cardinal boundaries 07:00, 12:00, 18:00
        and the last minute before each.
        Purpose: a band starts inclusively at its hour and the previous band
        ends at :59 of the hour before.
        """
        assert dispatcher._current_slot(datetime(2026, 10, 1, 6, 59)).name == "night"
        assert dispatcher._current_slot(datetime(2026, 10, 1, 7, 0)).name == "morning"
        assert dispatcher._current_slot(datetime(2026, 10, 1, 11, 59)).name == "morning"
        assert dispatcher._current_slot(datetime(2026, 10, 1, 12, 0)).name == "afternoon"
        assert dispatcher._current_slot(datetime(2026, 10, 1, 17, 59)).name == "afternoon"
        assert dispatcher._current_slot(datetime(2026, 10, 1, 18, 0)).name == "night"

    def test_every_hour_resolves_to_some_band(self) -> None:
        """
        What it does: asserts no hour returns a falsy/None band.
        Purpose: guard against gaps if the band table is ever edited.
        """
        for hour in range(24):
            slot = dispatcher._current_slot(datetime(2026, 10, 1, hour, 0))
            assert slot is not None
            assert slot.name in {"night", "morning", "afternoon"}


class TestBandDate:
    """Verify _band_date keys the night band correctly across midnight."""

    def test_evening_half_of_night_uses_today(self) -> None:
        """
        What it does: 18:00-23:59 belongs to the night that starts today.
        Purpose: the evening half must key on the current date.
        """
        now = datetime(2026, 10, 1, 20, 0)
        slot = dispatcher._current_slot(now)
        assert slot.name == "night"
        assert dispatcher._band_date(now, slot) == date(2026, 10, 1)

    def test_early_morning_half_of_night_uses_previous_day(self) -> None:
        """
        What it does: 00:00-06:59 belongs to the night that started yesterday.
        Purpose: the post-midnight tail must key on the previous date so it
        shares a marker with the pre-midnight half.
        """
        now = datetime(2026, 10, 2, 2, 0)
        slot = dispatcher._current_slot(now)
        assert slot.name == "night"
        assert dispatcher._band_date(now, slot) == date(2026, 10, 1)

    def test_both_halves_of_one_night_share_band_date(self) -> None:
        """
        What it does: 23:00 on day N and 02:00 on day N+1 resolve to the same
        band date.
        Purpose: this is the core guarantee preventing a duplicate send across
        midnight.
        """
        before = datetime(2026, 10, 1, 23, 0)
        after = datetime(2026, 10, 2, 2, 0)
        bd_before = dispatcher._band_date(before, dispatcher._current_slot(before))
        bd_after = dispatcher._band_date(after, dispatcher._current_slot(after))
        assert bd_before == bd_after == date(2026, 10, 1)

    @pytest.mark.parametrize("hour", [7, 9, 11])
    def test_morning_band_date_is_today(self, hour: int) -> None:
        """Daytime bands always key on the current date, never shifted."""
        now = datetime(2026, 10, 1, hour, 0)
        slot = dispatcher._current_slot(now)
        assert dispatcher._band_date(now, slot) == date(2026, 10, 1)

    @pytest.mark.parametrize("hour", [12, 15, 17])
    def test_afternoon_band_date_is_today(self, hour: int) -> None:
        """Afternoon always keys on the current date."""
        now = datetime(2026, 10, 1, hour, 0)
        slot = dispatcher._current_slot(now)
        assert dispatcher._band_date(now, slot) == date(2026, 10, 1)


class TestMarkerHelpers:
    """Verify the per-band-date marker record/query helpers."""

    def test_record_and_query_roundtrip(self) -> None:
        """A recorded slot reads back as sent for the same band date."""
        sends: dict = {}
        bd = date(2026, 10, 1)
        assert dispatcher._already_sent(sends, bd, "night") is False
        dispatcher._record_send(sends, bd, "night")
        assert dispatcher._already_sent(sends, bd, "night") is True

    def test_record_is_idempotent(self) -> None:
        """Recording the same slot twice does not duplicate the marker."""
        sends: dict = {}
        bd = date(2026, 10, 1)
        dispatcher._record_send(sends, bd, "morning")
        dispatcher._record_send(sends, bd, "morning")
        assert sends[bd.isoformat()] == ["morning"]

    def test_distinct_bands_tracked_independently(self) -> None:
        """Different bands on the same date have independent markers."""
        sends: dict = {}
        bd = date(2026, 10, 1)
        dispatcher._record_send(sends, bd, "morning")
        assert dispatcher._already_sent(sends, bd, "afternoon") is False
        dispatcher._record_send(sends, bd, "afternoon")
        assert set(sends[bd.isoformat()]) == {"morning", "afternoon"}

    def test_same_band_different_band_dates_are_separate(self) -> None:
        """
        What it does: 'night' recorded for Oct 1 does not mark Oct 2's night.
        Purpose: each night is its own send; consecutive nights must not alias.
        """
        sends: dict = {}
        dispatcher._record_send(sends, date(2026, 10, 1), "night")
        assert dispatcher._already_sent(sends, date(2026, 10, 2), "night") is False


class TestStatePersistence:
    """Verify _load_state / _save_state keep only the current band date."""

    @pytest.fixture
    def temp_state(self, tmp_path, monkeypatch):
        """Point the dispatcher's state file at a temp location."""
        state_dir = tmp_path / "kiro-tracker"
        state_file = state_dir / "send_state.json"
        monkeypatch.setattr(dispatcher, "_STATE_DIR", state_dir)
        monkeypatch.setattr(dispatcher, "_STATE_FILE", state_file)
        return state_file

    def test_save_then_load_roundtrip(self, temp_state) -> None:
        """A saved marker set loads back for the same band date."""
        bd = date(2026, 10, 1)
        dispatcher._save_state({bd.isoformat(): ["night"]})
        loaded = dispatcher._load_state(bd)
        assert loaded == {bd.isoformat(): ["night"]}

    def test_load_drops_other_band_dates(self, temp_state) -> None:
        """
        What it does: a state file holding several dates returns only the
        requested band date on load.
        Purpose: the file must never accumulate history.
        """
        dispatcher._save_state(
            {
                "2026-09-30": ["night", "morning"],
                "2026-10-01": ["afternoon"],
            }
        )
        loaded = dispatcher._load_state(date(2026, 10, 1))
        assert loaded == {"2026-10-01": ["afternoon"]}

    def test_load_missing_file_is_empty(self, temp_state) -> None:
        """No state file yields an empty mapping, not an error."""
        assert dispatcher._load_state(date(2026, 10, 1)) == {}

    def test_load_corrupt_file_is_empty(self, temp_state) -> None:
        """
        What it does: a non-JSON state file loads as empty.
        Purpose: a corrupt file must never wedge the scheduler.
        """
        temp_state.parent.mkdir(parents=True, exist_ok=True)
        temp_state.write_text("{ not json", encoding="utf-8")
        assert dispatcher._load_state(date(2026, 10, 1)) == {}

    def test_load_ignores_non_list_entry(self, temp_state) -> None:
        """A malformed (non-list) entry for the date is treated as empty."""
        temp_state.parent.mkdir(parents=True, exist_ok=True)
        temp_state.write_text(
            '{"sends": {"2026-10-01": "night"}}', encoding="utf-8"
        )
        assert dispatcher._load_state(date(2026, 10, 1)) == {}


class TestNightNoDoubleSendAcrossMidnight:
    """
    End-to-end-ish check of the core guarantee, at the helper level: a night
    send recorded at 23:00 is still seen as sent at 02:00 the next day.
    """

    def test_night_send_persists_across_midnight(self, tmp_path, monkeypatch) -> None:
        """
        What it does: records 'night' using the 23:00 band date, then checks
        the 02:00 band date sees it as already sent.
        Purpose: proves 18:00-06:59 is one logical slot for idempotency.
        """
        state_dir = tmp_path / "kiro-tracker"
        monkeypatch.setattr(dispatcher, "_STATE_DIR", state_dir)
        monkeypatch.setattr(dispatcher, "_STATE_FILE", state_dir / "send_state.json")

        before = datetime(2026, 10, 1, 23, 0)
        slot_before = dispatcher._current_slot(before)
        bd_before = dispatcher._band_date(before, slot_before)
        sends = dispatcher._load_state(bd_before)
        dispatcher._record_send(sends, bd_before, slot_before.name)
        dispatcher._save_state(sends)

        after = datetime(2026, 10, 2, 2, 0)
        slot_after = dispatcher._current_slot(after)
        bd_after = dispatcher._band_date(after, slot_after)
        sends_after = dispatcher._load_state(bd_after)
        assert dispatcher._already_sent(sends_after, bd_after, slot_after.name) is True
