# RPb5 Proxy Control

> 把 Mihomo 的“工程师控制台”，变成家里每个人都看得懂的内网遥控器。

[简体中文](README.md) · [English](README.en.md) · [日本語](README.ja.md) · [한국어](README.ko.md)

RPb5 Proxy Control 是一个轻量的 Flask + 静态网页控制面板。它不替代 Mihomo，而是站在 Mihomo 的 External Controller API 旁边，帮你更直观地完成这些事：

- 查看代理组和节点状态；
- 批量测试节点延迟并选择节点；
- 管理 profile；
- 为指定 IP/CIDR 固定节点、地区或自动选择策略；
- 在明确开启权限后，修改活动配置并让 Mihomo 重新加载。

如果你的树莓派已经运行 Mihomo，这个项目就是“控制台”；Mihomo 仍然负责真正的代理流量。

## 先看懂 3 个端口

很多部署问题都来自把下面三个端口混在一起：

| 端口 | 谁使用 | 作用 |
| --- | --- | --- |
| `7890` | 电脑、手机和其他代理客户端 | Mihomo 的 `mixed-port`，承载代理流量 |
| `9090` | RPb5 Proxy Control | Mihomo 的 `external-controller`，提供 HTTP API |
| `8080` | 本地 Demo / Pi 过渡服务 | 本项目自己的临时 HTTP 监听；最终 systemd 部署使用 Unix socket |

推荐拓扑：

```text
浏览器 ── LAN:80 ──> nginx ── Unix socket ──> RPb5 Proxy Control ── HTTP:9090 ──> Mihomo
代理客户端 ───────────────────────────────────────────────────────> Mihomo mixed-port:7890
```

`7890` 不是 External Controller；`9090` 也不是代理端口。先把这件事记住，后面的配置就不会迷路。

## 安全边界：它是内网工具，不是公网 SaaS

本项目没有浏览器登录、API token、TLS 或内置访问控制。默认只读配置比较安全，但仍然不能把它裸奔到公网。

- 只把 nginx 暴露给可信管理 LAN，并用 UFW、路由器 ACL 或上游反向代理限制来源；
- 不要把 `7890`、`9090`、`8080` 做公网端口转发；
- `MIHOMO_SECRET` 只能存在于服务器上的 root-only env 文件，不要提交、打印或送到浏览器；
- 生产默认关闭 `ALLOW_CONFIG_WRITE`、`ALLOW_PROFILE_ACTIVATE`、`ALLOW_SOURCE_IP_ROUTES`；
- 只有已经配置好认证、TLS 和来源限制后，才考虑逐项开启写操作；
- Mihomo、订阅、节点和活动配置属于外部依赖，本仓库不会替你下载或公开它们。

## 第一步：配置 Mihomo External Controller

这是本项目真正需要的 Mihomo 配置。编辑 Mihomo 实际加载的 `config.yaml`（Clash Verge 用户请在 Clash Verge 的 Mihomo 配置入口中修改，不要只改一个不会被加载的备份文件）：

```yaml
# Mihomo config.yaml
mixed-port: 7890

# RPb5 Proxy Control 通过这个 HTTP API 控制 Mihomo
external-controller: 127.0.0.1:9090

# 请换成长随机字符串；不要把真实值提交到 Git
secret: "replace-with-a-long-random-secret"
```

### 选择监听地址

- **RPb5 和 Mihomo 在同一台树莓派**：使用 `127.0.0.1:9090`，最安全。
- **RPb5 在另一台 Linux 服务器**：将 `external-controller` 改为树莓派的管理网卡地址，例如 `192.0.2.10:9090`；只允许 RPb5 服务器访问该端口，并在防火墙中拒绝其他来源。
- 不建议使用 `0.0.0.0:9090`。如果 Mihomo 必须监听所有地址，务必用防火墙和上游 ACL 锁定来源，并保留 `secret`。

`external-controller` 是 Mihomo 的 HTTP API，不是 `mixed-port`。改完后重启 Mihomo，使用下面的命令验证 API 和 secret：

```bash
export MIHOMO_CONTROLLER_URL=http://127.0.0.1:9090
read -rsp 'Mihomo secret: ' MIHOMO_SECRET
echo
curl -fsS \
  -H "Authorization: Bearer ${MIHOMO_SECRET}" \
  "${MIHOMO_CONTROLLER_URL}/version"
unset MIHOMO_SECRET
```

如果这里失败，先不要启动 RPb5。常见原因是 Mihomo 没有重启、端口写成了 `7890`、secret 不匹配，或防火墙阻断了 RPb5 到树莓派的访问。成功后还可以查看代理组：

```bash
curl -fsS \
  -H "Authorization: Bearer ${MIHOMO_SECRET}" \
  "${MIHOMO_CONTROLLER_URL}/proxies" > /tmp/mihomo-proxies.json
```

## 第二步：把 Mihomo 接入本项目

仓库里的 [部署环境示例](deploy/rpb5-proxy-control.env.example) 只放占位符。生产机器上复制一份到不会被 Git 更新覆盖的位置：

```bash
sudo install -m 0600 -o root -g root \
  deploy/rpb5-proxy-control.env.example \
  /etc/rpb5-proxy-control/app.env
sudoedit /etc/rpb5-proxy-control/app.env
```

至少填写这些变量：

```dotenv
DEMO_MODE=false
MIHOMO_CONTROLLER_URL=http://127.0.0.1:9090
MIHOMO_SECRET=your-real-mihomo-secret

# 只有需要管理活动 YAML / source-IP 路由时才填写真实路径
MIHOMO_CONFIG_PATH=/var/lib/mihomo/config/config.yaml
CONFIG_DIR=/var/lib/rpb5-proxy-control/config
PROFILES_DIR=/var/lib/rpb5-proxy-control/config/profiles
IP_MAPPING_FILE=/var/lib/rpb5-proxy-control/config/ip-mappings.json

# 没有认证时保持 false
ALLOW_CONFIG_WRITE=false
ALLOW_PROFILE_ACTIVATE=false
ALLOW_SOURCE_IP_ROUTES=false
```

配置变量的边界：

- `MIHOMO_CONTROLLER_URL`：RPb5 访问 Mihomo API 的地址，默认 `http://127.0.0.1:9090`；
- `MIHOMO_SECRET`：对应 Mihomo `secret`，RPb5 只在服务端作为 `Authorization: Bearer ...` 使用；
- `MIHOMO_CONFIG_PATH`：Mihomo 当前真正加载的 YAML。只有开启配置写入或 source-IP 路由时才需要可写；
- `CONFIG_DIR`：RPb5 自己保存映射和 profile 的目录；`PROFILES_DIR`、`IP_MAPPING_FILE` 必须位于其中；
- `APP_PORT=8080`：只给本地 Demo 和 Pi 过渡 unit 使用，最终生产 unit 不会把它暴露出去。

如果 Mihomo 是由另一个用户运行，`rpb5` 必须拥有 `MIHOMO_CONFIG_PATH` 的必要读/写权限。不要使用 `chmod 777`；优先使用专用组或 ACL。暂时只想看数据时，可以不填写写路径并保持三个 `ALLOW_*` 为 `false`。

## 最快 Demo：先看界面，再接硬件

Demo 使用模拟 provider，不会连接 Mihomo，也不需要真实 secret：

```bash
git clone https://github.com/sherlockjyzhang/linux-proxy-control.git rpb5-proxy-control
cd rpb5-proxy-control
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
DEMO_MODE=true .venv/bin/python -m backend.app
```

打开 <http://127.0.0.1:8080>，健康检查：

```bash
curl -fsS http://127.0.0.1:8080/api/health
```

前端是 Flask 直接提供的静态 HTML/CSS/JavaScript，不需要 Node.js、npm 或 `frontend/package.json`。

## Raspberry Pi OS / Debian / Ubuntu 生产部署

目标主机需要 Python 3、venv、git、nginx、curl 和 systemd。下面的路径与仓库里的 systemd/nginx 文件一致：

- 应用：`/opt/rpb5-proxy-control`；
- 服务用户：`rpb5`；
- 持久数据：`/var/lib/rpb5-proxy-control`；
- env：`/etc/rpb5-proxy-control/app.env`；
- Gunicorn：`/run/rpb5-proxy-control/gunicorn.sock`。

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

安装 env、systemd 和 nginx：

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

只向可信管理网段开放 nginx：

```bash
sudo ufw allow from <trusted-lan-cidr> to any port 80 proto tcp
sudo ufw deny 80/tcp
sudo systemctl is-active --quiet rpb5-proxy-control
curl -fsS http://127.0.0.1/api/health
curl -fsS http://<linux-host-lan-address>/api/health
```

## 更新、回滚和测试

更新脚本要求工作树干净、已有 env、sudo、nginx 和 systemd；它不会覆盖 `app.env`、profiles、映射或 Mihomo 活动配置：

```bash
cd /opt/rpb5-proxy-control
git pull --ff-only
sudo -v
./deploy/update-and-restart.sh
```

失败快照在 `/var/lib/rpb5-proxy-control/deploy-snapshots/`。生产更新前请另行备份 env、RPb5 配置目录和 Mihomo 活跃配置，备份权限设为 `0700`。

本地测试不需要 Mihomo：

```bash
.venv/bin/python -m compileall backend
.venv/bin/pytest -q
```

## 故障排查速查表

| 现象 | 先检查 |
| --- | --- |
| `connected: false` | Mihomo 是否运行、`external-controller` 是否为 `9090`、secret 是否匹配 |
| 浏览器打不开 | `systemctl status`、`nginx -t`、UFW、Unix socket 权限 |
| 能打开页面但看不到节点 | `curl /version`、`curl /proxies`，确认 RPb5 指向 controller 而不是 `7890` |
| 修改配置失败 | `ALLOW_*` 是否仍为 `false`、`MIHOMO_CONFIG_PATH` 是否是活动文件、`rpb5` 是否有最小必要权限 |
| 局域网外也能访问 | 立即关闭端口转发，检查 UFW/路由器 ACL；不要为了排查开放公网 |

## 许可证

MIT License，见 [LICENSE](LICENSE)。
