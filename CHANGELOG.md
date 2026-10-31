# Changelog

## 2.0.0 — 2026-10-31

**Breaking: new formula.** A flush is now a binary event; its duration no longer
matters. T = clamp(P2 + P3·S + n_short·(P5s + P6s·S) + n_long·(P5 + P6·S), P7, P8)
with S = min(D_t, P4).

- New numbers `flush_seat_factor` (0.3) and `short_flush_seat_factor` (0.15),
  s per second of lid-open time, range 0–2. They replace `flush_factor` and
  `short_flush_factor`; the old entities are removed and the new ones start at
  their defaults. `flush_extra` / `short_flush_extra` keep their values.
- State sensor attributes `flush_count` / `short_flush_count` replace the lists
  of durations. Sessions persisted by v1.x are converted to counts.
- README (EN/IT) examples and charts recomputed.

## 1.4.0 — 2026-10-31

- New optional **timer helper** (`timer_entity`): the remaining fan time is
  mirrored on a standard `timer` entity through `timer.start`, `timer.change`
  and `timer.cancel`. Started when the fan turns on, updated at the countdown
  start and on parameter changes, cancelled when the session ends early.
- Existing configuration is unchanged (the field is optional).

## 1.3.0 — 2026-10-31

- Documentation only: README (EN/IT) now has timer examples with the computed
  values of the formula, lid-left-open cases with and without the maximum
  on-time, and two charts (`docs/`).

## 1.2.0 — 2026-10-31

- New **maximum on-time**: the fan is switched off after `max_on_time` minutes
  (default 60, 5–240) from when it was turned on, countdown included; the
  session ends and a new rising edge is needed.
- New switch `max_on_enabled` (default on) to enable/disable the limit.
- New switch `max_on_manual` (default off) to apply the limit also when the
  fan was turned on by hand.
- New state sensor attributes `max_on_end` and `last_stop_reason`.
- The fan-on instant is persisted: a limit exceeded while HA was down is
  applied at startup. Existing configuration and values are kept.

## 1.1.0 — 2026-10-13

- Flush sensors are now optional: the integration works with the seat only.
- Two flush sensors, **short** and **long**, each with its own extra/factor
  parameters (`short_flush_extra` 30 s, `short_flush_factor` 2 s/s).
- Short and long on at the same time count as one long flush.
- Number entities of an unconfigured flush sensor are not created and are
  removed from the registry.
- Upgrade from 1.0: the existing flush sensor becomes the long flush; entity
  IDs, values and the persisted session are kept.
- New attribute `short_flush_durations` on the state sensor.
- New icon with dual-flush drops.

## 1.0.0 — 2026-10-11

- First release, implementing SPEC.md v1.0 (session-based post-ventilation,
  P1–P8 parameters, manual override, enable switch, restart persistence).
- Fan entity: `switch` or `fan`.
- Localized in English, Italian, French, Spanish and German.
