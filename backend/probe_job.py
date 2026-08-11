"""Re-apply the persisted source-IP mappings after Mihomo profile refreshes.

Clash Verge periodically regenerates the active configuration from the
subscription profile.  The main API can safely rebuild the managed source
routes, so this one-shot job asks the running app to reconcile the persisted
mappings instead of writing a node choice itself.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request


DEFAULT_TIMEOUT_SECONDS = 120
RETRY_COUNT = 3
RETRY_DELAY_SECONDS = 2


class _ProbeAPIError(RuntimeError):
    def __init__(self, code):
        super().__init__("probe API returned HTTP %s" % code)
        self.code = code


def _api_base():
    configured = os.getenv("LINUX_PROBE_API_URL", "").strip()
    if configured:
        return configured.rstrip("/")
    host = os.getenv("APP_HOST", "127.0.0.1").strip()
    if not host or host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    port = os.getenv("APP_PORT", "8080").strip()
    return "http://%s:%s" % (host, port)


def _timeout_seconds():
    raw = os.getenv("LINUX_PROBE_TIMEOUT_SECONDS", str(DEFAULT_TIMEOUT_SECONDS)).strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_TIMEOUT_SECONDS


def _request_json(method, path, payload=None):
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(_api_base() + path, data=data, headers=headers, method=method)
    # The probe endpoint is local.  Do not send it through Mihomo even when
    # the service environment inherits HTTP(S)_PROXY variables.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=_timeout_seconds()) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise _ProbeAPIError(exc.code) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError("probe API is unavailable") from exc
    try:
        return json.loads(body) if body else {}
    except json.JSONDecodeError as exc:
        raise RuntimeError("probe API returned invalid JSON") from exc


def _reconcile_once():
    current = _request_json("GET", "/api/mappings")
    mappings = current.get("mappings")
    if not isinstance(mappings, list):
        raise RuntimeError("probe API returned invalid mappings")
    if not mappings:
        return {"mapping_count": 0, "applied": False}

    result = _request_json("PUT", "/api/mappings", {"mappings": mappings})
    if result.get("mode") == "real" and result.get("applied") is not True:
        raise RuntimeError("source mappings were not applied")
    return {"mapping_count": len(mappings), "applied": result.get("applied", False)}


def reconcile():
    # A subscription refresh can reload Mihomo at the same moment the timer
    # runs.  Retry that transient controller/configuration collision without
    # hiding a permanent validation failure.
    for attempt in range(RETRY_COUNT):
        try:
            return _reconcile_once()
        except _ProbeAPIError as exc:
            if exc.code != 400 or attempt == RETRY_COUNT - 1:
                raise
            time.sleep(RETRY_DELAY_SECONDS)


def main():
    try:
        result = reconcile()
    except RuntimeError as exc:
        print("source mapping reconciliation failed: %s" % exc, file=sys.stderr)
        return 1
    print("source mapping reconciliation: %s" % json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
