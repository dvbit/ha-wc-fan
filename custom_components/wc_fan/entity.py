"""Base entity shared by all WC Fan entities (SPEC.md §3)."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.util import slugify

from .const import DOMAIN
from .manager import WcFanManager


class WcFanEntity(Entity):
    """One entity of a WC Fan instance, grouped under one device."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, manager: WcFanManager, platform: str, key: str) -> None:
        """Set ids, translated name and device.

        The localized name comes from ``translation_key`` (5 languages).
        The entity_id is suggested explicitly in English
        (``<platform>.<instance>_<key>``) so it does not depend on the
        Home Assistant language at first registration.
        """
        self.manager = manager
        entry_id = manager.entry.entry_id
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry_id}_{key}"
        self.entity_id = f"{platform}.{slugify(manager.name)}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)},
            name=manager.name,
            manufacturer="dvbit",
            model="WC Fan",
        )

    async def async_added_to_hass(self) -> None:
        """Refresh this entity on every state machine change."""
        await super().async_added_to_hass()
        self.async_on_remove(self.manager.async_add_listener(self.async_write_ha_state))
