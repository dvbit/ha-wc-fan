"""WC Fan: bathroom fan driven by seat lid and flush (SPEC.md §1).

One config entry = one fan (multi-instance). Setup order matters:
1. load the persisted session (§8);
2. add the entities, which restore P1..P8 and the enable switch (§3);
3. start the state machine once Home Assistant is running.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import CONF_FAN, CONF_FLUSH, CONF_SEAT
from .manager import WcFanManager

_LOGGER = logging.getLogger(__package__)

PLATFORMS: list[Platform] = [Platform.NUMBER, Platform.SENSOR, Platform.SWITCH]

type WcFanConfigEntry = ConfigEntry[WcFanManager]


async def async_setup_entry(hass: HomeAssistant, entry: WcFanConfigEntry) -> bool:
    """Set up one WC fan instance."""
    config = {**entry.data, **entry.options}
    missing = [key for key in (CONF_SEAT, CONF_FLUSH, CONF_FAN) if not config.get(key)]
    if missing:
        # §2: the three entities are mandatory.
        _LOGGER.error("[%s] invalid configuration, missing: %s", entry.title, missing)
        return False

    manager = WcFanManager(hass, entry)
    await manager.async_load()
    entry.runtime_data = manager
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    manager.async_start_when_ready()
    # §2: options flow changes -> reload with the new entities.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    _LOGGER.debug("[%s] entry set up", entry.title)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: WcFanConfigEntry) -> bool:
    """Unload the entry, persisting the session first (§8)."""
    await entry.runtime_data.async_shutdown()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: WcFanConfigEntry) -> None:
    """Delete the persisted session of a removed entry."""
    manager = WcFanManager(hass, entry)
    await manager.async_remove_store()
    _LOGGER.info("[%s] removed, stored session deleted", entry.title)


async def _async_update_listener(hass: HomeAssistant, entry: WcFanConfigEntry) -> None:
    """Reload after an options change."""
    _LOGGER.info("[%s] options changed, reloading", entry.title)
    await hass.config_entries.async_reload(entry.entry_id)
