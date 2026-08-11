# 🐧 linux-proxy-control

> 🎯 Route a specific IP/CIDR to a specific region or a specific Mihomo node — without hand-editing YAML.

[English](README.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md)

linux-proxy-control is a lightweight Flask dashboard with a build-free static frontend. It talks to an already-running [Mihomo](https://github.com/MetaCubeX/mihomo) External Controller and turns source-IP routing into a clear, visual workflow. ✨

## 🧠 If you already know Clash

If Clash is familiar to you, think of **Mihomo as the running proxy core in the Clash ecosystem**, not as another dashboard. It reads the familiar Clash-style configuration, manages proxies, proxy groups, rules, and connections, and carries the actual traffic. linux-proxy-control does not replace your Clash client or UI; it connects to Mihomo’s External Controller to manage it.

- 🧩 **Familiar model:** proxies, proxy groups, rules, subscriptions, and mixed-port behave in the same general configuration family.
- 🎛️ **What this project adds:** a source-IP routing dashboard for choosing a region or exact node, plus latency-aware decisions.
- 🔌 **How it connects:** Mihomo’s External Controller is its HTTP control API; this project reads the catalog and sends control actions through that API.

If you use Clash Verge or another Clash-style frontend, make sure it is actually running Mihomo and edit the active Mihomo configuration—not an unused backup file.

## ⭐ Core feature: IP → region/node routing

Create a rule such as “send `192.168.1.42` to the `Tokyo` region” or “send `2001:db8::42` to the exact node `Singapore-01`.” The mapping is saved by source IP/CIDR and can be applied to Mihomo when source-route writes are explicitly enabled. 🧭

```json
{
  "ip": "192.168.1.42",
  "selection": { "kind": "region", "value": "Tokyo" },
  "allow_cross_region_fallback": false
}
```

- 🌏 **Region target:** choose the fastest usable node discovered in that region.
- 🎯 **Exact node target:** prefer one named node, then use its region according to the fallback policy.
- 🧩 **IP or CIDR source:** support one client IP or a whole network range, with longest-prefix matching.
- ⚡ **Health-aware decisions:** test node latency and show the effective route before you commit.
- 🔒 **Controlled live changes:** keep `ALLOW_SOURCE_IP_ROUTES=false` until the deployment has authentication and network restrictions.

## 🧰 What else it can do

- 👀 Inspect proxy groups and node status
- ⏱️ Test node latency and pick a healthy route
- 📚 Manage profiles
- 🗺️ Manage many IP/CIDR mappings from the admin view
- 🔁 Reload Mihomo after an explicitly enabled configuration change

Mihomo still carries the traffic. linux-proxy-control is the control panel: it does not ship Mihomo, subscriptions, proxy nodes, or a configuration generator.

## 🔌 The three ports you must not mix up

| Port | Used by | Purpose |
| --- | --- | --- |
| `7890` | Your proxy clients | Mihomo `mixed-port`, carrying proxy traffic |
| `9090` | This dashboard | Mihomo `external-controller`, an HTTP API |
| `8080` | Local demo / transition unit | Temporary app HTTP port; production uses a Unix socket |

```text
Browser -- LAN:80 --> nginx -- Unix socket --> linux-proxy-control -- HTTP:9090 --> Mihomo
Proxy clients -----------------------------------------------> Mihomo:7890
```

`7890` is not the controller API, and `9090` is not the proxy port. Remember this one tiny map and most first-day deployment confusion disappears. 🗺️

## 🔐 Security boundary

There is no built-in browser login, API token, TLS, or access-control layer. Use this on a trusted LAN or behind an independently authenticated TLS reverse proxy.

- 🚫 Do not port-forward `7890`, `9090`, or `8080` to the Internet.
- 🧱 Restrict nginx port 80 with UFW, router ACLs, or an upstream firewall.
- 🤫 Keep `MIHOMO_SECRET` in a root-only server env file; never commit or send it to the browser.
- 🛡️ `ALLOW_CONFIG_WRITE`, `ALLOW_PROFILE_ACTIVATE`, and `ALLOW_SOURCE_IP_ROUTES` are `false` by default.
- ⚠️ Enable write operations only after adding authentication, TLS, network restrictions, and minimum file permissions.

## 🧩 Configure Mihomo External Controller first

Edit the Mihomo configuration that is actually loaded. If you use Clash Verge, change the active Mihomo configuration in Clash Verge—not an unused backup file:

```yaml
# Mihomo config.yaml
mixed-port: 7890
external-controller: 127.0.0.1:9090
secret: "replace-with-a-long-random-secret"
```

### 📍 Choose the controller address

- **Same Linux host:** use `127.0.0.1:9090`. This is the safest option.
- **Separate Linux server:** bind Mihomo to the management-LAN address and firewall port `9090` so only the linux-proxy-control server can connect.
- **Avoid `0.0.0.0:9090`:** if you truly need it, use a strong secret plus strict firewall and upstream ACL rules.

Restart Mihomo, then verify the API before starting linux-proxy-control:

```bash
export MIHOMO_CONTROLLER_URL=http://127.0.0.1:9090
read -rsp 'Mihomo secret: ' MIHOMO_SECRET
echo
curl -fsS \
  -H "Authorization: Bearer ${MIHOMO_SECRET}" \
  "${MIHOMO_CONTROLLER_URL}/version"
```

You can also check the proxy groups:

```bash
curl -fsS \
  -H "Authorization: Bearer ${MIHOMO_SECRET}" \
  "${MIHOMO_CONTROLLER_URL}/proxies" > /tmp/mihomo-proxies.json
unset MIHOMO_SECRET
```

If this fails, check that Mihomo restarted, the port is `9090` rather than `7890`, the secret matches, and the firewall allows the connection. 🩺

## 🛠️ Connect linux-proxy-control to Mihomo

Copy the example outside the repository and edit the copy:

```bash
sudo install -m 0600 -o root -g root \
  deploy/linux-proxy-control.env.example \
  /etc/linux-proxy-control/app.env
sudoedit /etc/linux-proxy-control/app.env
```

Important values:

```dotenv
DEMO_MODE=false
MIHOMO_CONTROLLER_URL=http://127.0.0.1:9090
MIHOMO_SECRET=your-real-mihomo-secret

# The active Mihomo YAML; needed for config/source-IP write features
MIHOMO_CONFIG_PATH=/var/lib/mihomo/config/config.yaml
CONFIG_DIR=/var/lib/linux-proxy-control/config
PROFILES_DIR=/var/lib/linux-proxy-control/config/profiles
IP_MAPPING_FILE=/var/lib/linux-proxy-control/config/ip-mappings.json

# Keep these false unless the deployment is protected
ALLOW_CONFIG_WRITE=false
ALLOW_PROFILE_ACTIVATE=false
ALLOW_SOURCE_IP_ROUTES=false
```

`MIHOMO_CONTROLLER_URL` points to the API above. `MIHOMO_SECRET` must match Mihomo’s `secret`. `MIHOMO_CONFIG_PATH` must point to the YAML Mihomo actually loads if you want configuration or source-IP route management. The `linux-proxy-control` service user needs the smallest necessary read/write permission—never use `chmod 777`. `PROFILES_DIR` and `IP_MAPPING_FILE` must stay inside `CONFIG_DIR`.

## 🚀 Quick demo: see the dashboard before touching hardware

The demo uses a simulated provider. It does not need Mihomo or a real secret:

```bash
git clone https://github.com/sherlockjyzhang/linux-proxy-control.git linux-proxy-control
cd linux-proxy-control
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
DEMO_MODE=true .venv/bin/python -m backend.app
```

Open <http://127.0.0.1:8080> and try the UI. Health check:

```bash
curl -fsS http://127.0.0.1:8080/api/health
```

The frontend is served directly by Flask. Node.js, npm, and a frontend build step are not required. 🎉

## 🐧 Production install on Linux (Debian, Ubuntu, or another systemd distribution)

Install the prerequisites and create the service user:

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip nginx curl
sudo useradd --system --home /var/lib/linux-proxy-control --shell /usr/sbin/nologin linux-proxy-control
sudo install -d -o linux-proxy-control -g linux-proxy-control /opt/linux-proxy-control
sudo install -d -o linux-proxy-control -g linux-proxy-control /var/lib/linux-proxy-control/config/profiles
sudo install -d -o root -g root -m 0750 /etc/linux-proxy-control
sudo git clone https://github.com/sherlockjyzhang/linux-proxy-control.git /opt/linux-proxy-control
sudo chown -R linux-proxy-control:linux-proxy-control /opt/linux-proxy-control
sudo -u linux-proxy-control python3 -m venv /opt/linux-proxy-control/.venv
sudo -u linux-proxy-control /opt/linux-proxy-control/.venv/bin/pip install -r /opt/linux-proxy-control/requirements.txt
```

Install the env, systemd unit, and nginx site:

```bash
sudo install -m 0600 -o root -g root \
  /opt/linux-proxy-control/deploy/linux-proxy-control.env.example \
  /etc/linux-proxy-control/app.env
sudoedit /etc/linux-proxy-control/app.env

sudo install -D -m 0644 \
  /opt/linux-proxy-control/deploy/linux-proxy-control.service \
  /etc/systemd/system/linux-proxy-control.service
sudo install -D -m 0644 \
  /opt/linux-proxy-control/deploy/nginx.conf \
  /etc/nginx/sites-available/linux-proxy-control
sudo ln -sfn /etc/nginx/sites-available/linux-proxy-control \
  /etc/nginx/sites-enabled/linux-proxy-control
sudo nginx -t
sudo systemctl daemon-reload
sudo systemctl enable --now linux-proxy-control
sudo systemctl reload nginx
```

Allow port 80 only from your trusted management network:

```bash
sudo ufw allow from <trusted-lan-cidr> to any port 80 proto tcp
sudo ufw deny 80/tcp
sudo systemctl is-active --quiet linux-proxy-control
curl -fsS http://127.0.0.1/api/health
curl -fsS http://<linux-host-lan-address>/api/health
```

The final service uses `/run/linux-proxy-control/gunicorn.sock`; it should not expose port 8080.

## 🔄 Updates and rollback

The update script expects a clean Git worktree, an existing env file, `sudo`, nginx, and systemd. It does not overwrite `app.env`, profiles, mappings, or Mihomo’s active configuration:

```bash
cd /opt/linux-proxy-control
git pull --ff-only
sudo -v
./deploy/update-and-restart.sh
```

Back up the env file, the linux-proxy-control config directory, and Mihomo’s active configuration separately. Failed deployment snapshots are kept under `/var/lib/linux-proxy-control/deploy-snapshots/`.

## 🧪 Tests

Mihomo is not required for the local test suite:

```bash
.venv/bin/python -m compileall backend
.venv/bin/pytest -q
```

## 🩺 Troubleshooting shortcuts

| Symptom | First checks |
| --- | --- |
| `connected: false` | Mihomo is running, `external-controller` is `9090`, and the secret matches |
| Page does not open | `systemctl status`, `nginx -t`, UFW, and Unix socket permissions |
| Page opens but no nodes appear | Call `/version` and `/proxies`; ensure linux-proxy-control points to `9090`, not `7890` |
| Config change fails | `ALLOW_*` flags, the active `MIHOMO_CONFIG_PATH`, and `linux-proxy-control` permissions |
| LAN access is wider than expected | Remove port forwards and review UFW/router ACLs immediately |

## 📄 License

## ⚠️ Upgrading from an older release

This release standardizes the runtime identity and filesystem paths around `linux-proxy-control`. Before upgrading, stop the previous service, back up its env/data and the active Mihomo configuration, and verify that only one service generation will manage that configuration. The managed source-route prefix has changed, so inspect existing Mihomo groups and rules and migrate them deliberately before enabling live writes.

MIT License: [LICENSE](LICENSE). Security guidance: [SECURITY.md](SECURITY.md).
