# Contributing

Contributions are welcome, especially compatibility reports for other ARTEMIS WebEvo deployments.

## Before opening an issue

1. Confirm that the issue is reproducible on a current Home Assistant release.
2. Enable debug logging only if needed.
3. Remove all credentials, cookies, CAS tickets, personal data and operational addresses from logs or HAR files.
4. State the Home Assistant version, integration version and ARTEMIS WebEvo deployment/version if known.

## Development checks

Run from the repository root:

```bash
python3 -m compileall -q custom_components/artemis
python3 -m unittest discover -s tests -v
python3 scripts/check_ready.py
```

GitHub Actions also run HACS validation and Hassfest.

## Releases

Update both:

- `custom_components/artemis/manifest.json` -> `version`
- `CHANGELOG.md`

Then create a GitHub release whose tag matches the manifest version, for example `0.1.1`.
