import json
import os
import re
import shutil
import tempfile
import threading
import hashlib
import time
from contextlib import contextmanager
from ipaddress import ip_address, ip_network
from pathlib import Path

import yaml

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._:/()+-]{0,127}$")
MANAGED_PREFIX = "linux-src-"
SENSITIVE_KEY_RE = re.compile(r"(?:secret|token|password|passwd|credential|authorization|auth|subscription|subscribe)", re.I)
URL_RE = re.compile(r"(?:https?|socks5?)://[^\s\"']+", re.I)
_MAPPING_LOCKS = {}
_MAPPING_LOCKS_GUARD = threading.Lock()
_MAPPING_TRANSACTION_STATE = threading.local()


def _mapping_process_lock(path):
    key = os.path.normcase(os.path.abspath(os.fspath(path)))
    with _MAPPING_LOCKS_GUARD:
        return _MAPPING_LOCKS.setdefault(key, threading.RLock())


class Storage:
    def __init__(self, settings):
        self.settings = settings
        root = settings.config_dir.resolve()
        for candidate in (settings.profiles_dir.resolve(), settings.mapping_file.resolve()):
            if candidate != root and root not in candidate.parents:
                raise ValueError("storage paths must stay inside CONFIG_DIR")
        self.settings.config_dir.mkdir(parents=True, exist_ok=True)
        self.settings.profiles_dir.mkdir(parents=True, exist_ok=True)
        self._config_root = root
        self.mapping_file = settings.mapping_file
        self.mapping_file.parent.mkdir(parents=True, exist_ok=True)
        self.mapping_lock_file = root / ".linux-mapping.lock"
        self._route_lock = threading.RLock()

    @staticmethod
    def source_group_name(source):
        return MANAGED_PREFIX + hashlib.sha256(source.encode("ascii")).hexdigest()[:20]

    @staticmethod
    def source_rule(source, group):
        return "SRC-IP-CIDR,%s,%s" % (source, group)

    @staticmethod
    def _managed_rule_source(rule):
        if not isinstance(rule, str) or not rule.startswith("SRC-IP-CIDR,"):
            return None
        parts = rule.split(",", 2)
        if len(parts) != 3 or not parts[2].startswith(MANAGED_PREFIX):
            return None
        try:
            source = str(ip_network(parts[1], strict=False))
        except ValueError:
            raise ValueError("active Mihomo configuration contains an invalid managed source rule")
        return source if parts[2] == Storage.source_group_name(source) else None

    def normalize_mappings(self, items):
        if not isinstance(items, list) or len(items) > 256:
            raise ValueError("invalid mappings")
        parsed_items = []
        seen_sources = set()
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("each mapping must be an object")
            address = str(item.get("ip", "")).strip()
            if not address:
                raise ValueError("mapping IP is required")
            try:
                ip_address(address)
            except ValueError:
                ip_network(address, strict=False)
            source = str(ip_network(address, strict=False))
            if source in seen_sources:
                raise ValueError("duplicate canonical mapping source")
            seen_sources.add(source)
            parsed_items.append((item, address))

        normalized = []
        for item, address in parsed_items:
            has_selection = "selection" in item
            has_legacy_region = "region" in item
            has_legacy_node = "node" in item
            allowed = {"ip", "selection", "allow_cross_region_fallback"} if has_selection else {"ip", "region", "node"}
            if set(item) - allowed or (has_selection and (has_legacy_region or has_legacy_node)):
                raise ValueError("mapping fields are invalid")

            if has_selection:
                if "allow_cross_region_fallback" not in item or not isinstance(item["allow_cross_region_fallback"], bool):
                    raise ValueError("mapping fallback toggle must be a boolean")
                selection = self._normalize_selection(item["selection"])
                fallback = item["allow_cross_region_fallback"]
            else:
                region_value = item.get("region", "")
                node_value = item.get("node", "")
                if not isinstance(region_value, str):
                    raise ValueError("invalid region")
                if not isinstance(node_value, str):
                    raise ValueError("invalid node")
                region = region_value.strip()
                node = node_value.strip()
                if region:
                    region = self._normalize_selection_value(region, "region")
                if node:
                    node = self._normalize_selection_value(node, "node")
                if bool(region) == bool(node):
                    raise ValueError("legacy mapping requires exactly one region or node")
                selection = {"kind": "region" if region else "node", "value": region or node}
                fallback = True
            normalized.append({"ip": address, "selection": selection, "allow_cross_region_fallback": fallback})
        return normalized

    @staticmethod
    def _normalize_selection_value(value, label):
        if not isinstance(value, str):
            raise ValueError("invalid %s" % label)
        value = value.strip()
        if not value or len(value) > 128 or any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("invalid %s" % label)
        return value

    def _normalize_selection(self, selection):
        if not isinstance(selection, dict) or set(selection) != {"kind", "value"}:
            raise ValueError("mapping selection is invalid")
        kind = selection.get("kind")
        if kind not in {"region", "node"}:
            raise ValueError("mapping selection kind is invalid")
        return {"kind": kind, "value": self._normalize_selection_value(selection.get("value"), kind)}

    @contextmanager
    def mapping_transaction(self):
        """Serialize a full mapping/configuration transaction across processes.

        Callers should keep the context open from reading mappings through Mihomo
        apply/verification and any rollback. The lock is reentrant per thread and
        config directory, and combines an in-process RLock with an OS file lock.
        """
        key = os.path.normcase(os.path.abspath(os.fspath(self.mapping_lock_file)))
        process_lock = _mapping_process_lock(key)
        with process_lock:
            depths = getattr(_MAPPING_TRANSACTION_STATE, "depths", None)
            if depths is None:
                depths = {}
                _MAPPING_TRANSACTION_STATE.depths = depths
            if depths.get(key, 0):
                depths[key] += 1
                try:
                    yield
                finally:
                    remaining = depths[key] - 1
                    if remaining:
                        depths[key] = remaining
                    else:
                        del depths[key]
                return

            handle = self._acquire_mapping_file_lock()
            depths[key] = 1
            try:
                yield
            finally:
                del depths[key]
                self._release_mapping_file_lock(handle)

    def _mapping_lock_path(self):
        path = self.mapping_lock_file
        if path.is_symlink() or path.resolve().parent != self._config_root:
            raise ValueError("mapping transaction lock must stay inside CONFIG_DIR")
        return path

    def _acquire_mapping_file_lock(self):
        path = self._mapping_lock_path()
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        if os.name != "nt" and hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        try:
            handle = os.fdopen(descriptor, "r+b")
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                while True:
                    try:
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        time.sleep(0.05)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except BaseException:
            handle.close()
            raise
        return handle

    @staticmethod
    def _release_mapping_file_lock(handle):
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def replace_exact_host_mapping(self, ip, selection, allow_cross_region_fallback=False):
        """Atomically replace one exact host mapping while retaining CIDR mappings."""
        try:
            address = ip_address(str(ip).strip())
        except ValueError as exc:
            raise ValueError("mapping IP must be an exact host address") from exc
        if not isinstance(allow_cross_region_fallback, bool):
            raise ValueError("mapping fallback toggle must be a boolean")
        exact_source = "%s/%s" % (address, address.max_prefixlen)
        replacement = {
            "ip": exact_source,
            "selection": self._normalize_selection(selection),
            "allow_cross_region_fallback": allow_cross_region_fallback,
        }
        with self.mapping_transaction():
            with self._route_lock:
                existing = self.mappings()
                retained = [item for item in existing if str(ip_network(item["ip"], strict=False)) != exact_source]
                return self.save_mappings(retained + [replacement])

    def transform_source_routes(self, content, source, selected_node, discovered_nodes):
        return self.transform_source_route_set(content, {source: selected_node}, discovered_nodes, remove_unlisted=False)

    def transform_source_route_set(self, content, routes, discovered_nodes, remove_unlisted=True):
        try:
            document = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            raise ValueError("active Mihomo configuration contains invalid YAML") from exc
        if not isinstance(document, dict) or not isinstance(document.get("proxy-groups"), list) or not isinstance(document.get("rules"), list):
            raise ValueError("active Mihomo configuration requires proxy-groups and rules")
        nodes = [name for name in discovered_nodes if isinstance(name, str) and name != "DIRECT"]
        canonical_routes = {}
        for source, selected_node in routes.items():
            canonical_source = str(ip_network(source, strict=False))
            if selected_node != "DIRECT" and selected_node not in nodes:
                raise ValueError("selected node was not discovered")
            canonical_routes[canonical_source] = selected_node
        existing_sources = set()
        owned_groups = set()
        for rule in document["rules"]:
            managed_source = self._managed_rule_source(rule)
            if managed_source is not None:
                existing_sources.add(managed_source)
                owned_groups.add(self.source_group_name(managed_source))
        for rule in document["rules"]:
            if isinstance(rule, str) and rule.startswith("SRC-IP-CIDR,"):
                parts = rule.split(",", 2)
                if len(parts) == 3:
                    try:
                        unrelated_source = ip_network(parts[1], strict=False)
                    except ValueError:
                        raise ValueError("active Mihomo configuration contains an invalid source rule")
                    if self._managed_rule_source(rule) is None:
                        for source in canonical_routes:
                            if unrelated_source.overlaps(ip_network(source, strict=False)):
                                raise ValueError("managed source scope overlaps an unrelated SRC-IP-CIDR rule")
        desired_groups = {self.source_group_name(source): (source, node) for source, node in canonical_routes.items()}
        groups = [group for group in document["proxy-groups"] if not (remove_unlisted and isinstance(group, dict) and group.get("name") in owned_groups and group.get("name") not in desired_groups)]
        for group_name, (source, selected_node) in desired_groups.items():
            groups = [group for group in groups if not (isinstance(group, dict) and group.get("name") == group_name)]
            groups.append({"name": group_name, "type": "select", "proxies": nodes + ["DIRECT"]})
        managed_rules = []
        rules = []
        for rule in document["rules"]:
            managed_source = self._managed_rule_source(rule)
            if managed_source is not None:
                if managed_source in canonical_routes:
                    managed_rules.append(self.source_rule(managed_source, self.source_group_name(managed_source)))
                elif not remove_unlisted:
                    managed_rules.append(rule)
                continue
            rules.append(rule)
        for source in canonical_routes:
            managed = self.source_rule(source, self.source_group_name(source))
            if managed not in managed_rules:
                managed_rules.append(managed)
        managed_rules.sort(key=lambda rule: ip_network(rule.split(",", 2)[1], strict=False).prefixlen, reverse=True)
        rules = managed_rules + rules
        document["proxy-groups"] = groups
        document["rules"] = rules
        metadata_routes = [{"group": group, "source": source, "node": node, "rule": self.source_rule(source, group)} for group, (source, node) in desired_groups.items()]
        removed = [{"group": self.source_group_name(source), "source": source, "rule": self.source_rule(source, self.source_group_name(source))} for source in existing_sources - set(canonical_routes) if remove_unlisted]
        metadata = {"routes": metadata_routes, "removed": removed}
        if len(metadata_routes) == 1:
            metadata.update(metadata_routes[0])
        return yaml.safe_dump(document, sort_keys=False, allow_unicode=True), metadata

    def managed_route_sources(self, content):
        try:
            document = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            raise ValueError("active Mihomo configuration contains invalid YAML") from exc
        if not isinstance(document, dict) or not isinstance(document.get("rules"), list):
            raise ValueError("active Mihomo configuration requires rules")
        return [(source, self.source_group_name(source)) for rule in document["rules"] if (source := self._managed_rule_source(rule)) is not None]

    def remove_managed_source_route(self, content, source):
        try:
            document = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            raise ValueError("active Mihomo configuration contains invalid YAML") from exc
        if not isinstance(document, dict) or not isinstance(document.get("proxy-groups"), list) or not isinstance(document.get("rules"), list):
            raise ValueError("active Mihomo configuration requires proxy-groups and rules")
        group_name = self.source_group_name(source)
        canonical_source = str(ip_network(source, strict=False))
        owned = any(self._managed_rule_source(rule) == canonical_source for rule in document["rules"])
        if owned:
            document["proxy-groups"] = [group for group in document["proxy-groups"] if not (isinstance(group, dict) and group.get("name") == group_name)]
            document["rules"] = [rule for rule in document["rules"] if rule != self.source_rule(canonical_source, group_name)]
        return yaml.safe_dump(document, sort_keys=False, allow_unicode=True)

    def remove_source_route(self, source, reload_and_verify):
        if not self.settings.allow_source_ip_routes:
            raise PermissionError("source-IP routes are disabled")
        target = self.settings.mihomo_config_path
        if target is None or not target.exists():
            raise ValueError("active Mihomo configuration path is unavailable")
        with self.mapping_transaction():
            with self._route_lock:
                prior = target.read_text(encoding="utf-8")
                updated = self.remove_managed_source_route(prior, source)
                self._atomic_yaml(target, updated)
                metadata = {"source": source, "group": self.source_group_name(source), "remove": True, "managed_groups": [group for _, group in self.managed_route_sources(prior)]}
                try:
                    reload_and_verify(metadata)
                except Exception as exc:
                    self._atomic_yaml(target, prior)
                    try:
                        reload_and_verify({"rollback": True, "routes": [], "forbidden": [], "prior_groups": metadata.get("prior_groups", {}), "prior_group_state": metadata.get("prior_group_state", {})})
                    except Exception as rollback_exc:
                        raise ValueError("source-IP route removal failed and rollback verification failed") from rollback_exc
                    raise ValueError("source-IP route removal failed; prior configuration retained") from exc
                return metadata

    def apply_source_route(self, source, selected_node, discovered_nodes, reload_and_verify, prior_routes=None):
        if not self.settings.allow_source_ip_routes:
            raise PermissionError("source-IP routes are disabled")
        target = self.settings.mihomo_config_path
        if target is None or not target.exists():
            raise ValueError("active Mihomo configuration path is unavailable")
        with self.mapping_transaction():
            with self._route_lock:
                prior = target.read_text(encoding="utf-8")
                updated, metadata = self.transform_source_routes(prior, source, selected_node, discovered_nodes)
                metadata["routes"] = metadata.get("routes", []) + [route for route in (prior_routes or []) if route.get("source") != metadata["source"]]
                self._atomic_yaml(target, updated)
                try:
                    reload_and_verify(metadata)
                except Exception as exc:
                    self._atomic_yaml(target, prior)
                    try:
                        prior_sources = {route.get("source") for route in (prior_routes or [])}
                        forbidden = [route for route in metadata.get("routes", []) if route.get("source") not in prior_sources]
                        reload_and_verify({"rollback": True, "routes": prior_routes or [], "forbidden": forbidden, "prior_groups": metadata.get("prior_groups", {}), "prior_group_state": metadata.get("prior_group_state", {})})
                    except Exception as rollback_exc:
                        raise ValueError("source-IP route application failed and rollback verification failed") from rollback_exc
                    raise ValueError("source-IP route application failed; prior configuration retained") from exc
                return metadata

    def apply_source_route_set(self, routes, discovered_nodes, mappings, reload_and_verify, prior_routes=None):
        if not self.settings.allow_source_ip_routes:
            raise PermissionError("source-IP routes are disabled")
        target = self.settings.mihomo_config_path
        if target is None or not target.exists():
            raise ValueError("active Mihomo configuration path is unavailable")
        with self.mapping_transaction():
            with self._route_lock:
                prior_content = target.read_text(encoding="utf-8")
                prior_mapping = self.mapping_file.read_bytes() if self.mapping_file.exists() else None
                updated, metadata = self.transform_source_route_set(prior_content, routes, discovered_nodes)
                self._atomic_yaml(target, updated)
                try:
                    reload_and_verify(metadata)
                    self._write_mappings(mappings)
                except Exception as exc:
                    self._atomic_yaml(target, prior_content)
                    if prior_mapping is None:
                        if self.mapping_file.exists():
                            self.mapping_file.unlink()
                    else:
                        self._atomic_bytes(self.mapping_file, prior_mapping)
                    try:
                        prior_sources = {route.get("source") for route in (prior_routes or [])}
                        forbidden = [route for route in metadata.get("routes", []) if route.get("source") not in prior_sources]
                        reload_and_verify({"rollback": True, "routes": prior_routes or [], "forbidden": forbidden, "prior_groups": metadata.get("prior_groups", {}), "prior_group_state": metadata.get("prior_group_state", {})})
                    except Exception as rollback_exc:
                        raise ValueError("source-IP route failed and rollback verification failed") from rollback_exc
                    raise ValueError("source-IP route failed; prior mappings and configuration retained") from exc
                return metadata

    def _safe_name(self, value):
        if not isinstance(value, str) or not NAME_RE.fullmatch(value):
            raise ValueError("invalid name")
        return value

    def _safe_child(self, base, name):
        if not isinstance(name, str) or not name or "\\" in name:
            raise ValueError("unsafe path")
        relative = Path(name)
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
            raise ValueError("unsafe path")
        path = (base / relative).resolve()
        base_path = base.resolve()
        if path != base_path and base_path not in path.parents or path.suffix.lower() not in {".yaml", ".yml"}:
            raise ValueError("unsafe path")
        return path

    def profiles(self):
        base = self.settings.profiles_dir.resolve()
        profiles = []
        for path in sorted(base.rglob("*")):
            resolved = path.resolve()
            if path.is_file() and resolved != base and base in resolved.parents and path.suffix.lower() in {".yaml", ".yml"}:
                profiles.append({"name": path.relative_to(base).as_posix(), "size": path.stat().st_size})
        return profiles

    def _profile_name(self, name):
        return Path(name).as_posix()

    def read_profile(self, name):
        path = self._safe_child(self.settings.profiles_dir, name)
        if not path.exists():
            raise FileNotFoundError(name)
        return {"name": self._profile_name(name), "content": self._redacted_yaml(path.read_text(encoding="utf-8"))}

    def activate_profile(self, name):
        if not self.settings.allow_profile_activate:
            raise PermissionError("profile activation is disabled")
        path = self._safe_child(self.settings.profiles_dir, name)
        content = path.read_text(encoding="utf-8")
        try:
            yaml.safe_load(content)
        except yaml.YAMLError as exc:
            raise ValueError("profile contains invalid YAML") from exc
        target = self.settings.config_dir / "active.yaml"
        self._atomic_yaml(target, content)
        return {"name": self._profile_name(name), "active": True}

    def read_config(self):
        candidates = [self.settings.config_dir / "config.yaml", self.settings.config_dir / "active.yaml"]
        path = next((p for p in candidates if p.exists()), candidates[0])
        return {"name": path.name, "content": self._redacted_yaml(path.read_text(encoding="utf-8")) if path.exists() else ""}

    def _redact_value(self, value, sensitive=False):
        if isinstance(value, dict):
            return {key: self._redact_value(item, bool(SENSITIVE_KEY_RE.search(str(key)))) for key, item in value.items()}
        if isinstance(value, list):
            return [self._redact_value(item, sensitive) for item in value]
        if sensitive:
            return "[REDACTED]"
        if isinstance(value, str) and URL_RE.search(value):
            return URL_RE.sub("[REDACTED_URL]", value)
        return value

    def _redacted_yaml(self, content):
        try:
            parsed = yaml.safe_load(content)
        except yaml.YAMLError:
            return "[REDACTED: invalid YAML]"
        if parsed is None:
            return ""
        return yaml.safe_dump(self._redact_value(parsed), sort_keys=False, allow_unicode=True)

    def save_config(self, content):
        if not self.settings.allow_config_write:
            raise PermissionError("config writing is disabled")
        if not isinstance(content, str) or len(content.encode("utf-8")) > self.settings.max_body_bytes:
            raise ValueError("config content too large")
        try:
            yaml.safe_load(content)
        except yaml.YAMLError as exc:
            raise ValueError("configuration contains invalid YAML") from exc
        target = self.settings.config_dir / "config.yaml"
        self._atomic_yaml(target, content)
        return {"name": target.name, "saved": True}

    def _atomic_yaml(self, target, content):
        if target.exists():
            shutil.copy2(target, str(target) + ".bak")
        fd, temp = tempfile.mkstemp(prefix=".linux-", dir=str(target.parent), text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, target)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def mappings(self):
        if not self.mapping_file.exists():
            return []
        try:
            data = json.loads(self.mapping_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("mapping file is invalid") from exc
        if not isinstance(data, list):
            raise ValueError("mapping file must contain a list")
        return self.normalize_mappings(data)

    def save_mappings(self, items):
        with self.mapping_transaction():
            normalized = self.normalize_mappings(items)
            self._write_mappings(normalized)
            return normalized

    def _write_mappings(self, normalized):
        fd, temp = tempfile.mkstemp(prefix=".linux-map-", dir=str(self.mapping_file.parent), text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(normalized, handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.mapping_file)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        return normalized

    def _atomic_bytes(self, target, content):
        fd, temp = tempfile.mkstemp(prefix=".linux-map-", dir=str(target.parent))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, target)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
