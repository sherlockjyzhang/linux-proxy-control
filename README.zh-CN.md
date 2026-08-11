# 🐧 linux-proxy-control

> 🎯 将指定 IP/CIDR 路由到指定 region 或指定 Mihomo 节点，不再需要手动编辑 YAML。

[English](README.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md)

linux-proxy-control 是一个轻量的 Flask 控制面板，连接已经运行的 [Mihomo](https://github.com/MetaCubeX/mihomo) External Controller。真正承载代理流量的仍然是 Mihomo，本项目负责把基于 IP 的路由规则变成清晰的可视化操作。✨

## 🧠 如果你熟悉 Clash

如果你知道 Clash，可以把 **Mihomo 理解成 Clash 生态中的代理核心/运行时**，而不是另一个控制面板。它读取熟悉的 Clash 风格配置，管理 proxies、proxy-groups、rules 和连接，并真正承载网络流量。linux-proxy-control 不替代你的 Clash 客户端或 UI，而是通过 Mihomo 的 External Controller 来管理它。

- 🧩 **熟悉的模型：** proxies、proxy-groups、rules、订阅和 mixed-port 都属于相近的配置体系。
- 🎛️ **本项目增加的能力：** 面向 source IP 的路由控制面板，可以选择 region 或准确节点，并根据延迟做决策。
- 🔌 **连接方式：** Mihomo 的 External Controller 是它的 HTTP 控制 API，本项目通过这个 API 读取节点目录并发送控制操作。

如果你使用 Clash Verge 或其他 Clash 风格前端，请确认它实际运行的是 Mihomo，并编辑当前生效的 Mihomo 配置，不要只修改不会加载的备份文件。

## ⭐ 核心功能：IP/CIDR → region/节点路由

最重要的功能是：把某个客户端 IP 或 CIDR 分配到指定 region，或者分配到某个准确的 Mihomo 节点。

~~~json
{
  "ip": "192.168.1.42",
  "selection": { "kind": "region", "value": "Tokyo" },
  "allow_cross_region_fallback": false
}
~~~

- 🌏 **指定 region：** 在该 region 中选择延迟最低且可用的节点。
- 🎯 **指定节点：** 优先使用指定节点；如果该节点不可用，先尝试它所属的 catalog region。
- 🔁 **fallback 顺序：** allow_cross_region_fallback 为 true 时，才继续允许跨 region 的全局 fallback；否则最后使用 DIRECT。
- 🧩 **IP 与 CIDR：** 支持单个 IP 或整段网络。一个 IP 同时匹配多条规则时，使用最长 prefix，也就是最具体的 CIDR 规则。
- ⚡ **健康状态决策：** 测试节点延迟，并在提交前查看最终会使用的 route。
- 🔒 **受控写入：** ALLOW_SOURCE_IP_ROUTES 默认是 false。只有完成认证、TLS、网络来源限制和权限配置后，才建议改为 true。

此外还可以：

- 👀 查看代理组和节点状态；
- ⏱️ 批量测试节点延迟；
- 📚 管理 profile；
- 🗺️ 在管理页面维护多条 IP/CIDR mapping；
- 🔁 仅在明确允许时修改配置并 reload Mihomo。

## 🔌 先分清 3 个端口

| 端口 | 使用者 | 作用 |
| --- | --- | --- |
| 7890 | 电脑、手机和其他代理客户端 | Mihomo mixed-port，承载实际代理流量 |
| 9090 | linux-proxy-control | Mihomo external-controller，提供 HTTP API |
| 8080 | 本地 Demo / Pi 过渡 unit | 临时应用 HTTP 端口；生产服务使用 Unix socket |

~~~text
浏览器 -- LAN:80 --> nginx -- Unix socket --> linux-proxy-control -- HTTP:9090 --> Mihomo
代理客户端 ----------------------------------------------> Mihomo:7890
~~~

7890 不是 controller API，9090 也不是代理端口。

## 🔐 安全边界

本项目没有浏览器登录、API token、TLS 或内置访问控制。请只在可信 LAN 中使用，或放在已经完成认证的 TLS reverse proxy 后面。

- 🚫 不要把 7890、9090、8080 做公网端口转发；
- 🧱 用 UFW、路由器 ACL 或上游 reverse proxy 将 nginx 限制在可信管理网段；
- 🤫 MIHOMO_SECRET 只能保存在服务器 root-only env 文件中，不要提交、打印或发送给浏览器；
- 🛡️ ALLOW_CONFIG_WRITE、ALLOW_PROFILE_ACTIVATE、ALLOW_SOURCE_IP_ROUTES 默认都是 false；
- ⚠️ 只有准备好认证、TLS、网络来源限制和最小文件权限后，才逐项开启写操作。

## 🧩 配置 Mihomo External Controller

请编辑 Mihomo 实际加载的 config.yaml。使用 Clash Verge 时，要修改当前生效的 Mihomo 配置，不要只修改不会被加载的备份文件。

~~~yaml
mixed-port: 7890
external-controller: 127.0.0.1:9090
secret: "replace-with-a-long-random-secret"
~~~

如果 linux-proxy-control 和 Mihomo 在同一台树莓派上，推荐使用 127.0.0.1:9090。如果它们在不同机器上，请让 Mihomo 监听管理 LAN 地址，并用防火墙只允许 linux-proxy-control 服务器访问 9090。尽量不要使用 0.0.0.0:9090。

重启 Mihomo 后，先验证 API 和 secret：

~~~bash
export MIHOMO_CONTROLLER_URL=http://127.0.0.1:9090
read -rsp 'Mihomo secret: ' MIHOMO_SECRET
echo
curl -fsS -H "Authorization: Bearer $MIHOMO_SECRET" "$MIHOMO_CONTROLLER_URL/version"
unset MIHOMO_SECRET
~~~

如果失败，请检查 Mihomo 是否重启、7890 与 9090 是否混淆、secret 是否一致以及防火墙是否允许访问。确认后也可以请求 $MIHOMO_CONTROLLER_URL/proxies 查看代理组。

## 🛠️ 连接 linux-proxy-control

将环境示例复制到仓库之外，并编辑真实值：

~~~bash
sudo install -m 0600 -o root -g root deploy/rpb5-proxy-control.env.example /etc/rpb5-proxy-control/app.env
sudoedit /etc/rpb5-proxy-control/app.env
~~~

~~~dotenv
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
~~~

MIHOMO_CONFIG_PATH 必须指向 Mihomo 当前真正加载的 YAML。只有要把 source-IP route 写入 Mihomo 时，才需要打开 ALLOW_SOURCE_IP_ROUTES=true 并准备必要权限。PROFILES_DIR 和 IP_MAPPING_FILE 必须位于 CONFIG_DIR 中。不要使用 chmod 777，请只授予 rpb5 最小必要权限。

## 🚀 Demo

Demo 使用模拟 provider，不会连接 Mihomo，也不需要真实 secret。

~~~bash
git clone https://github.com/sherlockjyzhang/linux-proxy-control.git rpb5-proxy-control
cd rpb5-proxy-control
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
DEMO_MODE=true .venv/bin/python -m backend.app
~~~

打开 http://127.0.0.1:8080 即可体验。前端由 Flask 直接提供，不需要 Node.js 或 npm。

## 🐧 Raspberry Pi OS / Debian / Ubuntu 生产部署

生产部署沿用仓库中的技术路径：

- 应用：/opt/rpb5-proxy-control
- 服务用户：rpb5
- 持久数据：/var/lib/rpb5-proxy-control
- env：/etc/rpb5-proxy-control/app.env
- Gunicorn Unix socket：/run/rpb5-proxy-control/gunicorn.sock

安装 git、Python venv、nginx、curl 后，创建 rpb5 用户和应用目录，安装 deploy/rpb5-proxy-control.service 与 deploy/nginx.conf，然后执行 nginx -t、systemctl daemon-reload、systemctl enable --now rpb5-proxy-control。80/tcp 只允许可信管理 LAN 访问，生产环境不要暴露 8080。

## 🔄 更新、回滚和测试

更新脚本要求 Git worktree 干净，并且已经配置 env、sudo、nginx 和 systemd。它不会覆盖 app.env、profile、mapping 或 Mihomo 活动配置。

~~~bash
cd /opt/rpb5-proxy-control
git pull --ff-only
sudo -v
./deploy/update-and-restart.sh
~~~

失败的 deployment snapshot 保存在 /var/lib/rpb5-proxy-control/deploy-snapshots/。生产更新前，请另行备份 env、linux-proxy-control 配置目录和 Mihomo 活动配置。

~~~bash
.venv/bin/python -m compileall backend
.venv/bin/pytest -q
~~~

本地测试不需要 Mihomo。

## 🩺 故障排查

| 现象 | 先检查 |
| --- | --- |
| connected: false | Mihomo 是否运行、external-controller 是否为 9090、secret 是否匹配 |
| 页面打不开 | systemctl status、nginx -t、UFW、Unix socket 权限 |
| 页面能开但没有节点 | /version、/proxies，以及 controller 是否指向 9090 而不是 7890 |
| mapping 应用失败 | ALLOW_*、MIHOMO_CONFIG_PATH、rpb5 最小权限 |
| 局域网外也能访问 | 立即删除 port-forward，检查 UFW 和路由器 ACL |

## 📄 许可证

MIT License：[LICENSE](LICENSE) · 安全说明：[SECURITY.md](SECURITY.md)
