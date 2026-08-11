# 🐧 linux-proxy-control

> 🎯 특정 IP/CIDR을 지정한 region 또는 특정 Mihomo 노드로 라우팅하세요. YAML을 직접 편집할 필요가 없습니다.

[English](README.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md)

linux-proxy-control은 이미 실행 중인 [Mihomo](https://github.com/MetaCubeX/mihomo) External Controller에 연결하는 가벼운 Flask 대시보드입니다. 실제 프록시 트래픽은 Mihomo가 처리하고, 이 프로젝트는 IP 기반 라우팅을 시각적으로 관리하는 제어 화면입니다. ✨

## 🧠 Clash를 알고 있다면

Clash를 알고 있다면 **Mihomo는 Clash 생태계에서 실제로 실행되는 프록시 코어/런타임**이라고 이해하면 됩니다. 익숙한 Clash 스타일 설정을 읽고 proxy, proxy group, rule, 연결을 관리하며 실제 트래픽을 처리합니다. linux-proxy-control은 Clash 클라이언트나 UI를 대체하는 것이 아니라 Mihomo의 External Controller를 통해 Mihomo를 관리하는 대시보드입니다.

- 🧩 **익숙한 모델:** proxy, proxy group, rule, 구독, mixed-port 등을 사용합니다.
- 🎛️ **이 프로젝트의 역할:** source IP마다 region 또는 exact node를 선택하고 지연 시간에 따른 route를 관리합니다.
- 🔌 **연결 방식:** Mihomo의 External Controller는 HTTP 제어 API이며, 이 프로젝트는 API로 catalog를 읽고 제어 작업을 보냅니다.

Clash Verge 같은 Clash 계열 프론트엔드를 사용한다면 Mihomo가 실제로 실행 중인지 확인하고, 사용하지 않는 백업 파일이 아니라 현재 활성 Mihomo 설정을 수정하세요.

## ⭐ 핵심 기능: IP/CIDR → region/노드 라우팅

이 프로젝트의 첫 번째 기능은 특정 클라이언트 IP 또는 CIDR을 원하는 region이나 정확한 Mihomo 노드에 매핑하는 것입니다.

~~~json
{
  "ip": "192.168.1.42",
  "selection": { "kind": "region", "value": "Tokyo" },
  "allow_cross_region_fallback": false
}
~~~

- 🌏 **region 지정:** 해당 region에서 가장 빠르고 사용 가능한 노드를 선택합니다.
- 🎯 **정확한 노드 지정:** 지정한 노드를 우선 사용하고, 사용할 수 없으면 해당 노드의 catalog region을 시도합니다.
- 🔁 **fallback 정책:** allow_cross_region_fallback가 true이면 다른 region의 전역 fallback도 허용합니다. false이면 마지막 fallback은 DIRECT입니다.
- 🧩 **IP/CIDR 규칙:** 단일 IP 또는 네트워크 범위를 지원합니다. 여러 CIDR이 동시에 일치하면 가장 긴 prefix, 즉 가장 구체적인 규칙을 사용합니다.
- ⚡ **상태 기반 결정:** 노드 지연 시간을 확인하고 실제 적용 route를 화면에서 확인할 수 있습니다.
- 🔒 **안전한 live 변경:** ALLOW_SOURCE_IP_ROUTES 기본값은 false입니다. 인증과 네트워크 제한을 준비한 뒤에만 true로 설정하세요.

추가로 프록시 그룹/노드 상태 확인, 지연 시간 테스트, profile 관리, 여러 mapping 관리, 허용된 경우 Mihomo reload를 지원합니다.

## 🔌 혼동하기 쉬운 3개 포트

| 포트 | 용도 |
| --- | --- |
| 7890 | Mihomo mixed-port, 프록시 클라이언트 트래픽 |
| 9090 | Mihomo external-controller, linux-proxy-control이 호출하는 HTTP API |
| 8080 | 로컬 Demo / Raspberry Pi 전환용; 운영 서비스는 Unix socket 사용 |

~~~text
브라우저 -- LAN:80 --> nginx -- Unix socket --> linux-proxy-control -- HTTP:9090 --> Mihomo
프록시 클라이언트 ----------------------------------------------> Mihomo:7890
~~~

7890은 Controller API가 아니며 9090은 프록시 포트가 아닙니다.

## 🔐 보안 경계

브라우저 로그인, API token, TLS, 내장 접근 제어가 없습니다. 신뢰할 수 있는 LAN에서 사용하거나 인증된 TLS reverse proxy 뒤에 배치하세요.

- 🚫 7890, 9090, 8080을 인터넷에 port-forward하지 마세요.
- 🧱 nginx는 UFW, 라우터 ACL 또는 상위 reverse proxy로 관리 LAN에만 공개하세요.
- 🤫 MIHOMO_SECRET은 root 전용 env 파일에 저장하고 Git이나 브라우저로 보내지 마세요.
- 🛡️ ALLOW_CONFIG_WRITE, ALLOW_PROFILE_ACTIVATE, ALLOW_SOURCE_IP_ROUTES는 기본값이 false입니다.
- ⚠️ 인증, TLS, 네트워크 제한과 최소 권한을 준비한 뒤 필요한 write 기능만 켜세요.

## 🧩 Mihomo External Controller 설정

Mihomo가 실제로 읽는 config.yaml을 수정하세요. Clash Verge를 사용한다면 활성 Mihomo 설정을 수정해야 합니다.

~~~yaml
mixed-port: 7890
external-controller: 127.0.0.1:9090
secret: "replace-with-a-long-random-secret"
~~~

같은 Raspberry Pi라면 127.0.0.1:9090을 권장합니다. 별도 Linux 서버에서 연결한다면 Mihomo를 관리 LAN 주소에 bind하고 9090은 linux-proxy-control 서버만 접근하도록 방화벽으로 제한하세요. 0.0.0.0:9090은 가급적 피하세요.

~~~bash
export MIHOMO_CONTROLLER_URL=http://127.0.0.1:9090
read -rsp 'Mihomo secret: ' MIHOMO_SECRET
echo
curl -fsS -H "Authorization: Bearer $MIHOMO_SECRET" "$MIHOMO_CONTROLLER_URL/version"
unset MIHOMO_SECRET
~~~

실패하면 Mihomo 재시작 여부, 7890/9090 혼동, secret 일치 여부와 방화벽을 확인하세요.

## 🛠️ linux-proxy-control 연결

예제 env 파일을 저장소 밖으로 복사하고 실제 값을 입력합니다.

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

MIHOMO_CONFIG_PATH는 Mihomo가 실제로 읽는 YAML입니다. source-IP route를 Mihomo에 쓰려면 ALLOW_SOURCE_IP_ROUTES=true와 필요한 권한이 필요합니다. PROFILES_DIR과 IP_MAPPING_FILE은 CONFIG_DIR 안에 있어야 합니다. chmod 777은 사용하지 마세요.

## 🚀 Demo

Demo는 시뮬레이션 provider를 사용하므로 Mihomo나 실제 secret이 필요하지 않습니다.

~~~bash
git clone https://github.com/sherlockjyzhang/linux-proxy-control.git rpb5-proxy-control
cd rpb5-proxy-control
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
DEMO_MODE=true .venv/bin/python -m backend.app
~~~

http://127.0.0.1:8080을 열어 보세요. Node.js와 npm은 필요하지 않습니다.

## 🐧 Raspberry Pi OS / Debian / Ubuntu 운영 배포

운영 기본 경로는 다음과 같습니다.

- 애플리케이션: /opt/rpb5-proxy-control
- 서비스 사용자: rpb5
- 영속 데이터: /var/lib/rpb5-proxy-control
- env: /etc/rpb5-proxy-control/app.env
- Gunicorn Unix socket: /run/rpb5-proxy-control/gunicorn.sock

apt로 git, python3-venv, python3-pip, nginx, curl을 설치하고 rpb5 사용자와 앱 디렉터리를 만드세요. deploy/rpb5-proxy-control.service와 deploy/nginx.conf를 systemd/nginx에 설치한 다음 nginx -t, systemctl daemon-reload, systemctl enable --now rpb5-proxy-control을 실행하세요. 80/tcp는 UFW로 관리 LAN에만 허용하고 운영에서는 8080을 공개하지 마세요.

## 🔄 업데이트, 롤백과 테스트

업데이트 스크립트는 clean Git worktree, env 파일, sudo, nginx, systemd를 요구합니다. app.env, profiles, mapping 및 Mihomo active config를 덮어쓰지 않습니다.

~~~bash
cd /opt/rpb5-proxy-control
git pull --ff-only
sudo -v
./deploy/update-and-restart.sh
~~~

실패한 deployment snapshot은 /var/lib/rpb5-proxy-control/deploy-snapshots/에 남습니다. 업데이트 전에 env, linux-proxy-control 설정 디렉터리, Mihomo active config를 별도로 백업하세요.

~~~bash
.venv/bin/python -m compileall backend
.venv/bin/pytest -q
~~~

Mihomo 없이 테스트할 수 있습니다.

## 🩺 문제 해결

| 증상 | 먼저 확인할 것 |
| --- | --- |
| connected: false | Mihomo 실행 여부, external-controller가 9090인지, secret 일치 여부 |
| 페이지가 열리지 않음 | systemctl status, nginx -t, UFW, Unix socket 권한 |
| 노드가 보이지 않음 | /version, /proxies, controller가 9090을 가리키는지 |
| mapping 적용 실패 | ALLOW_* 설정, MIHOMO_CONFIG_PATH, rpb5 권한 |
| 외부에서도 접속 가능 | port-forward를 즉시 제거하고 UFW/라우터 ACL 확인 |

## 📄 라이선스

MIT License: [LICENSE](LICENSE) · 보안 안내: [SECURITY.md](SECURITY.md)
