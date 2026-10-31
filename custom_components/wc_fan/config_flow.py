"""Config flow and options flow (SPEC.md §2, extended in v1.1).

v1.1: the seat sensor and the fan are required; the short and the long flush
sensors are optional. An optional field cleared in the form is simply absent
from ``user_input``, so it is always saved explicitly (``None``) to override
any value stored in ``entry.data`` by a previous version.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    TextSelector,
)

from .const import (
    CONF_FAN,
    CONF_FLUSH,
    CONF_SEAT,
    CONF_SHORT_FLUSH,
    CONF_TIMER,
    DOMAIN,
    FAN_DOMAINS,
)

_LOGGER = logging.getLogger(__package__)

_BINARY = EntitySelector(EntitySelectorConfig(domain="binary_sensor"))

# §2 / v1.1: seat required, short and long flush optional, fan switch or fan.
ENTITIES_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SEAT): _BINARY,
        vol.Optional(CONF_SHORT_FLUSH): _BINARY,
        vol.Optional(CONF_FLUSH): _BINARY,
        vol.Required(CONF_FAN): EntitySelector(
            EntitySelectorConfig(domain=list(FAN_DOMAINS))
        ),
        # v1.4: optional timer helper that mirrors the remaining fan time.
        vol.Optional(CONF_TIMER): EntitySelector(EntitySelectorConfig(domain="timer")),
    }
)
USER_SCHEMA = vol.Schema({vol.Required(CONF_NAME): TextSelector()}).extend(
    ENTITIES_SCHEMA.schema
)


def _entities(user_input: dict[str, Any]) -> dict[str, Any]:
    """Return the entity keys, with ``None`` for an empty optional one."""
    return {
        CONF_SEAT: user_input[CONF_SEAT],
        CONF_SHORT_FLUSH: user_input.get(CONF_SHORT_FLUSH) or None,
        CONF_FLUSH: user_input.get(CONF_FLUSH) or None,
        CONF_FAN: user_input[CONF_FAN],
        CONF_TIMER: user_input.get(CONF_TIMER) or None,
    }


def _validate(
    hass: HomeAssistant, data: dict[str, Any], current_entry_id: str | None
) -> dict[str, str]:
    """Return form errors; invalid configurations are logged at ERROR."""
    errors: dict[str, str] = {}
    sensors = [data[k] for k in (CONF_SEAT, CONF_SHORT_FLUSH, CONF_FLUSH) if data[k]]
    if len(sensors) != len(set(sensors)):
        # v1.1: seat, short and long flush must be distinct entities.
        errors["base"] = "same_entity"
    else:
        # One fan cannot be driven by two instances (they would fight).
        for entry in hass.config_entries.async_entries(DOMAIN):
            config = {**entry.data, **entry.options}
            if entry.entry_id == current_entry_id:
                continue
            if config.get(CONF_FAN) == data[CONF_FAN]:
                errors[CONF_FAN] = "fan_in_use"
                break
            # v1.4: a timer helper mirrors one instance only.
            if data[CONF_TIMER] and config.get(CONF_TIMER) == data[CONF_TIMER]:
                errors[CONF_TIMER] = "timer_in_use"
                break
    if errors:
        _LOGGER.error("Invalid configuration %s: %s", data, errors)
    return errors


class WcFanConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create a WC Fan instance (multi-instance, §1)."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask name, seat, optional flush sensors and fan."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _entities(user_input)
            errors = _validate(self.hass, data, None)
            if not errors:
                return self.async_create_entry(title=user_input[CONF_NAME], data=data)
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow (no arguments: config_entry is injected)."""
        return WcFanOptionsFlow()


class WcFanOptionsFlow(OptionsFlow):
    """§2: change the entities; the entry is reloaded afterwards."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the entities form prefilled with the current values."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _entities(user_input)
            errors = _validate(self.hass, data, self.config_entry.entry_id)
            if not errors:
                return self.async_create_entry(data=data)
        current = user_input or {
            k: v
            for k, v in {**self.config_entry.data, **self.config_entry.options}.items()
            if v  # leave empty optional fields empty in the form
        }
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(ENTITIES_SCHEMA, current),
            errors=errors,
        )
