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
- Fires `artemis_intervention_update` lifecycle events for operation snapshots, updates and completion.
- Provides structured operation payloads so Home Assistant notifications can format address, units, vehicles, states and external services freely.
- Automatically logs in through CAS and renews expired ARTEMIS sessions.
- Exposes a one-press **personal status cycle button**: `IND -> DI1 -> AS1 -> IND`.
- Status writes are deliberately limited to **your own personal planning** and only until the next status change that was already planned in ARTEMIS.
- Never modifies interventions, other firefighters, or centre planning.

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
| `button.cycle_artemis_status` | Cycle `IND -> DI1 -> AS1 -> IND` until the preserved next planned change | `mdi:account-switch` |

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

## One-button personal status control

The integration exposes a stateless Home Assistant button:

```text
button.cycle_artemis_status
```

Each press cycles the **currently active personal ARTEMIS status**:

```text
IND -> DI1 -> AS1 -> IND
```

The change is applied from the current minute **only until the next different status change that was already planned before the first override**. The schedule from that preserved boundary onward is left untouched.

For example:

```text
Before
Saturday 14:38                                  Monday 07:00
AS1 -------------------------------------------> DI1

Press once
AS1 history | DI1 -----------------------------> DI1
              ^ current minute                   ^ original boundary preserved

Press again later
AS1 history | DI1 history | AS1 ----------------> DI1
```

The preserved boundary is stored by Home Assistant, so repeated presses — and Home Assistant restarts — do not accidentally extend the temporary override when the selected temporary status happens to match the status planned at the boundary.

The button is unavailable when:

- ARTEMIS reports the personal planning as read-only;
- the current status is not `IND`, `DI1`, or `AS1`;
- the target status is not offered by that ARTEMIS deployment;
- no future planned status change can be found.

ARTEMIS WebEvo may also refuse a write because of its own consistency/ubiquity controls. The integration does **not** force those conflicts.

> **Important:** pressing this button writes to your real ARTEMIS personal planning. Home Assistant is not the authoritative operational system; verify important availability changes in the official tools used by your fire and rescue service.

### Simple dashboard button

No helper, `input_select`, script, or intermediate automation is required:

```yaml
type: button
entity: button.cycle_artemis_status
name: Changer statut
icon: mdi:account-switch
show_state: false
tap_action:
  action: perform-action
  perform_action: button.press
  target:
    entity_id: button.cycle_artemis_status
```

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

Two entities represent the current aggregate state:

```text
sensor.active_interventions_count_artemis
binary_sensor.active_interventions_artemis
```

### Structured intervention lifecycle event

For notification and automation design, listen to:

```text
artemis_intervention_update
```

This event is emitted when an active operation is first observed, when data exposed by ARTEMIS changes, and when the operation disappears from the active synoptic. It also emits a `snapshot` for operations already active when Home Assistant starts.

`lifecycle` can be:

- `snapshot`: already active when the integration starts;
- `new`: a newly observed operation;
- `updated`: ARTEMIS changed the operation, vehicle or unit data;
- `ended`: the operation disappeared from the active synoptic.

The event also exposes `active: true/false`. An `ended` lifecycle is inferred from disappearance from the live active-operation list; it is not an official replacement for the operational status recorded in ARTEMIS. The final event keeps the last known operation payload so a mobile notification can show the last known resources and states.

Typical structured event data:

```yaml
id: "26000042"
number: "26000042"
disaster: "SECOURS A PERSONNE"
address: "BEAUFORT - 12 RUE EXEMPLE - 39190"
created: "2026-09-30T11:43:00"
state: "EC - EN COURS"
state_code: "EC"
state_name: "EN COURS"
active: true
lifecycle: updated
fire_units_data:
  - id: "BEA"
    label: "BEAUF"
    state_code: "PA"
    state_name: "PARTI"
vehicles_data:
  - id: "123"
    center: "BEAUF"
    name: "VLTU 01"
    type: "VLTU"
    state_code: "PA"
    state_name: "PARTI"
    estimated_time: "11:48"
external_services_data: []
```

The exact fields depend on what the ARTEMIS server exposes for the operation. The older string fields (`fire_units`, `vehicles`, `external_services`) and the prebuilt `title` / `message` fields are kept for backward compatibility. You do not need to use the prebuilt message: the structured fields are intended for fully custom Home Assistant notifications.

The existing event remains available:

```text
artemis_new_intervention
```

It fires only for genuinely new operation IDs and is retained for existing automations.

### Persistent, live-updating Android intervention notification

This example creates one notification per operation. While the operation is active, the notification is persistent and is updated in place whenever ARTEMIS changes its state or resources. When the operation ends, the same notification is updated to show `Terminée`, becomes dismissible, and is left on the phone until the user removes it.

Replace `notify.mobile_app_TON_TELEPHONE` with your Home Assistant Companion notification service.

```yaml
alias: "ARTEMIS - Suivi intervention"
description: "Notification persistante mise à jour pendant toute l'intervention"
mode: queued
max: 20

triggers:
  - trigger: event
    event_type: artemis_intervention_update

conditions: []

actions:
  - variables:
      notification_title: >-
        🚒 {{ trigger.event.data.disaster }}
      notification_message: |-
        {{ trigger.event.data.address }}
        État : {{
          (trigger.event.data.state_name or trigger.event.data.state_code or trigger.event.data.state or 'En cours')
          if trigger.event.data.active
          else 'Terminée'
        }}
        {% for vehicle in trigger.event.data.vehicles_data %}
        {{ vehicle.center or 'Centre' }} : {{ vehicle.name }} [{{ vehicle.state_name or vehicle.state_code or vehicle.state or '?' }}]
        {% endfor %}
      notification_tag: >-
        artemis_intervention_{{ trigger.event.data.id }}

  - choose:
      - conditions:
          - condition: template
            value_template: "{{ trigger.event.data.active }}"
        sequence:
          - action: notify.mobile_app_TON_TELEPHONE
            data:
              title: "{{ notification_title }}"
              message: "{{ notification_message }}"
              data:
                tag: "{{ notification_tag }}"
                group: artemis_interventions
                persistent: true
                sticky: true
                alert_once: true
                importance: high
                priority: high
                ttl: 0
                notification_icon: mdi:fire-alert
                channel: Interventions SPV
                clickAction: "app://com.sis.smartemis"
    default:
      - action: notify.mobile_app_TON_TELEPHONE
        data:
          title: "{{ notification_title }}"
          message: "{{ notification_message }}"
          data:
            tag: "{{ notification_tag }}"
            group: artemis_interventions
            persistent: false
            sticky: false
            alert_once: true
            notification_icon: mdi:fire-alert
            channel: Interventions SPV
            clickAction: "app://com.sis.smartemis"
```

Example while active:

```text
🚒 SECOURS A PERSONNE
BEAUFORT - 12 RUE EXEMPLE - 39190
État : EN COURS
BEAUF : VLTU 01 [PARTI]
BEAUF : VSAV 01 [SUR LES LIEUX]
```

Example after the operation disappears from the active synoptic:

```text
🚒 SECOURS A PERSONNE
BEAUFORT - 12 RUE EXEMPLE - 39190
État : Terminée
BEAUF : VLTU 01 [RETOUR]
BEAUF : VSAV 01 [RETOUR]
```

The same YAML is included in [`automation_intervention_status.example.yaml`](automation_intervention_status.example.yaml).

## Persistent Android status notification with a cycle action

A compact persistent Android notification can act as an always-visible ARTEMIS summary. Example:

```text
🚒 4 dispo · AS1 > 05/10 07:00 > DI1
```

If the next planned change is today, the date is omitted:

```text
🚒 4 dispo · AS1 > 18:30 > DI1
```

Tapping the **notification itself** opens Smartemis. Expanding it shows one action button whose label follows the current cycle, for example `→ DI1`, `→ AS1`, or `→ IND`. Pressing that action triggers `button.cycle_artemis_status`.

Replace `notify.mobile_app_TON_TELEPHONE` with your Android Companion App notify action.

### 1. Persistent notification

```yaml
alias: ARTEMIS - Statut permanent

triggers:
  - trigger: state
    entity_id:
      - sensor.available_personnel_artemis
      - sensor.statut_artemis
      - sensor.prochain_changement_artemis

  - trigger: homeassistant
    event: start

conditions:
  - condition: template
    value_template: >-
      {{ states('sensor.available_personnel_artemis') not in ['unknown', 'unavailable']
         and states('sensor.statut_artemis') not in ['unknown', 'unavailable']
         and states('sensor.prochain_changement_artemis') not in ['unknown', 'unavailable'] }}

actions:
  - action: notify.mobile_app_TON_TELEPHONE
    data:
      title: >-
        {% set next_dt = as_local(as_datetime(
          state_attr('sensor.prochain_changement_artemis', 'date')
        )) %}
        {% set current_code = state_attr('sensor.statut_artemis', 'code') %}
        {% set next_code = state_attr('sensor.prochain_changement_artemis', 'prochain_code') %}
        🚒 {{ states('sensor.available_personnel_artemis') }} dispo ·
        {{ current_code }} >
        {% if next_dt.date() == now().date() %}
          {{ next_dt.strftime('%H:%M') }}
        {% else %}
          {{ next_dt.strftime('%d/%m %H:%M') }}
        {% endif %}
        > {{ next_code }}

      # Android requires a message. A zero-width space keeps the notification compact.
      message: "\u200B"

      data:
        tag: artemis_status
        persistent: true
        sticky: true
        alert_once: true
        notification_icon: mdi:fire-alert
        channel: ARTEMIS Status

        # Tap the notification body -> Smartemis.
        clickAction: "app://com.sis.smartemis"

        # Expanded notification -> cycle the current ARTEMIS status.
        actions:
          - action: ARTEMIS_CYCLE_STATUS
            title: >-
              {% set current = state_attr('sensor.statut_artemis', 'code') %}
              {% set cycle = {'IND': 'DI1', 'DI1': 'AS1', 'AS1': 'IND'} %}
              → {{ cycle.get(current, '—') }}

mode: restart
```

Because the same `tag` is reused, Android updates the existing notification instead of creating a new one. `alert_once: true` keeps normal status refreshes silent.

### 2. Handle the notification action

The Companion App sends a `mobile_app_notification_action` event when the action button is pressed. This automation forwards it to the integration button:

```yaml
alias: ARTEMIS - Changer statut depuis notification

triggers:
  - trigger: event
    event_type: mobile_app_notification_action
    event_data:
      action: ARTEMIS_CYCLE_STATUS

conditions:
  - condition: template
    value_template: >-
      {{ trigger.event.data.get('tag') == 'artemis_status' }}

actions:
  - action: button.press
    target:
      entity_id: button.cycle_artemis_status

mode: queued
max: 5
```

After ARTEMIS confirms the write, the integration refreshes the personal planning and centre counter. The persistent notification then updates automatically from the sensor state change.

The complete example is also available in [`automation_persistent_status.example.yaml`](automation_persistent_status.example.yaml).

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
- operation addresses and details are emitted in the `artemis_new_intervention` and `artemis_intervention_update` events for notification/automation use;
- the integration does not create a permanent address/history sensor;
- optional status writes are restricted to the authenticated user's personal planning and the `IND` / `DI1` / `AS1` cycle.

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
api/planningStaff/saveStaff
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

### Intervention already active after a Home Assistant restart

Existing operations still do **not** trigger a false `artemis_new_intervention`. They do emit an `artemis_intervention_update` event with `lifecycle: snapshot`, allowing a persistent status notification to be recreated or refreshed after restart.

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
