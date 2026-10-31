<p align="center"><img src="icon.png" width="128" alt="Icona Aeratore WC"></p>

# Aeratore WC (WC Fan)

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)
![Versione](https://img.shields.io/badge/versione-1.2.0-blue)

🇬🇧 [Read in English](README.md)

Integrazione custom per Home Assistant che accende e spegne l'aeratore del bagno
in base al sensore **tavoletta** e, opzionalmente, ai sensori di **scarico
breve** e **scarico lungo**, con un tempo di post-ventilazione calcolato
sull'intera sessione d'uso. Multi-istanza: una voce
per ogni bagno.

Specifica completa: [SPEC.md](SPEC.md).

## Funzionamento

1. Tavoletta aperta → dopo il **ritardo di attivazione** (P1) l'aeratore si
   accende. Uno scarico lo accende subito.
2. Finché la tavoletta è aperta o uno scarico è in corso, l'aeratore resta acceso.
3. Quando entrambi sono `off` parte un **countdown** di durata **T**:

   ```
   T = clamp( P2 + P3 · min(D_t, P4)
              + Σ_brevi (P5b + P6b · d_i) + Σ_lunghi (P5 + P6 · d_i), P7, P8 )
   ```

   `D_t` = tempo totale di tavoletta aperta nella sessione, `d_i` = durata di
   ogni scarico. Esempio: tavoletta aperta 300 s, uno scarico lungo da 6 s e
   uno breve da 3 s → 120 + 150 + 72 + 36 = **378 s**.

   Senza sensori di scarico le somme sono vuote:
   T = clamp(P2 + P3 · min(D_t, P4), P7, P8). Con i default, 1 min di
   tavoletta aperta → 150 s, 10 min → 420 s.
   Se i due contatti sono `on` insieme (placche a doppio tasto) contano come
   **un solo scarico lungo**, dal primo `on` all'ultimo `off`.
4. Una nuova apertura o un nuovo scarico durante il countdown riprende la stessa
   sessione.
5. Accendendo l'aeratore **a mano** l'automazione si sospende (`manual`) finché
   l'aeratore non si spegne; spegnendolo **a mano** la sessione termina.
6. **Tempo massimo di accensione** (sicurezza): in ogni caso l'aeratore si
   spegne dopo `max_on_time` minuti dall'accensione, countdown compreso (per
   esempio con un sensore tavoletta bloccato su aperto). La sessione termina e
   ne serve una nuova apertura o un nuovo scarico. Si disattiva con
   `switch.<nome>_max_on_enabled` e si estende alla modalità manuale con
   `switch.<nome>_max_on_manual`.

## Installazione

1. HACS → ⋮ → *Repository personalizzati* → `https://github.com/dvbit/ha-wc-fan`,
   categoria *Integrazione*.
2. Installa **WC Fan** e riavvia Home Assistant.
3. *Impostazioni → Dispositivi e servizi → Aggiungi integrazione → Aeratore WC*.

Richiede Home Assistant **2025.6.0** o successivo.

## Configurazione

| Campo | Entità | Significato |
| --- | --- | --- |
| Nome | testo | nome del dispositivo e prefisso delle entità |
| Sensore tavoletta | `binary_sensor` | `on` = tavoletta aperta (obbligatorio) |
| Sensore scarico breve | `binary_sensor` | `on` = scarico breve in corso (opzionale) |
| Sensore scarico lungo | `binary_sensor` | `on` = scarico lungo in corso (opzionale) |
| Aeratore | `switch` o `fan` | l'aeratore da comandare (obbligatorio) |

Le entità si possono cambiare in seguito da **Configura** (options flow).
I parametri di un sensore di scarico non configurato non vengono creati.

**Aggiornamento dalla 1.0:** il sensore sciacquone esistente diventa lo
scarico **lungo**; entity_id e valori restano invariati.

## Entità

Esempio con nome `Bagno`. Gli entity_id sono in inglese; i nomi visualizzati
seguono la lingua di Home Assistant.

| Entità | Default | Range | Specifica |
| --- | --- | --- | --- |
| `switch.bagno_enabled` | on | — | §7 abilitato |
| `number.bagno_activation_delay` | 10 s | 0–120 | P1 ritardo attivazione |
| `number.bagno_base_time` | 120 s | 0–1800 | P2 tempo base |
| `number.bagno_seat_factor` | 0.5 s/s | 0–5 | P3 fattore tavoletta |
| `number.bagno_seat_cap` | 1200 s | 60–3600 | P4 limite tavoletta |
| `number.bagno_short_flush_extra` | 30 s | 0–600 | P5b extra scarico breve |
| `number.bagno_short_flush_factor` | 2 s/s | 0–10 | P6b fattore scarico breve |
| `number.bagno_flush_extra` | 60 s | 0–600 | P5 extra scarico lungo |
| `number.bagno_flush_factor` | 2 s/s | 0–10 | P6 fattore scarico lungo |
| `number.bagno_min_time` | 120 s | 0–3600 | P7 tempo minimo |
| `number.bagno_max_time` | 1200 s | 60–7200 | P8 tempo massimo (solo countdown) |
| `number.bagno_max_on_time` | 60 min | 5–240 | tempo massimo di accensione |
| `switch.bagno_max_on_enabled` | on | — | limite di accensione attivo |
| `switch.bagno_max_on_manual` | off | — | limite anche in modalità manuale |
| `sensor.bagno_state` | — | vedi sotto | §5 stato |
| `sensor.bagno_expected_end` | — | timestamp, solo in `countdown` | §3 fine prevista |

Valori di `sensor.bagno_state` (tradotti nell'interfaccia): `idle` (inattivo),
`waiting` (attesa), `active` (attivo), `countdown`, `manual` (manuale),
`disabled` (disabilitato).

Attributi: `seat_total` (D_t, s), `flush_durations` (scarichi lunghi, s),
`short_flush_durations` (scarichi brevi, s), `post_ventilation` (T della
sessione corrente, s), `max_on_end` (istante dello spegnimento forzato),
`last_stop_reason` (`countdown`, `max_on_time`, `manual_off`, `disabled`).

Tutti i valori sopravvivono al riavvio; una sessione interrotta viene ripresa (§8).

## Esempi di utilizzo

### Disattivare l'automazione di notte

```yaml
automation:
  - alias: "Aeratore WC - silenzio notturno"
    triggers:
      - trigger: time
        at: "23:00:00"
        id: "notte"
      - trigger: time
        at: "07:00:00"
        id: "giorno"
    actions:
      - choose:
          - conditions:
              - condition: trigger
                id: "notte"
            sequence:
              - action: switch.turn_off
                target:
                  entity_id: switch.bagno_enabled
        default:
          - action: switch.turn_on
            target:
              entity_id: switch.bagno_enabled
```

### Post-ventilazione più lunga d'inverno

```yaml
automation:
  - alias: "Aeratore WC - tempo base invernale"
    triggers:
      - trigger: state
        entity_id: sensor.season
        to: "winter"
    actions:
      - action: number.set_value
        target:
          entity_id: number.bagno_base_time
        data:
          value: 300
```

### Notifica se l'aeratore resta in manuale

```yaml
automation:
  - alias: "Aeratore WC - manuale da 1 ora"
    triggers:
      - trigger: state
        entity_id: sensor.bagno_state
        to: "manual"
        for:
          hours: 1
    actions:
      - action: notify.notify
        data:
          message: "L'aeratore del bagno è acceso a mano da 1 ora."
```

### Card per la dashboard

```yaml
type: entities
title: Aeratore bagno
entities:
  - entity: switch.bagno_enabled
  - entity: sensor.bagno_state
  - entity: sensor.bagno_expected_end
    format: relative
  - type: attribute
    entity: sensor.bagno_state
    attribute: post_ventilation
    name: Post-ventilazione (s)
```

### Tavoletta da un sensore di inclinazione/vibrazione

Se la tavoletta non ha un contatto, un helper Template (*Impostazioni →
Dispositivi e servizi → Aiutanti → Crea aiutante → Template → Sensore
binario*) può ricavarla dall'angolo di inclinazione. *Stato*:

```jinja
{{ states('sensor.vibrazione_wc_angle_x') | float(0) > 35 }}
```

*Opzioni avanzate → Disponibilità*:

```jinja
{{ has_value('sensor.vibrazione_wc_angle_x') }}
```

### Notifica quando il tempo massimo ha spento l'aeratore

```yaml
automation:
  - alias: "Aeratore WC - spento per tempo massimo"
    triggers:
      - trigger: state
        entity_id: sensor.bagno_state
        attribute: last_stop_reason
        to: "max_on_time"
    actions:
      - action: notify.notify
        data:
          message: "L'aeratore del bagno ha raggiunto il tempo massimo: controlla il sensore tavoletta."
```

## Log

```yaml
logger:
  logs:
    custom_components.wc_fan: debug
```

- **DEBUG**: ogni transizione di stato con causa, D_t, S, T calcolato.
- **INFO**: apertura/chiusura sessione, override manuale, abilitazione, avvio.
- **WARNING**: aeratore non disponibile o comando fallito, sensore non disponibile.
- **ERROR**: configurazione non valida.

## Lingue

English, Italiano, Français, Español, Deutsch.

## Licenza

MIT
