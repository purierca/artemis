# ARTEMIS / Smartemis for Home Assistant

Unofficial Home Assistant custom integration for **ARTEMIS WebEvo** and the **Smartemis mobile API**.

> [!WARNING]
> This project is not affiliated with or endorsed by Inetum/Systel, ARTEMIS, Smartemis, any SDIS/SIM, or any emergency-service authority. It must **not** be used as the sole alerting, dispatch or operational-information channel.

## What it does

Version **0.7.2** keeps the Home Assistant surface deliberately small:

| Entity | WebEvo | Smartemis | Purpose |
| --- | --- | --- | --- |
| `sensor.statut_artemis` | yes | yes | Personal status, next planned change and available-personnel count when exposed |
| `sensor.interventions_artemis` | yes | yes | Active-intervention count + structured interventions, vehicles, address and GPS |
| `button.cycle_artemis_status` | yes | yes | `IND -> DI1 -> AS1 -> IND` personal status cycle when the backend advertises those statuses |

Version **0.7.2** reuses the exact Smartemis device identity already registered by the official app and exposes the same status-cycle button on both backends. Home Assistant does **not** register a second Smartemis device. Existing WebEvo installations are unaffected.

## Backends

### ARTEMIS WebEvo

The original backend authenticates through the observed CAS/WebEvo flow. It was developed and validated against SDIS 39 and retains the guarded personal-planning write path introduced in 0.5.x.

### Smartemis

The Smartemis backend follows the Apache Thrift JSON protocol used by the official Android app.

Home Assistant deliberately **does not call `recordSubscriber`**. Smartemis deployments may associate one registered device identity with an account, and registering a new random identity can invalidate the official mobile-app session.

Instead, setup reuses the official app's existing `phoneId`.

#### Automatic phoneId detection — Android and iOS

The recommended setup is entirely driven from the Home Assistant config flow:

1. choose **Smartemis mobile -> Detect automatically from the Smartemis app**;
2. Home Assistant discovers the official Smartemis service hosts using a disposable random UUID that contains no user data and is never persisted;
3. Home Assistant starts a temporary proxy bound only to its LAN IPv4 address on a random high port;
4. on the phone's current Wi-Fi network, set the proxy to **Automatic/PAC** and paste the PAC URL shown by Home Assistant; if PAC is unavailable, use the temporary manual proxy host/port shown by Home Assistant;
5. open the official Smartemis app once;
6. Home Assistant captures the existing `phoneId` from the first recognised Smartemis Thrift request, forwards that request to its original official Smartemis server, and validates the identity;
7. return to Home Assistant, continue setup, then remove the temporary Wi-Fi proxy.

The capture proxy is deliberately narrow:

- only hosts returned by the official Smartemis directory are proxied;
- only one phone peer is accepted during the five-minute capture window;
- only the first recognised Smartemis request is inspected for `phoneId`;
- once the ID is captured, later Smartemis connections are blind TCP tunnels and are no longer decrypted;
- request/response bodies, firefighter identity, intervention data and the raw `phoneId` are never written to integration logs;
- the captured ID exists only in memory during setup, then is stored in the local Home Assistant config entry;
- the temporary TLS certificate/private key are generated locally, loaded into memory and their temporary files are immediately deleted;
- no captured value is sent to any third-party service. The intercepted request is forwarded only to the official Smartemis endpoint it was already addressed to.

Smartemis 6.0.22 configures its native HTTP plugin with certificate trust mode `nocheck`, which is why the temporary interception does not require installing a CA certificate on the phone.

#### Manual phoneId fallback

Manual entry remains available. On Android, Smartemis stores `device.uuid` as `phoneId` and may log `PHONE ID GENERATED` when its local storage is initialised:

```bash
adb logcat -d | grep -F "PHONE ID GENERATED"
```

Do **not** generate a replacement UUID and do not use `adb shell settings get secure android_id` as a substitute. Home Assistant must reuse the exact identifier already registered by Smartemis.


The backend has been validated against the **Service de sécurité incendie de Montréal (SIM)** for authentication reuse and read access, including global active interventions, engaged stations/vehicles, vehicle states, addresses and GPS. Smartemis' `default_cs` setting is treated only as the app's default displayed station; personal planning uses the actual affiliated station exposed by the subscriber profile.

#### Smartemis status changes

The official app changes current availability through `updateCurrentPlanning`. Version 0.7.2 exposes that same write through the shared `button.cycle_artemis_status`.

Smartemis writes use `EOP` (end of current planning period), exactly as the app protocol does. Before every press the integration re-reads `getCurrentPlanning`, requires the target status to be explicitly listed by the server, sends the write for the authenticated subscriber's affiliated station/matricule, then re-reads until the new status is visible.

The public cycle is identical on both backends:

```text
IND -> DI1 -> AS1 -> IND
```

If a Smartemis deployment does not expose one of those status codes or does not grant write rights, the button is unavailable rather than forcing a write.


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

### Complete Smartemis setup walkthrough — Android and iOS

The recommended setup is the same whether the Smartemis phone is **Android or iPhone/iPad**. You can start the Home Assistant setup from **any device**: the Home Assistant mobile app, a tablet, a PC or a Mac. The only step that must be done on the Smartemis phone itself is the temporary Wi-Fi proxy configuration and opening Smartemis once.

1. Open Home Assistant from any device.
2. Open **HACS -> Integrations**.
3. Install **ARTEMIS / Smartemis**.
4. Restart Home Assistant.
5. Go to **Settings -> Devices & services -> Add integration -> ARTEMIS / Smartemis**.
6. Choose **Smartemis mobile**.
7. Choose **Detect automatically from the Smartemis app**.
8. Home Assistant starts a temporary local detection proxy and displays:
   - an automatic/PAC proxy URL;
   - a manual proxy host/port fallback;
   - short instructions for the phone.
9. Now use the **Android phone or iPhone/iPad where Smartemis is installed**.
10. Make sure that phone is connected to the **same local network as Home Assistant**.
11. Open the Wi-Fi settings for the current network.
12. Configure the proxy:
    - on **iOS/iPadOS**: Wi-Fi -> current network -> **Configure Proxy -> Automatic**, then paste the PAC URL shown by Home Assistant;
    - on **Android**: Wi-Fi -> current network -> edit/advanced network options -> **Proxy -> Auto-config/PAC** when available, then paste the PAC URL shown by Home Assistant;
    - if the phone does not offer PAC/automatic proxy mode, use the **manual proxy host and port** shown by Home Assistant.
13. Save the Wi-Fi settings.
14. Open the official **Smartemis** app normally.
15. You normally do **not** need to log out or sign in again.
16. Let Smartemis load for a few seconds.
17. Home Assistant detects the existing Smartemis `phoneId` from normal Smartemis traffic.
18. Home Assistant does **not** call `recordSubscriber` and does **not** create a second Smartemis device identity.
19. Return to the Home Assistant setup screen.
20. Home Assistant validates the existing identity and retrieves the available Smartemis service(s).
21. Choose your fire service, for example:
    - a French SDIS;
    - **SIM Montréal**.
22. Home Assistant validates:
    - the subscriber identity;
    - the actual affiliated station;
    - the Smartemis rights exposed by the server.
23. Check that the displayed service/station is correct, then finish setup.
24. Return to the Smartemis phone Wi-Fi settings.
25. Set the Wi-Fi proxy back to **Off / None**.
26. The temporary proxy is no longer needed after setup.
27. Home Assistant then uses the captured `phoneId` directly from its **local config entry** for future Smartemis reads/writes.

After setup, the entity surface is the same on both backends:

- `sensor.statut_artemis`;
- `sensor.interventions_artemis`;
- `button.cycle_artemis_status`.

The same status button therefore works whether the integration is connected through **ARTEMIS WebEvo** or **Smartemis**:

```text
IND -> DI1 -> AS1 -> IND
```

The server-side implementation differs, but the Home Assistant entity is intentionally identical:

- WebEvo writes through its guarded `saveStaff` workflow;
- Smartemis writes through `updateCurrentPlanning` with the current period's `EOP` boundary.

If automatic detection cannot be used, choose the manual `phoneId` setup option instead.

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

`button.cycle_artemis_status` is the same entity on **WebEvo and Smartemis** and performs a real personal-planning write:

```text
IND -> DI1 -> AS1 -> IND
```

On WebEvo, when a future different status is already planned, the change applies only from the current time until that **original planned boundary**. On Smartemis, the official app protocol uses the current period's `EOP` boundary.

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
- the active backend does not advertise the target status;
- Smartemis does not expose a current-period boundary;
- WebEvo requests an ubiquity/conflict confirmation.

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
- after a write on either backend, the integration briefly re-reads the planning so the new current code is reflected immediately;
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

Smartemis service discovery is dynamic and is not specific to Montréal, so the same backend is intended to work with French SDIS using the same Smartemis API. **Only SIM has been validated end-to-end so far.** Reads should work where the same API is exposed; the status button is enabled only when that deployment explicitly advertises the target `IND` / `DI1` / `AS1` status. A deployment using different codes or denying writes stays safely read-only.

Other deployments can differ in authentication policy, permissions, status codes, planning rules and exposed operational fields. Compatibility reports are welcome when all personal and operational data is redacted.

## Debug logging

For detailed integration diagnostics:

```yaml
logger:
  logs:
    custom_components.artemis: debug
```

Smartemis DEBUG logging records protocol method names, server host, HTTP status, latency, centre code, allowed status codes, write/read-back attempts and intervention counts. It deliberately does **not** log raw request/response payloads, `phoneId`, names, addresses, GPS coordinates or intervention contents.

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
