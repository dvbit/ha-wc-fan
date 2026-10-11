"""State and expected end sensors (SPEC.md §3 ``stato``, ``fine_prevista``).

Their values are derived from the state machine, whose session is persisted
by the manager's Store (§8), so they are restored after a restart as well.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import WcFanConfigEntry
from .const import KEY_EXPECTED_END, KEY_STATE, STATES
from .entity import WcFanEntity
from .manager import WcFanManager


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WcFanConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the two sensors."""
    manager = entry.runtime_data
    async_add_entities([WcFanStateSensor(manager), WcFanExpectedEndSensor(manager)])


class WcFanStateSensor(WcFanEntity, SensorEntity):
    """Enum sensor with the §5 state; attributes expose D_t, S and T (§9)."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(STATES)

    def __init__(self, manager: WcFanManager) -> None:
        """Create the sensor."""
        super().__init__(manager, Platform.SENSOR, KEY_STATE)

    @property
    def native_value(self) -> str:
        """Return the current state of the state machine."""
        return self.manager.engine.state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the session accumulators (§4) for diagnostics."""
        engine = self.manager.engine
        return {
            "seat_total": round(engine.data.seat_total, 1),
            "flush_durations": [round(d, 1) for d in engine.data.flushes or []],
            "post_ventilation": round(engine.post_ventilation(), 1),
        }


class WcFanExpectedEndSensor(WcFanEntity, SensorEntity):
    """Timestamp of the switch-off in countdown, otherwise None (§3)."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, manager: WcFanManager) -> None:
        """Create the sensor."""
        super().__init__(manager, Platform.SENSOR, KEY_EXPECTED_END)

    @property
    def native_value(self) -> datetime | None:
        """Return the expected switch-off instant."""
        return self.manager.expected_end
