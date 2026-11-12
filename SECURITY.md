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

## Local Smartemis phoneId capture

Automatic Smartemis setup uses a temporary LAN-only proxy. It binds to one local IPv4 address on an ephemeral port, allows only official Smartemis hosts discovered from the official directory, locks to one phone peer and stops automatically after five minutes.

Before capture, the proxy inspects only recognised Smartemis Thrift requests. After the `phoneId` is found, subsequent Smartemis connections are blind TCP tunnels. The integration never persists captured traffic and never logs request/response bodies or the raw `phoneId`. The ID is stored only in the local Home Assistant config entry after setup.

The proxy forwards intercepted requests only to their original official Smartemis host. No captured credential or operational payload is sent to a repository, analytics service, telemetry endpoint or other third party.

The temporary self-signed certificate and private key are generated locally. Temporary certificate/key files are deleted immediately after the in-memory TLS context loads them.

## Personal planning writes

`button.cycle_artemis_status` performs a real personal-planning write on both backends. WebEvo keeps its guarded single-row override boundary. Smartemis uses the official app's `updateCurrentPlanning` request with `EOP` (end of current period), and only when the target status is explicitly advertised by `getCurrentPlanning`. The public cycle is limited to `IND` / `DI1` / `AS1`.

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
