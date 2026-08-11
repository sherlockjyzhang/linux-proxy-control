# RPb5 Proxy Control

> Mihomo の技術者向けコンソールを、家庭 LAN 用の分かりやすいリモコンに。

[简体中文](README.md) · [English](README.en.md) · [日本語](README.ja.md) · [한국어](README.ko.md)

RPb5 Proxy Control は、Flask とビルド不要の静的フロントエンドで作った軽量なダッシュボードです。すでに動作している [Mihomo](https://github.com/MetaCubeX/mihomo) の External Controller API に接続し、プロキシグループの確認、遅延テスト、ノード選択、profile 管理、送信元 IP/CIDR ごとのルーティングを行います。

Mihomo 本体、サブスクリプション、ノード、設定生成機能は含みません。通信を処理するのは Mihomo、このプロジェクトは操作画面です。

## まず覚える 3 つのポート

| ポート | 用途 |
| --- | --- |
| `7890` | Mihomo の `mixed-port`。PC やスマートフォンのプロキシ通信 |
| `9090` | Mihomo の `external-controller`。RPb5 が呼び出す HTTP API |
| `8080` | ローカル Demo / Pi 移行用。本番サービスは Unix socket を使用 |

```text
ブラウザ -- LAN:80 --> nginx -- Unix socket --> RPb5 -- HTTP:9090 --> Mihomo
プロキシクライアント ------------------------------------------> Mihomo:7890
```

`7890` は Controller API ではなく、`9090` はプロキシポートではありません。

## セキュリティ境界

ブラウザログイン、API token、TLS、組み込みアクセス制御はありません。信頼できる LAN、または認証と TLS を担当する別のリバースプロキシの後ろで使用してください。`7890`、`9090`、`8080` をインターネットへポート転送しないでください。

`ALLOW_CONFIG_WRITE`、`ALLOW_PROFILE_ACTIVATE`、`ALLOW_SOURCE_IP_ROUTES` は既定値がすべて `false` です。認証、TLS、ネットワーク制限、最小権限のファイル設定を用意した後だけ、必要な機能を個別に有効にしてください。`MIHOMO_SECRET` は root のみ読める env ファイルに置き、Git やブラウザへ出さないでください。

## Mihomo External Controller を設定する

実際に Mihomo が読み込んでいる `config.yaml` を編集します。Clash Verge を使う場合は、Clash Verge のアクティブな Mihomo 設定を変更してください。

```yaml
mixed-port: 7890
external-controller: 127.0.0.1:9090
secret: "replace-with-a-long-random-secret"
```

RPb5 と Mihomo が同じ Raspberry Pi にある場合は `127.0.0.1:9090` が推奨です。別の Linux サーバーから接続する場合は、Mihomo 側を管理 LAN のアドレスに bind し、ファイアウォールで RPb5 サーバーだけを許可します。`0.0.0.0:9090` はなるべく避けてください。

Mihomo を再起動し、RPb5 を起動する前に API と secret を確認します。

```bash
export MIHOMO_CONTROLLER_URL=http://127.0.0.1:9090
read -rsp 'Mihomo secret: ' MIHOMO_SECRET
echo
curl -fsS -H "Authorization: Bearer ${MIHOMO_SECRET}" \
  "${MIHOMO_CONTROLLER_URL}/version"
unset MIHOMO_SECRET
```

失敗する場合は、Mihomo の再起動、`7890` と `9090` の取り違え、secret の一致、ファイアウォールを確認してください。`/proxies` でグループの取得も確認できます。

## RPb5 側の設定

例をリポジトリ外へコピーし、実際の値を入力します。

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

`MIHOMO_CONFIG_PATH` は、設定変更や source-IP ルートを使う場合のアクティブな YAML です。サービスユーザー `rpb5` に必要最小限の読み書き権限を付与し、`chmod 777` は使わないでください。`PROFILES_DIR` と `IP_MAPPING_FILE` は `CONFIG_DIR` の内部に置く必要があります。

## Demo と本番インストール

Mihomo なしで画面を試すには次を実行します。

```bash
git clone https://github.com/sherlockjyzhang/linux-proxy-control.git rpb5-proxy-control
cd rpb5-proxy-control
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
DEMO_MODE=true .venv/bin/python -m backend.app
```

<http://127.0.0.1:8080> を開いてください。Node.js と npm は不要です。

Raspberry Pi OS / Debian / Ubuntu では、`apt` で `git python3-venv python3-pip nginx curl` を入れ、`rpb5` ユーザーと `/opt/rpb5-proxy-control` を作成します。その後、`deploy/rpb5-proxy-control.env.example` を `/etc/rpb5-proxy-control/app.env` にコピーし、`deploy/rpb5-proxy-control.service` と `deploy/nginx.conf` を systemd/nginx にインストールして `nginx -t`、`systemctl daemon-reload`、`systemctl enable --now rpb5-proxy-control` を実行します。80/tcp は管理 LAN だけに UFW で許可してください。詳細なコマンドは [中国語 README](README.md) の本番手順を参照できます。

更新：

```bash
cd /opt/rpb5-proxy-control
git pull --ff-only
sudo -v
./deploy/update-and-restart.sh
```

テスト：`.venv/bin/python -m compileall backend` と `.venv/bin/pytest -q`。障害時は `journalctl -u rpb5-proxy-control`、`nginx -t`、`/version`、`/proxies`、UFW を確認してください。

MIT License：[LICENSE](LICENSE) · セキュリティ：[SECURITY.md](SECURITY.md)
