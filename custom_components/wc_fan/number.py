"""Number entities P1..P8 (SPEC.md §3), restored across restarts."""

from __future__ import annotations

import logging

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberMode,
    RestoreNumber,
)
from homeassistant.const import EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import WcFanConfigEntry
from .const import DOMAIN, FLUSH_PARAMS
from .engine import PARAM_SPECS
from .entity import WcFanEntity
from .manager import WcFanManager

_LOGGER = logging.getLogger(__package__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WcFanConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create one number entity per parameter of §3.

    v1.1: the extra/factor parameters of a flush sensor that is not
    configured are not created, and are removed from the entity registry if a
    previous configuration had created them.
    """
    manager = entry.runtime_data
    skipped = {
        key
        for kind, keys in FLUSH_PARAMS.items()
        if kind not in manager.flush_ids
        for key in keys
    }
    registry = er.async_get(hass)
    for key in skipped:
        unique_id = f"{entry.entry_id}_{key}"
        if entity_id := registry.async_get_entity_id(
            Platform.NUMBER, DOMAIN, unique_id
        ):
            registry.async_remove(entity_id)
            _LOGGER.info(
                "[%s] %s removed (flush sensor not set)", manager.name, entity_id
            )
    async_add_entities(
        WcFanNumber(manager, key) for key in PARAM_SPECS if key not in skipped
    )


class WcFanNumber(WcFanEntity, RestoreNumber):
    """Tunable parameter; the value is pushed into the state machine."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX

    def __init__(self, manager: WcFanManager, key: str) -> None:
        """Apply range, step and unit from the §3 table."""
        super().__init__(manager, Platform.NUMBER, key)
        spec = PARAM_SPECS[key]
        self._key = key
        self._attr_native_min_value = spec.minimum
        self._attr_native_max_value = spec.maximum
        self._attr_native_step = spec.step
        self._attr_native_unit_of_measurement = spec.unit
        # Seconds and minutes (v1.2) are durations; s/s factors have none.
        if spec.unit in ("s", "min"):
            self._attr_device_class = NumberDeviceClass.DURATION
        self._attr_native_value = spec.default

    async def async_added_to_hass(self) -> None:
        """Restore the last value (§3: values persist across restarts)."""
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            spec = PARAM_SPECS[self._key]
            value = min(max(float(last.native_value), spec.minimum), spec.maximum)
            self._attr_native_value = value
            _LOGGER.debug("[%s] restored %s = %s", self.manager.name, self._key, value)
        self.manager.set_param(
            self._key, float(self._attr_native_value), restoring=True
        )

    async def async_set_native_value(self, value: float) -> None:
        """User changed the parameter (§5: recompute T in countdown)."""
        self._attr_native_value = value
        self.manager.set_param(self._key, float(value))
        _LOGGER.info("[%s] %s set to %s", self.manager.name, self._key, value)
        self.async_write_ha_state()
