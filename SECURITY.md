# Security policy

ARTEMIS credentials and operational information can be sensitive.

## Do not publish secrets

Never include any of the following in a public GitHub issue, discussion, screenshot or HAR file:

- ARTEMIS username or password;
- CAS tickets;
- `JSESSIONID`, `MOD_AUTH_CAS_S`, `CASTGC` or other session cookies;
- the `ssoSha1` hand-off value;
- personal information about firefighters;
- addresses or details of active operations unless they have been fully anonymised.

If credentials were accidentally included in a HAR or issue, invalidate the session and change the password immediately.

## Reporting a vulnerability

Do not open a public issue containing exploit details or credentials. Contact the repository owner privately through the security contact configured on GitHub, or use GitHub Private Vulnerability Reporting if enabled.

## Scope

This integration is intended to use only data that the configured ARTEMIS account is already authorised to access. It does not attempt to bypass authentication, access controls, TLS protections or other security mechanisms.
