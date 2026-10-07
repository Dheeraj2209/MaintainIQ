"""The one sensor-node state rule (src/telemetry/device_health.py).

design/2026-10-06-device-health-design.md decision 3. Every case passes an
explicit `now`, so the 1.5x / 3x heartbeat boundaries are checked exactly.
"""
from datetime import datetime, timedelta, timezone

import pytest

from src.telemetry import device_health as dh
from src.telemetry import protocol
from src.telemetry.ingest import device_online

NOW = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)


def ago(seconds: float) -> str:
    return protocol.format_utc(NOW - timedelta(seconds=seconds))


_UNSET = object()


def state(online=1, seconds=0.0, hb=10.0, *, reported=True, last_seen=_UNSET, **kw):
    seen = ago(seconds) if last_seen is _UNSET else last_seen
    return dh.device_state(online, seen, hb, reported=reported, now=NOW, **kw)


def test_online_when_heard_recently():
    assert state(seconds=5) == dh.ONLINE


@pytest.mark.parametrize("seconds,expected", [
    (14.9, dh.ONLINE), (15.0, dh.STALE), (29.9, dh.STALE),
])
def test_stale_band_starts_at_one_and_a_half_heartbeats(seconds, expected):
    assert state(seconds=seconds) == expected


@pytest.mark.parametrize("seconds", [30.0, 31.0, 3600.0])
def test_offline_at_three_heartbeats_and_beyond(seconds):
    assert state(seconds=seconds) == dh.OFFLINE


def test_lwt_is_offline_immediately():
    assert state(online=0, seconds=0) == dh.OFFLINE


@pytest.mark.parametrize("online,seconds", [(0, 0), (1, 0), (1, 3600), (0, 3600)])
def test_never_reported_regardless_of_flag_or_age(online, seconds):
    assert state(online=online, seconds=seconds, reported=False) == dh.NEVER_REPORTED


@pytest.mark.parametrize("hb", [None, 0, 0.0, -5.0])
def test_unusable_heartbeat_falls_back_to_default(hb):
    assert dh.effective_heartbeat_s(hb) == dh.DEFAULT_HEARTBEAT_S == 10.0
    assert state(seconds=14, hb=hb) == dh.ONLINE
    assert state(seconds=16, hb=hb) == dh.STALE
    assert state(seconds=30, hb=hb) == dh.OFFLINE


def test_reported_heartbeat_scales_the_bands():
    assert dh.effective_heartbeat_s(20.0) == 20.0
    assert state(seconds=29, hb=20.0) == dh.ONLINE
    assert state(seconds=50, hb=20.0) == dh.STALE
    assert state(seconds=60, hb=20.0) == dh.OFFLINE


def test_newer_last_message_at_wins():
    newer = NOW - timedelta(seconds=2)
    assert state(seconds=300, last_message_at=newer) == dh.ONLINE
    # An older message never makes a fresh status look stale.
    assert state(seconds=2, last_message_at=NOW - timedelta(hours=1)) == dh.ONLINE


@pytest.mark.parametrize("last_seen", [None, "", "not-a-time"])
def test_missing_or_unparseable_last_seen_is_offline(last_seen):
    assert state(last_seen=last_seen) == dh.OFFLINE


def test_timestamp_suffixes_are_equivalent():
    moment = NOW - timedelta(seconds=20)
    variants = [
        moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
        moment.isoformat(),
        moment.replace(tzinfo=None).isoformat(),  # naive ⇒ UTC
        moment,  # datetime accepted too
    ]
    for value in variants:
        assert dh.parse_iso(value) == moment
        assert state(last_seen=value) == dh.STALE


def test_is_heard_is_the_legacy_online_boolean():
    assert dh.is_heard(dh.ONLINE) and dh.is_heard(dh.STALE)
    assert not dh.is_heard(dh.OFFLINE) and not dh.is_heard(dh.NEVER_REPORTED)
    assert set(dh.DEVICE_STATES) == {"online", "stale", "offline", "never_reported"}


@pytest.mark.parametrize("online,seconds,hb", [
    (1, 5, 10.0), (1, 14.9, 10.0), (1, 15, 10.0), (1, 29.9, 10.0), (1, 30, 10.0),
    (1, 31, None), (0, 0, 10.0), (1, 50, 20.0), (1, 25, 0),
])
def test_ingest_device_online_delegates_to_the_rule(online, seconds, hb):
    expected = dh.is_heard(state(online=online, seconds=seconds, hb=hb))
    assert device_online(online, ago(seconds), hb, now=NOW) is expected


def test_ingest_reexports_the_constants():
    from src.telemetry import ingest

    assert ingest.DEFAULT_HEARTBEAT_S == dh.DEFAULT_HEARTBEAT_S
    assert ingest.OFFLINE_AFTER_HEARTBEATS == dh.OFFLINE_AFTER_HEARTBEATS


def test_silent_for_s():
    assert dh.silent_for_s(ago(42.5), now=NOW) == pytest.approx(42.5)
    assert dh.silent_for_s(ago(60), now=NOW, last_message_at=NOW - timedelta(seconds=3)) == pytest.approx(3.0)
    assert dh.silent_for_s(None, now=NOW) is None
    assert dh.silent_for_s("garbage", now=NOW) is None
    # Only a message time known: still measurable.
    assert dh.silent_for_s(None, now=NOW, last_message_at=NOW - timedelta(seconds=7)) == pytest.approx(7.0)
