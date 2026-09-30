# ARTEMIS WebEvo for Home Assistant

Unofficial Home Assistant custom integration for **ARTEMIS WebEvo**.

It connects to an authorized ARTEMIS WebEvo account and exposes useful firefighter availability and live-operation information as Home Assistant entities and events.

> **Status:** early community release. Developed and tested against an SDIS 39 ARTEMIS WebEvo deployment. Other deployments may differ.
>
> **Unofficial:** this project is not affiliated with or endorsed by Inetum, ARTEMIS, Smartemis, SDIS 39, or any fire and rescue service.
>
> **Made with AI:** this integration was largely designed and implemented with AI assistance, using authorized browser traffic to understand the ARTEMIS WebEvo interfaces. Human review is very welcome. **Recommendations, code review, fixes, and architectural suggestions are especially welcome from experienced Python and Home Assistant developers.**

## What it does

- Shows your **current personal ARTEMIS status**.
- Shows your **next real status change**, including the upcoming ARTEMIS code such as `IND`, `AS1`, `DI1`, etc.
- Exposes a personal available/unavailable binary sensor.
- Shows the **number of personnel currently available at your ARTEMIS centre**, using ARTEMIS' native centre counter.
- Shows the number of **active interventions** and whether at least one intervention is active.
- Fires an `artemis_new_intervention` Home Assistant event when a new operation appears.
- Provides a notification-ready event payload with the operation details exposed by ARTEMIS, including address, units, vehicles, states and external services when available.
- Automatically logs in through CAS and renews expired ARTEMIS sessions.
- Operates read-only: it does **not** modify planning, availability, or interventions.

## Entities

Typical entity IDs after a fresh installation:

| Entity | Example / purpose | Icon |
|---|---|---|
| `sensor.statut_artemis` | `INDISPONIBLE`, `ASTREINTE NIVEAU 1`, etc. | `mdi:account-clock` |
| `sensor.prochain_changement_artemis` | `AS1 · 30/09 18:30` | `mdi:list-status` |
| `binary_sensor.disponible_artemis` | Your current availability | dynamic |
| `sensor.available_personnel_artemis` | Number of people currently available at the centre | `mdi:account-multiple-check` |
| `sensor.active_interventions_count_artemis` | Number of currently active interventions | `mdi:fire-alert` |
| `binary_sensor.active_interventions_artemis` | On when one or more interventions are active | `mdi:fire-alert` |

Home Assistant can adjust an entity ID if a naming collision already exists. Unique IDs remain stable.

### Next status change

The state intentionally contains both the upcoming ARTEMIS status code and the time:

```text
AS1 · 30/09 18:30
```

Additional attributes keep the complete information:

```yaml
statut_actuel: INDISPONIBLE
prochain_statut: ASTREINTE NIVEAU 1
prochain_code: AS1
date: 2026-09-30T18:30:00+02:00
horizon_semaines: 1
```

The integration ignores ARTEMIS row/day boundaries when the actual status remains unchanged, so an `IND -> IND` boundary is not reported as a status change.

## Centre availability

`sensor.available_personnel_artemis` uses the same native counter endpoint used by ARTEMIS WebEvo rather than trying to reconstruct availability by counting individual schedules.

Its state is the current `availableCounter` returned by ARTEMIS. The attributes also expose:

```yaml
centre: BEA
en_intervention: 0
```

Only aggregate counts are exposed. The integration does not create entities containing the names or schedules of other firefighters.

The centre counter is refreshed every 30 seconds.

## Active interventions

The integration polls the live ARTEMIS operations synoptic using the refresh interval announced by ARTEMIS, with a minimum of 15 seconds.

Two entities represent the current state:

```text
sensor.active_interventions_count_artemis
binary_sensor.active_interventions_artemis
```

Both use `mdi:fire-alert`.

When a previously unseen operation appears, Home Assistant fires:

```text
artemis_new_intervention
```

The first successful synoptic load after startup only seeds the current IDs. Existing operations therefore do **not** generate a burst of false "new intervention" notifications after a restart.

Typical event data:

```yaml
id: "26000042"
number: "26000042"
disaster: "SECOURS A PERSONNE"
address: "BEAUFORT - 12 RUE EXEMPLE - 39190"
created: "2026-09-30T11:43:00"
state: "EC – EN COURS"
fire_units:
  - "BEAUF (PA – PARTI)"
vehicles:
  - "VSAV BEAUFORT — VSAV · PA – PARTI · ETA 11:48"
title: "🚒 Nouvelle intervention — SECOURS A PERSONNE"
message: |-
  📍 BEAUFORT - 12 RUE EXEMPLE - 39190
  N° 26000042 · 2026-09-30T11:43:00
  ...
```

The exact fields depend on the ARTEMIS server and the operation.

## Recommended mobile notification automation

A notification automation is strongly recommended because the integration itself deliberately exposes an event instead of deciding how each Home Assistant installation should notify users.

Replace `notify.mobile_app_TON_TELEPHONE` with the notify action of your Home Assistant Companion device:

```yaml
alias: "ARTEMIS - Nouvelle intervention"
description: "Notification mobile lors d'une nouvelle intervention ARTEMIS"
mode: queued
max: 10

triggers:
  - trigger: event
    event_type: artemis_new_intervention

conditions: []

actions:
  - action: notify.mobile_app_TON_TELEPHONE
    data:
      title: "{{ trigger.event.data.title }}"
      message: "{{ trigger.event.data.message }}"
      data:
        channel: "Interventions SPV"
        importance: high
        priority: high
        ttl: 0
        notification_icon: "mdi:fire-alert"
        tag: "artemis_{{ trigger.event.data.id }}"
        actions:
          - action: "URI"
            title: "Ouvrir Smartemis"
            uri: "app://com.sis.smartemis"
```

The notification content generated by the integration intentionally uses only a couple of useful emojis, for example:

```text
🚒 Nouvelle intervention — SECOURS A PERSONNE

📍 BEAUFORT - 12 RUE EXEMPLE - 39190
N° 26000042 · 11:43
...
```

The same YAML is included in [`automation_notification.example.yaml`](automation_notification.example.yaml).

## Installation with HACS

This repository is a HACS **Integration** repository.

### Add as a custom repository

1. Open **HACS** in Home Assistant.
2. Open the HACS menu and choose **Custom repositories**.
3. Add:

   ```text
   https://github.com/purierca/artemis
   ```

4. Select category **Integration**.
5. Open **ARTEMIS WebEvo** in HACS and install it.
6. Restart Home Assistant.
7. Go to **Settings > Devices & services > Add integration**.
8. Search for **ARTEMIS WebEvo**.

The repository does not need to be accepted into the default HACS store to be installed as a custom repository.

## Manual installation

Copy:

```text
custom_components/artemis
```

to:

```text
/config/custom_components/artemis
```

and restart Home Assistant.

## Configuration

Configuration is entirely through the Home Assistant UI.

For the SDIS 39 deployment used during development, the defaults are:

```text
ARTEMIS server: https://artemisweb.sdis39.fr
CAS service URL: https://artemis/artemis-web
```

Then enter your ARTEMIS username and password.

The CAS service URL is configurable because other ARTEMIS WebEvo deployments may use another service identifier.

### Authentication flow

The integration reproduces the normal browser flow:

```text
CAS login
  -> CAS service ticket
  -> ARTEMIS WebEvo SSO hand-off
  -> centre/profile selection
  -> authenticated ARTEMIS API session
```

If the session expires, the integration attempts to authenticate again automatically. If the account credentials are no longer accepted, Home Assistant starts a reauthentication flow.

## How personal planning is interpreted

ARTEMIS can use an operational day that starts at a configured time such as `07:00` rather than midnight.

The integration reads the centre's `planningStartTime` and converts ARTEMIS periods into timezone-aware Home Assistant timestamps.

Consecutive periods with the same status are treated as one continuous block. Planning is refreshed every five minutes, and another refresh is scheduled immediately after the next known true status boundary.

When necessary, the integration looks ahead up to eight weeks to find the next genuinely different status.

## Privacy and security

ARTEMIS can contain sensitive personal and operational information.

This integration deliberately keeps persistent entities minimal:

- other firefighters are represented only through the aggregate available-personnel counter;
- operation addresses and details are emitted in the `artemis_new_intervention` event for notification/automation use;
- the integration does not create a permanent address/history sensor.

Home Assistant stores config-entry credentials locally. Protect:

- `/config/.storage`;
- Home Assistant backups;
- administrator access to Home Assistant.

Never publish an unredacted ARTEMIS HAR. HAR files can contain passwords, CAS tickets, SSO values, cookies, identities, planning information and active-operation details.

See [`SECURITY.md`](SECURITY.md).

## Compatibility

### Tested / observed API surfaces

The initial implementation was built from an SDIS 39 ARTEMIS WebEvo deployment and uses:

```text
api/personPlanning/initData
api/personPlanning/getPlanning
api/planningCounters/getCounters
api/synopticOperations/initData
api/synopticOperations
```

Other ARTEMIS deployments may use different hosts, CAS service identifiers, profiles, permissions, or backend versions. Compatibility outside the tested deployment is therefore not guaranteed yet.

If you test another deployment, please open an issue — but never include credentials, raw cookies, CAS tickets, firefighter names, or operational addresses.

## Troubleshooting

### Integration does not appear after HACS installation

Restart Home Assistant after installing the integration, then search again under **Settings > Devices & services > Add integration**.

### `invalid_auth`

Verify that the account can sign into ARTEMIS WebEvo normally. If another deployment uses a different CAS service identifier, use that identifier during integration setup.

### `cannot_connect`

Confirm that the Home Assistant host can reach the ARTEMIS WebEvo HTTPS endpoint and that DNS/TLS work from the Home Assistant environment.

### No notification after restart even though an intervention already exists

This is intentional. Operations already present during the initial poll are treated as existing operations. Only IDs appearing afterwards trigger `artemis_new_intervention`.

### Centre availability is unavailable

The integration needs ARTEMIS to expose a planning ID for the current operational day and permit access to `planningCounters/getCounters`. Deployments or profiles without this permission may not expose the sensor successfully.

## Development and contributions

This is an early community project and was **made with substantial AI assistance**. That makes review particularly valuable.

Recommendations are welcome from everyone, and especially from experienced developers who can help review:

- Home Assistant integration architecture;
- async Python and coordinator patterns;
- authentication/session handling;
- compatibility with other ARTEMIS deployments;
- tests and error handling;
- privacy and operational-data handling.

Issues and pull requests are welcome at:

```text
https://github.com/purierca/artemis
```

Local checks:

```bash
python3 -m compileall -q custom_components/artemis
python3 -m unittest discover -s tests -v
python3 scripts/check_ready.py
```

GitHub Actions validate the repository with:

- HACS validation;
- Home Assistant Hassfest;
- Python compile/smoke tests;
- repository metadata checks.

See [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Disclaimer

Use this integration only with an ARTEMIS account and data you are authorized to access. It is a convenience integration for Home Assistant and must not be treated as a replacement for official alerting, dispatch, availability, or operational systems.

## License

MIT. See [`LICENSE`](LICENSE).
