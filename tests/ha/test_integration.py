"""Home Assistant level tests: config flow, entities, timers, context (§2-§8)."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.wc_fan.const import (
    CONF_FAN,
    CONF_FLUSH,
    CONF_SEAT,
    CONF_SHORT_FLUSH,
    DOMAIN,
)
from homeassistant import config_entries
from homeassistant.core import Context, HomeAssistant, ServiceCall
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er

SEAT = "binary_sensor.seat"
FLUSH = "binary_sensor.flush"
SHORT = "binary_sensor.short_flush"
FAN = "fan.test_fan"
DATA = {CONF_SEAT: SEAT, CONF_FLUSH: FLUSH, CONF_FAN: FAN}


async def _setup(
    hass: HomeAssistant, data: dict | None = None
) -> tuple[MockConfigEntry, list[str]]:
    """Create inputs, a fake fan honouring the call context, and the entry."""
    hass.states.async_set(SEAT, "off")
    hass.states.async_set(FLUSH, "off")
    hass.states.async_set(SHORT, "off")
    hass.states.async_set(FAN, "off")
    calls: list[str] = []

    async def _fan(call: ServiceCall) -> None:
        calls.append(call.service)
        state = "on" if call.service == "turn_on" else "off"
        hass.states.async_set(FAN, state, context=call.context)

    hass.services.async_register("fan", "turn_on", _fan)
    hass.services.async_register("fan", "turn_off", _fan)
    entry = MockConfigEntry(domain=DOMAIN, title="Bagno", data=data or DATA)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, calls


async def _tick(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float
) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_config_flow(hass: HomeAssistant) -> None:
    """§2: user step creates the entry; validation errors."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Bagno", **DATA, CONF_FLUSH: SEAT}
    )
    assert result["errors"] == {"base": "same_entity"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Bagno", **DATA}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Bagno"
    assert result["data"] == {**DATA, CONF_SHORT_FLUSH: None}

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Altro", **DATA}
    )
    assert result["errors"] == {CONF_FAN: "fan_in_use"}


async def test_entities_and_options(hass: HomeAssistant) -> None:
    """§3 entities with English ids and defaults; §2 options flow."""
    entry, _ = await _setup(hass)
    assert hass.states.get("switch.bagno_enabled").state == "on"
    assert hass.states.get("sensor.bagno_state").state == "idle"
    assert hass.states.get("sensor.bagno_expected_end").state == "unknown"
    assert float(hass.states.get("number.bagno_activation_delay").state) == 10
    assert float(hass.states.get("number.bagno_seat_factor").state) == 0.5
    assert float(hass.states.get("number.bagno_max_time").state) == 1200

    hass.states.async_set("binary_sensor.seat2", "off")
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**DATA, CONF_SEAT: "binary_sensor.seat2"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.runtime_data.seat_id == "binary_sensor.seat2"


async def test_full_session(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """§5: seat -> P1 -> fan ON -> countdown -> fan OFF."""
    _, calls = await _setup(hass)
    hass.states.async_set(SEAT, "on")
    await hass.async_block_till_done()
    assert hass.states.get("sensor.bagno_state").state == "waiting"
    await _tick(hass, freezer, 11)
    assert calls == ["turn_on"]
    assert hass.states.get("sensor.bagno_state").state == "active"
    # Our own command must not be seen as manual (§6 context comparison).
    assert hass.states.get(FAN).state == "on"
    await _tick(hass, freezer, 9)  # seat open 20 s in total
    hass.states.async_set(SEAT, "off")
    await hass.async_block_till_done()
    state = hass.states.get("sensor.bagno_state")
    assert state.state == "countdown"
    assert state.attributes["post_ventilation"] == 130  # 120 + 0.5 * 20
    assert hass.states.get("sensor.bagno_expected_end").state != "unknown"
    await _tick(hass, freezer, 129)
    assert calls == ["turn_on"]
    await _tick(hass, freezer, 2)
    assert calls == ["turn_on", "turn_off"]
    assert hass.states.get("sensor.bagno_state").state == "idle"


async def test_manual_and_disable(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """§6 manual ON/OFF via foreign context; §7 disable."""
    _, calls = await _setup(hass)
    hass.states.async_set(FAN, "on", context=Context())
    await hass.async_block_till_done()
    assert hass.states.get("sensor.bagno_state").state == "manual"
    hass.states.async_set(FLUSH, "on")
    await hass.async_block_till_done()
    assert calls == []  # sensors ignored in manual
    hass.states.async_set(FAN, "off", context=Context())
    await hass.async_block_till_done()
    assert hass.states.get("sensor.bagno_state").state == "idle"

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.bagno_enabled"}, blocking=True
    )
    assert hass.states.get("sensor.bagno_state").state == "disabled"
    hass.states.async_set(FLUSH, "off")
    hass.states.async_set(FLUSH, "on")
    await hass.async_block_till_done()
    assert calls == []


async def test_param_change_and_reload_persistence(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """§5 parameter change in countdown; §8 session survives a reload."""
    entry, calls = await _setup(hass)
    hass.states.async_set(FLUSH, "on")
    await hass.async_block_till_done()
    assert calls == ["turn_on"]
    await _tick(hass, freezer, 5)
    hass.states.async_set(FLUSH, "off")
    await hass.async_block_till_done()
    # T = 120 + 60 + 2 * 5 = 190
    assert hass.states.get("sensor.bagno_state").attributes["post_ventilation"] == 190

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.bagno_state").state == "countdown"

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": "number.bagno_max_time", "value": 60},
        blocking=True,
    )
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": "number.bagno_min_time", "value": 0},
        blocking=True,
    )
    await _tick(hass, freezer, 61)
    assert calls == ["turn_on", "turn_off"]
    assert hass.states.get("sensor.bagno_state").state == "idle"
    assert float(hass.states.get("number.bagno_max_time").state) == 60


# ----------------------------------------------------------------- v1.1 tests
async def test_config_flow_without_flush(hass: HomeAssistant) -> None:
    """v1.1: both flush sensors are optional; duplicates are rejected."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "name": "Bagno",
            CONF_SEAT: SEAT,
            CONF_SHORT_FLUSH: FLUSH,
            CONF_FLUSH: FLUSH,
            CONF_FAN: FAN,
        },
    )
    assert result["errors"] == {"base": "same_entity"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Bagno", CONF_SEAT: SEAT, CONF_FAN: FAN}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        CONF_SEAT: SEAT,
        CONF_SHORT_FLUSH: None,
        CONF_FLUSH: None,
        CONF_FAN: FAN,
    }


async def test_seat_only_entities_and_session(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """v1.1: no flush sensors -> no flush numbers, T from the seat only."""
    await _setup(hass, {CONF_SEAT: SEAT, CONF_FAN: FAN})
    for key in (
        "flush_extra",
        "flush_factor",
        "short_flush_extra",
        "short_flush_factor",
    ):
        assert hass.states.get(f"number.bagno_{key}") is None
    hass.states.async_set(SEAT, "on")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 60)
    hass.states.async_set(SEAT, "off")
    await hass.async_block_till_done()
    state = hass.states.get("sensor.bagno_state")
    assert state.state == "countdown"
    assert state.attributes["post_ventilation"] == 150  # 120 + 0.5 * 60


async def test_short_and_long_flush(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """v1.1: short and long sensors, overlap counted once as long."""
    _, calls = await _setup(hass, {**DATA, CONF_SHORT_FLUSH: SHORT})
    assert hass.states.get("number.bagno_short_flush_extra") is not None
    hass.states.async_set(SHORT, "on")
    await hass.async_block_till_done()
    assert calls == ["turn_on"]
    await _tick(hass, freezer, 3)
    hass.states.async_set(SHORT, "off")
    await hass.async_block_till_done()
    attrs = hass.states.get("sensor.bagno_state").attributes
    assert attrs["short_flush_durations"] == [3]
    assert attrs["post_ventilation"] == 120 + 30 + 6

    hass.states.async_set(SHORT, "on")
    hass.states.async_set(FLUSH, "on")
    await hass.async_block_till_done()
    await _tick(hass, freezer, 5)
    hass.states.async_set(SHORT, "off")
    hass.states.async_set(FLUSH, "off")
    await hass.async_block_till_done()
    attrs = hass.states.get("sensor.bagno_state").attributes
    assert attrs["flush_durations"] == [5]
    assert attrs["short_flush_durations"] == [3]


async def test_options_remove_flush_removes_numbers(hass: HomeAssistant) -> None:
    """v1.1: clearing the long flush removes its numbers from the registry."""
    entry, _ = await _setup(hass)
    assert hass.states.get("number.bagno_flush_extra") is not None
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SEAT: SEAT, CONF_FAN: FAN}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    assert registry.async_get("number.bagno_flush_extra") is None
    assert entry.runtime_data.flush_ids == {}
