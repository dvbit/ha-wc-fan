"""Config flow and options flow (SPEC.md §2)."""

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

from .const import CONF_FAN, CONF_FLUSH, CONF_SEAT, DOMAIN, FAN_DOMAINS

_LOGGER = logging.getLogger(__package__)

# §2: seat and flush are binary_sensors; the fan is a switch or a fan.
ENTITIES_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SEAT): EntitySelector(
            EntitySelectorConfig(domain="binary_sensor")
        ),
        vol.Required(CONF_FLUSH): EntitySelector(
            EntitySelectorConfig(domain="binary_sensor")
        ),
        vol.Required(CONF_FAN): EntitySelector(
            EntitySelectorConfig(domain=list(FAN_DOMAINS))
        ),
    }
)
USER_SCHEMA = vol.Schema({vol.Required(CONF_NAME): TextSelector()}).extend(
    ENTITIES_SCHEMA.schema
)


def _validate(
    hass: HomeAssistant, data: dict[str, Any], current_entry_id: str | None
) -> dict[str, str]:
    """Return form errors; invalid configurations are logged at ERROR."""
    errors: dict[str, str] = {}
    if data[CONF_SEAT] == data[CONF_FLUSH]:
        errors["base"] = "same_entity"
    else:
        # One fan cannot be driven by two instances (they would fight).
        for entry in hass.config_entries.async_entries(DOMAIN):
            config = {**entry.data, **entry.options}
            if (
                entry.entry_id != current_entry_id
                and config.get(CONF_FAN) == data[CONF_FAN]
            ):
                errors[CONF_FAN] = "fan_in_use"
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
        """Ask name, seat, flush and fan."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(self.hass, user_input, None)
            if not errors:
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data={
                        CONF_SEAT: user_input[CONF_SEAT],
                        CONF_FLUSH: user_input[CONF_FLUSH],
                        CONF_FAN: user_input[CONF_FAN],
                    },
                )
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
    """§2: change the three entities; the entry is reloaded afterwards."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the entities form prefilled with the current values."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate(self.hass, user_input, self.config_entry.entry_id)
            if not errors:
                return self.async_create_entry(data=user_input)
        current = user_input or {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(ENTITIES_SCHEMA, current),
            errors=errors,
        )
