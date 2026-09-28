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
shellcheck scripts/*.sh deploy/*.sh deploy/proxmox/*.sh
bash deploy/proxmox/selftest.sh     # create-lxc.sh against fake Proxmox commands
```

The install script for the Pi is a template; render and check it with:

```bash
cd server && python3 -c "from app.agent_bundle import AgentBundle; open('/tmp/agent.sh','wb').write(AgentBundle.build('https://10.0.0.5').script)"
shellcheck -s sh /tmp/agent.sh
```

Run everything locally without hardware: `scripts/dev.sh` (see README).

## Conventions

- Every source file starts with a short docstring/comment that says what the module does.
- Server input is validated with strict Pydantic schemas (`server/app/schemas.py`); error responses
  are short machine-readable codes (`{"detail": "code"}`) that the frontend translates (`err.*`).
- Every query in a tenant context filters by `tenant_id`; other tenants' resources return 404.
- Frontend: no `innerHTML`, no inline scripts or styles (the CSP forbids them); build DOM nodes with
  `el()` from `web/static/js/ui.js`.
- Simulated data must always stay labelled as simulated (`source = "simulated"`).

## Versions

| What | Where | When to bump |
|---|---|---|
| Agent | `agent/VERSION` | every change in `agent/bikeagent/` that should reach the Pis; agents with automatic updates install it with their next heartbeat |
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
