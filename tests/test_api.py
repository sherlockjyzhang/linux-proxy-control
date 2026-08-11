import json
from pathlib import Path

import pytest

from backend.app import create_app
from backend.config import Settings
from backend.provider import MihomoProvider
from backend.storage import Storage


def client(tmp_path, **overrides):
    settings = Settings()
    settings.demo_mode = True
    settings.config_dir = Path(tmp_path) / "config"
    settings.profiles_dir = settings.config_dir / "profiles"
    settings.mapping_file = settings.config_dir / "mappings.json"
    for key, value in overrides.items():
        setattr(settings, key, value)
    return create_app(settings).test_client()


def headers():
    return {}


def test_health_contract_without_authentication(tmp_path):
    c = client(tmp_path)
    body = c.get("/api/health").get_json()
    assert body["mode"] == "demo" and body["proxy_port"] == 7890
    assert "secret" not in str(body).lower()


def test_root_and_admin_serve_the_dashboard(tmp_path):
    c = client(tmp_path)
    assert c.get("/").status_code == 200
    assert c.get("/admin").status_code == 200


def test_admin_mapping_persistence_uses_canonical_shape(tmp_path):
    c = client(tmp_path)
    mapping = {
        "ip": "192.168.1.42",
        "selection": {"kind": "node", "value": "Tokyo-01"},
        "allow_cross_region_fallback": True,
    }
    response = c.put("/api/mappings", headers=headers(), json={"mappings": [mapping]})
    assert response.status_code == 200
    saved = c.get("/api/mappings", headers=headers()).get_json()
    assert saved["mappings"] == [mapping]
    assert saved["regions"] == ["Korea", "Singapore", "Tokyo", "United States"]
    assert saved["catalog"]["nodes"] == saved["nodes"]
    assert saved["effective_decisions"][0]["decision"]["node"] == "Tokyo-01"
    assert saved["effective_decisions"][0]["decision"]["mode"] == "demo"
    assert saved["effective_decisions"][0]["decision"]["applied"] is False
    assert saved["effective_decisions"][0]["decision"]["simulated"] is True
    assert saved["effective_decisions"][0]["decision"]["probed"] is True
    assert c.put("/api/mappings", headers=headers(), json={"mappings":[{"ip":"not-an-ip"}]}).status_code == 400


@pytest.mark.parametrize("legacy, selection", [
    ({"ip": "192.168.1.46", "region": "", "node": "Tokyo-01"}, {"kind": "node", "value": "Tokyo-01"}),
    ({"ip": "192.168.1.47", "region": "Tokyo", "node": ""}, {"kind": "region", "value": "Tokyo"}),
])
def test_get_mappings_reads_legacy_shape_as_canonical(tmp_path, legacy, selection):
    config_dir = Path(tmp_path) / "config"
    config_dir.mkdir(parents=True)
    mapping_file = config_dir / "mappings.json"
    original = json.dumps([legacy])
    mapping_file.write_text(original, encoding="utf-8")
    c = client(tmp_path)

    response = c.get("/api/mappings")

    assert response.status_code == 200
    assert response.get_json()["mappings"] == [{
        "ip": legacy["ip"],
        "selection": selection,
        "allow_cross_region_fallback": True,
    }]
    assert mapping_file.read_text(encoding="utf-8") == original


def test_get_me_reads_legacy_mapping_without_normalize_failure(tmp_path):
    config_dir = Path(tmp_path) / "config"
    config_dir.mkdir(parents=True)
    mapping_file = config_dir / "mappings.json"
    mapping_file.write_text(json.dumps([{
        "ip": "192.168.1.48",
        "region": "",
        "node": "Tokyo-01",
    }]), encoding="utf-8")
    c = client(tmp_path)

    response = c.get("/api/me", headers={"X-Forwarded-For": "192.168.1.48"})

    assert response.status_code == 200
    body = response.get_json()
    assert body["mapping"] == {
        "ip": "192.168.1.48",
        "selection": {"kind": "node", "value": "Tokyo-01"},
        "allow_cross_region_fallback": True,
    }
    assert body["effective"] == body["mapping"]


def test_mapping_writes_reject_unknown_regions_before_persisting(tmp_path):
    c = client(tmp_path)
    mapping = {
        "ip": "192.168.1.43",
        "selection": {"kind": "region", "value": "Forged Region"},
        "allow_cross_region_fallback": True,
    }
    assert c.put("/api/mappings", json={"mappings": [mapping]}).status_code == 400
    assert c.get("/api/mappings").get_json()["mappings"] == []
    response = c.put("/api/me", headers={"X-Forwarded-For": "192.168.1.43"}, json={
        "selection": {"kind": "region", "value": "Forged Region"},
    })
    assert response.status_code == 400
    assert c.get("/api/me", headers={"X-Forwarded-For": "192.168.1.43"}).get_json()["mapping"] is None
    assert c.put("/api/mappings", json={"mappings": [{
        "ip": "192.168.1.45",
        "selection": {"kind": "node", "value": "DIRECT"},
        "allow_cross_region_fallback": False,
    }]}).status_code == 400


def test_me_uses_one_forwarded_ipv4_hop_and_shares_admin_mapping_state(tmp_path):
    c = client(tmp_path)
    forwarded = {"X-Forwarded-For": "192.168.1.42"}
    response = c.put("/api/me", headers=forwarded, json={
        "selection": {"kind": "region", "value": "Korea"},
    })
    assert response.status_code == 200
    body = response.get_json()
    assert body["ip"] == "192.168.1.42"
    assert body["mapping"] == {
        "ip": "192.168.1.42/32",
        "selection": {"kind": "region", "value": "Korea"},
        "allow_cross_region_fallback": False,
    }
    assert c.get("/api/me", headers=forwarded).get_json()["mapping"] == body["mapping"]
    assert c.get("/api/mappings").get_json()["mappings"] == [body["mapping"]]
    mine = c.get("/api/me", headers=forwarded).get_json()
    assert mine["effective"] == body["mapping"]
    assert mine["effective_decision"]["node"] == "Korea-01"
    assert mine["effective_decision"]["mode"] == "demo"
    assert mine["effective_decision"]["applied"] is False
    assert mine["effective_decision"]["simulated"] is True
    assert mine["effective_decision"]["probed"] is True
    assert any(node["name"] == "Korea-01" for node in mine["catalog"]["nodes"])


def test_me_uses_one_forwarded_ipv6_hop(tmp_path):
    c = client(tmp_path)
    response = c.put("/api/me", headers={"X-Forwarded-For": "2001:db8::42"}, json={
        "selection": {"kind": "node", "value": "Korea-01"},
        "allow_cross_region_fallback": True,
    })
    assert response.status_code == 200
    assert response.get_json()["mapping"]["ip"] == "2001:db8::42/128"
    decision = response.get_json()["effective_decision"]
    assert decision["node"] == "Korea-01"
    assert decision["mode"] == "demo" and decision["simulated"] is True


def test_mapping_and_me_reject_malformed_bodies(tmp_path):
    c = client(tmp_path)
    assert c.put("/api/mappings", json=[]).status_code == 400
    assert c.put("/api/me", json={"selection": {"kind": "region"}}).status_code == 400
    assert c.put("/api/me", json={"ip": "192.168.1.42", "selection": {"kind": "region", "value": "Tokyo"}}).status_code == 400


def test_mapping_writes_hold_the_shared_transaction_context(tmp_path, monkeypatch):
    events = []
    depth = {"value": 0}
    persistence_depths = []

    class FakeTransaction:
        def __enter__(self):
            depth["value"] += 1
            events.append(("enter", depth["value"]))

        def __exit__(self, exc_type, exc_value, traceback):
            events.append(("exit", depth["value"]))
            depth["value"] -= 1
            return False

    original_save_mappings = Storage.save_mappings

    def tracked_save_mappings(self, items):
        assert depth["value"] > 0
        persistence_depths.append(depth["value"])
        return original_save_mappings(self, items)

    monkeypatch.setattr("backend.app.Storage.mapping_transaction", lambda self: FakeTransaction())
    monkeypatch.setattr("backend.app.Storage.save_mappings", tracked_save_mappings)
    c = client(tmp_path)
    admin_response = c.put("/api/mappings", json={"mappings": []})
    assert admin_response.status_code == 200
    assert admin_response.get_json()["simulated"] is True
    assert events[0] == ("enter", 1)
    assert events[-1] == ("exit", 1)
    assert depth["value"] == 0
    assert persistence_depths and min(persistence_depths) >= 1

    events.clear()
    persistence_depths.clear()
    user_response = c.put("/api/me", headers={"X-Forwarded-For": "192.168.1.44"}, json={
        "selection": {"kind": "region", "value": "Korea"},
    })
    assert user_response.status_code == 200
    assert user_response.get_json()["simulated"] is True
    assert events[0] == ("enter", 1)
    assert events[-1] == ("exit", 1)
    assert depth["value"] == 0
    assert persistence_depths and min(persistence_depths) >= 1


def test_config_read_only_and_safe_path(tmp_path):
    c = client(tmp_path)
    assert c.get("/api/config", headers=headers()).status_code == 200
    assert c.put("/api/config", headers=headers(), json={"content":"a: 1"}).status_code == 403
    assert c.get("/api/profiles/../secret.yaml", headers=headers()).status_code in {400,404}


def test_profiles_api_reports_scan_directory_without_secret(tmp_path):
    c = client(tmp_path)
    response = c.get("/api/profiles", headers=headers())
    body = response.get_json()
    assert response.status_code == 200
    assert body["profiles"] == []
    assert body["profiles_dir"].endswith("config\\profiles") or body["profiles_dir"].endswith("config/profiles")
    assert "secret" not in str(body).lower()


def test_nested_profile_api_uses_list_name_for_read_and_activate(tmp_path):
    profiles_dir = Path(tmp_path) / "config" / "profiles" / "nested"
    profiles_dir.mkdir(parents=True)
    profile = profiles_dir / "work.yml"
    profile.write_text("mode: rule\n", encoding="utf-8")
    c = client(tmp_path, allow_profile_activate=True)

    listed = c.get("/api/profiles").get_json()["profiles"]
    assert listed == [{"name": "nested/work.yml", "size": profile.stat().st_size}]
    read = c.get("/api/profiles/nested/work.yml")
    assert read.status_code == 200
    assert read.get_json()["name"] == "nested/work.yml"
    activated = c.post("/api/profiles/nested/work.yml/activate")
    assert activated.status_code == 200
    assert activated.get_json()["name"] == "nested/work.yml"


def test_assign_contract(tmp_path):
    c = client(tmp_path)
    c.put("/api/mappings", json={"mappings": [{
        "ip": "192.168.1.42",
        "selection": {"kind": "region", "value": "Tokyo"},
        "allow_cross_region_fallback": False,
    }]})
    body = c.post("/api/assign", headers=headers(), json={"ip":"192.168.1.42"}).get_json()
    assert body["node"]["name"] == "Tokyo-01"
    assert "reason" in body
    assert body["mode"] == "demo" and body["applied"] is False and body["simulated"] is True


def test_assign_manual_node_remains_explicit_and_validated(tmp_path):
    c = client(tmp_path)
    body = c.post("/api/assign", json={"manual_node": "Korea-01"}).get_json()
    assert body["node"]["name"] == "Korea-01"
    assert "manual node" in body["reason"]
    rejected = c.post("/api/assign", json={"manual_node": "not-discovered"})
    assert rejected.status_code == 400
    assert "discovered" in rejected.get_json()["error"]


def test_assign_without_source_is_recommendation_only(tmp_path):
    body = client(tmp_path).post("/api/assign", json={}).get_json()
    assert body["applied"] is False and body["source_scope"] is None


def test_assign_rejects_invalid_cidr_suffix(tmp_path):
    response = client(tmp_path).post("/api/assign", json={"ip": "192.168.1.42/99"})
    assert response.status_code == 400
    assert response.get_json()["error"]


def test_batch_delay_returns_all_discovered_nodes(tmp_path):
    c = client(tmp_path)
    response = c.post("/api/proxies/delay")
    assert response.status_code == 200
    assert {node["name"] for node in response.get_json()["nodes"]} == {"Tokyo-01", "Tokyo-02", "Singapore-01", "Korea-01", "US-01"}


def test_demo_catalog_includes_korea(tmp_path):
    catalog = client(tmp_path).get("/api/proxies").get_json()
    nodes = catalog["nodes"]
    assert next(node for node in nodes if node["name"] == "Korea-01")["region"] == "Korea"
    assert catalog["groups"]["Korea"]["all"] == ["Korea-01"]


def test_cidr_mapping_uses_longest_prefix_for_ipv4_and_ipv6(tmp_path):
    c = client(tmp_path)
    response = c.put("/api/mappings", headers=headers(), json={"mappings": [
        {"ip": "192.168.0.0/16", "selection": {"kind": "node", "value": "Singapore-01"}, "allow_cross_region_fallback": True},
        {"ip": "192.168.1.0/24", "selection": {"kind": "node", "value": "Tokyo-02"}, "allow_cross_region_fallback": True},
        {"ip": "2001:db8::/32", "selection": {"kind": "node", "value": "Singapore-01"}, "allow_cross_region_fallback": True},
        {"ip": "2001:db8:1::/48", "selection": {"kind": "node", "value": "Tokyo-02"}, "allow_cross_region_fallback": True},
    ]})
    assert response.status_code == 200
    assert c.post("/api/assign", headers=headers(), json={"ip": "192.168.1.42"}).get_json()["node"]["name"] == "Tokyo-02"
    assert c.post("/api/assign", headers=headers(), json={"ip": "2001:db8:1::42"}).get_json()["node"]["name"] == "Tokyo-02"


def test_real_assign_probes_delays_without_controller(tmp_path, monkeypatch):
    class FakeProvider(MihomoProvider):
        def __init__(self, settings):
            super().__init__(settings)
            self.demo = False

        def health(self):
            return {"mode": "connected", "connected": True, "controller": "test"}

        def proxies(self):
            return {"groups": {}, "nodes": [
                {"name": "slow", "region": "Tokyo", "delay_ms": None, "available": True},
                {"name": "fast", "region": "Tokyo", "delay_ms": None, "available": True},
            ]}

        def delay(self, node):
            return {"name": node, "delay_ms": 900 if node == "slow" else 120, "available": node == "fast"}

    monkeypatch.setattr("backend.app.MihomoProvider", FakeProvider)
    settings = Settings()
    settings.demo_mode = False
    settings.config_dir = Path(tmp_path) / "config"
    settings.profiles_dir = settings.config_dir / "profiles"
    settings.mapping_file = settings.config_dir / "mappings.json"
    result = create_app(settings).test_client().post("/api/assign", headers=headers(), json={})
    assert result.status_code == 200
    assert result.get_json()["node"]["name"] == "fast"


def test_source_assign_holds_mapping_transaction_through_route_application(tmp_path, monkeypatch):
    events = []
    active = {"value": False}

    class FakeTransaction:
        def __enter__(self):
            active["value"] = True
            events.append("enter")

        def __exit__(self, exc_type, exc_value, traceback):
            events.append("exit")
            active["value"] = False
            return False

    class FakeProvider(MihomoProvider):
        def __init__(self, settings):
            super().__init__(settings)
            self.demo = False

        def proxies(self):
            return {"groups": {}, "nodes": [
                {"name": "fast", "region": "Tokyo", "delay_ms": 100, "available": True},
            ]}

        def nodes_with_delays(self):
            return self.proxies()["nodes"]

    def fake_apply(self, source, selected_node, discovered_nodes, reload_and_verify, prior_routes=None):
        assert active["value"] is True
        events.append("apply")
        return {"routes": [{"source": source, "node": selected_node}]}

    monkeypatch.setattr("backend.app.MihomoProvider", FakeProvider)
    monkeypatch.setattr("backend.app.Storage.mapping_transaction", lambda self: FakeTransaction())
    monkeypatch.setattr("backend.app.Storage.apply_source_route", fake_apply)
    settings = Settings()
    settings.demo_mode = False
    settings.allow_source_ip_routes = True
    settings.config_dir = Path(tmp_path) / "config"
    settings.profiles_dir = settings.config_dir / "profiles"
    settings.mapping_file = settings.config_dir / "mappings.json"
    result = create_app(settings).test_client().post("/api/assign", json={"ip": "192.0.2.7"})
    assert result.status_code == 200
    assert events == ["enter", "apply", "exit"]


def test_assign_uses_direct_when_no_nodes_are_usable(tmp_path, monkeypatch):
    class FakeProvider(MihomoProvider):
        def __init__(self, settings):
            super().__init__(settings)
            self.demo = False

        def proxies(self):
            return {"groups": {}, "nodes": []}

        def nodes_with_delays(self):
            return []

    monkeypatch.setattr("backend.app.MihomoProvider", FakeProvider)
    settings = Settings()
    settings.demo_mode = False
    settings.config_dir = Path(tmp_path) / "config"
    settings.profiles_dir = settings.config_dir / "profiles"
    settings.mapping_file = settings.config_dir / "mappings.json"
    result = create_app(settings).test_client().post("/api/assign", json={})
    assert result.status_code == 200
    assert result.get_json()["node"]["name"] == "DIRECT"


def test_me_effective_decision_reports_direct_without_usable_demo_nodes(tmp_path, monkeypatch):
    class EmptyDemoProvider(MihomoProvider):
        def __init__(self, settings):
            super().__init__(settings)
            self.demo = True

        def proxies(self):
            return {"groups": {}, "nodes": []}

        def nodes_with_delays(self):
            return []

    monkeypatch.setattr("backend.app.MihomoProvider", EmptyDemoProvider)
    settings = Settings()
    settings.demo_mode = True
    settings.config_dir = Path(tmp_path) / "config"
    settings.profiles_dir = settings.config_dir / "profiles"
    settings.mapping_file = settings.config_dir / "mappings.json"
    response = create_app(settings).test_client().get("/api/me", headers={"X-Forwarded-For": "192.0.2.8"})
    assert response.status_code == 200
    decision = response.get_json()["effective_decision"]
    assert decision["node"] == "DIRECT"
    assert decision["mode"] == "demo"
    assert decision["applied"] is False and decision["simulated"] is True
    assert decision["probed"] is True


def test_real_me_effective_decision_is_unavailable_without_delay_probe(tmp_path, monkeypatch):
    class UnprobedProvider(MihomoProvider):
        def __init__(self, settings):
            super().__init__(settings)
            self.demo = False

        def proxies(self):
            return {"groups": {}, "nodes": [{"name": "Tokyo-01", "region": "Tokyo"}]}

        def nodes_with_delays(self):
            raise AssertionError("GET /api/me must not probe real nodes")

    monkeypatch.setattr("backend.app.MihomoProvider", UnprobedProvider)
    settings = Settings()
    settings.demo_mode = False
    settings.config_dir = Path(tmp_path) / "config"
    settings.profiles_dir = settings.config_dir / "profiles"
    settings.mapping_file = settings.config_dir / "mappings.json"
    response = create_app(settings).test_client().get("/api/me")
    assert response.status_code == 200
    decision = response.get_json()["effective_decision"]
    assert decision == {
        "node": None,
        "reason": "real node latency is unprobed",
        "mode": "unavailable",
        "applied": False,
        "simulated": False,
        "probed": False,
    }
