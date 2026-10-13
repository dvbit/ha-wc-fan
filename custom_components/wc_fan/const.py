"""Constants for the WC Fan integration.

Every constant references the section of SPEC.md it implements.
"""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "wc_fan"

# --- §2 Configuration keys (config flow + options flow) ----------------------
CONF_SEAT: Final = "seat_entity"  # binary_sensor, on = seat lid open (required)
# v1.1: two OPTIONAL flush sensors. The v1.0 key "flush_entity" is kept for the
# LONG flush so existing entries migrate without changes.
CONF_FLUSH: Final = "flush_entity"  # binary_sensor, on = long flush in progress
CONF_SHORT_FLUSH: Final = "short_flush_entity"  # binary_sensor, short flush
CONF_FAN: Final = "fan_entity"  # switch or fan, on/off relay

# Flush kinds (v1.1).
FLUSH_SHORT: Final = "short"
FLUSH_LONG: Final = "long"

# Domains accepted for the fan entity (consolidated requirement: switch + fan).
FAN_DOMAINS: Final = ("switch", "fan")

# --- §5 State machine states (English keys, localized via translations) ------
# Mapping to the original Italian spec values:
#   idle -> idle, waiting -> attesa, active -> attivo, countdown -> countdown,
#   manual -> manuale, disabled -> disabilitato
STATE_IDLE: Final = "idle"
STATE_WAITING: Final = "waiting"
STATE_ACTIVE: Final = "active"
STATE_COUNTDOWN: Final = "countdown"
STATE_MANUAL: Final = "manual"
STATE_DISABLED: Final = "disabled"
STATES: Final = (
    STATE_IDLE,
    STATE_WAITING,
    STATE_ACTIVE,
    STATE_COUNTDOWN,
    STATE_MANUAL,
    STATE_DISABLED,
)

# --- §3 Parameter keys (number entities P1..P8) -------------------------------
P_ACTIVATION_DELAY: Final = "activation_delay"  # P1
P_BASE_TIME: Final = "base_time"  # P2
P_SEAT_FACTOR: Final = "seat_factor"  # P3
P_SEAT_CAP: Final = "seat_cap"  # P4
P_FLUSH_EXTRA: Final = "flush_extra"  # P5, long flush
P_FLUSH_FACTOR: Final = "flush_factor"  # P6, long flush
P_SHORT_FLUSH_EXTRA: Final = "short_flush_extra"  # P5b, short flush (v1.1)
P_SHORT_FLUSH_FACTOR: Final = "short_flush_factor"  # P6b, short flush (v1.1)

# v1.1: parameters that exist only when the matching flush sensor is configured.
FLUSH_PARAMS: Final = {
    FLUSH_LONG: (P_FLUSH_EXTRA, P_FLUSH_FACTOR),
    FLUSH_SHORT: (P_SHORT_FLUSH_EXTRA, P_SHORT_FLUSH_FACTOR),
}
P_MIN_TIME: Final = "min_time"  # P7
P_MAX_TIME: Final = "max_time"  # P8

# --- §3 Other entity keys ---------------------------------------------------
KEY_ENABLED: Final = "enabled"
KEY_EXPECTED_END: Final = "expected_end"
KEY_STATE: Final = "state"

# --- §8 Persistence (Store) ---------------------------------------------------
STORAGE_VERSION: Final = 1
STORAGE_SAVE_DELAY: Final = 1  # seconds; final write is flushed on shutdown
