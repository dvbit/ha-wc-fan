"""Unit tests of the pure state machine (SPEC.md §4-§8)."""

from __future__ import annotations

import logging

import pytest

from custom_components.wc_fan.const import (
    STATE_ACTIVE,
    STATE_COUNTDOWN,
    STATE_DISABLED,
    STATE_IDLE,
    STATE_MANUAL,
    STATE_WAITING,
)
from custom_components.wc_fan.engine import (
    FAN_OFF,
    FAN_ON,
    NA,
    OFF,
    ON,
    Params,
    WcFanEngine,
    compute_post_ventilation,
)

LOG = logging.getLogger("test_wc_fan")


def make(**params: float) -> WcFanEngine:
    """Return an engine started in idle with all inputs off."""
    eng = WcFanEngine("test", Params(**params), LOG)
    eng.start(None, OFF, OFF, OFF, True, 0.0)
    return eng


def test_formula_spec_example() -> None:
    """§4 example: seat 300 s, flushes 6 s and 3 s -> 408 s."""
    assert compute_post_ventilation(Params(), 300, [6, 3]) == pytest.approx(408)


def test_formula_clamps_and_inverted_bounds() -> None:
    """§4: clamp to [P7, P8]; P7 > P8 -> P8 on both sides; P4 caps D_t."""
    assert compute_post_ventilation(Params(), 0, []) == 120
    assert compute_post_ventilation(Params(), 100000, [500]) == 1200
    assert compute_post_ventilation(Params(min_time=900, max_time=600), 0, []) == 600
    # P4 = 1200 caps the seat contribution at 0.5 * 1200 = 600.
    p = Params(max_time=7200)
    assert compute_post_ventilation(p, 5000, []) == 120 + 600


def test_full_session_seat_and_flush() -> None:
    """§5: idle -> waiting -> active -> countdown -> idle, T from §4 example."""
    eng = make()
    assert eng.seat_event(ON, 0) == []
    assert eng.state == STATE_WAITING
    assert eng.data.p1_deadline == 10
    assert eng.p1_expired(10) == [FAN_ON]
    assert eng.state == STATE_ACTIVE
    assert eng.flush_event(ON, 100) == []
    assert eng.flush_event(OFF, 106) == []
    assert eng.state == STATE_ACTIVE  # seat still open
    assert eng.seat_event(OFF, 300) == []  # D_t = 300 (delay included)
    assert eng.state == STATE_COUNTDOWN
    eng.flush_event(ON, 310)  # re-trigger: same session, no P1
    assert eng.state == STATE_ACTIVE
    eng.flush_event(OFF, 313)
    assert eng.state == STATE_COUNTDOWN
    assert eng.data.countdown_end == pytest.approx(313 + 408)
    assert eng.countdown_expired(313 + 408) == [FAN_OFF]
    assert eng.state == STATE_IDLE
    assert eng.data.seat_total == 0 and eng.data.flushes == []


def test_seat_closed_before_p1_is_discarded() -> None:
    """§5 waiting + seat off before P1 -> idle, no fan command."""
    eng = make()
    eng.seat_event(ON, 0)
    assert eng.seat_event(OFF, 5) == []
    assert eng.state == STATE_IDLE
    assert eng.p1_expired(10) == []


def test_flush_in_waiting_counts_current_opening() -> None:
    """§5 waiting + flush: cancel P1, fan ON, opening counted in D_t."""
    eng = make()
    eng.seat_event(ON, 0)
    assert eng.flush_event(ON, 4) == [FAN_ON]
    assert eng.state == STATE_ACTIVE and eng.data.p1_deadline is None
    eng.flush_event(OFF, 8)
    eng.seat_event(OFF, 20)
    assert eng.data.seat_total == 20
    assert eng.data.flushes == [4]


def test_flush_from_idle_and_zero_delay() -> None:
    """§5 idle + flush -> active immediately; P1 = 0 skips waiting."""
    eng = make()
    assert eng.flush_event(ON, 0) == [FAN_ON]
    eng2 = make(activation_delay=0)
    assert eng2.seat_event(ON, 0) == [FAN_ON]
    assert eng2.state == STATE_ACTIVE


def test_manual_off_and_on() -> None:
    """§6: manual OFF closes the session; manual ON -> manual until off."""
    eng = make()
    eng.flush_event(ON, 0)
    assert eng.fan_event(OFF, ON, False, 0) == []  # our own echo
    eng.fan_event(ON, OFF, True, 5)
    assert eng.state == STATE_IDLE
    eng.fan_event(OFF, ON, True, 10)
    assert eng.state == STATE_MANUAL
    assert eng.flush_event(OFF, 11) == [] and eng.seat_event(ON, 12) == []
    assert eng.state == STATE_MANUAL
    eng.fan_event(ON, OFF, False, 20)  # off for any cause
    assert eng.state == STATE_IDLE
    # Seat still open since manual mode: no session until a new edge (§6).
    assert eng.p1_expired(30) == []
    eng.seat_event(OFF, 40)
    eng.seat_event(ON, 41)
    assert eng.state == STATE_WAITING


def test_disable_enable() -> None:
    """§7: disable closes everything; enable -> idle."""
    eng = make()
    eng.flush_event(ON, 0)
    eng.set_enabled(False, 1)
    assert eng.state == STATE_DISABLED
    assert eng.flush_event(OFF, 2) == []
    assert eng.fan_event(ON, OFF, True, 3) == []
    eng.set_enabled(True, 4)
    assert eng.state == STATE_IDLE


def test_params_change_during_countdown() -> None:
    """§5: end = countdown start + new T; past -> immediate OFF."""
    eng = make()
    eng.flush_event(ON, 0)
    eng.flush_event(OFF, 5)  # T = 120 + 60 + 10 = 190
    assert eng.data.countdown_end == 195
    eng.params.base_time = 300
    assert eng.params_changed(10) == []
    assert eng.data.countdown_end == 5 + 370
    eng.params.max_time = 60
    eng.params.min_time = 0
    assert eng.params_changed(100) == [FAN_OFF]
    assert eng.state == STATE_IDLE


def test_unavailable_sensor() -> None:
    """§8: on -> unavailable counts as off; na -> on is not an edge."""
    eng = make()
    eng.flush_event(ON, 0)
    eng.flush_event(NA, 7)
    assert eng.state == STATE_COUNTDOWN
    assert eng.data.flushes == [7]  # duration counted up to unavailable
    eng.countdown_expired(10000)
    assert eng.flush_event(ON, 10001) == []  # na -> on: no trigger
    assert eng.state == STATE_IDLE
    eng.flush_event(OFF, 10002)
    assert eng.flush_event(ON, 10003) == [FAN_ON]


def test_unavailable_flush_duration_recorded() -> None:
    """§8: the duration is counted up to the unavailable instant."""
    eng = make()
    eng.seat_event(ON, 0)
    eng.p1_expired(10)
    eng.flush_event(ON, 20)
    eng.flush_event(NA, 26)
    assert eng.data.flushes == [6]


def test_fan_reconciliation_after_unavailable() -> None:
    """§8: fan back available -> realign ON/OFF."""
    eng = make()
    eng.flush_event(ON, 0)
    assert eng.fan_event(ON, NA, False, 1) == []
    assert eng.fan_event(NA, OFF, False, 2) == [FAN_ON]
    eng.flush_event(OFF, 3)
    eng.fan_event(ON, NA, False, 4)
    assert eng.countdown_expired(1000) == [FAN_OFF]
    assert eng.fan_event(NA, ON, False, 1001) == [FAN_OFF]
    assert eng.fan_event(ON, OFF, False, 1002) == []
    assert eng.data.off_pending is False


def test_restart_scenarios() -> None:
    """§8 restart: countdown expired/resumed, active, waiting, manual."""
    base = make()
    base.flush_event(ON, 0)
    base.flush_event(OFF, 5)
    saved = base.as_dict()  # countdown, end 195

    eng = WcFanEngine("r", Params(), LOG)
    assert eng.start(saved, OFF, OFF, ON, True, 500) == [FAN_OFF]
    assert eng.state == STATE_IDLE

    eng = WcFanEngine("r", Params(), LOG)
    assert eng.start(saved, OFF, OFF, ON, True, 100) == []
    assert eng.state == STATE_COUNTDOWN and eng.data.countdown_end == 195

    active = make()
    active.seat_event(ON, 0)
    active.p1_expired(10)
    eng = WcFanEngine("r", Params(), LOG)
    eng.start(active.as_dict(), OFF, OFF, ON, True, 40)
    assert eng.state == STATE_COUNTDOWN
    assert eng.data.seat_total == 40  # closed at the startup instant
    eng = WcFanEngine("r", Params(), LOG)
    eng.start(active.as_dict(), ON, OFF, ON, True, 40)
    assert eng.state == STATE_ACTIVE

    waiting = make()
    waiting.seat_event(ON, 0)
    eng = WcFanEngine("r", Params(), LOG)
    eng.start(waiting.as_dict(), ON, OFF, OFF, True, 3)
    assert eng.state == STATE_IDLE

    manual = make()
    manual.fan_event(OFF, ON, True, 0)
    eng = WcFanEngine("r", Params(), LOG)
    eng.start(manual.as_dict(), OFF, OFF, ON, True, 3)
    assert eng.state == STATE_MANUAL
    eng = WcFanEngine("r", Params(), LOG)
    eng.start(manual.as_dict(), OFF, OFF, OFF, True, 3)
    assert eng.state == STATE_IDLE

    eng = WcFanEngine("r", Params(), LOG)
    eng.start(saved, OFF, OFF, ON, False, 100)
    assert eng.state == STATE_DISABLED
