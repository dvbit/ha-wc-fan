# Changelog

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
