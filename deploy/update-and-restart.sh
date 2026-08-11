#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/opt/rpb5-proxy-control}"
SERVICE_NAME="rpb5-proxy-control"
ENV_FILE="/etc/rpb5-proxy-control/app.env"
SERVICE_PATH="/etc/systemd/system/$SERVICE_NAME.service"
NGINX_SITE="/etc/nginx/sites-available/rpb5-proxy-control"
NGINX_LINK="/etc/nginx/sites-enabled/rpb5-proxy-control"
DEPLOY_SNAPSHOT_ROOT="/var/lib/rpb5-proxy-control/deploy-snapshots"
PI_SERVICE_ASSET="deploy/rpb5-proxy-control.pi.service"
PI_NGINX_ASSET="deploy/nginx.pi.conf"

OLD_COMMIT=""
TARGET_COMMIT=""
SERVICE_ASSET=""
NGINX_ASSET=""
OLD_SERVICE_ASSET=""
OLD_NGINX_ASSET=""
SERVICE_USER=""
SNAPSHOT_DIR=""
SNAPSHOT_READY=0
SNAPSHOT_META_TMP=""
OLD_SERVICE_ACTIVE=""
OLD_SERVICE_ENABLED=""
OLD_NGINX_ACTIVE=""
OLD_SERVICE_PATH_STATE=""
OLD_SERVICE_PATH_TARGET=""
SERVICE_USER_UID=""
SERVICE_USER_GID=""
ROOT_GROUP_GID=""
DEPLOY_REF_EXPLICIT=0

if [[ -n "${DEPLOY_REF+x}" && "$#" -gt 0 ]]; then
    printf 'ERROR: DEPLOY_REF and a positional deployment ref cannot be used together.\n' >&2
    exit 1
fi
if [[ "$#" -gt 1 ]]; then
    printf 'ERROR: Usage: DEPLOY_REF=<commit-or-ref> %s [commit-or-ref]\n' "$0" >&2
    exit 1
fi
if [[ "$#" -eq 1 ]]; then
    DEPLOY_REF="$1"
    DEPLOY_REF_EXPLICIT=1
elif [[ -z "${DEPLOY_REF+x}" ]]; then
    DEPLOY_REF="HEAD"
else
    DEPLOY_REF_EXPLICIT=1
fi
if [[ -z "$DEPLOY_REF" ]]; then
    printf 'ERROR: DEPLOY_REF must not be empty.\n' >&2
    exit 1
fi
if [[ "$DEPLOY_REF_EXPLICIT" -eq 1 && ! "$DEPLOY_REF" =~ ^[[:xdigit:]]{40}$ ]]; then
    printf 'ERROR: Explicit deployment ref must be a full 40-hex commit SHA.\n' >&2
    exit 1
fi

restore_config_file() {
    local marker="$1"
    local backup="$2"
    local destination="$3"

    if sudo test -e "$marker"; then
        sudo rm -f -- "$destination" || return 1
        sudo cp -a -- "$backup" "$destination" || return 1
    else
        sudo rm -f -- "$destination" || return 1
    fi
}

validate_snapshot_root() {
    local root_stat=""
    local root_uid=""
    local root_mode=""

    if sudo test -L "$DEPLOY_SNAPSHOT_ROOT"; then
        fail "Snapshot root must not be a symlink: $DEPLOY_SNAPSHOT_ROOT"
    fi
    if sudo test -e "$DEPLOY_SNAPSHOT_ROOT"; then
        if ! sudo test -d "$DEPLOY_SNAPSHOT_ROOT"; then
            fail "Snapshot root is not a directory: $DEPLOY_SNAPSHOT_ROOT"
        fi
    else
        sudo install -d -m 0700 "$DEPLOY_SNAPSHOT_ROOT"
    fi

    if ! root_stat="$(sudo stat -c '%u %a' -- "$DEPLOY_SNAPSHOT_ROOT" 2>/dev/null)"; then
        fail "Unable to inspect snapshot root: $DEPLOY_SNAPSHOT_ROOT"
    fi
    read -r root_uid root_mode <<<"$root_stat"
    if [[ "$root_uid" != "0" ]]; then
        fail "Snapshot root must be owned by UID 0: $DEPLOY_SNAPSHOT_ROOT"
    fi
    if [[ -z "$root_mode" || "${root_mode: -2}" != "00" ]]; then
        fail "Snapshot root must have no group/other permission bits: $DEPLOY_SNAPSHOT_ROOT"
    fi
}

clear_candidate_service_enablement() {
    local current_active=""
    local current_enabled=""

    current_active="$(sudo systemctl is-active "$SERVICE_NAME" 2>/dev/null || true)"
    if [[ "$current_active" == "active" ]]; then
        sudo systemctl stop "$SERVICE_NAME" || return 1
    fi

    # Only remove enablement created or retained by the candidate unit.
    case "$OLD_SERVICE_ENABLED" in
        masked|masked-runtime)
            sudo systemctl unmask "$SERVICE_NAME" || return 1
            ;;
    esac

    current_enabled="$(sudo systemctl is-enabled "$SERVICE_NAME" 2>/dev/null || true)"
    case "$current_enabled" in
        enabled|enabled-runtime)
            sudo systemctl disable "$SERVICE_NAME" || return 1
            ;;
        disabled|not-found|"")
            ;;
        *)
            return 1
            ;;
    esac
}

restore_service_enablement() {
    local restored_state=""

    case "$OLD_SERVICE_ENABLED" in
        enabled)
            sudo systemctl enable "$SERVICE_NAME" || return 1
            ;;
        enabled-runtime)
            sudo systemctl enable --runtime "$SERVICE_NAME" || return 1
            ;;
        disabled)
            ;;
        masked)
            [[ "$OLD_SERVICE_PATH_STATE" == "masked" ]] || return 1
            ;;
        masked-runtime)
            sudo systemctl mask --runtime "$SERVICE_NAME" || return 1
            ;;
        not-found|"")
            ;;
        *)
            return 1
            ;;
    esac

    restored_state="$(sudo systemctl is-enabled "$SERVICE_NAME" 2>/dev/null || true)"
    case "$OLD_SERVICE_ENABLED" in
        enabled)
            [[ "$restored_state" == "enabled" ]] || return 1
            ;;
        enabled-runtime)
            [[ "$restored_state" == "enabled-runtime" ]] || return 1
            ;;
        disabled)
            [[ "$restored_state" == "disabled" ]] || return 1
            ;;
        masked)
            [[ "$restored_state" == "masked" ]] || return 1
            ;;
        masked-runtime)
            [[ "$restored_state" == "masked-runtime" ]] || return 1
            ;;
        not-found|"")
            [[ "$restored_state" == "not-found" || -z "$restored_state" ]] || return 1
            ;;
    esac
}

restore_service_active_state() {
    if [[ "$OLD_SERVICE_ACTIVE" == "active" ]]; then
        sudo systemctl restart "$SERVICE_NAME" || return 1
        sudo systemctl is-active --quiet "$SERVICE_NAME" || return 1
    fi
}

rollback_install() {
    local rollback_failed=0
    local link_target=""

    trap - ERR INT TERM
    printf 'Rolling back to %s using snapshot %s...\n' "$OLD_COMMIT" "$SNAPSHOT_DIR" >&2

    if [[ "$TARGET_COMMIT" != "$OLD_COMMIT" ]]; then
        git checkout --detach "$OLD_COMMIT" || rollback_failed=1
    fi

    clear_candidate_service_enablement || rollback_failed=1

    restore_config_file \
        "$SNAPSHOT_DIR/service.present" \
        "$SNAPSHOT_DIR/service" \
        "$SERVICE_PATH" || rollback_failed=1
    restore_config_file \
        "$SNAPSHOT_DIR/nginx-site.present" \
        "$SNAPSHOT_DIR/nginx-site" \
        "$NGINX_SITE" || rollback_failed=1

    if sudo test -e "$SNAPSHOT_DIR/nginx-link.present"; then
        link_target="$(sudo cat "$SNAPSHOT_DIR/nginx-link.target")" || rollback_failed=1
        if [[ -n "$link_target" ]]; then
            sudo rm -f -- "$NGINX_LINK" || rollback_failed=1
            sudo ln -s -- "$link_target" "$NGINX_LINK" || rollback_failed=1
        else
            rollback_failed=1
        fi
    else
        sudo rm -f -- "$NGINX_LINK" || rollback_failed=1
    fi

    if [[ "$rollback_failed" -eq 0 ]]; then
        sudo systemctl daemon-reload || rollback_failed=1
    fi
    if [[ "$rollback_failed" -eq 0 ]]; then
        sudo nginx -t || rollback_failed=1
    fi

    if [[ "$rollback_failed" -eq 0 ]]; then
        restore_service_enablement || rollback_failed=1
    fi
    if [[ "$rollback_failed" -eq 0 ]]; then
        restore_service_active_state || rollback_failed=1
    fi
    if [[ "$rollback_failed" -eq 0 && "$OLD_NGINX_ACTIVE" == "active" ]]; then
        sudo systemctl reload nginx || rollback_failed=1
    fi

    return "$rollback_failed"
}

failure_report() {
    local status="${1:-$?}"
    local rollback_status=0
    trap - ERR INT TERM
    if [[ -n "$OLD_COMMIT" ]]; then
        printf 'Deployment failed (exit %s). Old commit: %s\n' "$status" "$OLD_COMMIT" >&2
        printf 'Old assets: service=%s nginx=%s\n' "${OLD_SERVICE_ASSET:-unknown}" "${OLD_NGINX_ASSET:-unknown}" >&2
        printf 'Selected assets: service=%s nginx=%s\n' "${SERVICE_ASSET:-unknown}" "${NGINX_ASSET:-unknown}" >&2
        if [[ -n "$TARGET_COMMIT" ]]; then
            printf 'Target commit: %s\n' "$TARGET_COMMIT" >&2
        fi
        if [[ "$SNAPSHOT_READY" -eq 1 ]]; then
            rollback_install || rollback_status=$?
            if [[ "$rollback_status" -ne 0 ]]; then
                printf 'AUTOMATIC ROLLBACK FAILED. Snapshot retained at %s.\n' "$SNAPSHOT_DIR" >&2
            else
                printf 'Rollback completed. Snapshot retained at %s.\n' "$SNAPSHOT_DIR" >&2
            fi
        elif [[ -n "$TARGET_COMMIT" && "$TARGET_COMMIT" != "$OLD_COMMIT" ]]; then
            if git checkout --detach "$OLD_COMMIT"; then
                printf 'Repository checkout restored to %s; no service/nginx snapshot was installed.\n' "$OLD_COMMIT" >&2
            else
                printf 'Repository checkout rollback failed; restore %s manually.\n' "$OLD_COMMIT" >&2
            fi
        fi
        printf 'Rollback path: %s (metadata and service/nginx backups); validate with systemctl daemon-reload and nginx -t before restarting.\n' \
            "${SNAPSHOT_DIR:-not-created}" >&2
    fi
    if [[ -n "$SNAPSHOT_DIR" ]]; then
        printf 'Production snapshot: %s\n' "$SNAPSHOT_DIR" >&2
    fi
    if [[ -n "$SNAPSHOT_META_TMP" ]]; then
        rm -f -- "$SNAPSHOT_META_TMP" || true
    fi
    exit "$status"
}
trap failure_report ERR
trap 'failure_report 130' INT
trap 'failure_report 143' TERM

fail() {
    printf 'ERROR: %s\n' "$1" >&2
    failure_report 1
}

ensure_sudo_access() {
    if sudo -n true >/dev/null 2>&1; then
        return 0
    fi
    if [[ -t 0 && -t 1 ]]; then
        sudo -v || fail "Unable to obtain sudo credentials."
        return 0
    fi
    fail "Noninteractive deployment requires passwordless sudo; sudo -n true failed."
}

if [[ "$(id -u)" -eq 0 ]]; then
    fail "Run this script as pi, without sudo."
fi
if [[ "$#" -gt 1 ]]; then
    fail "Usage: DEPLOY_REF=<commit-or-ref> $0 [commit-or-ref]"
fi
if [[ ! -d "$APP_DIR/.git" ]]; then
    fail "Repository not found: $APP_DIR"
fi
if [[ ! -f "$ENV_FILE" ]]; then
    fail "Missing environment file: $ENV_FILE"
fi

cd -- "$APP_DIR" || fail "Unable to enter repository: $APP_DIR"
OLD_COMMIT="$(git rev-parse HEAD)"
OLD_SERVICE_ASSET="deploy/rpb5-proxy-control.service"
OLD_NGINX_ASSET="deploy/nginx.conf"
if id -u rpb5 >/dev/null 2>&1; then
    SERVICE_USER="rpb5"
else
    SERVICE_USER="pi"
    OLD_SERVICE_ASSET="deploy/rpb5-proxy-control.pi.service"
    OLD_NGINX_ASSET="deploy/nginx.pi.conf"
fi
if ! SERVICE_USER_UID="$(id -u "$SERVICE_USER" 2>/dev/null)"; then
    fail "Unable to resolve service user UID: $SERVICE_USER"
fi
if [[ ! "$SERVICE_USER_UID" =~ ^[0-9]+$ ]]; then
    fail "Resolved service user UID is invalid: $SERVICE_USER"
fi
if ! SERVICE_USER_GID="$(id -g "$SERVICE_USER" 2>/dev/null)"; then
    fail "Unable to resolve service user primary GID: $SERVICE_USER"
fi
if [[ ! "$SERVICE_USER_GID" =~ ^[0-9]+$ ]]; then
    fail "Resolved service user primary GID is invalid: $SERVICE_USER"
fi
if ! ROOT_GROUP_GID="$(id -g root 2>/dev/null)"; then
    fail "Unable to resolve root group GID"
fi
if [[ ! "$ROOT_GROUP_GID" =~ ^[0-9]+$ ]]; then
    fail "Resolved root group GID is invalid"
fi
if [[ -n "$(git status --porcelain=v1)" ]]; then
    fail "Refusing deployment: worktree is dirty. No files were cleaned or overwritten."
fi

command -v git >/dev/null || fail "git is required"
command -v sudo >/dev/null || fail "sudo is required"
command -v curl >/dev/null || fail "curl is required"
ensure_sudo_access

if [[ "$DEPLOY_REF" != "HEAD" ]]; then
    git fetch origin --prune
fi
TARGET_COMMIT="$(git rev-parse --verify "${DEPLOY_REF}^{commit}")"
git cat-file -e "$TARGET_COMMIT:$PI_SERVICE_ASSET" || \
    fail "Target commit $TARGET_COMMIT does not track $PI_SERVICE_ASSET"
git cat-file -e "$TARGET_COMMIT:$PI_NGINX_ASSET" || \
    fail "Target commit $TARGET_COMMIT does not track $PI_NGINX_ASSET"
SERVICE_ASSET="deploy/rpb5-proxy-control.service"
NGINX_ASSET="deploy/nginx.conf"
if [[ "$SERVICE_USER" == "pi" ]]; then
    SERVICE_ASSET="$PI_SERVICE_ASSET"
    NGINX_ASSET="$PI_NGINX_ASSET"
fi
git cat-file -e "$TARGET_COMMIT:$SERVICE_ASSET" || \
    fail "Target commit $TARGET_COMMIT does not track $SERVICE_ASSET"
git cat-file -e "$TARGET_COMMIT:$NGINX_ASSET" || \
    fail "Target commit $TARGET_COMMIT does not track $NGINX_ASSET"
printf 'Deploying %s (previous %s)\n' "$TARGET_COMMIT" "$OLD_COMMIT"
git checkout --detach "$TARGET_COMMIT"

[[ -f "$SERVICE_ASSET" ]] || fail "Missing service asset: $SERVICE_ASSET"
[[ -f "$NGINX_ASSET" ]] || fail "Missing nginx asset: $NGINX_ASSET"
printf 'Selected assets: service=%s nginx=%s\n' "$SERVICE_ASSET" "$NGINX_ASSET"

# systemd reads EnvironmentFile as the manager before applying User=.
if sudo test -L "$ENV_FILE"; then
    fail "Environment file must not be a symlink: $ENV_FILE"
fi
if ! sudo test -f "$ENV_FILE"; then
    fail "Environment file is not a regular file: $ENV_FILE"
fi
if ! env_stat="$(sudo stat -c '%u %g %a' -- "$ENV_FILE" 2>/dev/null)"; then
    fail "Unable to inspect EnvironmentFile ownership or mode: $ENV_FILE"
fi
read -r env_uid env_gid env_mode <<<"$env_stat"
if [[ ! "$env_uid" =~ ^[0-9]+$ || ! "$env_gid" =~ ^[0-9]+$ ]]; then
    fail "Environment file ownership is not numeric: $ENV_FILE"
fi
if [[ "$env_uid" == "0" ]]; then
    if [[ "$env_gid" != "${ROOT_GROUP_GID:-0}" ]]; then
        fail "Root-owned EnvironmentFile must use root group GID ${ROOT_GROUP_GID:-0}: $ENV_FILE"
    fi
elif [[ "$env_uid" == "$SERVICE_USER_UID" ]]; then
    if [[ "$env_gid" != "$SERVICE_USER_GID" ]]; then
        fail "Service-owned EnvironmentFile must use primary GID $SERVICE_USER_GID ($SERVICE_USER): $ENV_FILE"
    fi
else
    fail "Environment file must be owned by UID 0 or $SERVICE_USER_UID ($SERVICE_USER): $ENV_FILE"
fi
if [[ "$env_mode" != "600" ]]; then
    fail "Environment file must have mode 0600: $ENV_FILE"
fi

if [[ "$SERVICE_USER" == "pi" ]]; then
    grep -Eq '^User=pi$' "$SERVICE_ASSET" || fail "Pi transition service must run as User=pi"
    grep -Eq '^Group=pi$' "$SERVICE_ASSET" || fail "Pi transition service must run as Group=pi"
    grep -Eq '127\.0\.0\.1:8080' "$SERVICE_ASSET" || fail "Pi transition service must bind 127.0.0.1:8080"
fi

sudo systemctl --version >/dev/null
OLD_SERVICE_ACTIVE="$(sudo systemctl is-active "$SERVICE_NAME" 2>/dev/null || true)"
OLD_SERVICE_ENABLED="$(sudo systemctl is-enabled "$SERVICE_NAME" 2>/dev/null || true)"
OLD_NGINX_ACTIVE="$(sudo systemctl is-active nginx 2>/dev/null || true)"
case "$OLD_SERVICE_ENABLED" in
    enabled|enabled-runtime|disabled|masked|masked-runtime|not-found|"")
        ;;
    *)
        fail "Unsupported existing service enablement state: $OLD_SERVICE_ENABLED"
        ;;
esac

OLD_SERVICE_PATH_STATE="absent"
if sudo test -L "$SERVICE_PATH"; then
    OLD_SERVICE_PATH_TARGET="$(sudo readlink -- "$SERVICE_PATH")"
    if [[ "$OLD_SERVICE_ENABLED" == "masked" && "$OLD_SERVICE_PATH_TARGET" == "/dev/null" ]]; then
        OLD_SERVICE_PATH_STATE="masked"
    else
        fail "Refusing to replace unexpected service symlink: $SERVICE_PATH"
    fi
elif sudo test -e "$SERVICE_PATH"; then
    if ! sudo test -f "$SERVICE_PATH"; then
        fail "Refusing to replace non-file service path: $SERVICE_PATH"
    fi
    if [[ "$OLD_SERVICE_ENABLED" == "masked" || "$OLD_SERVICE_ENABLED" == "masked-runtime" ]]; then
        fail "Masked service state has an unexpected regular unit path: $SERVICE_PATH"
    fi
    OLD_SERVICE_PATH_STATE="regular"
fi

if sudo test -L "$NGINX_SITE"; then
    fail "Refusing to replace unexpected nginx site symlink: $NGINX_SITE"
fi
if sudo test -e "$NGINX_SITE" && ! sudo test -f "$NGINX_SITE"; then
    fail "Refusing to replace non-file nginx site path: $NGINX_SITE"
fi
if sudo test -e "$NGINX_LINK" && ! sudo test -L "$NGINX_LINK"; then
    fail "Refusing to replace non-symlink nginx enabled path: $NGINX_LINK"
fi

if ! sudo nginx -t >/dev/null 2>&1; then
    fail "Existing nginx configuration failed validation; no deployment changes were made"
fi

nginx_dump=""
if ! nginx_dump="$(sudo nginx -T 2>/dev/null)"; then
    fail "Unable to inspect the existing nginx configuration; no deployment changes were made"
fi
default_server_status=0
printf '%s\n' "$nginx_dump" | awk \
    -v managed_site="$NGINX_SITE" \
    -v managed_link="$NGINX_LINK" '
        function is_port_80(target) {
            return target == "80" ||
                target == "*:80" ||
                target ~ /^[0-9]+(\.[0-9]+){3}:80$/ ||
                target ~ /^\[[^][]+\]:80$/
        }
        /^# configuration file / {
            file = $0
            sub(/^# configuration file /, "", file)
            sub(/:$/, "", file)
            next
        }
        /^[[:space:]]*listen[[:space:]]+/ {
            line = $0
            sub(/[[:space:]]*#.*/, "", line)
            target = $2
            sub(/;$/, "", target)
            if (is_port_80(target) && line ~ /(^|[[:space:]])default_server([[:space:]]|;|$)/ &&
                file != managed_site && file != managed_link) { found = 1 }
        }
        END { exit(found ? 0 : 1) }
    ' || default_server_status=$?
if [[ "$default_server_status" -eq 0 ]]; then
    fail "Conflicting nginx port-80 default_server exists outside the managed site"
elif [[ "$default_server_status" -ne 1 ]]; then
    fail "Unable to inspect nginx default_server declarations"
fi

if [[ "$OLD_NGINX_ACTIVE" != "active" ]]; then
    fail "nginx is not active; refusing a deployment that cannot safely reload it"
fi

[[ -x "$APP_DIR/.venv/bin/pip" ]] || {
    printf 'Creating virtual environment...\n'
    python3 -m venv "$APP_DIR/.venv"
}
printf 'Installing Python dependencies...\n'
"$APP_DIR/.venv/bin/pip" install -r requirements.txt
command -v systemd-analyze >/dev/null || fail "systemd-analyze is required"
sudo systemd-analyze verify "$APP_DIR/$SERVICE_ASSET"

validate_snapshot_root

snapshot_id="$(date +%Y%m%dT%H%M%S%z)-${OLD_COMMIT:0:12}"
SNAPSHOT_DIR="$DEPLOY_SNAPSHOT_ROOT/$snapshot_id"
if sudo test -e "$SNAPSHOT_DIR"; then
    fail "Refusing to overwrite existing production snapshot: $SNAPSHOT_DIR"
fi
sudo install -d -m 0700 "$SNAPSHOT_DIR"

if sudo test -e "$SERVICE_PATH"; then
    sudo cp -a -- "$SERVICE_PATH" "$SNAPSHOT_DIR/service"
    sudo touch "$SNAPSHOT_DIR/service.present"
fi
if sudo test -e "$NGINX_SITE"; then
    sudo cp -a -- "$NGINX_SITE" "$SNAPSHOT_DIR/nginx-site"
    sudo touch "$SNAPSHOT_DIR/nginx-site.present"
fi
if sudo test -L "$NGINX_LINK"; then
    sudo readlink -- "$NGINX_LINK" | sudo tee "$SNAPSHOT_DIR/nginx-link.target" >/dev/null
    sudo touch "$SNAPSHOT_DIR/nginx-link.present"
fi

SNAPSHOT_META_TMP="$(mktemp)"
printf 'snapshot_version=1\nold_commit=%s\ntarget_commit=%s\nold_service_asset=%s\nold_nginx_asset=%s\nservice_user=%s\nservice_path=%s\nold_service_path_state=%s\nold_service_path_target=%s\nnginx_site=%s\nnginx_link=%s\nold_service_active=%s\nold_service_enabled=%s\nold_nginx_active=%s\n' \
    "$OLD_COMMIT" "$TARGET_COMMIT" "$OLD_SERVICE_ASSET" "$OLD_NGINX_ASSET" \
    "$SERVICE_USER" "$SERVICE_PATH" "$OLD_SERVICE_PATH_STATE" "$OLD_SERVICE_PATH_TARGET" \
    "$NGINX_SITE" "$NGINX_LINK" \
    "$OLD_SERVICE_ACTIVE" "$OLD_SERVICE_ENABLED" "$OLD_NGINX_ACTIVE" \
    > "$SNAPSHOT_META_TMP"
sudo install -m 0600 "$SNAPSHOT_META_TMP" "$SNAPSHOT_DIR/metadata"
rm -f -- "$SNAPSHOT_META_TMP"
SNAPSHOT_META_TMP=""
SNAPSHOT_READY=1
printf 'Production snapshot: %s\n' "$SNAPSHOT_DIR"

printf 'Installing service and nginx configuration...\n'
case "$OLD_SERVICE_ENABLED" in
    masked|masked-runtime)
        sudo systemctl unmask "$SERVICE_NAME"
        ;;
esac
sudo install -D -m 0644 "$SERVICE_ASSET" "$SERVICE_PATH"
sudo install -D -m 0644 "$NGINX_ASSET" "$NGINX_SITE"
sudo ln -sfn "$NGINX_SITE" "$NGINX_LINK"

sudo systemctl daemon-reload
sudo nginx -t
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"
sudo systemctl is-active --quiet "$SERVICE_NAME"
sudo systemctl reload nginx

printf 'Waiting for health check...\n'
for _ in {1..10}; do
    if health="$(curl -fsS --max-time 5 http://127.0.0.1/api/health 2>/dev/null)" \
        && grep -Eq '"connected"[[:space:]]*:[[:space:]]*true' <<<"$health"; then
        printf 'Healthy: %s\n' "$health"
        exit 0
    fi
    sleep 1
done

printf 'The service did not report connected health.\n' >&2
sudo systemctl status "$SERVICE_NAME" --no-pager -l || true
sudo journalctl -u "$SERVICE_NAME" -n 40 --no-pager || true
false
