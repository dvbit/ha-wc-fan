"""Pure state machine of the WC Fan integration (SPEC.md §4-§8).

This module has NO Home Assistant dependency on purpose: it receives
normalized events with an explicit timestamp ``now`` (epoch seconds) and
returns the fan commands to execute. Timers are exposed as deadlines
(``p1_deadline`` and ``countdown_end``) that the Home Assistant glue
(manager.py) schedules and reports back when they expire.

This keeps the logic deterministic and unit-testable (tests/test_engine.py).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import logging
from typing import Any, Final

from .const import (
    P_ACTIVATION_DELAY,
    P_BASE_TIME,
    P_FLUSH_EXTRA,
    P_FLUSH_FACTOR,
    P_MAX_TIME,
    P_MIN_TIME,
    P_SEAT_CAP,
    P_SEAT_FACTOR,
    STATE_ACTIVE,
    STATE_COUNTDOWN,
    STATE_DISABLED,
    STATE_IDLE,
    STATE_MANUAL,
    STATE_WAITING,
    STATES,
)

# --- Normalized entity states -------------------------------------------------
# §4/§8: only off -> on is a rising edge; unavailable/unknown are collapsed
# into NA so they can never generate an edge.
ON: Final = "on"
OFF: Final = "off"
NA: Final = "na"

# --- Fan commands returned by the engine --------------------------------------
FAN_ON: Final = "turn_on"
FAN_OFF: Final = "turn_off"


@dataclass(frozen=True)
class ParamSpec:
    """Definition of one tunable parameter (§3 table)."""

    default: float
    minimum: float
    maximum: float
    step: float
    unit: str


# §3: defaults and ranges of P1..P8.
PARAM_SPECS: Final[dict[str, ParamSpec]] = {
    P_ACTIVATION_DELAY: ParamSpec(10, 0, 120, 1, "s"),  # P1
    P_BASE_TIME: ParamSpec(120, 0, 1800, 1, "s"),  # P2
    P_SEAT_FACTOR: ParamSpec(0.5, 0, 5, 0.1, "s/s"),  # P3
    P_SEAT_CAP: ParamSpec(1200, 60, 3600, 1, "s"),  # P4
    P_FLUSH_EXTRA: ParamSpec(60, 0, 600, 1, "s"),  # P5
    P_FLUSH_FACTOR: ParamSpec(2, 0, 10, 0.1, "s/s"),  # P6
    P_MIN_TIME: ParamSpec(120, 0, 3600, 1, "s"),  # P7
    P_MAX_TIME: ParamSpec(1200, 60, 7200, 1, "s"),  # P8
}


@dataclass
class Params:
    """Current values of P1..P8 (§3), pushed in by the number entities."""

    activation_delay: float = PARAM_SPECS[P_ACTIVATION_DELAY].default
    base_time: float = PARAM_SPECS[P_BASE_TIME].default
    seat_factor: float = PARAM_SPECS[P_SEAT_FACTOR].default
    seat_cap: float = PARAM_SPECS[P_SEAT_CAP].default
    flush_extra: float = PARAM_SPECS[P_FLUSH_EXTRA].default
    flush_factor: float = PARAM_SPECS[P_FLUSH_FACTOR].default
    min_time: float = PARAM_SPECS[P_MIN_TIME].default
    max_time: float = PARAM_SPECS[P_MAX_TIME].default


def compute_post_ventilation(
    params: Params, seat_total: float, flushes: list[float]
) -> float:
    """Return T in seconds (§4).

    T = clamp(P2 + P3 * min(D_t, P4) + sum_i(P5 + P6 * d_i), P7, P8)
    If P7 > P8, P8 is used as both lower and upper bound.
    """
    raw = (
        params.base_time
        + params.seat_factor * min(seat_total, params.seat_cap)
        + sum(params.flush_extra + params.flush_factor * d for d in flushes)
    )
    upper = params.max_time
    lower = min(params.min_time, upper)  # §4: P7 > P8 -> P8 on both sides
    return max(lower, min(raw, upper))


@dataclass
class SessionData:
    """Everything §8 requires to persist across a Home Assistant restart."""

    state: str = STATE_IDLE
    seat_total: float = 0.0  # D_t, seconds
    flushes: list[float] | None = None  # S, list of d_i in seconds
    seat_since: float | None = None  # start of the seat opening in progress
    flush_since: float | None = None  # start of the flush in progress
    p1_deadline: float | None = None  # P1 timer expiry (state waiting)
    countdown_start: float | None = None  # instant of entering countdown
    countdown_end: float | None = None  # expected switch-off instant
    off_pending: bool = False  # §8: OFF issued, not yet confirmed by the fan

    def __post_init__(self) -> None:
        """Avoid a shared mutable default for the flush list."""
        if self.flushes is None:
            self.flushes = []

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> SessionData:
        """Build from persisted data, ignoring unknown or invalid keys."""
        if not data:
            return cls()
        known = {f.name for f in fields(cls)}
        session = cls(**{k: v for k, v in data.items() if k in known})
        if session.state not in STATES:
            session.state = STATE_IDLE
        return session


class WcFanEngine:
    """State machine of §5, with manual override (§6) and enable (§7)."""

    def __init__(self, name: str, params: Params, logger: logging.Logger) -> None:
        """Create the engine in state idle; call ``start`` before use."""
        self.name = name
        self.params = params
        self._log = logger
        self.data = SessionData()
        # Last normalized physical state of each input, tracked in EVERY
        # state (even manual/disabled) so that edges are always correct.
        self.seat: str = NA
        self.flush: str = NA

    # ------------------------------------------------------------------ helpers
    @property
    def state(self) -> str:
        """Return the current state (§5)."""
        return self.data.state

    def post_ventilation(self) -> float:
        """Return T for the accumulators of the current session (§4)."""
        return compute_post_ventilation(
            self.params, self.data.seat_total, self.data.flushes or []
        )

    def _transition(self, new_state: str, cause: str) -> None:
        """Change state and trace it at debug level (§9)."""
        old = self.data.state
        self.data.state = new_state
        self._log.debug(
            "[%s] %s -> %s (cause: %s) D_t=%.1fs S=%s",
            self.name,
            old,
            new_state,
            cause,
            self.data.seat_total,
            [round(d, 1) for d in self.data.flushes or []],
        )

    def _open_session(self) -> None:
        """§4: the session starts when entering ``active``.

        ``seat_since`` is preserved so the opening that triggered the session
        is counted in D_t from its rising edge, delay included.
        """
        self.data.seat_total = 0.0
        self.data.flushes = []
        self.data.p1_deadline = None
        self._log.info("[%s] session opened, fan ON", self.name)

    def _close_session(self) -> None:
        """§4: accumulators and timers are reset at the end of the session."""
        self.data.seat_total = 0.0
        self.data.flushes = []
        self.data.seat_since = None
        self.data.flush_since = None
        self.data.p1_deadline = None
        self.data.countdown_start = None
        self.data.countdown_end = None

    def _maybe_start_countdown(self, now: float, cause: str) -> None:
        """§5 active: seat off AND flush off -> compute T, start countdown.

        §8: a sensor in ``na`` (unavailable/unknown) counts as off.
        """
        if self.seat == ON or self.flush == ON:
            return
        t = self.post_ventilation()
        self.data.countdown_start = now
        self.data.countdown_end = now + t
        self._transition(STATE_COUNTDOWN, cause)
        self._log.debug("[%s] T computed: %.1fs", self.name, t)

    # ------------------------------------------------------------------ startup
    def start(
        self,
        restored: dict[str, Any] | None,
        seat: str,
        flush: str,
        fan: str,
        enabled: bool,
        now: float,
    ) -> list[str]:
        """Resume after a restart or reload (§8 "Riavvio HA")."""
        self.data = SessionData.from_dict(restored)
        self.seat = seat
        self.flush = flush
        cmds: list[str] = []
        state = self.data.state

        if not enabled:
            # §7: disabled overrides any persisted state.
            self._close_session()
            self.data.off_pending = False
            self._transition(STATE_DISABLED, "startup, disabled")
        elif state == STATE_COUNTDOWN and self.data.countdown_end is not None:
            if self.data.countdown_end <= now:
                # §8: countdown expired while HA was down -> fan OFF -> idle.
                self._close_session()
                self.data.off_pending = True
                self._transition(STATE_IDLE, "startup, countdown expired")
                if fan != OFF:
                    cmds.append(FAN_OFF)
            else:
                # §8: resume the countdown (deadline kept as persisted).
                self._transition(STATE_COUNTDOWN, "startup, countdown resumed")
                if fan == OFF:
                    cmds.append(FAN_ON)
        elif state == STATE_ACTIVE:
            # §8: close the durations at the startup instant and re-evaluate.
            self._resume_trackers(now)
            self._transition(STATE_ACTIVE, "startup, active resumed")
            self._maybe_start_countdown(now, "startup, both sensors off")
            if fan == OFF:
                cmds.append(FAN_ON)
        elif state == STATE_MANUAL and fan != OFF:
            # §8: stay manual while the fan is (still) on.
            self._close_session()
            self._transition(STATE_MANUAL, "startup, fan still on")
        else:
            # waiting -> idle (§8), manual with fan off -> idle (§8),
            # idle -> idle, disabled re-enabled meanwhile -> idle (§7).
            self._close_session()
            self.data.off_pending = self.data.off_pending and state == STATE_IDLE
            self._transition(STATE_IDLE, f"startup from {state}")

        self._log.info(
            "[%s] started in state %s (seat=%s flush=%s fan=%s)",
            self.name,
            self.state,
            seat,
            flush,
            fan,
        )
        return cmds

    def _resume_trackers(self, now: float) -> None:
        """Close/open the in-progress trackers against the current sensors."""
        d = self.data
        if d.seat_since is not None and self.seat != ON:
            d.seat_total += max(0.0, now - d.seat_since)
            d.seat_since = None
        elif d.seat_since is None and self.seat == ON:
            d.seat_since = now  # opened while HA was down: count from now
        if d.flush_since is not None and self.flush != ON:
            d.flushes = [*(d.flushes or []), max(0.0, now - d.flush_since)]
            d.flush_since = None
        elif d.flush_since is None and self.flush == ON:
            d.flush_since = now

    # ------------------------------------------------------------ sensor events
    def seat_event(self, new: str, now: float) -> list[str]:
        """Handle a seat state change (normalized)."""
        old, self.seat = self.seat, new
        if self.state in (STATE_MANUAL, STATE_DISABLED):
            return []  # §6/§7: sensor events ignored
        if old == OFF and new == ON:
            return self._seat_rising(now)
        if old == ON and new in (OFF, NA):
            # §8: on -> unavailable/unknown is treated as off.
            return self._seat_falling(now)
        return []

    def flush_event(self, new: str, now: float) -> list[str]:
        """Handle a flush state change (normalized)."""
        old, self.flush = self.flush, new
        if self.state in (STATE_MANUAL, STATE_DISABLED):
            return []  # §6/§7: sensor events ignored
        if old == OFF and new == ON:
            return self._flush_rising(now)
        if old == ON and new in (OFF, NA):
            return self._flush_falling(now)
        return []

    def _seat_rising(self, now: float) -> list[str]:
        d = self.data
        d.seat_since = now
        if self.state == STATE_IDLE:
            # §5 idle + seat rising: start P1 timer -> waiting.
            d.p1_deadline = now + self.params.activation_delay
            self._transition(STATE_WAITING, "seat opened")
            if self.params.activation_delay <= 0:
                return self.p1_expired(now)
        elif self.state == STATE_COUNTDOWN:
            # §5 countdown + rising edge: same session, no P1 delay.
            d.countdown_start = None
            d.countdown_end = None
            self._transition(STATE_ACTIVE, "seat opened during countdown")
        return []

    def _seat_falling(self, now: float) -> list[str]:
        d = self.data
        if self.state == STATE_WAITING:
            # §5 waiting + seat off before P1: discard the opening.
            d.seat_since = None
            d.p1_deadline = None
            self._transition(STATE_IDLE, "seat closed before P1, discarded")
            return []
        if self.state == STATE_ACTIVE:
            # §5: D_t is updated at every seat closing.
            if d.seat_since is not None:
                d.seat_total += max(0.0, now - d.seat_since)
            d.seat_since = None
            self._maybe_start_countdown(now, "seat closed")
            return []
        d.seat_since = None
        return []

    def _flush_rising(self, now: float) -> list[str]:
        d = self.data
        d.flush_since = now
        if self.state in (STATE_IDLE, STATE_WAITING):
            # §5 idle/waiting + flush rising: cancel P1, fan ON, open session.
            # In waiting the opening in progress (seat_since) counts in D_t.
            self._open_session()
            self._transition(STATE_ACTIVE, "flush started")
            return [FAN_ON]
        if self.state == STATE_COUNTDOWN:
            d.countdown_start = None
            d.countdown_end = None
            self._transition(STATE_ACTIVE, "flush started during countdown")
        return []

    def _flush_falling(self, now: float) -> list[str]:
        d = self.data
        if self.state == STATE_ACTIVE:
            # §5: d_i is recorded at every end of flush.
            if d.flush_since is not None:
                d.flushes = [*(d.flushes or []), max(0.0, now - d.flush_since)]
            d.flush_since = None
            self._maybe_start_countdown(now, "flush ended")
            return []
        d.flush_since = None
        return []

    # ------------------------------------------------------------- timer events
    def p1_expired(self, now: float) -> list[str]:
        """§5 waiting + P1 expired with seat still on -> fan ON, active."""
        if self.state != STATE_WAITING:
            return []
        if self.seat != ON:
            self.data.seat_since = None
            self.data.p1_deadline = None
            self._transition(STATE_IDLE, "P1 expired, seat not open")
            return []
        self._open_session()
        self._transition(STATE_ACTIVE, "P1 expired, seat still open")
        return [FAN_ON]

    def countdown_expired(self, now: float) -> list[str]:
        """§5 countdown expired -> fan OFF, close session, idle."""
        end = self.data.countdown_end
        # Small tolerance: the HA timer may fire a few ms before the deadline.
        if self.state != STATE_COUNTDOWN or end is None or now + 0.5 < end:
            return []
        self._close_session()
        self.data.off_pending = True
        self._transition(STATE_IDLE, "countdown expired")
        self._log.info("[%s] session closed, fan OFF", self.name)
        return [FAN_OFF]

    # --------------------------------------------------------------- fan events
    def fan_event(self, old: str, new: str, manual: bool, now: float) -> list[str]:
        """Handle a fan state change (§6 manual override, §8 availability).

        ``manual`` is decided by the glue by comparing the context ids; it is
        always False for transitions from/to unavailable (§6).
        """
        if new == NA:
            return []  # §8: commands while unavailable are only logged
        if new == OFF:
            self.data.off_pending = False

        if old == NA:
            return self._fan_available_again(new)
        if self.state == STATE_DISABLED:
            return []  # §7: the integration does not care about the fan
        if self.state == STATE_MANUAL:
            if new == OFF:
                # §6: manual mode ends when the fan goes off for any cause.
                self._close_session()
                self._transition(STATE_IDLE, "fan off, manual mode ended")
            return []
        if not manual:
            return []  # our own command echoed back

        if new == OFF:
            # §6 manual OFF in any state: cancel timers, close session, idle.
            if self.state != STATE_IDLE:
                self._close_session()
                self._transition(STATE_IDLE, "manual fan OFF")
                self._log.info("[%s] manual OFF, session closed", self.name)
            return []
        # §6 manual ON -> manual: sensors ignored, fan not commanded.
        self._close_session()
        self.data.off_pending = False
        self._transition(STATE_MANUAL, "manual fan ON")
        self._log.info("[%s] manual ON, automation suspended", self.name)
        return []

    def _fan_available_again(self, new: str) -> list[str]:
        """§8: realign the fan when it comes back available."""
        if self.state in (STATE_ACTIVE, STATE_COUNTDOWN) and new == OFF:
            self._log.info("[%s] fan available again: re-sending ON", self.name)
            return [FAN_ON]
        if self.state == STATE_IDLE and self.data.off_pending and new == ON:
            self._log.info("[%s] fan available again: re-sending OFF", self.name)
            return [FAN_OFF]
        if self.state == STATE_MANUAL and new == OFF:
            self._close_session()
            self._transition(STATE_IDLE, "fan back off, manual mode ended")
        return []

    # ------------------------------------------------------- enable / parameters
    def set_enabled(self, enabled: bool, now: float) -> list[str]:
        """§7: enable switch toggled."""
        if not enabled and self.state != STATE_DISABLED:
            self._close_session()
            self.data.off_pending = False
            self._transition(STATE_DISABLED, "disabled by user")
            self._log.info("[%s] disabled", self.name)
        elif enabled and self.state == STATE_DISABLED:
            self._transition(STATE_IDLE, "enabled by user")
            self._log.info("[%s] enabled", self.name)
        return []

    def params_changed(self, now: float) -> list[str]:
        """§5: parameter change during countdown -> end = start + new T."""
        d = self.data
        if self.state != STATE_COUNTDOWN or d.countdown_start is None:
            return []
        t = self.post_ventilation()
        d.countdown_end = d.countdown_start + t
        self._log.debug("[%s] parameters changed: T recomputed %.1fs", self.name, t)
        if d.countdown_end <= now:
            return self.countdown_expired(now)  # already past: switch off now
        return []

    # -------------------------------------------------------------- persistence
    def as_dict(self) -> dict[str, Any]:
        """Return the data to persist (§8)."""
        return asdict(self.data)
