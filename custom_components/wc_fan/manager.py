"""Home Assistant glue around the pure engine (SPEC.md §4-§8).

Responsibilities:
- listen to the seat / flush / fan state changes and normalize them;
- tell manual fan changes apart from ours by comparing context ids (§6);
- schedule the P1 and countdown timers exposed by the engine as deadlines;
- send the fan commands, logging a warning when they cannot be sent (§8);
- persist the session in a Store and resume it after a restart (§8);
- notify the entities (state / expected end sensors) of every change.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from datetime import datetime
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import (
    CALLBACK_TYPE,
    Context,
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import (
    async_track_point_in_utc_time,
    async_track_state_change_event,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_FAN,
    CONF_FLUSH,
    CONF_SEAT,
    CONF_SHORT_FLUSH,
    DOMAIN,
    FLUSH_LONG,
    FLUSH_SHORT,
    STATE_COUNTDOWN,
    STORAGE_SAVE_DELAY,
    STORAGE_VERSION,
)
from .engine import FAN_ON, NA, OFF, ON, Params, WcFanEngine

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__package__)

# How many of our own context ids are remembered for the §6 comparison.
_OWN_CONTEXTS = 20


def normalize(state: State | None) -> str:
    """Collapse an HA state into on / off / na (§4, §8)."""
    if state is None:
        return NA
    if state.state == STATE_ON:
        return ON
    if state.state == STATE_OFF:
        return OFF
    return NA  # unavailable, unknown or anything unexpected


class WcFanManager:
    """Runtime object of one config entry (one bathroom fan)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Read the configuration (§2: options override the initial data)."""
        self.hass = hass
        self.entry = entry
        config = {**entry.data, **entry.options}
        self.name: str = entry.title
        self.seat_id: str = config[CONF_SEAT]
        self.fan_id: str = config[CONF_FAN]
        # v1.1: both flush sensors are optional; only configured ones are kept.
        self.flush_ids: dict[str, str] = {
            kind: entity_id
            for kind, key in ((FLUSH_SHORT, CONF_SHORT_FLUSH), (FLUSH_LONG, CONF_FLUSH))
            if (entity_id := config.get(key))
        }
        self._flush_kind_by_id = {v: k for k, v in self.flush_ids.items()}
        self.params = Params()
        self.enabled = True
        self.engine = WcFanEngine(self.name, self.params, _LOGGER)
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self._restored: dict[str, Any] | None = None
        self._started = False
        self._own_contexts: deque[str] = deque(maxlen=_OWN_CONTEXTS)
        self._listeners: list[Callable[[], None]] = []
        self._unsubs: list[CALLBACK_TYPE] = []
        self._p1_unsub: CALLBACK_TYPE | None = None
        self._p1_at: float | None = None
        self._end_unsub: CALLBACK_TYPE | None = None
        self._end_at: float | None = None
        self._max_on_unsub: CALLBACK_TYPE | None = None  # v1.2
        self._max_on_at: float | None = None

    # ---------------------------------------------------------------- lifecycle
    async def async_load(self) -> None:
        """Load the persisted session (§8) before the entities are added."""
        self._restored = await self._store.async_load()
        _LOGGER.debug("[%s] restored session: %s", self.name, self._restored)

    @callback
    def async_start_when_ready(self) -> None:
        """Start once HA is running, so the input entities exist (§8)."""
        self._unsubs.append(async_at_started(self.hass, self._async_start))

    async def _async_start(self, _hass: HomeAssistant) -> None:
        """Resume the engine and subscribe to the input entities."""
        tracked = [self.seat_id, *self.flush_ids.values(), self.fan_id]
        for entity_id in tracked:
            if self.hass.states.get(entity_id) is None:
                _LOGGER.warning(
                    "[%s] entity %s not found, treated as unavailable",
                    self.name,
                    entity_id,
                )
        cmds = self.engine.start(
            self._restored,
            normalize(self.hass.states.get(self.seat_id)),
            self._flush_initial(FLUSH_SHORT),
            self._flush_initial(FLUSH_LONG),
            normalize(self.hass.states.get(self.fan_id)),
            self.enabled,
            dt_util.utcnow().timestamp(),
        )
        self._started = True
        self._unsubs.append(
            async_track_state_change_event(
                self.hass,
                tracked,
                self._async_state_changed,
            )
        )
        self._apply(cmds)
        _LOGGER.info(
            "[%s] running: seat=%s short=%s long=%s fan=%s",
            self.name,
            self.seat_id,
            self.flush_ids.get(FLUSH_SHORT, "-"),
            self.flush_ids.get(FLUSH_LONG, "-"),
            self.fan_id,
        )

    def _flush_initial(self, kind: str) -> str:
        """Return the initial state of a flush sensor; NA when not configured."""
        entity_id = self.flush_ids.get(kind)
        return normalize(self.hass.states.get(entity_id)) if entity_id else NA

    async def async_shutdown(self) -> None:
        """Unsubscribe everything and flush the persisted session."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self._cancel_p1()
        self._cancel_end()
        self._cancel_max_on()
        if self._started:
            await self._store.async_save(self.engine.as_dict())
        self._started = False

    async def async_remove_store(self) -> None:
        """Delete the persisted session when the entry is removed."""
        await self._store.async_remove()

    # -------------------------------------------------------------- entity API
    @callback
    def async_add_listener(self, update: Callable[[], None]) -> CALLBACK_TYPE:
        """Register an entity refresh callback; returns the remover."""
        self._listeners.append(update)

        @callback
        def _remove() -> None:
            self._listeners.remove(update)

        return _remove

    @callback
    def set_param(
        self, key: str, value: float | bool, *, restoring: bool = False
    ) -> None:
        """Update a parameter from a number/option switch (§3, §5, v1.2).

        The new value is applied to T in countdown and to the max on-time
        deadline (an already exceeded deadline switches the fan off at once).
        """
        setattr(self.params, key, value)
        if restoring or not self._started:
            return
        _LOGGER.debug("[%s] parameter %s = %s", self.name, key, value)
        self._apply(self.engine.params_changed(dt_util.utcnow().timestamp()))

    @callback
    def set_enabled(self, enabled: bool, *, restoring: bool = False) -> None:
        """Update the enable switch (§7)."""
        self.enabled = enabled
        if restoring or not self._started:
            return
        self._apply(self.engine.set_enabled(enabled, dt_util.utcnow().timestamp()))

    @property
    def expected_end(self) -> datetime | None:
        """§3 expected_end: switch-off instant in countdown, otherwise None."""
        end = self.engine.data.countdown_end
        if self.engine.state != STATE_COUNTDOWN or end is None:
            return None
        return dt_util.utc_from_timestamp(end)

    # ------------------------------------------------------------------ events
    @callback
    def _async_state_changed(self, event: Event[EventStateChangedData]) -> None:
        """Dispatch a state change of one of the tracked input entities."""
        entity_id = event.data["entity_id"]
        old_state = event.data["old_state"]
        new_state = event.data["new_state"]
        old, new = normalize(old_state), normalize(new_state)
        now = dt_util.utcnow().timestamp()
        _LOGGER.debug("[%s] %s: %s -> %s", self.name, entity_id, old, new)
        if new == NA and old != NA:
            _LOGGER.warning("[%s] %s became unavailable", self.name, entity_id)

        if entity_id == self.seat_id:
            cmds = self.engine.seat_event(new, now)
        elif (kind := self._flush_kind_by_id.get(entity_id)) is not None:
            cmds = self.engine.flush_event(kind, new, now)
        else:
            cmds = self.engine.fan_event(
                old, new, self._is_manual(old, new, new_state), now
            )
        self._apply(cmds)

    def _is_manual(self, old: str, new: str, new_state: State | None) -> bool:
        """§6: manual if the context was not generated by this integration.

        Transitions from/to unavailable are never manual. ``parent_id`` is
        also checked so wrappers (e.g. template switches) count as ours.
        """
        if old == NA or new == NA or new_state is None:
            return False
        context = new_state.context
        return not (
            context.id in self._own_contexts
            or (
                context.parent_id is not None
                and context.parent_id in self._own_contexts
            )
        )

    # ---------------------------------------------------------------- actions
    @callback
    def _apply(self, cmds: list[str]) -> None:
        """Execute commands, align timers, persist and refresh the entities."""
        for cmd in cmds:
            self.entry.async_create_task(
                self.hass, self._async_command(cmd), f"{DOMAIN} fan {cmd}"
            )
        self._sync_timers()
        self._store.async_delay_save(self.engine.as_dict, STORAGE_SAVE_DELAY)
        for update in list(self._listeners):
            update()

    async def _async_command(self, service: str) -> None:
        """Send turn_on/turn_off to the fan (switch or fan domain).

        §8: when the fan is unavailable or the call fails a warning is logged
        and the state machine keeps going; realignment happens when the fan
        comes back available.
        """
        if normalize(self.hass.states.get(self.fan_id)) == NA:
            _LOGGER.warning(
                "[%s] fan %s unavailable: %s not sent", self.name, self.fan_id, service
            )
            return
        context = Context()
        self._own_contexts.append(context.id)
        domain = self.fan_id.split(".", 1)[0]
        try:
            await self.hass.services.async_call(
                domain,
                SERVICE_TURN_ON if service == FAN_ON else SERVICE_TURN_OFF,
                {ATTR_ENTITY_ID: self.fan_id},
                blocking=True,
                context=context,
            )
        except HomeAssistantError as err:
            _LOGGER.warning(
                "[%s] fan %s: %s failed: %s", self.name, self.fan_id, service, err
            )
            return
        _LOGGER.debug("[%s] fan %s: %s sent", self.name, self.fan_id, service)

    # ------------------------------------------------------------------ timers
    @callback
    def _sync_timers(self) -> None:
        """Reschedule the P1 / countdown / max on-time timers on change."""
        p1 = self.engine.data.p1_deadline
        if p1 != self._p1_at:
            self._cancel_p1()
            if p1 is not None:
                self._p1_at = p1
                self._p1_unsub = async_track_point_in_utc_time(
                    self.hass, self._async_p1_fired, dt_util.utc_from_timestamp(p1)
                )
        end = self.engine.data.countdown_end
        if end != self._end_at:
            self._cancel_end()
            if end is not None:
                self._end_at = end
                self._end_unsub = async_track_point_in_utc_time(
                    self.hass, self._async_end_fired, dt_util.utc_from_timestamp(end)
                )
        # v1.2: the max on-time deadline is derived from on_since + parameters.
        max_on = self.engine.max_on_end
        if max_on != self._max_on_at:
            self._cancel_max_on()
            if max_on is not None:
                self._max_on_at = max_on
                self._max_on_unsub = async_track_point_in_utc_time(
                    self.hass,
                    self._async_max_on_fired,
                    dt_util.utc_from_timestamp(max_on),
                )
                _LOGGER.debug(
                    "[%s] max on-time deadline: %s",
                    self.name,
                    dt_util.utc_from_timestamp(max_on).isoformat(),
                )

    @callback
    def _cancel_p1(self) -> None:
        if self._p1_unsub is not None:
            self._p1_unsub()
        self._p1_unsub = None
        self._p1_at = None

    @callback
    def _cancel_end(self) -> None:
        if self._end_unsub is not None:
            self._end_unsub()
        self._end_unsub = None
        self._end_at = None

    @callback
    def _cancel_max_on(self) -> None:
        if self._max_on_unsub is not None:
            self._max_on_unsub()
        self._max_on_unsub = None
        self._max_on_at = None

    @callback
    def _async_p1_fired(self, now: datetime) -> None:
        """P1 timer expired (§5 waiting)."""
        self._p1_unsub = None
        self._p1_at = None
        self._apply(self.engine.p1_expired(now.timestamp()))

    @callback
    def _async_end_fired(self, now: datetime) -> None:
        """Countdown expired (§5 countdown)."""
        self._end_unsub = None
        self._end_at = None
        self._apply(self.engine.countdown_expired(now.timestamp()))

    @callback
    def _async_max_on_fired(self, now: datetime) -> None:
        """Maximum fan on-time reached (v1.2)."""
        self._max_on_unsub = None
        self._max_on_at = None
        self._apply(self.engine.max_on_expired(now.timestamp()))

    @property
    def max_on_end(self) -> datetime | None:
        """v1.2: instant of the forced switch-off, or None."""
        end = self.engine.max_on_end
        return None if end is None else dt_util.utc_from_timestamp(end)
