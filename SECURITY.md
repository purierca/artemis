# Security policy

ARTEMIS credentials and operational information can be sensitive.

## Do not publish secrets

Never include any of the following in a public GitHub issue, discussion, screenshot or HAR file:

- ARTEMIS username or password;
- CAS tickets;
- `JSESSIONID`, `MOD_AUTH_CAS_S`, `CASTGC` or other session cookies;
- the `ssoSha1` hand-off value;
- personal information about firefighters;
- addresses, GPS coordinates or resource details of active operations unless fully anonymised.

If credentials were accidentally included in a HAR or issue, invalidate the session and change the password immediately.

## Reporting a vulnerability

Do not open a public issue containing exploit details or credentials. Contact the repository owner privately through the security contact configured on GitHub, or use GitHub Private Vulnerability Reporting if enabled.

## Scope

This integration is intended to use only data that the configured ARTEMIS account is already authorised to access. It does not attempt to bypass authentication, access controls, TLS protections or other security mechanisms.

## Personal planning writes

`button.cycle_artemis_status` performs a real write to ARTEMIS, not a Home Assistant-only helper. The implementation is intentionally limited to the authenticated user's personal planning, the `IND` / `DI1` / `AS1` cycle, and the next planned status boundary that existed before the override.

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
