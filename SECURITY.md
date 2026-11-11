# Security policy

ARTEMIS/Smartemis credentials, device identifiers and operational information can be sensitive.

## Do not publish secrets

Never include any of the following in a public GitHub issue, discussion, screenshot or HAR file:

- ARTEMIS username/password or Smartemis authentication code;
- Smartemis device/phone identifiers;
- CAS tickets;
- `JSESSIONID`, `MOD_AUTH_CAS_S`, `CASTGC` or other session cookies;
- the `ssoSha1` hand-off value;
- personal information about firefighters;
- addresses, GPS coordinates or resource details of active operations unless fully anonymised.

If credentials were accidentally included in a HAR or issue, invalidate the session and change the password immediately.

## Reporting a vulnerability

Do not open a public issue containing exploit details or credentials. Contact the repository owner privately through the security contact configured on GitHub, or use GitHub Private Vulnerability Reporting if enabled.

## Scope

This integration is intended to use only data that the configured ARTEMIS/Smartemis account is already authorised to access. It does not bypass authentication or application access controls. Smartemis setup reuses an identity already registered by the official app and does not call `recordSubscriber`. The official Smartemis app uses a no-check TLS trust mode; the Smartemis backend mirrors that compatibility behaviour, so only configure a Smartemis server obtained from the official service directory.

## Personal planning writes

Smartemis remains read-only in the Home Assistant entity surface. Its `updateCurrentPlanning` protocol is implemented only for controlled validation and is not exposed as a button. On the WebEvo backend, `button.cycle_artemis_status` performs a real write to ARTEMIS, not a Home Assistant-only helper. The implementation is intentionally limited to the authenticated user's personal planning, the `IND` / `DI1` / `AS1` cycle, and the next planned status boundary that existed before the override.

The integration refuses ARTEMIS ubiquity/conflict confirmations rather than forcing them. Do not expose the button or notification action to untrusted Home Assistant users.

## Intervention sensor data

`sensor.interventions_artemis` can expose operational addresses, GPS coordinates, vehicle names and states as Home Assistant attributes while an operation is active.

Consider excluding it from Recorder:

```yaml
recorder:
  exclude:
    entities:
      - sensor.interventions_artemis
```

Automation traces, logs and mobile notification history can also retain operational information. Treat them accordingly.
