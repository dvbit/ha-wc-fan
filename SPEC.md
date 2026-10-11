# Aeratore WC — Specifica v1.0

Oct 10, 2026 · @Nicola Reina

## 1. Scopo

Custom component HA (dominio `wc_fan`) che accende/spegne un aeratore in base a tavoletta e sciacquone, con tempo di post-ventilazione calcolato sulla sessione d'uso. Multi-istanza.

Fuori ambito: umidità, fasce orarie silenziose, velocità variabile.

## 2. Configurazione (config flow + options flow)

| Campo | Tipo | Semantica |
| --- | --- | --- |
| Nome | testo | prefisso entità |
| Tavoletta | `binary_sensor` | `on` = aperta |
| Sciacquone | `binary_sensor` | `on` = scarico in corso |
| Aeratore | `switch` | relè on/off |

L'options flow permette di cambiare le tre entità.

## 3. Entità esposte (per istanza)

Tutte persistono il valore ai riavvii (RestoreEntity).

| Entità | Tipo | Default | Range / valori |
| --- | --- | --- | --- |
| `abilitato` | switch | on | — |
| `ritardo_attivazione` (P1) | number, s | 10 | 0–120 |
| `t_base` (P2) | number, s | 120 | 0–1800 |
| `k_tavoletta` (P3) | number, s/s | 0.5 | 0–5, step 0.1 |
| `cap_tavoletta` (P4) | number, s | 1200 | 60–3600 |
| `extra_scarico` (P5) | number, s | 60 | 0–600 |
| `k_scarico` (P6) | number, s/s | 2 | 0–10, step 0.1 |
| `t_min` (P7) | number, s | 120 | 0–3600 |
| `t_max` (P8) | number, s | 1200 | 60–7200 |
| `fine_prevista` | sensor, timestamp | — | istante di spegnimento in `countdown`, altrimenti `None` |
| `stato` | sensor, enum | — | `idle`, `attesa`, `attivo`, `countdown`, `manuale`, `disabilitato` |

Nessun helper `input_*`.

## 4. Modello di sessione

La sessione nasce all'ingresso in `attivo` e termina al ritorno in `idle`. Accumulatori, azzerati a fine sessione:

- **D\_t**: somma delle durate di apertura tavoletta nella sessione, inclusa l'apertura che l'ha avviata (dal suo fronte di salita, ritardo compreso).
- **S**: lista delle durate d\_i degli scarichi completati nella sessione.

Tempo di post-ventilazione:

`T = clamp( P2 + P3 · min(D_t, P4) + Σ_i (P5 + P6 · d_i), P7, P8 )`

Se P7 > P8 si usa P8 come limite inferiore e superiore.

Esempio: tavoletta aperta 300 s, scarichi da 6 s e 3 s → 120 + 150 + 72 + 66 = **408 s**.

Fronte di salita = transizione `off → on`. Le transizioni da/verso `unavailable`/`unknown` non sono fronti di salita.

## 5. Macchina a stati

| Stato | Evento | Azione | Nuovo stato |
| --- | --- | --- | --- |
| `idle` | fronte salita tavoletta | avvia timer P1 | `attesa` |
| `idle` | fronte salita sciacquone | aeratore ON, apre sessione | `attivo` |
| `attesa` | timer P1 scaduto, tavoletta ancora `on` | aeratore ON, apre sessione | `attivo` |
| `attesa` | tavoletta → `off` prima di P1 | scarta l'apertura | `idle` |
| `attesa` | fronte salita sciacquone | annulla P1, aeratore ON, apre sessione (l'apertura in corso conta in D\_t) | `attivo` |
| `attivo` | tavoletta `off` **e** sciacquone `off` | calcola T, fine = now + T, avvia countdown | `countdown` |
| `countdown` | fronte salita tavoletta o sciacquone | annulla countdown (stessa sessione, nessun ritardo P1) | `attivo` |
| `countdown` | countdown scaduto | aeratore OFF, chiude sessione | `idle` |

D\_t si aggiorna a ogni chiusura tavoletta; d\_i si registra a ogni fine scarico. In `attivo` l'aeratore resta acceso per tutta la durata di aperture/scarichi, anche oltre P4 (P4 limita solo il contributo a T).

Modifiche ai parametri durante `countdown`: T ricalcolato, fine = istante di ingresso in countdown + nuovo T; se già passata, spegnimento immediato.

## 6. Intervento manuale

Un cambio di stato dell'aeratore è manuale se il suo `context` non è uno generato dall'integrazione (confronto `context_id`). Transizioni da/verso `unavailable` non sono manuali.

- **OFF manuale** (qualunque stato): annulla timer, chiude sessione → `idle`. Nuove sessioni solo da nuovi fronti di salita.
- **ON manuale**: → `manuale`. In `manuale` gli eventi dei sensori sono ignorati e l'integrazione non comanda l'aeratore. Si esce in `idle` quando l'aeratore va `off` per qualunque causa.

## 7. Abilitazione

- `abilitato` → off: annulla timer, chiude sessione, non comanda l'aeratore → `disabilitato`.
- `abilitato` → on: → `idle` (solo fronti di salita successivi).

## 8. Robustezza

**Sensori** — transizione `on → unavailable/unknown` trattata come `off` (durata conteggiata fino a quell'istante). Da `unavailable/unknown` nessun trigger finché il sensore non torna `off` e poi `on`.

**Aeratore non disponibile** — il comando fallito è loggato (warning), la macchina a stati prosegue. Al ritorno disponibile: se stato `attivo`/`countdown` e aeratore `off` → ON; se il countdown è scaduto nel frattempo e aeratore `on` → OFF.

**Riavvio HA** — persistere: stato, istante fine countdown, D\_t, S, istanti di inizio di apertura/scarico in corso. All'avvio:

- `countdown` con fine ≤ now → aeratore OFF → `idle`; con fine > now → riprende il countdown.
- `attivo` → rivaluta i sensori: se entrambi `off`, calcola T usando durate chiuse all'istante dell'avvio e passa a `countdown`; altrimenti resta `attivo`.
- `attesa` → `idle`.
- `manuale` → resta `manuale` se aeratore `on`, altrimenti `idle`.

## 9. Logging

Livello debug: ogni transizione di stato con evento causa, D\_t, S, T calcolato.

---

## Appendice — Requisito consolidato v1.0.0 (11/10/2026)

- Ambito generico; repository `dvbit/ha-wc-fan`, dominio `wc_fan`, HA minimo `2025.6.0`.
- Aeratore: entità `switch` **o** `fan`, comandata con `<dominio>.turn_on` / `turn_off`.
- Chiavi entità in inglese, nomi localizzati (en, it, fr, es, de):
  `enabled` (abilitato), `activation_delay` (P1), `base_time` (P2), `seat_factor` (P3),
  `seat_cap` (P4), `flush_extra` (P5), `flush_factor` (P6), `min_time` (P7),
  `max_time` (P8), `expected_end` (fine_prevista), `state` (stato).
- Valori del sensore `state`: `idle`, `waiting` (attesa), `active` (attivo),
  `countdown`, `manual` (manuale), `disabled` (disabilitato).
- Un dispositivo per istanza.
- Logging: DEBUG transizioni con causa, D_t, S, T (§9); INFO apertura/chiusura
  sessione, override manuale, abilitazione; WARNING comando aeratore non inviato o
  fallito, sensore non disponibile; ERROR configurazione non valida.
- In `idle` con tavoletta già `on` (uscita da `manuale`/`disabilitato`) si attende un
  nuovo fronte di salita.

### Scelte implementative su punti non coperti dalla specifica

- Ripresa `attivo` dopo riavvio: un'apertura/scarico iniziata a HA spento viene
  conteggiata dall'istante di avvio; se l'aeratore è `off` viene riacceso.
- Ripresa `countdown` dopo riavvio con aeratore `off`: viene riacceso fino alla fine.
- Lo stesso aeratore non può essere usato da due istanze (errore nel config flow).
