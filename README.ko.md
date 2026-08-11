# RPb5 Proxy Control

> Mihomo의 엔지니어용 콘솔을 신뢰할 수 있는 홈 LAN용 리모컨으로 바꿉니다.

[简体中文](README.zh-CN.md) · [English](README.md) · [日本語](README.ja.md) · [한국어](README.ko.md)

RPb5 Proxy Control은 Flask와 빌드가 필요 없는 정적 프론트엔드로 만든 가벼운 대시보드입니다. 이미 실행 중인 [Mihomo](https://github.com/MetaCubeX/mihomo)의 External Controller API에 연결하여 프록시 그룹 확인, 지연 시간 테스트, 노드 선택, profile 관리, 소스 IP/CIDR별 라우팅을 제공합니다.

Mihomo 본체, 구독, 노드, 설정 생성기는 포함하지 않습니다. 실제 트래픽은 Mihomo가 처리하고, 이 프로젝트는 제어 화면 역할을 합니다.

## 헷갈리기 쉬운 세 포트

| 포트 | 용도 |
| --- | --- |
| `7890` | Mihomo `mixed-port`; PC·휴대폰의 프록시 트래픽 |
| `9090` | Mihomo `external-controller`; RPb5가 호출하는 HTTP API |
| `8080` | 로컬 Demo / Raspberry Pi 전환용. 최종 서비스는 Unix socket 사용 |

```text
브라우저 -- LAN:80 --> nginx -- Unix socket --> RPb5 -- HTTP:9090 --> Mihomo
프록시 클라이언트 --------------------------------------------> Mihomo:7890
```

`7890`은 Controller API가 아니며 `9090`은 프록시 포트가 아닙니다.

## 보안 경계

브라우저 로그인, API token, TLS, 내장 접근 제어가 없습니다. 신뢰할 수 있는 LAN에서만 사용하거나, 별도의 인증/TLS reverse proxy 뒤에 배치하세요. `7890`, `9090`, `8080`을 인터넷에 포트 포워딩하지 마세요.

`ALLOW_CONFIG_WRITE`, `ALLOW_PROFILE_ACTIVATE`, `ALLOW_SOURCE_IP_ROUTES`의 기본값은 모두 `false`입니다. 인증, TLS, 네트워크 제한, 최소 권한 파일 설정을 준비한 뒤 필요한 기능만 개별적으로 켜세요. `MIHOMO_SECRET`은 root 전용 env 파일에만 저장하고 Git이나 브라우저로 보내지 마세요.

## 먼저 Mihomo External Controller 설정하기

Mihomo가 실제로 읽는 `config.yaml`을 수정하세요. Clash Verge를 사용하는 경우 활성 Mihomo 설정을 변경해야 하며, 사용하지 않는 백업 파일을 수정하면 안 됩니다.

```yaml
mixed-port: 7890
external-controller: 127.0.0.1:9090
secret: "replace-with-a-long-random-secret"
```

RPb5와 Mihomo가 같은 Raspberry Pi에서 실행되면 `127.0.0.1:9090`을 권장합니다. 다른 Linux 서버에서 접속하면 Mihomo를 관리 LAN 주소에 bind하고 방화벽에서 RPb5 서버만 허용하세요. 가능하면 `0.0.0.0:9090`은 사용하지 마세요.

Mihomo를 재시작한 뒤 RPb5를 실행하기 전에 API와 secret을 확인합니다.

```bash
export MIHOMO_CONTROLLER_URL=http://127.0.0.1:9090
read -rsp 'Mihomo secret: ' MIHOMO_SECRET
echo
curl -fsS -H "Authorization: Bearer ${MIHOMO_SECRET}" \
  "${MIHOMO_CONTROLLER_URL}/version"
unset MIHOMO_SECRET
```

실패하면 Mihomo 재시작 여부, 포트 `7890`/`9090` 혼동, secret 일치 여부, 방화벽을 확인하세요. `/proxies`를 호출하면 프록시 그룹도 확인할 수 있습니다.

## RPb5 환경 설정

예제 파일을 저장소 밖으로 복사하고 실제 값을 입력합니다.

```bash
sudo install -m 0600 -o root -g root \
  deploy/rpb5-proxy-control.env.example \
  /etc/rpb5-proxy-control/app.env
sudoedit /etc/rpb5-proxy-control/app.env
```

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

`MIHOMO_CONFIG_PATH`는 설정 수정이나 source-IP route를 사용할 때의 활성 YAML 경로입니다. 서비스 계정 `rpb5`에는 필요한 최소한의 읽기/쓰기 권한만 주세요. `chmod 777`은 사용하지 마세요. `PROFILES_DIR`과 `IP_MAPPING_FILE`은 `CONFIG_DIR` 안에 있어야 합니다.

## Demo 및 운영 설치

Mihomo 없이 화면을 먼저 확인하려면 다음을 실행합니다.

```bash
git clone https://github.com/sherlockjyzhang/linux-proxy-control.git rpb5-proxy-control
cd rpb5-proxy-control
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
DEMO_MODE=true .venv/bin/python -m backend.app
```

<http://127.0.0.1:8080>을 여세요. Node.js와 npm은 필요하지 않습니다.

Raspberry Pi OS / Debian / Ubuntu에서는 `apt`로 `git python3-venv python3-pip nginx curl`을 설치하고 `rpb5` 사용자와 `/opt/rpb5-proxy-control`을 만드세요. `deploy/rpb5-proxy-control.env.example`을 `/etc/rpb5-proxy-control/app.env`로 복사하고, `deploy/rpb5-proxy-control.service`와 `deploy/nginx.conf`를 systemd/nginx에 설치한 뒤 `nginx -t`, `systemctl daemon-reload`, `systemctl enable --now rpb5-proxy-control`을 실행합니다. 80/tcp는 UFW로 관리 LAN에만 허용하세요. 전체 명령은 [중국어 README](README.md)의 운영 설치 절차에 있습니다.

업데이트:

```bash
cd /opt/rpb5-proxy-control
git pull --ff-only
sudo -v
./deploy/update-and-restart.sh
```

테스트: `.venv/bin/python -m compileall backend` 및 `.venv/bin/pytest -q`. 장애 시 `journalctl -u rpb5-proxy-control`, `nginx -t`, `/version`, `/proxies`, UFW 설정을 확인하세요.

MIT License: [LICENSE](LICENSE) · 보안: [SECURITY.md](SECURITY.md)
