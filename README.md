# 🐧 linux-proxy-control

> 🎯 Route a specific IP/CIDR to a specific region or a specific Mihomo node — without hand-editing YAML.

[English](README.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md)

linux-proxy-control is a lightweight Flask dashboard with a build-free static frontend. It talks to an already-running [Mihomo](https://github.com/MetaCubeX/mihomo) External Controller and turns source-IP routing into a clear, visual workflow. ✨

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
| `8080` | Local demo / Pi transition unit | Temporary app HTTP port; production uses a Unix socket |

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

- **Same Raspberry Pi:** use `127.0.0.1:9090`. This is the safest option.
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
  deploy/rpb5-proxy-control.env.example \
  /etc/rpb5-proxy-control/app.env
sudoedit /etc/rpb5-proxy-control/app.env
```

Important values:

```dotenv
DEMO_MODE=false
MIHOMO_CONTROLLER_URL=http://127.0.0.1:9090
MIHOMO_SECRET=your-real-mihomo-secret

# The active Mihomo YAML; needed for config/source-IP write features
MIHOMO_CONFIG_PATH=/var/lib/mihomo/config/config.yaml
CONFIG_DIR=/var/lib/rpb5-proxy-control/config
PROFILES_DIR=/var/lib/rpb5-proxy-control/config/profiles
IP_MAPPING_FILE=/var/lib/rpb5-proxy-control/config/ip-mappings.json

# Keep these false unless the deployment is protected
ALLOW_CONFIG_WRITE=false
ALLOW_PROFILE_ACTIVATE=false
ALLOW_SOURCE_IP_ROUTES=false
```

`MIHOMO_CONTROLLER_URL` points to the API above. `MIHOMO_SECRET` must match Mihomo’s `secret`. `MIHOMO_CONFIG_PATH` must point to the YAML Mihomo actually loads if you want configuration or source-IP route management. The `rpb5` service user needs the smallest necessary read/write permission—never use `chmod 777`. `PROFILES_DIR` and `IP_MAPPING_FILE` must stay inside `CONFIG_DIR`.

## 🚀 Quick demo: see the dashboard before touching hardware

The demo uses a simulated provider. It does not need Mihomo or a real secret:

```bash
git clone https://github.com/sherlockjyzhang/linux-proxy-control.git rpb5-proxy-control
cd rpb5-proxy-control
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
DEMO_MODE=true .venv/bin/python -m backend.app
```

Open <http://127.0.0.1:8080> and try the UI. Health check:

```bash
curl -fsS http://127.0.0.1:8080/api/health
```

The frontend is served directly by Flask. Node.js, npm, and a frontend build step are not required. 🎉

## 🐧 Production install on Raspberry Pi OS, Debian, or Ubuntu

Install the prerequisites and create the service user:

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

Install the env, systemd unit, and nginx site:

```bash
sudo install -m 0600 -o root -g root \
  /opt/rpb5-proxy-control/deploy/rpb5-proxy-control.env.example \
  /etc/rpb5-proxy-control/app.env
sudoedit /etc/rpb5-proxy-control/app.env

sudo install -D -m 0644 \
  /opt/rpb5-proxy-control/deploy/rpb5-proxy-control.service \
  /etc/systemd/system/rpb5-proxy-control.service
sudo install -D -m 0644 \
  /opt/rpb5-proxy-control/deploy/nginx.conf \
  /etc/nginx/sites-available/rpb5-proxy-control
sudo ln -sfn /etc/nginx/sites-available/rpb5-proxy-control \
  /etc/nginx/sites-enabled/rpb5-proxy-control
sudo nginx -t
sudo systemctl daemon-reload
sudo systemctl enable --now rpb5-proxy-control
sudo systemctl reload nginx
```

Allow port 80 only from your trusted management network:

```bash
sudo ufw allow from <trusted-lan-cidr> to any port 80 proto tcp
sudo ufw deny 80/tcp
sudo systemctl is-active --quiet rpb5-proxy-control
curl -fsS http://127.0.0.1/api/health
curl -fsS http://<linux-host-lan-address>/api/health
```

The final service uses `/run/rpb5-proxy-control/gunicorn.sock`; it should not expose port 8080.

## 🔄 Updates and rollback

The update script expects a clean Git worktree, an existing env file, `sudo`, nginx, and systemd. It does not overwrite `app.env`, profiles, mappings, or Mihomo’s active configuration:

```bash
cd /opt/rpb5-proxy-control
git pull --ff-only
sudo -v
./deploy/update-and-restart.sh
```

Back up the env file, the linux-proxy-control config directory, and Mihomo’s active configuration separately. Failed deployment snapshots are kept under `/var/lib/rpb5-proxy-control/deploy-snapshots/`.

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
| Config change fails | `ALLOW_*` flags, the active `MIHOMO_CONFIG_PATH`, and `rpb5` permissions |
| LAN access is wider than expected | Remove port forwards and review UFW/router ACLs immediately |

## 📄 License

MIT License: [LICENSE](LICENSE). Security guidance: [SECURITY.md](SECURITY.md).
