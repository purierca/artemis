# Changelog

All notable changes to this project will be documented here.

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
