# Changelog

All notable changes to this project will be documented here.

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
