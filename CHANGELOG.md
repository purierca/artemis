# Changelog

All notable changes to this project will be documented here.

## 0.5.0

### Changed

- Reduced the Home Assistant entity surface to two sensors plus the existing status-cycle button:
  - `sensor.statut_artemis`
  - `sensor.interventions_artemis`
  - `button.cycle_artemis_status`
- Folded next status/change and native centre availability into attributes of `sensor.statut_artemis`.
- Replaced intervention lifecycle events with one structured, polled `sensor.interventions_artemis` snapshot.
- Removed the separate availability/intervention binary sensors and separate next-change/available-personnel/operation-count sensors.
- Operations now use WebEvo's advertised refresh interval (minimum 15 seconds) and only publish a new Home Assistant snapshot when data actually changes.

### Intervention tracking

- `sensor.interventions_artemis` exposes structured intervention id, title, address, GPS/navigation URI, state and vehicles.
- Notification lifecycle is intentionally handled in Home Assistant by comparing the previous and current sensor snapshots.
- Added a single automation example that creates/updates a persistent notification while an intervention is active, marks it `Terminée` when it disappears from the active synoptic, keeps the final notification until the user dismisses it, opens Smartemis on the main tap, and exposes a `Naviguer` action.

### Personal status

- Preserved the safe `IND -> DI1 -> AS1 -> IND` write button and the original planned boundary across repeated presses and Home Assistant restarts.
- Added `available_personnel`, `next_code`, `next_change`, `can_cycle` and `cycle_next_code` attributes to the compact status sensor.
- Reworked the persistent status-notification example into one automation that both refreshes the notification and handles its dynamic cycle-status action button.

### Breaking changes

- Removed `artemis_new_intervention` and `artemis_intervention_update` as the primary automation interface. Use `sensor.interventions_artemis` instead.
- Removed the old standalone next-change, available-personnel, active-operation-count and binary-sensor entities. Existing dashboards/automations using those entities need to be migrated to attributes of `sensor.statut_artemis` or `sensor.interventions_artemis`.

## 0.3.1

### Added

- GPS-aware intervention payload fields: `latitude`, `longitude`, `has_coordinates`, `location_source`, and `location_data`.
- Ready-to-use `navigation_uri` for mobile notification actions. It uses the ARTEMIS GPS point when available and falls back to the formatted intervention address.
- `Naviguer` action in the persistent Android intervention notification example while preserving Smartemis as the main notification tap action.

### Safety / compatibility

- Only plausible WGS84 latitude/longitude values are accepted. Ambiguous projected `x`/`y` coordinates are ignored instead of being interpreted as GPS.
- All existing `artemis_intervention_update` fields remain backward compatible.

## 0.3.0

### Added

- Structured intervention lifecycle event `artemis_intervention_update`.
- Lifecycle values `snapshot`, `new`, `updated`, and `ended`, plus an `active` boolean.
- Structured `fire_units_data`, `vehicles_data`, and `external_services_data` fields so notification automations can format operation data freely.
- Persistent Android intervention-notification example that updates in place while an operation is active and becomes dismissible when it ends.

### Behaviour

- `artemis_new_intervention` is preserved for backward compatibility and still fires only for newly seen operations.
- Active operations present when Home Assistant starts emit a lifecycle `snapshot`, not a false new-intervention event.
- When an operation disappears from the active synoptic, the lifecycle event reports `active: false` and retains the last operation payload so the final mobile notification can display the last known vehicles and states.

## 0.2.0

### Added

- `button.cycle_artemis_status` for one-press `IND -> DI1 -> AS1 -> IND` personal status changes.
- Status overrides stop at the next change that was already planned in ARTEMIS.
- The preserved boundary survives Home Assistant restarts so repeated cycling cannot accidentally extend the override.
- Persistent Android status-notification example with Smartemis tap action and a dynamic cycle-status action button.

### Safety / behaviour

- Writes are restricted to the authenticated user's personal planning.
- The integration refuses unsupported current/target statuses, read-only personal planning, missing future boundaries, and ARTEMIS ubiquity conflicts.
- Centre availability is refreshed immediately after a successful status write.

## 0.1.0

Initial public release.

### Added

- CAS / ARTEMIS WebEvo authentication and automatic session renewal.
- Personal current ARTEMIS status.
- Next real status change displayed as status code + date/time.
- Personal availability binary sensor.
- Native ARTEMIS centre available-personnel counter.
- Active-intervention count and binary sensor using `mdi:fire-alert`.
- `artemis_new_intervention` event with notification-ready operation details.
- Recommended Home Assistant Companion notification automation.
- HACS metadata and GitHub validation workflows.
- English and French config-flow strings.
- Privacy and security guidance.
