# 🐧 linux-proxy-control

> 🎯 特定の IP/CIDR を、指定した region または Mihomo の特定ノードへルーティング。YAML の手編集は不要です。

[English](README.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md) · [한국어](README.ko.md)

linux-proxy-control は、実行中の [Mihomo](https://github.com/MetaCubeX/mihomo) External Controller に接続する軽量な Flask ダッシュボードです。通信を処理するのは Mihomo、このプロジェクトは IP ベースのルーティングを管理する操作画面です。✨

## 🧠 Clash を知っている方へ

Clash を知っているなら、**Mihomo は Clash エコシステムで実際に動作するプロキシコア／ランタイム**だと考えると分かりやすいです。Clash で馴染みのある設定モデルを読み込み、proxy、proxy group、rule、接続を管理して実際の通信を処理します。linux-proxy-control は Clash のクライアントや UI の代わりではなく、Mihomo の External Controller から管理するダッシュボードです。

- 🧩 **馴染みのある概念:** proxy、proxy group、rule、サブスクリプション、mixed-port など。
- 🎛️ **このプロジェクトの役割:** source IP ごとに region または exact node を選び、遅延を考慮した route を管理します。
- 🔌 **接続方法:** Mihomo の External Controller は HTTP の操作 API で、このプロジェクトはそこから catalog を読み取り、操作を送ります。

Clash Verge などの Clash 系フロントエンドを使う場合は、実際に Mihomo が起動していることを確認し、読み込まれている active config を編集してください。

## ⭐ 主な機能: IP/CIDR → region / node ルーティング

このプロジェクトの主な機能は、特定のクライアント IP または CIDR を、指定した region または正確な Mihomo node に割り当てることです。

~~~json
{
  "ip": "192.168.1.42",
  "selection": { "kind": "region", "value": "Tokyo" },
  "allow_cross_region_fallback": false
}
~~~

- 🌏 **region 指定:** その region 内で最も速く利用可能な node を選択します。
- 🎯 **exact node 指定:** 指定 node を優先します。利用できない場合は、その node の catalog region を試します。
- 🔁 **fallback:** allow_cross_region_fallback が true なら他 region の全体 fallback も許可します。false の場合、最後は DIRECT です。
- 🧩 **IP/CIDR:** 単一 IP とネットワーク範囲を指定できます。複数の CIDR に一致した場合は、最長 prefix、つまり最も具体的なルールを使います。
- ⚡ **health-aware:** node の遅延を確認し、適用される route を画面で確認できます。
- 🔒 **安全な変更:** ALLOW_SOURCE_IP_ROUTES の既定値は false。認証、TLS、ネットワーク制限を準備してから true にしてください。

その他、proxy group と node の状態確認、遅延テスト、profile 管理、複数 mapping の編集、許可した場合の Mihomo reload に対応します。

## 🔌 3 つのポート

| ポート | 用途 |
| --- | --- |
| 7890 | Mihomo mixed-port。クライアントのプロキシ通信 |
| 9090 | Mihomo external-controller。linux-proxy-control が呼ぶ HTTP API |
| 8080 | ローカル Demo / Pi 移行 unit。本番は Unix socket |

~~~text
ブラウザ -- LAN:80 --> nginx -- Unix socket --> linux-proxy-control -- HTTP:9090 --> Mihomo
プロキシクライアント ----------------------------------------------> Mihomo:7890
~~~

7890 は Controller API ではなく、9090 はプロキシポートではありません。

## 🔐 セキュリティ境界

ブラウザログイン、API token、TLS、組み込みアクセス制御はありません。信頼できる LAN、または認証済み TLS reverse proxy の後ろで使用してください。7890、9090、8080 をインターネットへ転送しないでください。

MIHOMO_SECRET は root のみ読める env ファイルに置き、Git やブラウザへ出さないでください。ALLOW_CONFIG_WRITE、ALLOW_PROFILE_ACTIVATE、ALLOW_SOURCE_IP_ROUTES は既定で false です。認証、TLS、ネットワーク制限、最小権限を用意した後だけ必要な write 機能を有効にしてください。

## 🧩 Mihomo External Controller の設定

Mihomo が実際に読み込む config.yaml を編集します。Clash Verge の場合は active な Mihomo 設定を変更してください。

~~~yaml
mixed-port: 7890
external-controller: 127.0.0.1:9090
secret: "replace-with-a-long-random-secret"
~~~

同じ Raspberry Pi なら 127.0.0.1:9090 が推奨です。別の Linux サーバーから接続する場合は、Mihomo を管理 LAN のアドレスに bind し、9090 は linux-proxy-control サーバーだけに許可します。0.0.0.0:9090 はできるだけ避けてください。

~~~bash
export MIHOMO_CONTROLLER_URL=http://127.0.0.1:9090
read -rsp 'Mihomo secret: ' MIHOMO_SECRET
echo
curl -fsS -H "Authorization: Bearer $MIHOMO_SECRET" "$MIHOMO_CONTROLLER_URL/version"
unset MIHOMO_SECRET
~~~

失敗時は Mihomo の再起動、7890/9090、secret、ファイアウォールを確認してください。

## 🛠️ linux-proxy-control の接続設定

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

MIHOMO_CONFIG_PATH は Mihomo が実際に読み込む YAML です。source-IP route を Mihomo に書くには ALLOW_SOURCE_IP_ROUTES=true と必要な権限が必要です。PROFILES_DIR と IP_MAPPING_FILE は CONFIG_DIR の内部に置いてください。chmod 777 は使わず、rpb5 に最小権限だけを与えます。

## 🚀 Demo

Mihomo を使わずに画面を確認できます。

~~~bash
git clone https://github.com/sherlockjyzhang/linux-proxy-control.git rpb5-proxy-control
cd rpb5-proxy-control
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
DEMO_MODE=true .venv/bin/python -m backend.app
~~~

http://127.0.0.1:8080 を開いてください。Node.js と npm は不要です。

## 🐧 Raspberry Pi OS / Debian / Ubuntu 本番配置

既定の配置は次のとおりです。

- アプリ: /opt/rpb5-proxy-control
- サービスユーザー: rpb5
- 永続データ: /var/lib/rpb5-proxy-control
- env: /etc/rpb5-proxy-control/app.env
- Gunicorn socket: /run/rpb5-proxy-control/gunicorn.sock

apt で git、python3-venv、python3-pip、nginx、curl をインストールし、rpb5 ユーザーとアプリディレクトリを作成します。deploy/rpb5-proxy-control.service と deploy/nginx.conf を systemd/nginx にインストールし、nginx -t、systemctl daemon-reload、systemctl enable --now rpb5-proxy-control を実行してください。80/tcp は管理 LAN にだけ UFW で許可し、本番で 8080 を公開しないでください。

## 🔄 更新、ロールバック、テスト

更新スクリプトは clean な Git worktree、env、sudo、nginx、systemd を要求します。app.env、profile、mapping、Mihomo の active config は上書きしません。

~~~bash
cd /opt/rpb5-proxy-control
git pull --ff-only
sudo -v
./deploy/update-and-restart.sh
~~~

失敗した deployment snapshot は /var/lib/rpb5-proxy-control/deploy-snapshots/ に残ります。更新前に env、linux-proxy-control の設定ディレクトリ、Mihomo の active config を別途バックアップしてください。

~~~bash
.venv/bin/python -m compileall backend
.venv/bin/pytest -q
~~~

Mihomo なしでテストできます。

## 🩺 トラブルシューティング

| 症状 | まず確認すること |
| --- | --- |
| connected: false | Mihomo、external-controller が 9090、secret の一致 |
| ページが開かない | systemctl status、nginx -t、UFW、Unix socket の権限 |
| node が表示されない | /version、/proxies、controller が 9090 を指しているか |
| mapping が適用できない | ALLOW_*、MIHOMO_CONFIG_PATH、rpb5 の権限 |
| LAN 外からも開ける | port-forward を削除し、UFW とルーター ACL を確認 |

## 📄 ライセンス

MIT License: [LICENSE](LICENSE) · セキュリティ: [SECURITY.md](SECURITY.md)
