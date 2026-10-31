"""Pure state machine of the WC Fan integration (SPEC.md §4-§8).

This module has NO Home Assistant dependency on purpose: it receives
normalized events with an explicit timestamp ``now`` (epoch seconds) and
returns the fan commands to execute. Timers are exposed as deadlines
(``p1_deadline`` and ``countdown_end``) that the Home Assistant glue
(manager.py) schedules and reports back when they expire.

This keeps the logic deterministic and unit-testable (tests/engine/test_engine.py).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import logging
from typing import Any, Final

from .const import (
    FLUSH_LONG,
    FLUSH_SHORT,
    P_ACTIVATION_DELAY,
    P_BASE_TIME,
    P_FLUSH_EXTRA,
    P_FLUSH_FACTOR,
    P_MAX_ON_TIME,
    P_MAX_TIME,
    P_MIN_TIME,
    P_SEAT_CAP,
    P_SEAT_FACTOR,
    P_SHORT_FLUSH_EXTRA,
    P_SHORT_FLUSH_FACTOR,
    STATE_ACTIVE,
    STATE_COUNTDOWN,
    STATE_DISABLED,
    STATE_IDLE,
    STATE_MANUAL,
    STATE_WAITING,
    STATES,
    STOP_COUNTDOWN,
    STOP_DISABLED,
    STOP_MANUAL_OFF,
    STOP_MAX_ON,
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
    P_FLUSH_EXTRA: ParamSpec(60, 0, 600, 1, "s"),  # P5, long flush
    P_FLUSH_FACTOR: ParamSpec(0.3, 0, 2, 0.05, "s/s"),  # P6, per long flush (v2.0)
    P_SHORT_FLUSH_EXTRA: ParamSpec(30, 0, 600, 1, "s"),  # P5b, short (v1.1)
    P_SHORT_FLUSH_FACTOR: ParamSpec(0.15, 0, 2, 0.05, "s/s"),  # P6b, per short (v2.0)
    P_MIN_TIME: ParamSpec(120, 0, 3600, 1, "s"),  # P7
    P_MAX_TIME: ParamSpec(1200, 60, 7200, 1, "s"),  # P8
    P_MAX_ON_TIME: ParamSpec(60, 5, 240, 1, "min"),  # v1.2: max fan on-time
}


@dataclass
class Params:
    """Current values of P1..P8 (§3), pushed in by the number entities."""

    activation_delay: float = PARAM_SPECS[P_ACTIVATION_DELAY].default
    base_time: float = PARAM_SPECS[P_BASE_TIME].default
    seat_factor: float = PARAM_SPECS[P_SEAT_FACTOR].default
    seat_cap: float = PARAM_SPECS[P_SEAT_CAP].default
    flush_extra: float = PARAM_SPECS[P_FLUSH_EXTRA].default
    flush_seat_factor: float = PARAM_SPECS[P_FLUSH_FACTOR].default
    short_flush_extra: float = PARAM_SPECS[P_SHORT_FLUSH_EXTRA].default
    short_flush_seat_factor: float = PARAM_SPECS[P_SHORT_FLUSH_FACTOR].default
    min_time: float = PARAM_SPECS[P_MIN_TIME].default
    max_time: float = PARAM_SPECS[P_MAX_TIME].default
    # v1.2: maximum fan on-time (minutes) and its two switches.
    max_on_time: float = PARAM_SPECS[P_MAX_ON_TIME].default
    max_on_enabled: bool = True
    max_on_manual: bool = False


def compute_post_ventilation(
    params: Params,
    seat_total: float,
    flushes: int,
    short_flushes: int = 0,
) -> float:
    """Return T in seconds (§4; v2.0: flushes are binary events).

    With S = min(D_t, P4):
    T = clamp(P2 + P3 * S + n_short * (P5b + P6b * S) + n_long * (P5 + P6 * S),
              P7, P8)
    A flush counts once whatever its duration; its weight grows with the lid
    open time through the factors P6 / P6b. If P7 > P8, P8 is used as both
    lower and upper bound. Without flushes T depends on the seat only.
    """
    seat = min(seat_total, params.seat_cap)
    raw = (
        params.base_time
        + params.seat_factor * seat
        + flushes * (params.flush_extra + params.flush_seat_factor * seat)
        + short_flushes
        * (params.short_flush_extra + params.short_flush_seat_factor * seat)
    )
    upper = params.max_time
    lower = min(params.min_time, upper)  # §4: P7 > P8 -> P8 on both sides
    return max(lower, min(raw, upper))


@dataclass
class SessionData:
    """Everything §8 requires to persist across a Home Assistant restart."""

    state: str = STATE_IDLE
    seat_total: float = 0.0  # D_t, seconds
    flush_count: int = 0  # n long flushes of the session (v2.0)
    short_flush_count: int = 0  # n short flushes of the session (v2.0)
    seat_since: float | None = None  # start of the seat opening in progress
    flush_since: float | None = None  # start of the flush in progress (any kind)
    flush_long: bool = False  # v1.1: the flush in progress counts as long
    p1_deadline: float | None = None  # P1 timer expiry (state waiting)
    countdown_start: float | None = None  # instant of entering countdown
    countdown_end: float | None = None  # expected switch-off instant
    off_pending: bool = False  # §8: OFF issued, not yet confirmed by the fan
    on_since: float | None = None  # v1.2: fan switched on (session or manual)
    last_stop_reason: str | None = None  # v1.2: why the fan was last stopped

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> SessionData:
        """Build from persisted data, ignoring unknown or invalid keys."""
        if not data:
            return cls()
        data = dict(data)
        # v2.0: sessions persisted by v1.x hold lists of durations; keep the count.
        for old, new in (
            ("flushes", "flush_count"),
            ("short_flushes", "short_flush_count"),
        ):
            if isinstance(data.get(old), list):
                data.setdefault(new, len(data[old]))
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
        # A flush sensor that is not configured simply stays NA forever.
        self.seat: str = NA
        self.flush_state: dict[str, str] = {FLUSH_SHORT: NA, FLUSH_LONG: NA}

    @property
    def flush_on(self) -> bool:
        """v1.1: the logical flush is on while ANY flush sensor is on."""
        return ON in self.flush_state.values()

    # ------------------------------------------------------------------ helpers
    @property
    def state(self) -> str:
        """Return the current state (§5)."""
        return self.data.state

    def post_ventilation(self) -> float:
        """Return T for the accumulators of the current session (§4)."""
        return compute_post_ventilation(
            self.params,
            self.data.seat_total,
            self.data.flush_count,
            self.data.short_flush_count,
        )

    @property
    def max_on_end(self) -> float | None:
        """v1.2: instant at which the fan is forced off, or None.

        Counted from ``on_since`` (fan switched on) for the whole session,
        countdown included; in manual mode only if ``max_on_manual`` is on.
        """
        p, d = self.params, self.data
        if not p.max_on_enabled or d.on_since is None:
            return None
        if self.state in (STATE_ACTIVE, STATE_COUNTDOWN) or (
            self.state == STATE_MANUAL and p.max_on_manual
        ):
            return d.on_since + p.max_on_time * 60
        return None

    def _transition(self, new_state: str, cause: str) -> None:
        """Change state and trace it at debug level (§9)."""
        old = self.data.state
        self.data.state = new_state
        self._log.debug(
            "[%s] %s -> %s (cause: %s) D_t=%.1fs n_long=%d n_short=%d",
            self.name,
            old,
            new_state,
            cause,
            self.data.seat_total,
            self.data.flush_count,
            self.data.short_flush_count,
        )

    def _open_session(self, now: float) -> None:
        """§4: the session starts when entering ``active``.

        ``seat_since`` is preserved so the opening that triggered the session
        is counted in D_t from its rising edge, delay included.
        """
        self.data.seat_total = 0.0
        self.data.flush_count = 0
        self.data.short_flush_count = 0
        self.data.p1_deadline = None
        self.data.on_since = now  # v1.2: start of the max on-time count
        self._log.info("[%s] session opened, fan ON", self.name)

    def _close_session(self) -> None:
        """§4: accumulators and timers are reset at the end of the session."""
        self.data.seat_total = 0.0
        self.data.flush_count = 0
        self.data.short_flush_count = 0
        self.data.seat_since = None
        self.data.flush_since = None
        self.data.flush_long = False
        self.data.p1_deadline = None
        self.data.countdown_start = None
        self.data.countdown_end = None
        self.data.on_since = None

    def _maybe_start_countdown(self, now: float, cause: str) -> None:
        """§5 active: seat off AND flush off -> compute T, start countdown.

        §8: a sensor in ``na`` (unavailable/unknown) counts as off.
        """
        if self.seat == ON or self.flush_on:
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
        short_flush: str,
        long_flush: str,
        fan: str,
        enabled: bool,
        now: float,
    ) -> list[str]:
        """Resume after a restart or reload (§8 "Riavvio HA")."""
        self.data = SessionData.from_dict(restored)
        self.seat = seat
        self.flush_state = {FLUSH_SHORT: short_flush, FLUSH_LONG: long_flush}
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
            on_since = self.data.on_since
            self._close_session()
            self.data.on_since = on_since  # v1.2: keep the manual on instant
            self._transition(STATE_MANUAL, "startup, fan still on")
        else:
            # waiting -> idle (§8), manual with fan off -> idle (§8),
            # idle -> idle, disabled re-enabled meanwhile -> idle (§7).
            self._close_session()
            self.data.off_pending = self.data.off_pending and state == STATE_IDLE
            self._transition(STATE_IDLE, f"startup from {state}")

        # v1.2: sessions persisted by older versions have no on_since: count
        # from now. Then apply a max on-time already exceeded while HA was down.
        if self.state in (STATE_ACTIVE, STATE_COUNTDOWN, STATE_MANUAL):
            if self.data.on_since is None:
                self.data.on_since = now
            if expired := self.max_on_expired(now):
                cmds = expired

        self._log.info(
            "[%s] started in state %s (seat=%s short=%s long=%s fan=%s)",
            self.name,
            self.state,
            seat,
            short_flush,
            long_flush,
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
        if d.flush_since is not None and not self.flush_on:
            self._record_flush(now)
        elif d.flush_since is None and self.flush_on:
            d.flush_since = now
            d.flush_long = self.flush_state[FLUSH_LONG] == ON

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

    def flush_event(self, kind: str, new: str, now: float) -> list[str]:
        """Handle a short/long flush state change (normalized).

        v1.1: the two sensors form ONE logical flush. It starts at the first
        rising edge while no other flush is on, ends when both are off, and
        counts as long if the long sensor was on at any time in between
        (dual-button plates may close both contacts).
        """
        other = FLUSH_LONG if kind == FLUSH_SHORT else FLUSH_SHORT
        old = self.flush_state[kind]
        self.flush_state[kind] = new
        if self.state in (STATE_MANUAL, STATE_DISABLED):
            return []  # §6/§7: sensor events ignored
        other_on = self.flush_state[other] == ON
        if old == OFF and new == ON:
            if other_on:
                # Overlap: same flush, upgraded to long if the long one joins.
                if kind == FLUSH_LONG and self.data.flush_since is not None:
                    self.data.flush_long = True
                return []
            return self._flush_rising(now, kind == FLUSH_LONG)
        if old == ON and new in (OFF, NA) and not other_on:
            # §8: on -> unavailable/unknown is treated as off.
            return self._flush_falling(now)
        return []

    def _record_flush(self, now: float) -> None:
        """Count the finished flush once, by kind (§4; v2.0: binary event)."""
        d = self.data
        if d.flush_since is not None:
            if d.flush_long:
                d.flush_count += 1
            else:
                d.short_flush_count += 1
            self._log.debug(
                "[%s] %s flush counted (n_long=%d n_short=%d, lasted %.1fs)",
                self.name,
                FLUSH_LONG if d.flush_long else FLUSH_SHORT,
                d.flush_count,
                d.short_flush_count,
                max(0.0, now - d.flush_since),
            )
        d.flush_since = None
        d.flush_long = False

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

    def _flush_rising(self, now: float, long: bool) -> list[str]:
        d = self.data
        d.flush_since = now
        d.flush_long = long
        if self.state in (STATE_IDLE, STATE_WAITING):
            # §5 idle/waiting + flush rising: cancel P1, fan ON, open session.
            # In waiting the opening in progress (seat_since) counts in D_t.
            self._open_session(now)
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
            self._record_flush(now)
            self._maybe_start_countdown(now, "flush ended")
            return []
        d.flush_since = None
        d.flush_long = False
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
        self._open_session(now)
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
        self.data.last_stop_reason = STOP_COUNTDOWN
        self._transition(STATE_IDLE, "countdown expired")
        self._log.info("[%s] session closed, fan OFF", self.name)
        return [FAN_OFF]

    def max_on_expired(self, now: float) -> list[str]:
        """v1.2: maximum on-time reached -> fan OFF, close session, idle.

        Applies in active/countdown, and in manual mode when enabled. A new
        session needs a new rising edge (same rule as a manual OFF, §6).
        """
        end = self.max_on_end
        if end is None or now + 0.5 < end:
            return []
        old = self.state
        self._close_session()
        self.data.off_pending = True
        self.data.last_stop_reason = STOP_MAX_ON
        self._transition(STATE_IDLE, f"max on-time reached in {old}")
        self._log.info(
            "[%s] max on-time (%s min) reached: fan OFF",
            self.name,
            self.params.max_on_time,
        )
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
                self.data.last_stop_reason = STOP_MANUAL_OFF
                self._transition(STATE_IDLE, "fan off, manual mode ended")
            return []
        if not manual:
            return []  # our own command echoed back

        if new == OFF:
            # §6 manual OFF in any state: cancel timers, close session, idle.
            if self.state != STATE_IDLE:
                self._close_session()
                self.data.last_stop_reason = STOP_MANUAL_OFF
                self._transition(STATE_IDLE, "manual fan OFF")
                self._log.info("[%s] manual OFF, session closed", self.name)
            return []
        # §6 manual ON -> manual: sensors ignored, fan not commanded.
        self._close_session()
        self.data.on_since = now  # v1.2: max on-time counted from here
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
            fan_was_on = self.state in (STATE_ACTIVE, STATE_COUNTDOWN)
            self._close_session()
            self.data.off_pending = False
            if fan_was_on:
                self.data.last_stop_reason = STOP_DISABLED
            self._transition(STATE_DISABLED, "disabled by user")
            self._log.info("[%s] disabled", self.name)
        elif enabled and self.state == STATE_DISABLED:
            self._transition(STATE_IDLE, "enabled by user")
            self._log.info("[%s] enabled", self.name)
        return []

    def params_changed(self, now: float) -> list[str]:
        """§5: parameter change during countdown -> end = start + new T.

        v1.2: the max on-time deadline is derived from the parameters, so a
        change that makes it already past switches the fan off at once.
        """
        if expired := self.max_on_expired(now):
            return expired
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
