# RPb5 Proxy Control Agent Notes

Always address the user as Jingyue.

## Scope and safety

- Keep changes within the requested files and preserve unrelated user changes.
- Do not place real IP addresses, hostnames, passwords, tokens, controller secrets, or private deployment details in source control.
- This project has no browser login or `APP_TOKEN`. Keep Flask on loopback or behind the Unix socket, and restrict nginx to a trusted LAN.
- Mihomo is an external dependency. Do not assume this repository installs Mihomo, subscriptions, or its active configuration.

## Architecture

- `backend/app.py`: Flask API and static frontend serving.
- `backend/provider.py`: demo provider or Mihomo controller adapter.
- `backend/selection.py`: deterministic source/IP, manual, region, and fallback policy.
- `backend/storage.py`: confined config/profile paths and atomic JSON/YAML writes.
- `frontend/`: build-free static dashboard served by Flask; there is no npm build.
- `deploy/`: environment example, systemd units, nginx reverse proxy, and update script.

## Interfaces

The API includes health, proxy groups, delay tests, profiles, config, mappings, and assignment endpoints under `/api`. `MIHOMO_SECRET` is server-side only and must never be returned to the browser.

## Local checks

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
DEMO_MODE=true .venv/bin/python -m backend.app
python3 -m compileall backend
pytest -q
```

## Deployment assumptions

The final Linux deployment uses `/opt/rpb5-proxy-control`, service user `rpb5`, data under `/var/lib/rpb5-proxy-control`, env at `/etc/rpb5-proxy-control/app.env`, nginx on port 80, and a Gunicorn Unix socket at `/run/rpb5-proxy-control/gunicorn.sock`. The application default Mihomo controller is `http://127.0.0.1:9090`; local/transition HTTP uses `127.0.0.1:8080`.

`PROFILES_DIR` and `IP_MAPPING_FILE` must remain inside `CONFIG_DIR`. Keep production env files and Mihomo configuration outside the repository.
