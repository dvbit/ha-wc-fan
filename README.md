<p align="center"><img src="icon.png" width="128" alt="WC Fan icon"></p>

# WC Fan

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)
![Version](https://img.shields.io/badge/version-1.0.0-blue)

🇮🇹 [Leggi in italiano](README.it.md)

Home Assistant custom integration that turns a bathroom fan on and off from a
**seat lid** sensor and a **flush** sensor, with a post-ventilation time
computed on the whole usage session. Multi-instance: one entry per bathroom.

Full specification: [SPEC.md](SPEC.md).

## How it works

1. Lid opens → after the **activation delay** (P1) the fan turns on.
   A flush turns it on immediately.
2. While the lid is open or a flush is running, the fan stays on.
3. When both are off, a **countdown** of length **T** starts:

   ```
   T = clamp( P2 + P3 · min(D_t, P4) + Σ (P5 + P6 · d_i), P7, P8 )
   ```

   `D_t` = total lid-open time in the session, `d_i` = duration of each flush.
   Example: lid open 300 s, flushes of 6 s and 3 s → 120 + 150 + 72 + 66 = **408 s**.
4. A new opening or flush during the countdown resumes the same session.
5. Turning the fan **on by hand** suspends the automation (`manual`) until the
   fan goes off; turning it **off by hand** ends the session.

## Installation

1. HACS → ⋮ → *Custom repositories* → `https://github.com/dvbit/ha-wc-fan`,
   category *Integration*.
2. Install **WC Fan** and restart Home Assistant.
3. *Settings → Devices & services → Add integration → WC Fan*.

Requires Home Assistant **2025.6.0** or newer.

## Configuration

| Field | Entity | Meaning |
| --- | --- | --- |
| Name | text | device name and entity prefix |
| Seat lid sensor | `binary_sensor` | `on` = lid open |
| Flush sensor | `binary_sensor` | `on` = flush in progress |
| Fan | `switch` or `fan` | the fan to drive |

The three entities can be changed later from **Configure** (options flow).

## Entities

Example with name `Bathroom`:

| Entity | Default | Range | Spec |
| --- | --- | --- | --- |
| `switch.bathroom_enabled` | on | — | §7 |
| `number.bathroom_activation_delay` | 10 s | 0–120 | P1 |
| `number.bathroom_base_time` | 120 s | 0–1800 | P2 |
| `number.bathroom_seat_factor` | 0.5 s/s | 0–5 | P3 |
| `number.bathroom_seat_cap` | 1200 s | 60–3600 | P4 |
| `number.bathroom_flush_extra` | 60 s | 0–600 | P5 |
| `number.bathroom_flush_factor` | 2 s/s | 0–10 | P6 |
| `number.bathroom_min_time` | 120 s | 0–3600 | P7 |
| `number.bathroom_max_time` | 1200 s | 60–7200 | P8 |
| `sensor.bathroom_state` | — | `idle`, `waiting`, `active`, `countdown`, `manual`, `disabled` | §5 |
| `sensor.bathroom_expected_end` | — | timestamp, only in `countdown` | §3 |

`sensor.bathroom_state` attributes: `seat_total` (D_t, s), `flush_durations`
(S, s), `post_ventilation` (T for the current session, s).

All values survive a restart; an interrupted session is resumed (§8).

## Usage examples

### Disable the automation at night

```yaml
automation:
  - alias: "WC fan - quiet at night"
    triggers:
      - trigger: time
        at: "23:00:00"
        id: "night"
      - trigger: time
        at: "07:00:00"
        id: "day"
    actions:
      - choose:
          - conditions:
              - condition: trigger
                id: "night"
            sequence:
              - action: switch.turn_off
                target:
                  entity_id: switch.bathroom_enabled
        default:
          - action: switch.turn_on
            target:
              entity_id: switch.bathroom_enabled
```

### Longer post-ventilation in winter

```yaml
automation:
  - alias: "WC fan - winter base time"
    triggers:
      - trigger: state
        entity_id: sensor.season
        to: "winter"
    actions:
      - action: number.set_value
        target:
          entity_id: number.bathroom_base_time
        data:
          value: 300
```

### Notify when the fan was left in manual mode

```yaml
automation:
  - alias: "WC fan - manual for 1 hour"
    triggers:
      - trigger: state
        entity_id: sensor.bathroom_state
        to: "manual"
        for:
          hours: 1
    actions:
      - action: notify.notify
        data:
          message: "The bathroom fan has been on by hand for 1 hour."
```

### Dashboard card

```yaml
type: entities
title: Bathroom fan
entities:
  - entity: switch.bathroom_enabled
  - entity: sensor.bathroom_state
  - entity: sensor.bathroom_expected_end
    format: relative
  - type: attribute
    entity: sensor.bathroom_state
    attribute: post_ventilation
    name: Post-ventilation (s)
```

## Logging

```yaml
logger:
  logs:
    custom_components.wc_fan: debug
```

- **DEBUG**: every state transition with its cause, D_t, S, computed T.
- **INFO**: session opened/closed, manual override, enable/disable, startup.
- **WARNING**: fan unavailable or command failed, input sensor unavailable.
- **ERROR**: invalid configuration.

## Languages

English, Italiano, Français, Español, Deutsch.

## License

MIT
