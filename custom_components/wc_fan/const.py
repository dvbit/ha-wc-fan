"""Constants for the WC Fan integration.

Every constant references the section of SPEC.md it implements.
"""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "wc_fan"

# --- §2 Configuration keys (config flow + options flow) ----------------------
CONF_SEAT: Final = "seat_entity"  # binary_sensor, on = seat lid open
CONF_FLUSH: Final = "flush_entity"  # binary_sensor, on = flush in progress
CONF_FAN: Final = "fan_entity"  # switch or fan, on/off relay

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
P_FLUSH_EXTRA: Final = "flush_extra"  # P5
P_FLUSH_FACTOR: Final = "flush_factor"  # P6
P_MIN_TIME: Final = "min_time"  # P7
P_MAX_TIME: Final = "max_time"  # P8

# --- §3 Other entity keys ---------------------------------------------------
KEY_ENABLED: Final = "enabled"
KEY_EXPECTED_END: Final = "expected_end"
KEY_STATE: Final = "state"

# --- §8 Persistence (Store) ---------------------------------------------------
STORAGE_VERSION: Final = 1
STORAGE_SAVE_DELAY: Final = 1  # seconds; final write is flushed on shutdown
