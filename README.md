<p align="center"><img src="icon.png" width="128" alt="WC Fan icon"></p>

# WC Fan

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)
![Version](https://img.shields.io/badge/version-1.4.0-blue)

🇮🇹 [Leggi in italiano](README.it.md)

Home Assistant custom integration that turns a bathroom fan on and off from a
**seat lid** sensor and, optionally, a **short flush** and a **long flush**
sensor, with a post-ventilation time computed on the whole usage session. Multi-instance: one entry per bathroom.

Full specification: [SPEC.md](SPEC.md).

## How it works

1. Lid opens → after the **activation delay** (P1) the fan turns on.
   A flush turns it on immediately.
2. While the lid is open or a flush is running, the fan stays on.
3. When both are off, a **countdown** of length **T** starts:

   ```
   T = clamp( P2 + P3 · min(D_t, P4)
              + Σ_short (P5s + P6s · d_i) + Σ_long (P5 + P6 · d_i), P7, P8 )
   ```

   `D_t` = total lid-open time in the session, `d_i` = duration of each flush.
   Example: lid open 300 s, a long flush of 6 s and a short one of 3 s →
   120 + 150 + 72 + 36 = **378 s**.

   Without flush sensors the sums are empty: T = clamp(P2 + P3 · min(D_t, P4), P7, P8).
   With the defaults, 1 min of lid open → 150 s, 10 min → 420 s.
   If both flush contacts are on at the same time (dual-button plates) they
   count as **one long flush**, from the first `on` to the last `off`.
4. A new opening or flush during the countdown resumes the same session.
5. Turning the fan **on by hand** suspends the automation (`manual`) until the
   fan goes off; turning it **off by hand** ends the session.
6. **Maximum on-time** (safety): whatever happens, the fan is switched off
   after `max_on_time` minutes from when it was turned on, countdown included
   (e.g. a lid sensor stuck open). The session ends and a new one needs a new
   opening or flush. It can be disabled with `switch.<name>_max_on_enabled`,
   and extended to manual mode with `switch.<name>_max_on_manual`.

7. **Timer helper** (optional, v1.4): select an existing `timer` helper
   (*Settings → Devices & services → Helpers → Timer*) and the integration
   mirrors the remaining fan time on it, so the standard timer cards and
   `remaining` / `finishes_at` attributes work. See *Timer helper* below.

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
| Seat lid sensor | `binary_sensor` | `on` = lid open (required) |
| Short flush sensor | `binary_sensor` | `on` = short flush in progress (optional) |
| Long flush sensor | `binary_sensor` | `on` = long flush in progress (optional) |
| Fan | `switch` or `fan` | the fan to drive (required) |
| Timer helper | `timer` | optional: mirrors the remaining time, see below |

The entities can be changed later from **Configure** (options flow).
The parameters of a flush sensor that is not configured are not created.

**Upgrading from 1.0:** the existing flush sensor becomes the **long** flush;
entity IDs and values are kept.

## Entities

Example with name `Bathroom`:

| Entity | Default | Range | Spec |
| --- | --- | --- | --- |
| `switch.bathroom_enabled` | on | — | §7 |
| `number.bathroom_activation_delay` | 10 s | 0–120 | P1 |
| `number.bathroom_base_time` | 120 s | 0–1800 | P2 |
| `number.bathroom_seat_factor` | 0.5 s/s | 0–5 | P3 |
| `number.bathroom_seat_cap` | 1200 s | 60–3600 | P4 |
| `number.bathroom_short_flush_extra` | 30 s | 0–600 | P5s (short flush only) |
| `number.bathroom_short_flush_factor` | 2 s/s | 0–10 | P6s (short flush only) |
| `number.bathroom_flush_extra` | 60 s | 0–600 | P5 (long flush only) |
| `number.bathroom_flush_factor` | 2 s/s | 0–10 | P6 (long flush only) |
| `number.bathroom_min_time` | 120 s | 0–3600 | P7 |
| `number.bathroom_max_time` | 1200 s | 60–7200 | P8 (countdown only) |
| `number.bathroom_max_on_time` | 60 min | 5–240 | maximum on-time |
| `switch.bathroom_max_on_enabled` | on | — | maximum on-time limit active |
| `switch.bathroom_max_on_manual` | off | — | limit also in manual mode |
| `sensor.bathroom_state` | — | `idle`, `waiting`, `active`, `countdown`, `manual`, `disabled` | §5 |
| `sensor.bathroom_expected_end` | — | timestamp, only in `countdown` | §3 |

`sensor.bathroom_state` attributes: `seat_total` (D_t, s), `flush_durations`
(long flushes, s), `short_flush_durations` (short flushes, s),
`post_ventilation` (T for the current session, s), `max_on_end` (forced
switch-off instant), `last_stop_reason` (`countdown`, `max_on_time`,
`manual_off`, `disabled`).

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

### Seat sensor from a tilt/vibration sensor

If the lid has no contact sensor, a Template helper (*Settings → Devices &
services → Helpers → Create helper → Template → Binary sensor*) can derive it
from the tilt angle. *State*:

```jinja
{{ states('sensor.wc_vibration_angle_x') | float(0) > 35 }}
```

*Advanced options → Availability*:

```jinja
{{ has_value('sensor.wc_vibration_angle_x') }}
```

### Notify when the maximum on-time stopped the fan

```yaml
automation:
  - alias: "WC fan - stopped by maximum on-time"
    triggers:
      - trigger: state
        entity_id: sensor.bathroom_state
        attribute: last_stop_reason
        to: "max_on_time"
    actions:
      - action: notify.notify
        data:
          message: "The bathroom fan reached its maximum on-time: check the lid sensor."
```

## Timer helper

Optional field *Timer helper* (domain `timer`). The integration drives it only
through the documented services `timer.start`, `timer.change` and
`timer.cancel`; the duration set in the helper itself is ignored.

- Fan turned **on** → `timer.start`, ending at the maximum on-time deadline
  (`max_on_time`). With `max_on_enabled` off the end is unknown while the lid
  is open: the timer starts when the countdown starts.
- Countdown starts → `timer.change` so that the timer ends at the countdown end
  (never later than the maximum on-time).
- A parameter change (e.g. base time, maximum on-time) → `timer.change` with
  the difference. A timer cannot grow beyond its running duration: in that case
  it is restarted with `timer.start`.
- Session ends early (manual OFF, forced OFF, automation disabled) →
  `timer.cancel`. At the normal end of the countdown the timer finishes by
  itself, so `timer.finished` is fired.
- Manual mode: the timer follows the maximum on-time only if
  `max_on_manual` is on.

Dashboard card:

```yaml
type: tile
entity: timer.bathroom_fan
name: Fan remaining time
```

Each timer helper can mirror one instance only. A helper that is not available
is logged once as a warning and skipped.

## Timer examples

All values use the **defaults** (P1 10 s, P2 120 s, P3 0.5, P4 1200 s, P5s 30 s,
P6s 2, P5 60 s, P6 2, P7 120 s, P8 1200 s, maximum on-time 60 min) and were
computed with the integration's own state machine. `T` is how long the fan keeps
running **after** the lid is closed and the last flush has ended.

```
T = clamp( 120 + 0.5 · min(D_t, 1200) + Σ_short (30 + 2·d) + Σ_long (60 + 2·d), 120, 1200 )
```

### Different lid times and flushes

| # | Lid open (D_t) | Flushes | Calculation | T |
| --- | --- | --- | --- | --- |
| 1 | 30 s | none | 120 + 15 | **135 s** (2 min 15 s) |
| 2 | 30 s | short 3 s | 120 + 15 + (30 + 6) | **171 s** (2 min 51 s) |
| 3 | 2 min | long 6 s | 120 + 60 + (60 + 12) | **252 s** (4 min 12 s) |
| 4 | 5 min | long 6 s + short 3 s | 120 + 150 + (60 + 12) + (30 + 6) | **378 s** (6 min 18 s) |
| 5 | 10 min | 2 × long 6 s | 120 + 300 + 2 · (60 + 12) | **564 s** (9 min 24 s) |
| 6 | 25 min | long 8 s | 120 + 600 *(D_t capped by P4)* + (60 + 16) | **796 s** (13 min 16 s) |
| 7 | 30 min | 10 × long 8 s | 120 + 600 + 10 · (60 + 16) = 1480 → clamped by P8 | **1200 s** (20 min) |

Without flush sensors only the first two terms count (rows 1, 3 and 5 would
give 135 s, 180 s and 420 s). The fan switches on 10 s after the lid opens (P1),
so in row 4 it runs for 5 min − 10 s + 6 min 18 s in total.

![T versus lid open time](docs/formula-curves.png)

### Lid left open: with and without the maximum on-time

The fan turns on at 10 s and the lid never closes (or closes late). Maximum
on-time = 60 min (`max_on_time`), counted from when the fan was switched on,
**countdown included**.

| Case | `max_on_enabled` | Fan switched off | `last_stop_reason` |
| --- | --- | --- | --- |
| Lid open 3 h | off | 3 h 12 min after opening (3 h + T = 12 min) | `countdown` |
| Lid open 3 h | on | **60 min** after the fan started (at 60 min 10 s) | `max_on_time` |
| Lid open 50 min | off | 62 min (50 min + T = 12 min) | `countdown` |
| Lid open 50 min | on | **60 min 10 s**: the 12 min countdown is cut | `max_on_time` |
| Lid open 20 min | on | 32 min (20 min + T = 12 min), limit not reached | `countdown` |

After a forced switch-off the session ends: the fan comes back only on a new
lid opening or flush, even if the lid is still open.

![Lid left open](docs/max-on-time.png)

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
