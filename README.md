# ARTEMIS WebEvo for Home Assistant

Unofficial Home Assistant custom integration for **ARTEMIS WebEvo**, initially developed and tested against the SDIS 39 deployment.

> [!WARNING]
> This project is not affiliated with or endorsed by Systel, ARTEMIS, SDIS 39, or any French emergency-service authority. It must **not** be used as the sole alerting, dispatch or operational-information channel.

## What it does

Version **0.5.2** deliberately keeps the Home Assistant surface small:

| Entity | Purpose |
| --- | --- |
| `sensor.statut_artemis` | Personal status, next planned change, station availability and write capability |
| `sensor.interventions_artemis` | Number of currently active interventions + structured active-intervention snapshot |
| `button.cycle_artemis_status` | `IND -> DI1 -> AS1 -> IND`, only until the next already-planned change |

There are no separate next-change, availability, active-operation-count or binary sensors in 0.5.x. The useful data is grouped into the two sensors above. Upgrades from older releases automatically remove the retired entity-registry entries so they do not remain visible as unavailable entities.

## Architecture

The integration authenticates to WebEvo through the observed CAS/WebEvo login flow and uses only endpoints already available to the configured account.

It intentionally avoids a local intervention lifecycle/state machine:

```text
ARTEMIS synoptic
      |
      | poll (WebEvo refresh interval, minimum 15 s)
      v
sensor.interventions_artemis
      |
      | Home Assistant compares snapshots
      v
notifications / automations / dashboards
```

Network refreshes use different cadences to avoid unnecessary ARTEMIS traffic:

- active interventions: WebEvo's advertised synoptic refresh interval, with a 15-second minimum;
- personal planning: every 5 minutes, plus an exact refresh just after the next known planning boundary;
- native centre availability counter: every 30 seconds.

`DataUpdateCoordinator` equality checks prevent entity updates when a returned snapshot is unchanged.

## Installation

### HACS

1. Open HACS -> **Integrations**.
2. Add `https://github.com/purierca/artemis` as a custom repository of type **Integration**.
3. Install **ARTEMIS WebEvo**.
4. Restart Home Assistant.
5. Go to **Settings -> Devices & services -> Add integration -> ARTEMIS WebEvo**.

The defaults match the deployment used during development:

```text
Host:        https://artemisweb.sdis39.fr
CAS service: https://artemis/artemis-web
```

Other WebEvo deployments may use different values or expose different permissions/data structures.

### Manual

Copy:

```text
custom_components/artemis/
```

into:

```text
/config/custom_components/artemis/
```

and restart Home Assistant.

## `sensor.statut_artemis`

The sensor state remains the human-readable current ARTEMIS status. Its attributes contain everything needed for dashboards and compact notifications.

Example:

```yaml
state: DISPONIBLE NIV 1
attributes:
  code: DI1
  since: "2026-10-05T07:00:00+02:00"
  next_status: ASTREINTE NIV 1
  next_code: AS1
  next_change: "2026-10-05T18:30:00+02:00"
  available_personnel: 4
  personnel_in_operation: 2
  center: BEA
  person: "..."
  override_active: false
  writable: true
  can_cycle: true
  cycle_next_code: AS1
```

This single sensor replaces the separate next-change and available-personnel sensors used by earlier releases.

## One-button personal status cycle

`button.cycle_artemis_status` performs a real ARTEMIS planning write:

```text
IND -> DI1 -> AS1 -> IND
```

The change applies only from the current time until the **next status boundary that was already planned before the first override**.

For example:

```text
Before
AS1 ---------------------------> IND
now                            Monday 07:00

Press button
DI1 ---------------------------> IND
now                            Monday 07:00
```

Monday 07:00 is preserved. Repeated button presses continue to use that same original boundary, including across a Home Assistant restart.

The integration refuses the write when:

- the personal planning is read-only;
- the current/target status is outside `IND`, `DI1`, `AS1`;
- WebEvo does not advertise the target status;
- no future planned boundary is available;
- ARTEMIS requests an ubiquity/conflict confirmation.

It never automatically forces an ubiquity conflict.

A minimal dashboard button is provided in [`dashboard_cycle_button.example.yaml`](dashboard_cycle_button.example.yaml).

## Persistent personal-status notification

The included example creates a compact Android notification such as:

```text
🚒 4 dispo · DI1 > 18:30 > AS1
```

or, when the next change is not today:

```text
🚒 4 dispo · AS1 > 07/10 07:00 > IND
```

Behaviour:

- the notification stays persistent;
- tapping the notification opens Smartemis;
- its action button dynamically shows the next cycle status, e.g. `-> AS1`;
- tapping that action presses `button.cycle_artemis_status`;
- the same automation handles both notification refreshes and action taps.

See [`automation_status_notification.example.yaml`](automation_status_notification.example.yaml).

Replace `notify.mobile_app_YOUR_PHONE` with your own Companion App notification service.

## `sensor.interventions_artemis`

The sensor state is simply the number of interventions currently returned by the ARTEMIS active synoptic.

The `interventions` attribute is a structured snapshot intended for your own Home Assistant automations. The integration does **not** pre-format the mobile notification.

Example:

```yaml
state: 1
attributes:
  interventions:
    - id: "26000042"
      number: "26000042"
      title: "SECOURS A PERSONNE"
      created: "2026-10-05T15:41:00"
      address: "BEAUFORT - 12 RUE EXEMPLE - 39190"
      latitude: 46.575123
      longitude: 5.438456
      navigation_uri: "geo:46.575123,5.438456?q=46.575123,5.438456"
      state_code: EC
      state_name: EN COURS
      vehicles:
        - center: BEAUF
          name: VLTU 01
          state_code: PA
          state_name: PARTI
        - center: BEAUF
          name: VSAV 01
          state_code: SL
          state_name: SUR LES LIEUX
```

If ARTEMIS does not expose a plausible WGS84 GPS pair, `navigation_uri` falls back to an Android `geo:` search for the formatted address. Ambiguous projected `x`/`y` coordinates are deliberately ignored.

### Why there is no `new/updated/ended` field

Every item in `sensor.interventions_artemis` is active **right now**. That is the entire contract.

Home Assistant can infer lifecycle from two consecutive snapshots:

- present now, absent before -> new;
- present in both, data changed -> updated;
- absent now, present before -> ended.

This keeps ARTEMIS polling/state handling in the integration and notification policy in Home Assistant.

## Persistent intervention tracking notification

[`automation_interventions.example.yaml`](automation_interventions.example.yaml) is one automation for the complete intervention lifecycle.

For an active intervention it creates/updates a persistent notification with a stable tag:

```text
🚒 SECOURS A PERSONNE
BEAUFORT - 12 RUE EXEMPLE - 39190
État : EN COURS
BEAUF : VLTU 01 [PARTI]
BEAUF : VSAV 01 [SUR LES LIEUX]

[Naviguer]
```

As ARTEMIS updates the operation or vehicle states, the same notification is replaced in place.

When the intervention disappears from the active ARTEMIS snapshot, the automation reuses the **last known snapshot** and updates the same notification to:

```text
🚒 SECOURS A PERSONNE
BEAUFORT - 12 RUE EXEMPLE - 39190
État : Terminée
BEAUF : VLTU 01 [RETOUR]
BEAUF : VSAV 01 [RETOUR]

[Naviguer]
```

The integration does not invent a server-side `Terminée` state: the automation uses that label because the intervention is no longer present in the active synoptic. Vehicle lines use the **last states actually seen in ARTEMIS**; the automation does not invent a `RETOUR` state if WebEvo never exposed one before removing the operation.

The final notification is **not cleared**. It becomes dismissible and remains on the phone until the user removes it.

The example also provides:

- main notification tap -> Smartemis;
- `Naviguer` -> ARTEMIS GPS point when available, otherwise address search;
- one notification per intervention using `artemis_intervention_<id>` as the notification tag;
- `alert_once` so state/resource updates do not repeatedly alert the phone.

## Privacy and Recorder

Operational addresses, vehicle states and potentially GPS coordinates are stored in the Home Assistant state attributes while an intervention is active.

If you do not want those snapshots written to Recorder/history, exclude the sensor:

```yaml
recorder:
  exclude:
    entities:
      - sensor.interventions_artemis
```

Also remember that automation traces and mobile notification history may contain operational information.

Never publish unredacted HAR files, automation traces or logs containing operational or authentication data.

## Authentication and security

Credentials are stored in the local Home Assistant config entry and used to authenticate to the configured WebEvo deployment.

The integration automatically renews the WebEvo session when it detects an authentication redirect/expiry.

Never publish:

- ARTEMIS username/password;
- CAS tickets;
- `JSESSIONID`, `MOD_AUTH_CAS_S`, `CASTGC` or similar cookies;
- `ssoSha1` values;
- identifiable firefighter information;
- active-operation addresses/GPS/resource data.

See [`SECURITY.md`](SECURITY.md).

## Compatibility

This integration is based on WebEvo behaviour observed on one deployment. Different SDIS deployments can differ in:

- authentication configuration;
- permissions;
- status codes;
- planning rules;
- intervention/address/GPS fields;
- availability-counter access.

Compatibility reports are welcome, but please redact all sensitive data before sharing them.

## Development

Local checks:

```bash
python3 -m compileall -q custom_components/artemis
python3 -m unittest discover -s tests -v
python3 scripts/check_ready.py
```

GitHub Actions additionally run HACS validation and Hassfest.

## AI-assisted development

This integration was largely designed and implemented with AI assistance, with the repository owner defining the intended behaviour and testing it against their authorised ARTEMIS account.

Human review is very welcome. Recommendations, code review, fixes and architectural suggestions are especially welcome from experienced Python and Home Assistant developers.

## Licence

MIT. See [`LICENSE`](LICENSE).
