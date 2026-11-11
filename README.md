# ARTEMIS / Smartemis for Home Assistant

Unofficial Home Assistant custom integration for **ARTEMIS WebEvo** and the **Smartemis mobile API**.

> [!WARNING]
> This project is not affiliated with or endorsed by Inetum/Systel, ARTEMIS, Smartemis, any SDIS/SIM, or any emergency-service authority. It must **not** be used as the sole alerting, dispatch or operational-information channel.

## What it does

Version **0.7.0** keeps the Home Assistant surface deliberately small:

| Entity | WebEvo | Smartemis | Purpose |
| --- | --- | --- | --- |
| `sensor.statut_artemis` | yes | yes | Personal status, next planned change and available-personnel count when exposed |
| `sensor.interventions_artemis` | yes | yes | Active-intervention count + structured interventions, vehicles, address and GPS |
| `button.cycle_artemis_status` | yes | no | Safe `IND -> DI1 -> AS1 -> IND` WebEvo planning write |

Smartemis support is read-only in Home Assistant. Version **0.7.0** reuses the exact device identity already registered by the official Smartemis app; Home Assistant does **not** register a second Smartemis device. Existing WebEvo installations are unaffected.

## Backends

### ARTEMIS WebEvo

The original backend authenticates through the observed CAS/WebEvo flow. It was developed and validated against SDIS 39 and retains the guarded personal-planning write path introduced in 0.5.x.

### Smartemis

The Smartemis backend follows the Apache Thrift JSON protocol used by the official Android app.

Home Assistant deliberately **does not call `recordSubscriber`**. Smartemis deployments may associate one registered device identity with an account, and registering a new random identity can invalidate the official mobile-app session.

Instead, setup reuses the official app's existing `phoneId`:

1. enter the existing Smartemis `phoneId`;
2. Home Assistant uses it only to fetch the official service list;
3. choose the same fire service as in the app;
4. Home Assistant validates the identity with `getSubscriber(phoneId)`;
5. all later reads use that same identity in parallel with the mobile app.

The `phoneId` is stored in the Home Assistant config entry and must be treated like a credential.

#### Getting the existing phoneId on Android

Smartemis 6.0.22 stores `device.uuid` as `phoneId`. On Android this comes from the app's Android ID and the app logs the value as `PHONE ID GENERATED` when its local storage is initialised.

With Android platform tools / ADB connected, first check whether the line is still in the device log:

```bash
adb logcat -d | grep -F "PHONE ID GENERATED"
```

If it is no longer present:

1. run `adb logcat -c`;
2. start a live capture:

   ```bash
   adb logcat | grep -F "PHONE ID GENERATED"
   ```

3. in Android settings, clear **Smartemis app storage/data**;
4. launch Smartemis once;
5. copy the value printed after `PHONE ID GENERATED`;
6. sign back into the official Smartemis app normally;
7. use that exact value in Home Assistant.

Clearing app storage signs the app out and removes its local settings, so make sure you can sign back in first. Do **not** use a random UUID and do not use `adb shell settings get secure android_id` as a substitute on modern Android: the identifier exposed to an app can be scoped to the app signing key/user/device.

The backend has been validated against the **Service de sécurité incendie de Montréal (SIM)** for authentication reuse and read access, including global active interventions, engaged stations/vehicles, vehicle states, addresses and GPS. Smartemis' `default_cs` setting is treated only as the app's default displayed station; personal planning uses the actual affiliated station exposed by the subscriber profile.

#### Smartemis status changes

The official app changes current availability through `updateCurrentPlanning`. The integration now implements and tests that exact Thrift request internally using the safest app boundary, `EOP` (end of current planning period).

It is **not exposed as a Home Assistant button yet**. A controlled real-account write/read-back test is required before enabling it publicly.


## Polling architecture

Both backends normalize their data into the same two sensor snapshots:

```text
WebEvo synoptic / Smartemis
            |
            v
sensor.interventions_artemis
            |
            v
Home Assistant automations / notifications / dashboards
```

WebEvo keeps its existing independent polling cadences. Smartemis currently polls personal planning and interventions every 30 seconds; intervention detail is refreshed for each active intervention so resource states and GPS remain current.

## Installation

### HACS

1. Open HACS -> **Integrations**.
2. Add `https://github.com/purierca/artemis` as a custom repository of type **Integration**.
3. Install **ARTEMIS / Smartemis**.
4. Restart Home Assistant.
5. Go to **Settings -> Devices & services -> Add integration -> ARTEMIS / Smartemis**.
6. Choose **ARTEMIS WebEvo** or **Smartemis mobile**.

Existing 0.5.x installations migrate automatically to the WebEvo backend without changing stored credentials.

### Manual

Copy `custom_components/artemis/` into `/config/custom_components/artemis/` and restart Home Assistant.

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

`button.cycle_artemis_status` is available on the **WebEvo backend only** and performs a real ARTEMIS planning write:

```text
IND -> DI1 -> AS1 -> IND
```

When a future different status is already planned, the change applies only from the current time until that **original planned boundary**.

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

A manual cycle is always bounded by the **earliest** of:

- the next real ARTEMIS status change; or
- the next 12-hour duty handover derived from ARTEMIS `planningStartTime`.

On the SDIS 39 deployment, where the operational day starts at 07:00, those handovers are **07:00 and 19:00**. An indefinitely `IND` planning therefore never gets overwritten through the whole look-ahead horizon: a press at 09:30 stops at 19:00, and a press at 21:00 stops at 07:00 the next morning.

Repeated presses before that boundary keep the **same original boundary**, including across a Home Assistant restart, so cycling `IND -> DI1 -> AS1 -> IND` cannot progressively extend the temporary change.

Before every write, the integration re-reads the **exact current operational-day row** from WebEvo. Only that single row may be submitted back to `saveStaff`; all periods outside the temporary override window are preserved unchanged. The write is aborted if it would require more than one planning row. Legacy stored boundaries from older releases are also rejected when they extend beyond the next handover.

The integration refuses the write when:

- the personal planning is read-only;
- the current/target status is outside `IND`, `DI1`, `AS1`;
- WebEvo does not advertise the target status;
- ARTEMIS requests an ubiquity/conflict confirmation.

It never automatically forces an ubiquity conflict.

A minimal dashboard button is provided in [`dashboard_cycle_button.example.yaml`](dashboard_cycle_button.example.yaml).

## Persistent personal-status notification

The included example creates a compact Android notification such as:

```text
4 dispo · DI1 > 18:30 > AS1
```

or, when the next change is not today:

```text
4 dispo · AS1 > 07/10 07:00 > IND
```

When there is no future different status, the notification simply stays compact:

```text
4 dispo · IND
```

The action button is still available, for example `-> DI1`.

Behaviour:

- Android uses the `mdi:fire-truck` notification icon with firefighter red `#CE2029`; no truck emoji is added to the title;
- the notification stays persistent;
- tapping the notification opens Smartemis;
- its action button derives the next cycle status directly from the current `code`, e.g. `DI1` always shows `-> AS1`;
- tapping that action presses `button.cycle_artemis_status`;
- on WebEvo, after the write, the integration briefly re-reads the planning so the new current code is reflected immediately;
- the same automation handles the action tap; the resulting `sensor.statut_artemis` change triggers the normal refresh branch, which rebuilds the notification cleanly; no separate `ARTEMIS - Changer statut depuis notification` automation is needed.

See [`automation_status_notification.example.yaml`](automation_status_notification.example.yaml).

Replace `notify.mobile_app_YOUR_PHONE` with your own Companion App notification service.

## `sensor.interventions_artemis`

The sensor state is the number of active interventions currently visible to the configured WebEvo or Smartemis account.

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
      navigation_uri: "deep-link://geo:0,0?q=46.575123%2C5.438456"
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

`navigation_uri` uses Home Assistant Companion's `deep-link://` wrapper around Android's standard `geo:` URI. No navigation package is hard-coded: Android opens the user's compatible/default mapping or navigation app (or offers a chooser when applicable). ARTEMIS GPS coordinates are used when available; otherwise the formatted intervention address is used as the destination. Ambiguous projected `x`/`y` coordinates are deliberately ignored.

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
SECOURS A PERSONNE
BEAUFORT - 12 RUE EXEMPLE - 39190
État : EN COURS
BEAUF : VLTU 01 [PARTI]
BEAUF : VSAV 01 [SUR LES LIEUX]

[Naviguer]
```

As ARTEMIS updates the operation or vehicle states, the same notification is replaced in place.

When the intervention disappears from the active ARTEMIS snapshot, the automation reuses the **last known snapshot** and updates the same notification to:

```text
SECOURS A PERSONNE
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
- `Naviguer` -> ARTEMIS GPS point when available, otherwise address search, using the user's compatible/default navigation app;
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

WebEvo credentials are stored in the local Home Assistant config entry and used only against the configured WebEvo deployment. Smartemis setup stores the existing mobile-app `phoneId` plus the selected service/server. It never registers a second Smartemis device. Treat the `phoneId` as a credential and never publish it.

The official Smartemis app disables strict server-certificate verification. The Smartemis backend mirrors that behaviour for compatibility with deployments whose certificate chain is not accepted by the default Python trust store. Treat the configured Smartemis server as trusted.

Never publish credentials, device identifiers, session cookies, authentication codes, unredacted logs, or active operational data. See [`SECURITY.md`](SECURITY.md).

## Compatibility

Validated deployments:

- **ARTEMIS WebEvo:** SDIS 39 (France);
- **Smartemis:** Service de sécurité incendie de Montréal / SIM (Canada).

Other deployments can differ in authentication policy, permissions, status codes, planning rules and exposed operational fields. Compatibility reports are welcome when all personal and operational data is redacted.

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
