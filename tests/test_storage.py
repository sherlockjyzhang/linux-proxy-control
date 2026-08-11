import json
import multiprocessing
import queue
from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml

from backend.config import Settings
from backend.storage import Storage


_PROCESS_TIMEOUT_SECONDS = 10
_BLOCK_OBSERVATION_SECONDS = 1


def _process_storage(config_dir):
    config_dir = Path(config_dir)
    settings = Settings()
    settings.config_dir = config_dir
    settings.profiles_dir = config_dir / "profiles"
    settings.mapping_file = config_dir / "ip-mappings.json"
    return Storage(settings)


def _hold_mapping_transaction(config_dir, acquired, release, results):
    try:
        with _process_storage(config_dir).mapping_transaction():
            acquired.set()
            if not release.wait(_PROCESS_TIMEOUT_SECONDS):
                raise TimeoutError("holder release timed out")
        results.put(("holder", "released", ""))
    except BaseException as exc:
        results.put(("holder", "error", repr(exc)))


def _attempt_mapping_transaction(config_dir, ready, begin, attempting, entered, results):
    try:
        store = _process_storage(config_dir)
        ready.set()
        if not begin.wait(_PROCESS_TIMEOUT_SECONDS):
            raise TimeoutError("contender start timed out")
        attempting.set()
        with store.mapping_transaction():
            entered.set()
        results.put(("contender", "entered", ""))
    except BaseException as exc:
        results.put(("contender", "error", repr(exc)))


def _raise_then_hold_after_mapping_transaction(config_dir, acquired, released, exit_worker, results):
    try:
        try:
            with _process_storage(config_dir).mapping_transaction():
                acquired.set()
                raise RuntimeError("intentional transaction failure")
        except RuntimeError as exc:
            if str(exc) != "intentional transaction failure":
                raise
        released.set()
        if not exit_worker.wait(_PROCESS_TIMEOUT_SECONDS):
            raise TimeoutError("exception worker exit timed out")
        results.put(("raiser", "released", ""))
    except BaseException as exc:
        results.put(("raiser", "error", repr(exc)))


def _enter_mapping_transaction(config_dir, entered, results):
    try:
        with _process_storage(config_dir).mapping_transaction():
            entered.set()
        results.put(("verifier", "entered", ""))
    except BaseException as exc:
        results.put(("verifier", "error", repr(exc)))


def _assert_process_finished(process, label):
    process.join(_PROCESS_TIMEOUT_SECONDS)
    assert not process.is_alive(), "%s did not exit" % label
    assert process.exitcode == 0, "%s exited with %s" % (label, process.exitcode)


def _collect_process_results(results, count):
    collected = {}
    for _ in range(count):
        try:
            name, status, detail = results.get(timeout=_PROCESS_TIMEOUT_SECONDS)
        except queue.Empty as exc:
            raise AssertionError("worker result timed out") from exc
        collected[name] = (status, detail)
    return collected


def _terminate_processes(processes):
    for process in processes:
        if process.is_alive():
            process.terminate()
    for process in processes:
        process.join(_PROCESS_TIMEOUT_SECONDS)


def storage(tmp_path, **flags):
    settings = Settings()
    settings.config_dir = Path(tmp_path) / "config"
    settings.profiles_dir = settings.config_dir / "profiles"
    settings.mapping_file = settings.config_dir / "ip-mappings.json"
    for key, value in flags.items():
        setattr(settings, key, value)
    return Storage(settings), settings


def test_mapping_rejects_empty_or_invalid_ip(tmp_path):
    store, _ = storage(tmp_path)
    with pytest.raises(ValueError, match="mapping IP"):
        store.save_mappings([{"ip": ""}])
    with pytest.raises(ValueError):
        store.save_mappings([{"ip": "not-an-ip"}])


def test_mapping_rejects_duplicate_canonical_sources(tmp_path):
    store, _ = storage(tmp_path)
    with pytest.raises(ValueError, match="duplicate canonical"):
        store.save_mappings([{"ip": "192.168.3.148"}, {"ip": "192.168.3.148/32"}])


def test_mappings_normalize_legacy_without_rewriting_read_file(tmp_path):
    store, settings = storage(tmp_path)
    legacy = '[{"ip": "192.168.3.148", "region": "East"}]\n'
    settings.mapping_file.write_text(legacy, encoding="utf-8")
    assert store.mappings() == [{"ip": "192.168.3.148", "selection": {"kind": "region", "value": "East"}, "allow_cross_region_fallback": True}]
    assert settings.mapping_file.read_text(encoding="utf-8") == legacy


@pytest.mark.parametrize("legacy, selection", [
    ({"ip": "192.168.3.148", "region": "", "node": "east-1"}, {"kind": "node", "value": "east-1"}),
    ({"ip": "192.168.3.148", "region": "East", "node": ""}, {"kind": "region", "value": "East"}),
])
def test_mappings_normalize_legacy_region_or_node_value(tmp_path, legacy, selection):
    store, settings = storage(tmp_path)
    original = json.dumps([legacy])
    settings.mapping_file.write_text(original, encoding="utf-8")

    assert store.mappings() == [{
        "ip": "192.168.3.148",
        "selection": selection,
        "allow_cross_region_fallback": True,
    }]
    assert settings.mapping_file.read_text(encoding="utf-8") == original


@pytest.mark.parametrize("item", [
    {"ip": "192.168.3.148", "region": "", "node": ""},
    {"ip": "192.168.3.148", "region": "East", "node": "east-1"},
    {"ip": "192.168.3.148", "selection": {"kind": "region", "value": "East"}},
    {"ip": "192.168.3.148", "selection": {"kind": "region", "value": "East"}, "allow_cross_region_fallback": "false"},
])
def test_mappings_reject_invalid_legacy_or_canonical_records(tmp_path, item):
    store, _ = storage(tmp_path)
    with pytest.raises(ValueError):
        store.save_mappings([item])


@pytest.mark.parametrize("item", [
    {"ip": "192.168.3.148", "region": ""},
    {"ip": "192.168.3.148", "node": ""},
    {"ip": "192.168.3.148", "region": "", "node": ""},
    {"ip": "192.168.3.148", "region": None},
    {"ip": "192.168.3.148", "node": None},
])
def test_mappings_reject_blank_legacy_records_on_read(tmp_path, item):
    store, settings = storage(tmp_path)
    settings.mapping_file.write_text(json.dumps([item]), encoding="utf-8")
    with pytest.raises(ValueError):
        store.mappings()


def test_mappings_reject_blank_canonical_selection_on_read(tmp_path):
    store, settings = storage(tmp_path)
    settings.mapping_file.write_text(json.dumps([{
        "ip": "192.168.3.148",
        "selection": {"kind": "node", "value": None},
        "allow_cross_region_fallback": False,
    }]), encoding="utf-8")
    with pytest.raises(ValueError):
        store.mappings()


def test_exact_host_replace_preserves_unrelated_cidr_mappings(tmp_path):
    store, _ = storage(tmp_path)
    store.save_mappings([{"ip": "10.0.0.0/8", "region": "East"}])
    mappings = store.replace_exact_host_mapping("10.1.2.3", {"kind": "node", "value": "east-1"})
    assert mappings == [
        {"ip": "10.0.0.0/8", "selection": {"kind": "region", "value": "East"}, "allow_cross_region_fallback": True},
        {"ip": "10.1.2.3/32", "selection": {"kind": "node", "value": "east-1"}, "allow_cross_region_fallback": False},
    ]


def test_mapping_transaction_is_reentrant_confined_and_releases_after_exception(tmp_path):
    store, settings = storage(tmp_path)
    other = Storage(settings)
    assert store.mapping_lock_file == settings.config_dir.resolve() / ".rpb5-mapping.lock"
    with pytest.raises(RuntimeError, match="rollback"):
        with store.mapping_transaction():
            assert store.mapping_lock_file.exists()
            with other.mapping_transaction():
                other.save_mappings([{
                    "ip": "192.168.3.148",
                    "selection": {"kind": "region", "value": "East"},
                    "allow_cross_region_fallback": False,
                }])
            raise RuntimeError("rollback")
    with other.mapping_transaction():
        assert other.mapping_lock_file.exists()


def test_mapping_transaction_rejects_external_lock_symlink(tmp_path):
    store, _ = storage(tmp_path)
    outside = Path(tmp_path) / "outside.lock"
    outside.write_bytes(b"x")
    try:
        store.mapping_lock_file.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable")
    with pytest.raises(ValueError, match="inside CONFIG_DIR"):
        with store.mapping_transaction():
            pass


def test_mapping_transaction_blocks_independent_process_until_release(tmp_path):
    _, settings = storage(tmp_path)
    context = multiprocessing.get_context("spawn")
    holder_acquired = context.Event()
    holder_release = context.Event()
    contender_ready = context.Event()
    contender_begin = context.Event()
    contender_attempting = context.Event()
    contender_entered = context.Event()
    results = context.Queue()
    holder = context.Process(
        target=_hold_mapping_transaction,
        args=(str(settings.config_dir), holder_acquired, holder_release, results),
    )
    contender = context.Process(
        target=_attempt_mapping_transaction,
        args=(str(settings.config_dir), contender_ready, contender_begin, contender_attempting, contender_entered, results),
    )
    started = []
    try:
        holder.start()
        started.append(holder)
        assert holder_acquired.wait(_PROCESS_TIMEOUT_SECONDS), "holder did not acquire the transaction lock"
        contender.start()
        started.append(contender)
        assert contender_ready.wait(_PROCESS_TIMEOUT_SECONDS), "contender did not initialize"
        contender_begin.set()
        assert contender_attempting.wait(_PROCESS_TIMEOUT_SECONDS), "contender did not attempt the transaction lock"
        assert not contender_entered.wait(_BLOCK_OBSERVATION_SECONDS), "contender entered before holder released the lock"
        holder_release.set()
        assert contender_entered.wait(_PROCESS_TIMEOUT_SECONDS), "contender did not enter after holder released the lock"
        _assert_process_finished(holder, "holder")
        _assert_process_finished(contender, "contender")
        assert _collect_process_results(results, 2) == {
            "holder": ("released", ""),
            "contender": ("entered", ""),
        }
    finally:
        holder_release.set()
        contender_begin.set()
        _terminate_processes(started)
        results.close()
        results.join_thread()


def test_mapping_transaction_releases_cross_process_lock_after_exception(tmp_path):
    _, settings = storage(tmp_path)
    context = multiprocessing.get_context("spawn")
    raiser_acquired = context.Event()
    raiser_released = context.Event()
    raiser_exit = context.Event()
    verifier_entered = context.Event()
    results = context.Queue()
    raiser = context.Process(
        target=_raise_then_hold_after_mapping_transaction,
        args=(str(settings.config_dir), raiser_acquired, raiser_released, raiser_exit, results),
    )
    verifier = context.Process(
        target=_enter_mapping_transaction,
        args=(str(settings.config_dir), verifier_entered, results),
    )
    started = []
    try:
        raiser.start()
        started.append(raiser)
        assert raiser_acquired.wait(_PROCESS_TIMEOUT_SECONDS), "exception worker did not acquire the transaction lock"
        assert raiser_released.wait(_PROCESS_TIMEOUT_SECONDS), "exception worker did not release the transaction lock"
        assert raiser.is_alive(), "exception worker exited before release was verified"
        verifier.start()
        started.append(verifier)
        assert verifier_entered.wait(_PROCESS_TIMEOUT_SECONDS), "verifier could not acquire the lock after exception release"
        raiser_exit.set()
        _assert_process_finished(raiser, "exception worker")
        _assert_process_finished(verifier, "verifier")
        assert _collect_process_results(results, 2) == {
            "raiser": ("released", ""),
            "verifier": ("entered", ""),
        }
    finally:
        raiser_exit.set()
        _terminate_processes(started)
        results.close()
        results.join_thread()


def test_mapping_mutation_entry_points_use_transaction_when_standalone(tmp_path, monkeypatch):
    store, settings = storage(tmp_path, allow_source_ip_routes=True)
    settings.mihomo_config_path = settings.config_dir / "active.yaml"
    settings.mihomo_config_path.write_text(
        "proxy-groups: [{name: Proxy, type: select, proxies: [old]}]\nrules: [\"MATCH,Proxy\"]\n",
        encoding="utf-8",
    )
    calls = []
    original = store.mapping_transaction

    @contextmanager
    def tracked_transaction():
        calls.append("enter")
        try:
            with original():
                yield
        finally:
            calls.append("exit")

    monkeypatch.setattr(store, "mapping_transaction", tracked_transaction)
    store.save_mappings([{
        "ip": "192.168.3.148",
        "selection": {"kind": "region", "value": "East"},
        "allow_cross_region_fallback": False,
    }])
    store.apply_source_route("192.168.3.148/32", "node-a", ["node-a"], lambda metadata: None)
    store.apply_source_route_set(
        {"192.168.3.148/32": "node-a"},
        ["node-a"],
        [{
            "ip": "192.168.3.148/32",
            "selection": {"kind": "region", "value": "East"},
            "allow_cross_region_fallback": False,
        }],
        lambda metadata: None,
    )
    store.remove_source_route("192.168.3.148/32", lambda metadata: None)
    assert calls == ["enter", "exit", "enter", "exit", "enter", "exit", "enter", "exit"]


def test_apply_source_route_set_reenters_mapping_transaction(tmp_path):
    store, settings = storage(tmp_path, allow_source_ip_routes=True)
    settings.mihomo_config_path = settings.config_dir / "active.yaml"
    settings.mihomo_config_path.write_text(
        "proxy-groups: [{name: Proxy, type: select, proxies: [old]}]\nrules: [\"MATCH,Proxy\"]\n",
        encoding="utf-8",
    )
    with store.mapping_transaction():
        metadata = store.apply_source_route_set(
            {"192.168.3.148/32": "node-a"},
            ["node-a"],
            [{
                "ip": "192.168.3.148/32",
                "selection": {"kind": "region", "value": "East"},
                "allow_cross_region_fallback": False,
            }],
            lambda value: None,
        )
    assert metadata["source"] == "192.168.3.148/32"


def test_source_transform_does_not_own_lookalike_group(tmp_path):
    store, _ = storage(tmp_path)
    content = """proxy-groups:
  - name: rpb5-src-user
    type: select
    proxies: [old]
rules:
  - SRC-IP-CIDR,10.0.0.0/8,rpb5-src-user
"""
    updated, _ = store.transform_source_routes(content, "192.168.3.148/32", "node-a", ["node-a"])
    document = yaml.safe_load(updated)
    assert any(group["name"] == "rpb5-src-user" for group in document["proxy-groups"])
    assert "SRC-IP-CIDR,10.0.0.0/8,rpb5-src-user" in document["rules"]


def test_source_route_transform_isolated_and_unicode_safe(tmp_path):
    store, _ = storage(tmp_path)
    content = """proxy-groups:
  - name: Proxy
    type: select
    proxies: [old]
  - name: unrelated
    type: select
    proxies: [old]
rules:
  - MATCH,Proxy
"""
    updated, metadata = store.transform_source_routes(content, "192.168.3.148/32", "韩国-01", ["韩国-01", "日本-01"])
    assert metadata["group"].startswith("rpb5-src-")
    assert "SRC-IP-CIDR,192.168.3.148/32," + metadata["group"] in updated
    assert "name: Proxy" in updated and "name: unrelated" in updated
    assert store.save_mappings([{"ip": "192.168.3.148", "region": "韩国"}])[0]["selection"] == {"kind": "region", "value": "韩国"}


def test_source_route_update_preserves_other_managed_route(tmp_path):
    store, _ = storage(tmp_path)
    content = 'proxy-groups: [{name: Proxy, type: select, proxies: [old]}]\nrules: ["MATCH,Proxy"]\n'
    content, route_a = store.transform_source_routes(content, "192.168.3.148/32", "node-a", ["node-a", "node-b"])
    content, route_b = store.transform_source_routes(content, "192.168.3.0/24", "node-b", ["node-a", "node-b"])
    content, updated_a = store.transform_source_routes(content, "192.168.3.148/32", "node-b", ["node-a", "node-b"])
    assert updated_a["group"] == route_a["group"]
    updated_document = yaml.safe_load(content)
    updated_groups = {group["name"]: group for group in updated_document["proxy-groups"]}
    assert updated_groups["Proxy"]["proxies"] == ["old"]
    assert updated_groups[route_b["group"]]["proxies"] == ["node-a", "node-b", "DIRECT"]
    assert route_b["rule"] in updated_document["rules"]
    removed = store.remove_managed_source_route(content, "192.168.3.148/32")
    removed_document = yaml.safe_load(removed)
    removed_groups = {group["name"]: group for group in removed_document["proxy-groups"]}
    assert route_a["group"] not in removed_groups
    assert route_a["rule"] not in removed_document["rules"]
    assert removed_groups[route_b["group"]]["proxies"] == ["node-a", "node-b", "DIRECT"]
    assert route_b["rule"] in removed_document["rules"]
    assert removed_groups["Proxy"]["proxies"] == ["old"]
    assert "MATCH,Proxy" in removed_document["rules"]


def test_overlapping_managed_sources_are_ordered_most_specific_first(tmp_path):
    store, _ = storage(tmp_path)
    content = 'proxy-groups: [{name: Proxy, type: select, proxies: [old]}]\nrules: ["MATCH,Proxy"]\n'
    updated, metadata = store.transform_source_route_set(content, {
        "192.168.3.0/24": "node-a",
        "192.168.3.148/32": "node-b",
    }, ["node-a", "node-b"])
    document = yaml.safe_load(updated)
    assert document["rules"][:2] == [
        metadata["routes"][1]["rule"],
        metadata["routes"][0]["rule"],
    ]


def test_config_write_is_atomic_and_backed_up(tmp_path):
    store, settings = storage(tmp_path, allow_config_write=True)
    store.save_config("mode: rule\n")
    store.save_config("mode: global\n")
    assert (settings.config_dir / "config.yaml").read_text() == "mode: global\n"
    assert (settings.config_dir / "config.yaml.bak").read_text() == "mode: rule\n"
    with pytest.raises(ValueError, match="invalid YAML"):
        store.save_config("mode: [broken\n")
    assert (settings.config_dir / "config.yaml").read_text() == "mode: global\n"


def test_profile_activation_requires_switch_and_stays_in_config_dir(tmp_path):
    store, settings = storage(tmp_path)
    profile = settings.profiles_dir / "home.yaml"
    profile.write_text("mode: rule\n", encoding="utf-8")
    with pytest.raises(PermissionError):
        store.activate_profile(profile.name)
    settings.allow_profile_activate = True
    assert store.activate_profile(profile.name) == {"name": "home.yaml", "active": True}
    assert (settings.config_dir / "active.yaml").exists()


def test_storage_rejects_paths_outside_config_dir(tmp_path):
    settings = Settings()
    settings.config_dir = Path(tmp_path) / "config"
    settings.profiles_dir = Path(tmp_path) / "outside"
    settings.mapping_file = settings.config_dir / "mappings.json"
    with pytest.raises(ValueError, match="inside CONFIG_DIR"):
        Storage(settings)


def test_config_and_profile_reads_redact_nested_sensitive_values(tmp_path):
    store, settings = storage(tmp_path)
    content = """secret: real-secret\nproxy-provider:\n  url: https://example.invalid/sub?token=real-token\nproxy-groups:\n  - name: Auto\n    headers:\n      Authorization: Bearer real-auth\n"""
    settings.allow_config_write = True
    store.save_config(content)
    (settings.profiles_dir / "home.yaml").write_text(content, encoding="utf-8")
    for result in (store.read_config(), store.read_profile("home.yaml")):
        assert "real-secret" not in result["content"]
        assert "real-token" not in result["content"]
        assert "real-auth" not in result["content"]
        assert "[REDACTED]" in result["content"]


def test_profiles_discovers_yaml_files_recursively_and_returns_relative_names(tmp_path):
    store, settings = storage(tmp_path)
    (settings.profiles_dir / "nested").mkdir()
    (settings.profiles_dir / "home.yaml").write_text("mode: rule\n", encoding="utf-8")
    (settings.profiles_dir / "nested" / "work.yml").write_text("mode: global\n", encoding="utf-8")
    (settings.profiles_dir / "ignored.txt").write_text("not yaml", encoding="utf-8")

    assert [item["name"] for item in store.profiles()] == ["home.yaml", "nested/work.yml"]
    assert store.read_profile("nested/work.yml")["name"] == "nested/work.yml"
    settings.allow_profile_activate = True
    assert store.activate_profile("nested/work.yml")["name"] == "nested/work.yml"


def test_profiles_empty_or_missing_directory_is_reported_without_files(tmp_path):
    settings = Settings()
    settings.config_dir = Path(tmp_path) / "config"
    settings.profiles_dir = settings.config_dir / "missing" / "profiles"
    settings.mapping_file = settings.config_dir / "mappings.json"
    store = Storage(settings)
    assert store.profiles() == []


def test_nested_profile_path_escape_is_rejected(tmp_path):
    store, _ = storage(tmp_path)
    for name in ("../secret.yaml", "/tmp/secret.yaml", "nested/../../secret.yaml", "nested/file.txt"):
        with pytest.raises(ValueError, match="unsafe path"):
            store.read_profile(name)


def test_profiles_does_not_discover_or_read_directory_external_symlink(tmp_path):
    store, settings = storage(tmp_path)
    outside = Path(tmp_path) / "outside.yaml"
    outside.write_text("secret: outside\n", encoding="utf-8")
    link = settings.profiles_dir / "outside.yaml"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable")
    assert store.profiles() == []
    with pytest.raises(ValueError, match="unsafe path"):
        store.read_profile("outside.yaml")


def test_internal_symlink_keeps_alias_name_across_profile_operations(tmp_path):
    store, settings = storage(tmp_path, allow_profile_activate=True)
    real = settings.profiles_dir / "nested" / "real.yaml"
    real.parent.mkdir()
    real.write_text("mode: rule\n", encoding="utf-8")
    alias = settings.profiles_dir / "alias.yaml"
    try:
        alias.symlink_to(real)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable")

    assert [item["name"] for item in store.profiles()] == ["alias.yaml", "nested/real.yaml"]
    assert store.read_profile("alias.yaml")["name"] == "alias.yaml"
    assert store.activate_profile("alias.yaml")["name"] == "alias.yaml"
