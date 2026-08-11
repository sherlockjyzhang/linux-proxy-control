# RPb5 Proxy Control

> Turn Mihomo's engineer console into a friendly remote control for a trusted home LAN.

[简体中文](README.md) · [English](README.en.md) · [日本語](README.ja.md) · [한국어](README.ko.md)

RPb5 Proxy Control is a lightweight Flask dashboard with a build-free static frontend. It talks to an already-running [Mihomo](https://github.com/MetaCubeX/mihomo) External Controller and lets you inspect proxy groups, test delays, choose nodes, manage profiles, and assign a node or region to a source IP/CIDR.

It does not ship Mihomo, subscriptions, proxy nodes, or a configuration generator. Mihomo still carries the traffic; this project is only the control panel.

## The three ports you must not mix up

| Port | Used by | Purpose |
| --- | --- | --- |
| `7890` | Your proxy clients | Mihomo `mixed-port`, carrying proxy traffic |
| `9090` | This dashboard | Mihomo `external-controller`, an HTTP API |
| `8080` | Local demo / Pi transition unit | Temporary app HTTP port; production uses a Unix socket |

```text
Browser -- LAN:80 --> nginx -- Unix socket --> RPb5 -- HTTP:9090 --> Mihomo
Proxy clients -----------------------------------------------> Mihomo:7890
```

`7890` is not the controller API, and `9090` is not the proxy port.

## Security boundary

There is no built-in browser login, API token, TLS, or access-control layer. Use this on a trusted LAN or behind an independently authenticated TLS reverse proxy. Do not port-forward `7890`, `9090`, or `8080` to the Internet. Keep `MIHOMO_SECRET` in a root-only server env file and never commit it.

Write operations are disabled by default: `ALLOW_CONFIG_WRITE`, `ALLOW_PROFILE_ACTIVATE`, and `ALLOW_SOURCE_IP_ROUTES` are all `false`. Enable them only after adding authentication, TLS, network restrictions, and the minimum file permissions required by your deployment.

## Configure Mihomo first

Edit the Mihomo configuration that is actually loaded. Clash Verge users should change the active Mihomo configuration in Clash Verge, not an unused backup file:

```yaml
mixed-port: 7890
external-controller: 127.0.0.1:9090
secret: "replace-with-a-long-random-secret"
```

Use `127.0.0.1:9090` when both programs run on the same Raspberry Pi. If RPb5 runs on another server, bind the controller to the Raspberry Pi's management-LAN address and firewall port 9090 so only that server can connect. Avoid `0.0.0.0:9090` unless a firewall and upstream ACL strictly limit the source.

Restart Mihomo and verify the controller before starting RPb5:

```bash
export MIHOMO_CONTROLLER_URL=http://127.0.0.1:9090
read -rsp 'Mihomo secret: ' MIHOMO_SECRET
echo
curl -fsS -H "Authorization: Bearer ${MIHOMO_SECRET}" \
  "${MIHOMO_CONTROLLER_URL}/version"
unset MIHOMO_SECRET
```

If this fails, check that Mihomo restarted, the port is `9090` rather than `7890`, the secret matches, and the firewall allows the connection. `/proxies` is a useful second check.

## Connect the project to Mihomo

Copy the example outside the repository and edit the copy:

```bash
sudo install -m 0600 -o root -g root \
  deploy/rpb5-proxy-control.env.example \
  /etc/rpb5-proxy-control/app.env
sudoedit /etc/rpb5-proxy-control/app.env
```

The important values are:

```dotenv
DEMO_MODE=false
MIHOMO_CONTROLLER_URL=http://127.0.0.1:9090
MIHOMO_SECRET=your-real-mihomo-secret
MIHOMO_CONFIG_PATH=/var/lib/mihomo/config/config.yaml
CONFIG_DIR=/var/lib/rpb5-proxy-control/config
PROFILES_DIR=/var/lib/rpb5-proxy-control/config/profiles
IP_MAPPING_FILE=/var/lib/rpb5-proxy-control/config/ip-mappings.json
ALLOW_CONFIG_WRITE=false
ALLOW_PROFILE_ACTIVATE=false
ALLOW_SOURCE_IP_ROUTES=false
```

`MIHOMO_CONFIG_PATH` must point at the active YAML if you want to manage configuration or source-IP routes. The `rpb5` service user needs the smallest necessary read/write permission; never use `chmod 777`. `CONFIG_DIR` must contain `PROFILES_DIR` and `IP_MAPPING_FILE`. `APP_PORT=8080` is only for local development and the Pi transition unit.

## Quick demo

The demo uses a simulated provider and does not need Mihomo or a real secret:

```bash
git clone https://github.com/sherlockjyzhang/linux-proxy-control.git rpb5-proxy-control
cd rpb5-proxy-control
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
DEMO_MODE=true .venv/bin/python -m backend.app
```

Open <http://127.0.0.1:8080>. The frontend is served directly by Flask; Node.js and npm are not required.

## Production install on Raspberry Pi OS, Debian, or Ubuntu

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip nginx curl
sudo useradd --system --home /var/lib/rpb5-proxy-control --shell /usr/sbin/nologin rpb5
sudo install -d -o rpb5 -g rpb5 /opt/rpb5-proxy-control
sudo install -d -o rpb5 -g rpb5 /var/lib/rpb5-proxy-control/config/profiles
sudo install -d -o root -g root -m 0750 /etc/rpb5-proxy-control
sudo git clone https://github.com/sherlockjyzhang/linux-proxy-control.git /opt/rpb5-proxy-control
sudo chown -R rpb5:rpb5 /opt/rpb5-proxy-control
sudo -u rpb5 python3 -m venv /opt/rpb5-proxy-control/.venv
sudo -u rpb5 /opt/rpb5-proxy-control/.venv/bin/pip install -r /opt/rpb5-proxy-control/requirements.txt
```

Install `/etc/rpb5-proxy-control/app.env`, then install the service and nginx files from `deploy/`, run `sudo nginx -t`, `sudo systemctl daemon-reload`, `sudo systemctl enable --now rpb5-proxy-control`, and `sudo systemctl reload nginx`. Restrict port 80 to your management CIDR with UFW or an upstream firewall. Verify:

```bash
curl -fsS http://127.0.0.1/api/health
curl -fsS http://<linux-host-lan-address>/api/health
```

The final service uses `/run/rpb5-proxy-control/gunicorn.sock`; it should not expose port 8080.

## Updates and tests

```bash
cd /opt/rpb5-proxy-control
git pull --ff-only
sudo -v
./deploy/update-and-restart.sh
```

The update script does not overwrite `app.env`, profiles, mappings, or Mihomo's active configuration. Back up those files separately. Run tests locally with:

```bash
.venv/bin/python -m compileall backend
.venv/bin/pytest -q
```

For troubleshooting, check `sudo journalctl -u rpb5-proxy-control -n 100 --no-pager`, `sudo nginx -t`, `/version`, `/proxies`, controller permissions, and UFW rules.

See [SECURITY.md](SECURITY.md) before exposing the dashboard. MIT License: [LICENSE](LICENSE).
