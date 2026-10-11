"""Enable switch (SPEC.md §3 ``abilitato``, §7), restored across restarts."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_OFF, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import WcFanConfigEntry
from .const import KEY_ENABLED
from .entity import WcFanEntity
from .manager import WcFanManager

_LOGGER = logging.getLogger(__package__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WcFanConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the enable switch."""
    async_add_entities([WcFanEnabledSwitch(entry.runtime_data)])


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
