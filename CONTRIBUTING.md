# Contributing

Thanks for helping! The code base is deliberately small and framework-free on the frontend. All
code, comments, logs and documentation are in **English**; the user interface is available in
English (default), German and Dutch.

## Set up and run the tests

Python ≥ 3.11.

```bash
pip install -r server/requirements-dev.txt -r agent/requirements.txt
cd server && python3 -m pytest -q
cd ../agent && python3 -m pytest -q
```

Shell scripts must pass `sh -n` / `bash -n` and [ShellCheck](https://www.shellcheck.net/)
(`pip install shellcheck-py`):

```bash
shellcheck scripts/*.sh deploy/*.sh deploy/proxmox/*.sh deploy/docker/*.sh agent/*.sh integrations/home-assistant/*/run.sh
bash deploy/proxmox/selftest.sh     # create-lxc.sh against fake Proxmox commands
```

The install script for the Pi is a template; render and check it with:

```bash
cd server && python3 -c "from app.agent_bundle import AgentBundle; open('/tmp/agent.sh','wb').write(AgentBundle.build('https://10.0.0.5').script)"
shellcheck -s sh /tmp/agent.sh
```

Run everything locally without hardware: `scripts/dev.sh` (see README).

Docker images (from the repository root):

```bash
docker build -t bike-station-agent agent/
docker build -f deploy/docker/Dockerfile -t bike-station-platform .
# Home Assistant builds the add-on itself; to try it:
docker build --build-arg BUILD_FROM=python:3.12-slim -t bike-station-ha-addon integrations/home-assistant/bike-station-agent
```

## Conventions

- Every source file starts with a short docstring/comment that says what the module does.
- Server input is validated with strict Pydantic schemas (`server/app/schemas.py`); error responses
  are short machine-readable codes (`{"detail": "code"}`) that the frontend translates (`err.*`).
- Every query in a tenant context filters by `tenant_id`; other tenants' resources return 404.
- Frontend: no `innerHTML`, no inline scripts or styles (the CSP forbids them); build DOM nodes with
  `el()` from `web/static/js/ui.js`.
- Simulated data must always stay labelled as simulated (`source = "simulated"`).

## Design system

The UI follows the "premium" design system (Apple-inspired): Inter / JetBrains Mono (self-hosted in
`web/static/fonts`, SIL OFL), type scale 12/14/16/18/24/30/36 px, spacing scale 4/8/12/16/24/32 px,
brand #3B82F6 / #8B5CF6. All values are tokens at the top of `web/static/css/app.css` – use
`var(--sp-4)`, `var(--fs-18)`, `var(--primary)` instead of raw values. Accessibility wins over raw
tokens: text and filled buttons use `--primary` (#2563EB, 5.2:1), form controls `--control-border`
(≥ 3:1). Check pages with axe-core (0 serious/critical findings, light and dark mode).

The social preview `web/static/img/og.png` is rendered from `scripts/og/og.html`:
`node scripts/render-og.mjs` (needs Playwright). The Home Assistant add-on icon/logo come from
`web/static/img/icon.svg`: `node scripts/render-ha-addon-images.mjs`.

## Versions

| What | Where | When to bump |
|---|---|---|
| Agent | `agent/VERSION` | every change in `agent/bikeagent/` that should reach the Pis; agents with automatic updates install it with their next heartbeat. Then run `scripts/sync-ha-addon.sh` (copies the code into the Home Assistant add-on and sets its version; `agent/tests/test_homeassistant.py` fails while they differ) |
| Database schema | `SCHEMA_VERSION` in `server/app/db.py` | every schema change; add an idempotent `_migrate_<n>_to_<n+1>` step and a migration test |

## Translations

- Portal and landing page: `web/static/js/i18n.js`. English (`en`) is the reference; every key must
  exist in `en`, `de` and `nl`. Missing keys fall back to English.
- Kiosk display: `web/static/js/display-i18n.js` (same rule).
- Check that all languages have the same keys and every `t("…")` used in the code exists:

  ```bash
  node scripts/check-i18n.mjs
  ```

- A new language: add a block to both files and the language code to `Locale` in
  `server/app/schemas.py`.

## Pull requests

Keep changes focused, add or update tests, and describe how you verified the change. Never commit
secrets (data keys, tokens, passwords) – configuration secrets only come from environment variables.
