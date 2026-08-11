# RPb5 Proxy Control

RPb5 Proxy Control 是一个 Flask + 静态前端控制面板，用来管理已经运行的
[Mihomo](https://github.com/MetaCubeX/mihomo) External Controller。它可以查看代理组、测试延迟、选择节点、管理配置文件和按来源地址分配节点。

本项目不包含 Mihomo 本体、订阅、代理节点或 Mihomo 配置生成器。你必须先自行运行 Mihomo，并让 External Controller 在本机或受信网络地址监听。

## 安全边界

- 项目没有浏览器登录、API token、TLS 或内置访问控制。Unix socket 只保护后端到 nginx 的内部链路，不是用户认证。
- 默认假定只部署在受信任 LAN。不要把 nginx 的 80 端口、Flask 的 8080 端口或 Mihomo controller 端口直接暴露到公网；路由器也不要做公网端口转发。
- `MIHOMO_SECRET` 只应存在于服务器的 root 可读 env 文件中，不能提交、打印或复制到浏览器。
- 如果密码、secret 或地址曾经真实使用并出现在公开或共享位置，请立即轮换，并检查 Mihomo、SSH 和系统账户的访问日志。
- nginx 示例监听所有 IPv4/IPv6 地址。生产环境必须用 UFW、上游防火墙或受信反向代理把 80/tcp 限制到管理 LAN；示例不提供认证或 TLS。

## 最快 Demo

需要 Python 3.11+。Demo 不连接 Mihomo，也不需要真实 secret：

```bash
git clone <your-public-repository-url> rpb5-proxy-control
cd rpb5-proxy-control
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
DEMO_MODE=true .venv/bin/python -m backend.app
```

打开 `http://127.0.0.1:8080`。健康检查：

```bash
curl -fsS http://127.0.0.1:8080/api/health
```

前端是 `frontend/` 中由 Flask 直接提供的静态文件，不要假设存在 npm、Node.js 或 `frontend/package.json`。

## Raspberry Pi OS / Debian / Ubuntu 生产部署

以下路径与仓库提供的最终 systemd 单元一致：应用 `/opt/rpb5-proxy-control`，服务用户 `rpb5`，持久数据 `/var/lib/rpb5-proxy-control`，env `/etc/rpb5-proxy-control/app.env`，nginx 站点 `/etc/nginx/sites-available/rpb5-proxy-control`。系统需要 Python 3、venv、git、nginx、curl 和 systemd。

1. 在目标 Linux 主机上安装系统依赖并取得代码：

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip nginx curl
sudo useradd --system --home /var/lib/rpb5-proxy-control --shell /usr/sbin/nologin rpb5
sudo install -d -o rpb5 -g rpb5 /opt/rpb5-proxy-control
sudo install -d -o rpb5 -g rpb5 /var/lib/rpb5-proxy-control/config/profiles
sudo install -d -o root -g root -m 0750 /etc/rpb5-proxy-control
sudo git clone <your-public-repository-url> /opt/rpb5-proxy-control
sudo chown -R rpb5:rpb5 /opt/rpb5-proxy-control
sudo -u rpb5 python3 -m venv /opt/rpb5-proxy-control/.venv
sudo -u rpb5 /opt/rpb5-proxy-control/.venv/bin/pip install -r /opt/rpb5-proxy-control/requirements.txt
```

若 `rpb5` 或目录已经存在，先核对其所有者，不要重复创建或覆盖已有数据。

2. 创建不会被仓库更新覆盖的 secret env：

```bash
sudo install -m 0600 -o root -g root \
  /opt/rpb5-proxy-control/deploy/rpb5-proxy-control.env.example \
  /etc/rpb5-proxy-control/app.env
sudoedit /etc/rpb5-proxy-control/app.env
```

至少设置 `MIHOMO_SECRET`、`MIHOMO_CONTROLLER_URL`、`MIHOMO_CONFIG_PATH`，并确认 `CONFIG_DIR`、`PROFILES_DIR` 和 `IP_MAPPING_FILE` 位于 `/var/lib/rpb5-proxy-control/config` 内。env 示例中的 controller 默认值是代码实际默认值 `http://127.0.0.1:9090`；9090 是 Mihomo controller 端口，不是本项目 HTTP 端口。`APP_PORT=8080` 只用于本地运行和 `pi` 过渡单元，最终 Unix-socket 单元不会使用它。

3. 安装最终 systemd/nginx 资产并限制网络：

```bash
sudo install -D -m 0644 /opt/rpb5-proxy-control/deploy/rpb5-proxy-control.service \
  /etc/systemd/system/rpb5-proxy-control.service
sudo install -D -m 0644 /opt/rpb5-proxy-control/deploy/nginx.conf \
  /etc/nginx/sites-available/rpb5-proxy-control
sudo ln -sfn /etc/nginx/sites-available/rpb5-proxy-control \
  /etc/nginx/sites-enabled/rpb5-proxy-control
sudo nginx -t
sudo systemctl daemon-reload
sudo systemctl enable --now rpb5-proxy-control
sudo systemctl reload nginx
sudo ufw allow from <trusted-lan-cidr> to any port 80 proto tcp
sudo ufw deny 80/tcp
```

按实际防火墙策略调整 UFW 规则，并确认 Mihomo 的 controller 只接受本机或管理 LAN。服务后端使用 `/run/rpb5-proxy-control/gunicorn.sock`，不应存在对外的 8080 监听。

4. 验证：

```bash
sudo systemctl is-active --quiet rpb5-proxy-control
sudo systemctl status rpb5-proxy-control --no-pager -l
curl -fsS http://127.0.0.1/api/health
curl -fsS http://<linux-host-lan-address>/api/health
```

## 配置

所有可部署变量都在 `deploy/rpb5-proxy-control.env.example`。复制后只修改 `/etc/rpb5-proxy-control/app.env`，不要修改仓库里的示例来存放 secret。

`DEMO_MODE=true` 仅用于本地演示。生产应为 `false`，并提供可访问的 Mihomo controller。env 示例默认将 `ALLOW_CONFIG_WRITE`、`ALLOW_PROFILE_ACTIVATE` 和 `ALLOW_SOURCE_IP_ROUTES` 设为 `false`，因为本项目没有浏览器认证。只有在前置认证、TLS 和受信 LAN 访问控制已经生效，并确认风险可接受后，才可在服务器 env 中逐项开启；不要修改仓库中的示例文件来存放这些设置。

## 更新与回滚

现有 `deploy/update-and-restart.sh` 需要在 `/opt/rpb5-proxy-control` 以普通部署用户运行，不要用 root 运行；它要求 git 工作树干净、已有 env 文件、sudo 和 nginx/systemd，可在更新前创建 root-only 的 service/nginx 快照，失败后自动回滚。它不会复制或覆盖 app.env、映射、profiles 或 Mihomo 配置。

```bash
cd /opt/rpb5-proxy-control
git pull --ff-only
sudo -v
./deploy/update-and-restart.sh
```

生产更新前应另外备份 `/etc/rpb5-proxy-control/app.env`、`/var/lib/rpb5-proxy-control/config` 和 Mihomo 活跃配置，且备份目录权限应为 0700。若要部署已审阅的完整 commit SHA：

```bash
DEPLOY_REF=<40-hex-commit-sha> ./deploy/update-and-restart.sh
```

脚本失败时保留的快照位于 `/var/lib/rpb5-proxy-control/deploy-snapshots/`，先查看其 `metadata`，再按输出恢复并重新执行 `nginx -t`。不要把 env 或 Mihomo 配置放入该快照目录或提交到 git。

## 测试

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m compileall backend
.venv/bin/pytest -q
```

测试不要求 Mihomo、订阅或网络服务；Demo 和单元测试使用模拟 provider。

## 故障排查

- 服务未启动：`sudo journalctl -u rpb5-proxy-control -n 100 --no-pager`，检查 env 权限为 `0600`、venv 路径和 `MIHOMO_CONTROLLER_URL`。
- nginx 失败：`sudo nginx -t`；确认 80 端口没有冲突的 `default_server`，并确认 nginx 用户能访问 `/run/rpb5-proxy-control/gunicorn.sock`。
- 健康检查 `connected: false`：确认 Mihomo 正在运行、controller 地址/端口正确、secret 匹配且 Mihomo 允许本机访问。
- LAN 无法访问：检查 `sudo ufw status verbose`、路由器 ACL、IPv4/IPv6 监听和客户端是否处于受信 LAN；不要为排查而开放公网。
- 看到 8080：最终单元应只使用 Unix socket；8080 仅是本地开发或 `rpb5-proxy-control.pi.service` 过渡配置。

## 许可证

本项目采用 MIT License，见 [LICENSE](LICENSE)。
