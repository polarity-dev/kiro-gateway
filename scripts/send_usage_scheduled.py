#!/usr/bin/env python3
"""
Scheduled dispatcher for the kiro-tracker usage snapshot.

This wraps ``scripts/send_usage.py`` with a time-window scheduler so it can be
invoked frequently (e.g. once a minute from the SwiftBar widget) while only
actually sending a snapshot around three target times a day: night, morning,
and afternoon.

Why a dispatcher instead of a cron/launchd entry:
    The SwiftBar widget already ticks every 60s. Reusing that tick means no
    extra scheduling machinery and natural catch-up: the day is split into three
    bands and each band gets exactly one send, on the first tick inside it.

Bands (local time): the day is partitioned into three contiguous bands by the
cardinal times 07:00, 12:00 and 18:00. There are no dead gaps -- every moment
of the day falls into exactly one band, so a band's send fires at the first
tick inside it whatever the exact minute, not only on the hour.

    night     : 18:00-06:59  (wraps past midnight)
    morning   : 07:00-11:59
    afternoon : 12:00-17:59

The ``night`` band straddles midnight, so it is keyed by its *band date*: the
calendar date on which the band started (18:00). The hours 00:00-06:59 belong
to the night that began at 18:00 the previous day, so a session at 23:00 and
one at 02:00 share a single marker and never double-send across midnight.

The send fires on the *first* tick inside a band whose marker (for that band's
band date) has not yet been recorded. A per-band marker guarantees exactly one
send per band per band-date (idempotent across the 60s ticks). If the Mac is
off for an entire band, that band is simply skipped -- we never batch multiple
sends at once.

State (marker) file:
    ~/.cache/kiro-tracker/send_state.json
    { "sends": { "2026-09-28": ["night", "morning"] } }
    Keyed by band date. Only the current band date's entry is kept: reading the
    state drops every other date, so the file never grows beyond a single day.

Config file (gitignored), searched in this order:
    1. $KIRO_TRACKER_CONF if set
    2. <repo>/scripts/swiftbar/tracker.conf
    A simple KEY=VALUE file exporting KIRO_TRACKER_API and KIRO_TRACKER_KEY.
    Values already present in the environment take precedence over the file, so
    a manual ``KIRO_TRACKER_API=... send_usage_scheduled.py`` still works.

Exit codes:
    0  Nothing to do (outside any window, or slot already sent), OR a send that
       succeeded. A successful no-op and a successful send are both "fine" from
       the caller's point of view (SwiftBar does not care).
    2  A send was attempted and failed, or the config is unusable when a send
       was due. Callers that background this (SwiftBar) ignore the code; a human
       running it by hand sees the reason on stderr.

Usage:
    python3 scripts/send_usage_scheduled.py            # respect the schedule
    python3 scripts/send_usage_scheduled.py --force    # send now, ignore windows
    python3 scripts/send_usage_scheduled.py --dry-run  # report the decision only
    python3 scripts/send_usage_scheduled.py --status   # print state + exit
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, List, NamedTuple

# Repo root is two levels up: <repo>/scripts/send_usage_scheduled.py
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SEND_USAGE = _REPO_ROOT / "scripts" / "send_usage.py"
_DEFAULT_CONF = _REPO_ROOT / "scripts" / "swiftbar" / "tracker.conf"

_STATE_DIR = Path.home() / ".cache" / "kiro-tracker"
_STATE_FILE = _STATE_DIR / "send_state.json"


class Slot(NamedTuple):
    """A daily send band."""

    name: str
    start_hour: int  # inclusive (band starts at HH:00)
    wraps_midnight: bool = False  # True if the band runs past 00:00 into the next day


# The day is partitioned into three contiguous bands by the cardinal hours
# 07:00, 12:00 and 18:00. There are no gaps and no overlaps.
#
#   night     : 18:00-06:59  (wraps past midnight)
#   morning   : 07:00-11:59
#   afternoon : 12:00-17:59
#
# ``night`` is the band that wraps midnight: it owns 18:00-23:59 of its band
# date plus 00:00-06:59 of the following calendar day. Band-date keying (see
# _band_date) collapses both halves onto a single marker.
_SLOTS: List[Slot] = [
    Slot(name="night", start_hour=18, wraps_midnight=True),
    Slot(name="morning", start_hour=7),
    Slot(name="afternoon", start_hour=12),
]


def _current_slot(now: datetime) -> Slot:
    """
    Return the band that contains ``now``.

    The three bands partition the whole day with no gaps, so this always returns
    a band. The ``night`` band wraps midnight (18:00-06:59): any hour at or
    after its start (>= 18) and any hour before the earliest non-wrapping band
    starts (< 07) both fall in ``night``.

    Args:
        now: The current local datetime.

    Returns:
        The matching Slot for ``now``.
    """
    hour = now.hour
    # Daytime bands (non-wrapping): pick the latest whose start_hour <= hour.
    day_slots = sorted(
        (s for s in _SLOTS if not s.wraps_midnight),
        key=lambda s: s.start_hour,
    )
    earliest_day_start = day_slots[0].start_hour  # 07
    night = next(s for s in _SLOTS if s.wraps_midnight)

    # Before the first daytime band opens, or at/after the night band opens:
    # we are in the wrapping night band.
    if hour < earliest_day_start or hour >= night.start_hour:
        return night

    chosen = day_slots[0]
    for slot in day_slots:
        if hour >= slot.start_hour:
            chosen = slot
    return chosen


def _band_date(now: datetime, slot: Slot) -> date:
    """
    Return the band date that keys the marker for ``slot`` at ``now``.

    For normal bands this is simply today's date. For the wrapping ``night``
    band, the early-morning hours (00:00-06:59) belong to the night that began
    at 18:00 the *previous* calendar day, so the band date is yesterday. This
    makes the 18:00-23:59 and 00:00-06:59 halves of one night share a single
    marker, preventing a duplicate send across midnight.

    Args:
        now: The current local datetime.
        slot: The band ``now`` falls into (from ``_current_slot``).

    Returns:
        The calendar date on which the band started.
    """
    if slot.wraps_midnight and now.hour < slot.start_hour:
        # Early-morning tail of a night that started the previous day.
        return (now - timedelta(days=1)).date()
    return now.date()


def _load_config_file(conf_path: Path) -> Dict[str, str]:
    """
    Parse a simple KEY=VALUE config file into a dict.

    Blank lines and lines starting with ``#`` are ignored. A leading ``export``
    is stripped so the same file can be ``source``d by a shell. Surrounding
    single or double quotes on the value are removed.

    Args:
        conf_path: Path to the config file.

    Returns:
        A dict of the parsed keys. Empty if the file does not exist.
    """
    result: Dict[str, str] = {}
    if not conf_path.exists():
        return result
    for raw in conf_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key:
            result[key] = value
    return result


def _resolve_env(conf_path: Path) -> Dict[str, str]:
    """
    Build the child-process environment: os.environ overlaid with conf-file values.

    Environment variables that are already set win over the config file, so a
    manual override on the command line still takes effect. Only the tracker
    keys are pulled from the file; everything else comes from the inherited env.

    Args:
        conf_path: Path to the gitignored config file.

    Returns:
        A dict suitable for passing as ``env`` to subprocess.
    """
    env = dict(os.environ)
    file_values = _load_config_file(conf_path)
    for key in ("KIRO_TRACKER_API", "KIRO_TRACKER_KEY", "KIRO_TRACKER_RESOURCE_TYPE"):
        if not env.get(key) and file_values.get(key):
            env[key] = file_values[key]
    return env


def _load_state(band_date: date) -> Dict[str, List[str]]:
    """
    Load the marker state, keeping only the current band date's entry.

    Every date other than ``band_date`` is discarded on read, so the file never
    accumulates history: the markers only exist to answer "did I already send in
    this band for its current band date", so older data is useless. The caller
    writes the filtered result back via ``_save_state``, so the on-disk file is
    trimmed the next time a send is recorded.

    Note the key is the *band date*, not strictly today's calendar date: during
    the early-morning tail of the wrapping night band (00:00-06:59) the band
    date is yesterday, and that is the entry we must retain so the night marker
    set the previous evening is still visible.

    A corrupt or unreadable state file must never wedge the scheduler; the worst
    case of treating it as empty is one duplicate send, which the tracker
    tolerates (it stores snapshots keyed by time).

    Args:
        band_date: The current band date; only this date's markers are retained.

    Returns:
        The ``sends`` mapping, containing at most the band date's entry:
        {band_date_iso: [slot_name, ...]}.
    """
    if not _STATE_FILE.exists():
        return {}
    try:
        data = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    sends = data.get("sends")
    if not isinstance(sends, dict):
        return {}
    key = band_date.isoformat()
    slots = sends.get(key)
    if not isinstance(slots, list):
        return {}
    return {key: [s for s in slots if isinstance(s, str)]}


def _save_state(sends: Dict[str, List[str]]) -> None:
    """
    Persist the marker state atomically.

    The mapping is expected to already contain only today's entry (``_load_state``
    strips everything else), so no pruning is needed here.

    Args:
        sends: The {date_iso: [slot_name, ...]} mapping to write.
    """
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _STATE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"sends": sends}, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, _STATE_FILE)  # atomic on the same filesystem


def _already_sent(sends: Dict[str, List[str]], band_date: date, slot_name: str) -> bool:
    """Return True if ``slot_name`` was already recorded for ``band_date``."""
    return slot_name in sends.get(band_date.isoformat(), [])


def _record_send(sends: Dict[str, List[str]], band_date: date, slot_name: str) -> None:
    """Mark ``slot_name`` as sent for ``band_date`` in the in-memory mapping."""
    key = band_date.isoformat()
    day_slots = sends.setdefault(key, [])
    if slot_name not in day_slots:
        day_slots.append(slot_name)


def _run_send_usage(env: Dict[str, str]) -> subprocess.CompletedProcess:
    """
    Invoke send_usage.py as a child process.

    Args:
        env: The environment (already merged with the conf file) to pass down.

    Returns:
        The CompletedProcess with captured stdout/stderr.
    """
    return subprocess.run(
        [sys.executable, str(_SEND_USAGE)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(_REPO_ROOT),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Send now regardless of the time windows or today's markers.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would happen without sending or writing state.",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Print the current schedule state and exit.",
    )
    parser.add_argument(
        "--conf",
        default=os.environ.get("KIRO_TRACKER_CONF", str(_DEFAULT_CONF)),
        help="Path to the gitignored tracker config file.",
    )
    args = parser.parse_args()

    now = datetime.now()
    slot = _current_slot(now)
    band_date = _band_date(now, slot)
    sends = _load_state(band_date)

    if args.status:
        print(f"Now:        {now.isoformat(timespec='seconds')}")
        print(f"Band now:   {slot.name}")
        print(f"Band date:  {band_date.isoformat()}")
        print(f"State file: {_STATE_FILE}")
        print(f"Sent (band date): {sends.get(band_date.isoformat(), [])}")
        return 0

    # Decide whether a send is due.
    if args.force:
        slot_name = "forced"
        due = True
    else:
        slot_name = slot.name
        if _already_sent(sends, band_date, slot_name):
            if args.dry_run:
                print(f"Band '{slot_name}' already sent for band date {band_date.isoformat()}; nothing to do.")
            return 0
        due = True

    if not due:
        return 0

    if args.dry_run:
        print(f"Would send now for slot '{slot_name}'.")
        return 0

    env = _resolve_env(Path(args.conf))
    if not env.get("KIRO_TRACKER_API"):
        print(
            "KIRO_TRACKER_API is not set and not found in the config file "
            f"({args.conf}). Cannot send.",
            file=sys.stderr,
        )
        return 2

    try:
        proc = _run_send_usage(env)
    except subprocess.TimeoutExpired:
        print("send_usage.py timed out after 60s.", file=sys.stderr)
        return 2

    if proc.returncode != 0:
        # Do NOT record a marker on failure, so the next tick retries within the
        # same window.
        sys.stderr.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        print(f"send_usage.py failed (exit {proc.returncode}) for slot '{slot_name}'.", file=sys.stderr)
        return 2

    # Success: record the marker (skipped for --force so a manual send never
    # consumes a scheduled slot).
    if not args.force:
        _record_send(sends, band_date, slot_name)
        _save_state(sends)

    sys.stdout.write(proc.stdout)
    print(f"Sent snapshot for slot '{slot_name}'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
