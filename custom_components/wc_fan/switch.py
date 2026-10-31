"""Switches, restored across restarts.

- ``enabled``: automation on/off (SPEC.md §3 ``abilitato``, §7).
- ``max_on_enabled`` / ``max_on_manual`` (v1.2): maximum fan on-time limit,
  and whether it also applies when the fan was switched on by hand.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_OFF, STATE_ON, EntityCategory, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import WcFanConfigEntry
from .const import KEY_ENABLED, OPTION_SWITCHES
from .entity import WcFanEntity
from .manager import WcFanManager

_LOGGER = logging.getLogger(__package__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WcFanConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the enable switch and the v1.2 option switches."""
    manager = entry.runtime_data
    async_add_entities(
        [
            WcFanEnabledSwitch(manager),
            *(
                WcFanOptionSwitch(manager, key, default)
                for key, default in OPTION_SWITCHES.items()
            ),
        ]
    )


class WcFanEnabledSwitch(WcFanEntity, SwitchEntity, RestoreEntity):
    """On = automation active (default on, §3); off = disabled (§7)."""

    def __init__(self, manager: WcFanManager) -> None:
        """Create the switch."""
        super().__init__(manager, Platform.SWITCH, KEY_ENABLED)

    @property
    def is_on(self) -> bool:
        """Return the enable flag held by the manager."""
        return self.manager.enabled

    async def async_added_to_hass(self) -> None:
        """Restore the last state; default on when never stored (§3)."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        enabled = last is None or last.state != STATE_OFF
        self.manager.set_enabled(enabled, restoring=True)
        _LOGGER.debug("[%s] restored enabled = %s", self.manager.name, enabled)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable (§7: -> idle, only subsequent rising edges)."""
        self.manager.set_enabled(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable (§7: cancel timers, close session, fan not commanded)."""
        self.manager.set_enabled(False)
        self.async_write_ha_state()


class WcFanOptionSwitch(WcFanEntity, SwitchEntity, RestoreEntity):
    """v1.2: boolean option of the max on-time limit (configuration switch)."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, manager: WcFanManager, key: str, default: bool) -> None:
        """Create the switch with its default (used when nothing is stored)."""
        super().__init__(manager, Platform.SWITCH, key)
        self._key = key
        self._default = default

    @property
    def is_on(self) -> bool:
        """Return the option value held by the parameters."""
        return bool(getattr(self.manager.params, self._key))

    async def async_added_to_hass(self) -> None:
        """Restore the last state, or apply the default."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        value = self._default
        if last is not None and last.state in (STATE_ON, STATE_OFF):
            value = last.state == STATE_ON
        self.manager.set_param(self._key, value, restoring=True)
        _LOGGER.debug("[%s] restored %s = %s", self.manager.name, self._key, value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the option on; the deadline is recomputed at once."""
        self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the option off; the deadline is recomputed at once."""
        self._set(False)

    def _set(self, value: bool) -> None:
        self.manager.set_param(self._key, value)
        _LOGGER.info("[%s] %s set to %s", self.manager.name, self._key, value)
        self.async_write_ha_state()
