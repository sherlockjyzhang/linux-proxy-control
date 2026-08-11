import hashlib
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
import urllib.error

import pytest

from backend.config import Settings
from backend.provider import MihomoProvider


def provider():
    settings = Settings()
    settings.demo_mode = False
    settings.delay_timeout_ms = 2500
    return MihomoProvider(settings)


def test_demo_catalog_includes_korea_node():
    settings = Settings()
    settings.demo_mode = True
    catalog = MihomoProvider(settings).proxies()
    nodes = catalog["nodes"]
    assert {node["name"] for node in nodes} >= {"Korea-01"}
    assert next(node for node in nodes if node["name"] == "Korea-01")["region"] == "Korea"
    assert catalog["groups"]["Korea"]["now"] == "Korea-01"


@pytest.mark.parametrize(
    "controller_url, public_url, sensitive_parts",
    [
        (
            "https://alice:secret@example.com:9443/controller?token=hidden#fragment",
            "https://example.com:9443",
            ("alice", "secret", "token=hidden", "fragment"),
        ),
        (
            "http://192.0.2.10:9090/?password=hidden#status",
            "http://192.0.2.10:9090",
            ("password=hidden", "status"),
        ),
        (
            "http://user:pass@[2001:db8::10]:9090/path?auth=hidden#fragment",
            "http://[2001:db8::10]:9090",
            ("user", "pass", "auth=hidden", "fragment"),
        ),
        (
            "http://example.com:not-a-port/api?secret=hidden#fragment",
            "http://example.com",
            ("not-a-port", "secret=hidden", "fragment"),
        ),
    ],
)
def test_health_sanitizes_controller_url(
    monkeypatch, controller_url, public_url, sensitive_parts
):
    instance = provider()
    instance.settings.controller_url = controller_url
    monkeypatch.setattr(instance, "_request", lambda *args, **kwargs: {})

    connected = instance.health()
    assert connected["controller"] == public_url
    assert all(part not in str(connected) for part in sensitive_parts)
    assert controller_url not in str(connected)

    monkeypatch.setattr(
        instance,
        "_request",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("request failed for " + controller_url)
        ),
    )
    disconnected = instance.health()
    assert disconnected["controller"] == public_url
    assert disconnected["error"] == "controller request failed"
    assert all(part not in str(disconnected) for part in sensitive_parts)
    assert controller_url not in str(disconnected)


def test_delay_error_does_not_expose_exception_url_or_text(monkeypatch):
    instance = provider()
    secret_url = "https://user:password@example.com:9443/?token=hidden#fragment"
    monkeypatch.setattr(instance, "proxies", lambda: {"nodes": [{"name": "node"}]})
    monkeypatch.setattr(
        instance,
        "_request",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("request failed for " + secret_url)
        ),
    )

    result = instance.delay("node")
    assert result["error"] == "controller request failed"
    assert secret_url not in str(result)
    assert all(part not in str(result) for part in ("user", "password", "token=hidden", "fragment"))


def test_health_failure_logs_are_redacted_and_response_is_bounded(monkeypatch, caplog):
    instance = provider()
    controller_url = "https://alice:secret@example.com:9443/controller?token=hidden#fragment"
    exception_text = "health request failed for " + controller_url
    instance.settings.controller_url = controller_url
    monkeypatch.setattr(
        instance,
        "_request",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError(exception_text)),
    )

    with caplog.at_level(logging.WARNING, logger="backend.provider"):
        result = instance.health()

    assert result == {
        "mode": "disconnected",
        "connected": False,
        "controller": "https://example.com:9443",
        "error": "controller request failed",
    }
    assert len(str(result)) < 200
    rendered_logs = caplog.text
    assert controller_url not in rendered_logs
    assert exception_text not in rendered_logs
    assert all(part not in rendered_logs for part in ("alice", "secret", "token=hidden", "fragment"))


def test_delay_failure_logs_are_redacted_and_response_is_bounded(monkeypatch, caplog):
    instance = provider()
    controller_url = "https://user:password@example.com:9443/controller?token=hidden#fragment"
    exception_text = "delay request failed for " + controller_url
    instance.settings.controller_url = controller_url
    monkeypatch.setattr(instance, "proxies", lambda: {"nodes": [{"name": "node"}]})
    monkeypatch.setattr(
        instance,
        "_request",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError(exception_text)),
    )

    with caplog.at_level(logging.WARNING, logger="backend.provider"):
        result = instance.delay("node")

    assert result == {
        "name": "node",
        "delay_ms": None,
        "available": False,
        "error": "controller request failed",
    }
    assert len(str(result)) < 160
    rendered_logs = caplog.text
    assert controller_url not in rendered_logs
    assert exception_text not in rendered_logs
    assert all(part not in rendered_logs for part in ("user", "password", "token=hidden", "fragment"))


def test_request_accepts_empty_success_response(monkeypatch):
    instance = provider()

    class EmptyResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b""

    monkeypatch.setattr("backend.provider.urllib.request.urlopen", lambda *args, **kwargs: EmptyResponse())
    assert instance._request("PUT", "/configs", {"force": True}) == {}


def test_delay_marks_latency_over_cutoff_unavailable(monkeypatch):
    instance = provider()
    monkeypatch.setattr(instance, "proxies", lambda: {"nodes": [{"name": "node"}]})
    monkeypatch.setattr(instance, "_request", lambda *args, **kwargs: {"delay": 601})
    result = instance.delay("node")
    assert result["delay_ms"] == 601
    assert result["available"] is False


def test_delay_preserves_display_boundary_values(monkeypatch):
    instance = provider()
    monkeypatch.setattr(instance, "proxies", lambda: {"nodes": [{"name": "node"}]})
    for value in (300, 301, 1000, 1001):
        monkeypatch.setattr(instance, "_request", lambda *args, value=value, **kwargs: {"delay": value})
        instance._delay_cache.clear()
        result = instance.delay("node")
        assert result["delay_ms"] == value
        assert result["available"] is (value <= 600)


def test_delay_reports_http_error_and_timeout(monkeypatch):
    instance = provider()
    monkeypatch.setattr(instance, "proxies", lambda: {"nodes": [{"name": "node"}]})
    monkeypatch.setattr(instance, "_request", lambda *args, **kwargs: (_ for _ in ()).throw(urllib.error.HTTPError("url", 503, "busy", {}, None)))
    assert instance.delay("node")["error"] == "HTTP error 503"
    monkeypatch.setattr(instance, "_request", lambda *args, **kwargs: (_ for _ in ()).throw(urllib.error.URLError("timed out")))
    assert instance.delay("node")["error"] == "timeout"


def test_delay_preserves_http_504_for_timeout_mapping(monkeypatch):
    instance = provider()
    monkeypatch.setattr(instance, "proxies", lambda: {"nodes": [{"name": "node"}]})
    monkeypatch.setattr(instance, "_request", lambda *args, **kwargs: (_ for _ in ()).throw(urllib.error.HTTPError("url", 504, "timeout", {}, None)))
    assert instance.delay("node")["error"] == "HTTP error 504"


def test_batch_delay_serializes_real_probes_and_preserves_nodes(monkeypatch):
    instance = provider()
    instance.settings.delay_workers = 1
    catalog_calls = []

    def catalog():
        catalog_calls.append(1)
        return {"nodes": [{"name": "one"}, {"name": "two"}]}

    monkeypatch.setattr(instance, "proxies", catalog)
    active = [0]
    maximum = [0]

    def request(*args, **kwargs):
        active[0] += 1
        maximum[0] = max(maximum[0], active[0])
        active[0] -= 1
        return {"delay": 100}

    monkeypatch.setattr(instance, "_request", request)
    assert [node["name"] for node in instance.batch_delay_results()] == ["one", "two"]
    assert maximum[0] == 1
    assert len(catalog_calls) == 1


def test_batch_delay_uses_bounded_concurrent_workers(monkeypatch):
    instance = provider()
    instance.settings.delay_workers = 2
    nodes = [{"name": name} for name in ("one", "two", "three", "four")]
    monkeypatch.setattr(instance, "proxies", lambda: {"nodes": nodes})
    wave = threading.Barrier(2)
    active = [0]
    maximum = [0]
    active_lock = threading.Lock()

    def request(*args, **kwargs):
        with active_lock:
            active[0] += 1
            maximum[0] = max(maximum[0], active[0])
        wave.wait(timeout=2)
        with active_lock:
            active[0] -= 1
        return {"delay": 100}

    monkeypatch.setattr(instance, "_request", request)
    result = instance.batch_delay_results()
    assert [item["name"] for item in result] == ["one", "two", "three", "four"]
    assert maximum[0] == 2


def test_delay_cache_prevents_repeated_real_controller_probe(monkeypatch):
    instance = provider()
    monkeypatch.setattr(instance, "proxies", lambda: {"nodes": [{"name": "node"}]})
    calls = []

    def request(*args, **kwargs):
        calls.append(1)
        return {"delay": 123}

    monkeypatch.setattr(instance, "_request", request)
    assert instance.delay("node")["delay_ms"] == 123
    assert instance.delay("node")["delay_ms"] == 123
    assert len(calls) == 1


def test_concurrent_same_node_delay_calls_share_cache_result(monkeypatch):
    instance = provider()
    monkeypatch.setattr(instance, "proxies", lambda: {"nodes": [{"name": "node"}]})
    started = threading.Event()
    release = threading.Event()
    calls = []

    def request(*args, **kwargs):
        calls.append(1)
        started.set()
        release.wait(timeout=2)
        return {"delay": 123}

    monkeypatch.setattr(instance, "_request", request)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(instance.delay, "node")
        assert started.wait(timeout=2)
        second = executor.submit(instance.delay, "node")
        release.set()
        assert first.result(timeout=2)["delay_ms"] == 123
        assert second.result(timeout=2)["delay_ms"] == 123
    assert len(calls) == 1


def test_proxy_name_region_takes_precedence_over_metadata_and_region_map(monkeypatch):
    instance = provider()
    monkeypatch.setattr(
        instance,
        "_request",
        lambda *args, **kwargs: {
            "proxies": {
                "🇯🇵日本东京01": {"type": "Shadowsocks", "metadata": {"region": "wrong-metadata"}},
                "unmapped-node": {"type": "Shadowsocks", "metadata": {"region": "metadata-region"}},
            }
        },
    )
    instance.settings.region_map = {"unmapped-node": "mapped-region"}
    nodes = instance.proxies()["nodes"]
    by_name = {node["name"]: node for node in nodes}
    assert by_name["🇯🇵日本东京01"]["region"] == "日本"
    assert by_name["unmapped-node"]["region"] == "metadata-region"


def test_proxy_catalog_excludes_mihomo_internal_entries(monkeypatch):
    instance = provider()
    monkeypatch.setattr(
        instance,
        "_request",
        lambda *args, **kwargs: {
            "proxies": {
                "COMPATIBLE": {"type": "Compatible"},
                "PASS": {"type": "Pass"},
                "PASS-RULE": {"type": "PassRule"},
                "REJECT-DROP": {"type": "RejectDrop"},
                "🇯🇵日本东京01": {"type": "Shadowsocks"},
            }
        },
    )
    assert [node["name"] for node in instance.proxies()["nodes"]] == ["🇯🇵日本东京01"]


def test_proxy_catalog_includes_unknown_group_type_with_members(monkeypatch):
    instance = provider()
    monkeypatch.setattr(instance, "_request", lambda *args, **kwargs: {"proxies": {
        "BalancerX": {"type": "Balancer", "now": "node-a", "all": ["node-a", "node-b"]},
        "node-a": {"type": "Shadowsocks"},
    }})
    result = instance.proxies()
    assert result["groups"]["BalancerX"] == {"type": "Balancer", "now": "node-a", "all": ["node-a", "node-b"]}
    assert [node["name"] for node in result["nodes"]] == ["node-a"]


def test_proxy_catalog_tracks_reserved_group_state_without_exposing_it(monkeypatch):
    instance = provider()
    monkeypatch.setattr(instance, "_request", lambda *args, **kwargs: {"proxies": {
        "GLOBAL": {"type": "Global", "now": "node-a", "all": ["node-a", "node-b"]},
        "node-a": {"type": "Shadowsocks"},
    }})
    result = instance.proxies()
    assert result["group_states"]["GLOBAL"] == {
        "type": "Global",
        "now": "node-a",
        "all": ["node-a", "node-b"],
    }
    assert "GLOBAL" not in result["groups"]
    assert [node["name"] for node in result["nodes"]] == ["node-a"]


def test_reserved_group_state_drift_fails_closed(monkeypatch):
    instance = provider()
    prior = {
        "GLOBAL": {"type": "Global", "now": "node-a", "all": ["node-a", "node-b"]},
    }
    monkeypatch.setattr(instance, "proxies", lambda: {"groups": {}, "group_states": {
        "GLOBAL": {"type": "Global", "now": "node-b", "all": ["node-a", "node-b"]},
    }})
    try:
        instance._verify_unmanaged_group_state(prior, set())
    except ValueError as exc:
        assert "unmanaged" in str(exc)
    else:
        assert False


def test_unmanaged_group_state_ignores_managed_members(monkeypatch):
    instance = provider()
    managed = "linux-src-example"
    prior = {
        "GLOBAL": {"type": "Global", "now": "node-a", "all": ["node-a", "Proxy"]},
    }
    monkeypatch.setattr(instance, "proxies", lambda: {"groups": {}, "group_states": {
        "GLOBAL": {"type": "Global", "now": "node-a", "all": ["node-a", "Proxy", managed]},
    }})
    instance._verify_unmanaged_group_state(prior, {managed})


def test_unmanaged_dynamic_group_state_allows_reload_selection_drift(monkeypatch):
    instance = provider()
    prior = {
        "Auto": {"type": "URLTest", "now": "before", "all": ["before", "other"]},
    }
    monkeypatch.setattr(instance, "proxies", lambda: {"groups": {}, "group_states": {
        "Auto": {"type": "URLTest", "now": "after", "all": ["before", "other"]},
    }})
    instance._verify_unmanaged_group_state(prior, set())


def test_group_selection_verification_ignores_dynamic_group_selection(monkeypatch):
    instance = provider()
    monkeypatch.setattr(instance, "proxies", lambda: {"groups": {}, "group_states": {
        "Auto": {"type": "URLTest", "now": "after", "all": ["before", "other"]},
    }})
    instance._verify_group_selections({"Auto": "before"})


def test_group_selection_verification_includes_reserved_group_state(monkeypatch):
    instance = provider()
    monkeypatch.setattr(instance, "proxies", lambda: {"groups": {}, "group_states": {
        "GLOBAL": {"type": "Global", "now": "node-a", "all": ["node-a", "node-b"]},
    }})
    instance._verify_group_selections({"GLOBAL": "node-a"})


def test_structured_source_rule_matching_requires_exact_type_source_and_group():
    source = "192.168.3.148/32"
    group = "linux-src-expected"
    assert MihomoProvider._source_rule_matches({"type": "SRC-IP-CIDR", "payload": source, "proxy": group}, source, group)
    assert MihomoProvider._source_rule_matches({"type": "SrcIPCIDR", "payload": source, "proxy": group}, source, group)
    assert not MihomoProvider._source_rule_matches({"type": "DOMAIN", "payload": source, "proxy": group}, source, group)
    assert not MihomoProvider._source_rule_matches({"type": "SRC-IP-CIDR", "payload": "192.168.3.0/24", "proxy": group}, source, group)


def test_source_restore_selects_only_managed_groups(monkeypatch):
    instance = provider()
    calls = []
    source = "192.168.3.148/32"
    managed = "linux-src-" + hashlib.sha256(source.encode("ascii")).hexdigest()[:20]
    monkeypatch.setattr(instance, "proxies", lambda: {"groups": {
        "Proxy": {"now": "new-shared", "all": ["old-shared", "new-shared"]},
        managed: {"now": "new-node", "all": ["old-node", "new-node"]},
    }})
    monkeypatch.setattr(instance, "select", lambda group, node: calls.append((group, node)))
    instance._restore_group_selections({"Proxy": "old-shared", managed: "old-node"}, {managed})
    assert calls == [(managed, "old-node")]


def test_unmanaged_reload_drift_fails_closed(monkeypatch):
    instance = provider()
    prior = {"Proxy": {"type": "Selector", "now": "before", "all": ["before", "other"]}}
    for changed in (
        {"Proxy": {"type": "Selector", "now": "before", "all": ["before"]}},
        {"Proxy": {"type": "URLTest", "now": "before", "all": ["before", "other"]}},
        {"Proxy": {"type": "Selector", "now": "changed", "all": ["before", "other"]}},
        {"Proxy": {"type": "Selector", "now": "before", "all": ["before", "other"]}, "New": {"type": "Selector", "now": "x", "all": ["x"]}},
    ):
        monkeypatch.setattr(instance, "proxies", lambda changed=changed: {"groups": changed})
        try:
            instance._verify_unmanaged_group_state(prior, set())
        except ValueError as exc:
            assert "unmanaged" in str(exc)
        else:
            assert False
