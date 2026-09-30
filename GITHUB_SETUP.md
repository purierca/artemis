# Publish `purierca/artemis` on GitHub without Git

The repository is already configured for:

```text
https://github.com/purierca/artemis
```

No Git installation is required for the first publication.

## 1. Extract the prepared archive on your desktop computer

Open the extracted `artemis-repo-ready` folder. The files that need to end up at the root of the GitHub repository include:

```text
.github/
custom_components/
tests/
scripts/
README.md
LICENSE
hacs.json
pyproject.toml
...
```

Do not upload the ZIP itself as the repository contents. GitHub/HACS need the actual files and directories.

## 2. Upload through GitHub.com

Open:

```text
https://github.com/purierca/artemis
```

Then:

1. Click **Add file > Upload files**.
2. Open the extracted folder on your computer.
3. Select **all files and folders inside it** and drag them into GitHub's upload area.
   - Make sure `.github` is included.
   - The repository root should contain `README.md`, `hacs.json`, `custom_components`, etc.; there must not be an extra `artemis-repo-ready/` parent directory inside the repository.
4. Use commit message:

   ```text
   Initial ARTEMIS WebEvo Home Assistant integration
   ```

5. Commit the files to `main`.

The repository contains fewer than GitHub's browser-upload limit of 100 files, and no individual file approaches the browser file-size limit.

## 3. Check GitHub Actions

Open the **Actions** tab. The `Validate` workflow should run automatically and includes:

- HACS validation;
- Home Assistant Hassfest;
- Python compile/smoke tests;
- repository metadata checks.

All jobs should be green before making the first release.

## 4. Create release `0.1.0`

On GitHub:

1. Open **Releases**.
2. Click **Draft a new release**.
3. Create tag:

   ```text
   0.1.0
   ```

4. Release title:

   ```text
   ARTEMIS WebEvo 0.1.0
   ```

5. Publish the release.

The tag matches `custom_components/artemis/manifest.json`.

## 5. Install it through HACS

In Home Assistant:

1. Open **HACS**.
2. Open **Custom repositories**.
3. Add:

   ```text
   https://github.com/purierca/artemis
   ```

4. Category: **Integration**.
5. Install **ARTEMIS WebEvo**.
6. Restart Home Assistant.
7. Go to **Settings > Devices & services > Add integration > ARTEMIS WebEvo**.

You do not need Git on the Beelink for HACS installation or updates.
