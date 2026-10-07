# Publish / update `purierca/artemis` on GitHub without Git

The repository metadata is configured for:

```text
https://github.com/purierca/artemis
```

## Upload the prepared 0.5.4 tree

Extract the GitHub-ready ZIP locally. Upload the **contents** of the extracted folder to the repository root; do not upload the ZIP itself and do not create an extra parent folder.

The root must contain, among other files:

```text
.github/
custom_components/
tests/
scripts/
README.md
CHANGELOG.md
LICENSE
hacs.json
pyproject.toml
automation_status_notification.example.yaml
automation_interventions.example.yaml
dashboard_cycle_button.example.yaml
```

Version 0.5.x intentionally removes `custom_components/artemis/binary_sensor.py` and the obsolete pre-0.5 notification-example files. Delete those old files from GitHub if they are still present after uploading the new tree.

Suggested commit message:

```text
Release ARTEMIS WebEvo 0.5.4
```

## Validate

Open the **Actions** tab. The `Validate` workflow runs:

- HACS validation;
- Home Assistant Hassfest;
- Python compilation and unit tests;
- repository metadata checks.

All jobs should be green before publishing the release.

## Create release `0.5.4`

1. Open **Releases** -> **Draft a new release**.
2. Create tag `0.5.4`.
3. Use release title `ARTEMIS WebEvo 0.5.4`.
4. Summarise the breaking entity cleanup from `CHANGELOG.md`.
5. Publish the release.

The tag must match `custom_components/artemis/manifest.json`.

## Install / update with HACS

Add the repository as a HACS custom repository of type **Integration** if it is not already installed:

```text
https://github.com/purierca/artemis
```

Then install/update **ARTEMIS WebEvo** and restart Home Assistant.
