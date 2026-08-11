import copy
from concurrent.futures import ThreadPoolExecutor
import logging
import threading
import time
from urllib.parse import urljoin

import urllib.request
import urllib.error
import urllib.parse

from .regions import region_from_proxy_name


logger = logging.getLogger(__name__)


DEMO_PROXIES = {
    "Auto": {"type": "Selector", "now": "Tokyo-01", "all": ["Tokyo-01", "Tokyo-02", "Singapore-01", "Korea-01", "US-01"]},
    "Tokyo": {"type": "Selector", "now": "Tokyo-01", "all": ["Tokyo-01", "Tokyo-02"]},
    "Singapore": {"type": "Selector", "now": "Singapore-01", "all": ["Singapore-01", "Tokyo-02"]},
    "Korea": {"type": "Selector", "now": "Korea-01", "all": ["Korea-01"]},
}
DEMO_NODES = [
    {"name": "Tokyo-01", "region": "Tokyo", "delay_ms": 92, "available": True, "type": "Shadowsocks"},
    {"name": "Tokyo-02", "region": "Tokyo", "delay_ms": 168, "available": True, "type": "Vmess"},
    {"name": "Singapore-01", "region": "Singapore", "delay_ms": 220, "available": True, "type": "Trojan"},
    {"name": "Korea-01", "region": "Korea", "delay_ms": 148, "available": True, "type": "Shadowsocks"},
    {"name": "US-01", "region": "United States", "delay_ms": 410, "available": True, "type": "WireGuard"},
]
NON_NODE_TYPES = {"Direct", "Reject", "Compatible", "Pass", "PassRule", "RejectDrop"}
DYNAMIC_GROUP_TYPES = {"URLTest", "Fallback", "LoadBalance"}


class MihomoProvider:
    def __init__(self, settings):
        self.settings = settings
        self.demo = settings.demo_mode
        self.demo_proxies = copy.deepcopy(DEMO_PROXIES)
        self._delay_cache = {}
        self._probe_lock = threading.RLock()
        self._delay_cache_lock = threading.RLock()
        self._node_probe_locks = {}

    def _public_controller_url(self):
        """Return controller location without credentials or non-location parts."""
        raw_url = str(self.settings.controller_url)
        try:
            parsed = urllib.parse.urlsplit(raw_url)
            if not parsed.scheme or not parsed.hostname:
                return "controller"
            try:
                port = parsed.port
            except ValueError:
                port = None
            hostname = parsed.hostname
            if ":" in hostname and not hostname.startswith("["):
                hostname = "[" + hostname + "]"
            netloc = hostname + (":" + str(port) if port is not None else "")
            return parsed.scheme + "://" + netloc
        except (TypeError, ValueError):
            return "controller"

    def _request(self, method, path, payload=None, timeout=3):
        data = None
        headers = {"Accept": "application/json"}
        if self.settings.mihomo_secret:
            headers["Authorization"] = "Bearer " + self.settings.mihomo_secret
        if payload is not None:
            import json
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(urljoin(self.settings.controller_url + "/", path.lstrip("/")), data=data, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            import json
            body = response.read().decode("utf-8").strip()
            return json.loads(body) if body else {}

    def health(self):
        controller = self._public_controller_url()
        if self.demo:
            return {"mode": "demo", "connected": False, "controller": controller}
        try:
            self._request("GET", "/version")
            return {"mode": "connected", "connected": True, "controller": controller}
        except Exception:
            logger.warning("mihomo_health_check_failed")
            return {"mode": "disconnected", "connected": False, "controller": controller, "error": "controller request failed"}

    def proxies(self):
        if self.demo:
            group_states = {
                name: self._normalize_group_state(item)
                for name, item in self.demo_proxies.items()
            }
            return {
                "groups": copy.deepcopy(self.demo_proxies),
                "group_states": group_states,
                "nodes": copy.deepcopy(DEMO_NODES),
            }
        raw = self._request("GET", "/proxies")
        groups = {}
        group_states = {}
        nodes = []
        for name, item in raw.get("proxies", {}).items():
            item_type = item.get("type")
            members = item.get("all")
            valid_members = isinstance(members, list) and all(isinstance(member, str) for member in members)
            known_group = item_type in {"Selector", "URLTest", "Fallback", "LoadBalance"}
            if valid_members:
                group_states[name] = {
                    "type": item_type,
                    "now": item.get("now"),
                    "all": list(members),
                }
            if name not in {"DIRECT", "REJECT", "GLOBAL"} and item_type not in NON_NODE_TYPES and (known_group or valid_members):
                groups[name] = {"type": item_type, "now": item.get("now"), "all": list(members) if valid_members else []}
            elif name not in {"DIRECT", "REJECT", "GLOBAL"} and item.get("type") not in NON_NODE_TYPES:
                metadata = item.get("metadata") or {}
                region = region_from_proxy_name(name) or metadata.get("region") or item.get("region") or self.settings.region_map.get(name)
                nodes.append({"name": name, "region": region or "Unmapped", "delay_ms": None, "available": True, "type": item.get("type", "unknown")})
        return {"groups": groups, "group_states": group_states, "nodes": nodes}

    def select(self, group, node):
        if self.demo:
            if group not in self.demo_proxies or node not in self.demo_proxies[group]["all"]:
                raise ValueError("unknown proxy group or node")
            self.demo_proxies[group]["now"] = node
            return {"group": group, "node": node, "mode": "demo"}
        discovered = self.proxies().get("groups", {})
        if group not in discovered or node not in discovered[group].get("all", []):
            raise ValueError("unknown discovered proxy group or node")
        return self._request("PUT", "/proxies/" + urllib.parse.quote(group, safe=""), {"name": node})

    def reload_and_verify_source_route(self, metadata):
        if self.demo:
            raise ValueError("source-IP routes are simulated and unavailable in demo mode")
        if self.settings.mihomo_config_path is None:
            raise ValueError("active Mihomo configuration path is unavailable")
        prior_groups = metadata.get("prior_groups")
        prior_group_state = metadata.get("prior_group_state")
        if prior_groups is None or prior_group_state is None:
            catalog = self.proxies()
            prior_catalog = catalog.get("group_states") or catalog.get("groups", {})
            prior_group_state = {name: self._normalize_group_state(item) for name, item in prior_catalog.items()}
            prior_groups = {name: state["now"] for name, state in prior_group_state.items() if state["now"] is not None}
            metadata["prior_groups"] = prior_groups
            metadata["prior_group_state"] = prior_group_state
        self._request("PUT", "/configs", {"path": str(self.settings.mihomo_config_path), "force": True})
        if metadata.get("rollback"):
            managed_groups = {route["group"] for route in metadata.get("routes", [])}
            managed_groups.update(route["group"] for route in metadata.get("forbidden", []))
            self._verify_unmanaged_group_state(prior_group_state, managed_groups)
            self._restore_group_selections(prior_groups, managed_groups)
            self._verify_source_routes(metadata.get("routes", []))
            self._verify_absent_source_routes(metadata.get("forbidden", []))
            self._verify_group_selections(prior_groups, managed_groups)
            return {"verified": True, "rollback": True}
        if metadata.get("remove"):
            managed_groups = set(metadata.get("managed_groups", [metadata["group"]]))
            self._verify_unmanaged_group_state(prior_group_state, managed_groups)
            self._restore_group_selections(prior_groups, managed_groups - {metadata["group"]})
            self._verify_group_selections(prior_groups, {metadata["group"]})
            groups = self.proxies().get("groups", {})
            if metadata["group"] in groups:
                raise ValueError("Mihomo retained the removed dedicated source group")
            rules = self._request("GET", "/rules")
            values = rules.get("rules", []) if isinstance(rules, dict) else []
            if any(self._source_rule_matches(item, metadata["source"], metadata["group"]) for item in values):
                raise ValueError("Mihomo retained the removed source rule")
            return {"verified": True, "removed": True}
        managed_groups = {route["group"] for route in metadata.get("routes", [])}
        managed_groups.update(route["group"] for route in metadata.get("removed", []))
        self._verify_unmanaged_group_state(prior_group_state, managed_groups)
        removed_groups = {route["group"] for route in metadata.get("removed", [])}
        self._restore_group_selections(prior_groups, managed_groups - removed_groups)
        self._verify_source_routes(metadata.get("routes", []), select=True)
        self._verify_group_selections(prior_groups, managed_groups)
        for route in metadata.get("removed", []):
            groups = self.proxies().get("groups", {})
            if route["group"] in groups:
                raise ValueError("Mihomo retained a removed dedicated source group")
            rules = self._request("GET", "/rules")
            values = rules.get("rules", []) if isinstance(rules, dict) else []
            if any(self._source_rule_matches(item, route["source"], route["group"]) for item in values):
                raise ValueError("Mihomo retained a removed managed source rule")
        return {"verified": True}

    def _restore_group_selections(self, prior_groups, managed_groups):
        current = self.proxies().get("groups", {})
        for group, node in prior_groups.items():
            if group in managed_groups and group in current and current[group].get("now") != node:
                self.select(group, node)

    @staticmethod
    def _normalize_group_state(item):
        members = item.get("all")
        return {"type": item.get("type"), "now": item.get("now"), "all": list(members) if isinstance(members, list) else members}

    @staticmethod
    def _normalize_reload_group_state(item):
        state = MihomoProvider._normalize_group_state(item)
        if state["type"] in DYNAMIC_GROUP_TYPES:
            state["now"] = None
        return state

    def _verify_unmanaged_group_state(self, prior_group_state, managed_groups):
        catalog = self.proxies()
        current_catalog = catalog.get("group_states") or catalog.get("groups", {})
        def without_managed_members(item):
            state = self._normalize_reload_group_state(item)
            if isinstance(state["all"], list):
                state["all"] = [member for member in state["all"] if member not in managed_groups]
            return state

        expected = {name: without_managed_members(state) for name, state in prior_group_state.items() if name not in managed_groups}
        observed = {name: without_managed_members(item) for name, item in current_catalog.items() if name not in managed_groups}
        if expected != observed:
            raise ValueError("controller reload changed unmanaged proxy-group state")

    def _verify_group_selections(self, expected, ignored=None):
        ignored = ignored or set()
        catalog = self.proxies()
        groups = catalog.get("group_states") or catalog.get("groups", {})
        for group, node in expected.items():
            if group in ignored:
                continue
            state = groups.get(group)
            if isinstance(state, dict) and state.get("type") in DYNAMIC_GROUP_TYPES:
                continue
            if state is None or state.get("now") != node:
                raise ValueError("Mihomo did not restore a prior proxy-group selection")

    def _verify_source_routes(self, routes, select=False):
        for route in routes:
            if not self._is_managed_route(route):
                raise ValueError("source route target is not an exact RPb5-managed group")
            if select:
                self.select(route["group"], route["node"])
        groups = self.proxies().get("groups", {})
        for route in routes:
            group = route["group"]
            if group not in groups or groups[group].get("now") != route["node"]:
                raise ValueError("Mihomo did not verify the dedicated source group")
        rules = self._request("GET", "/rules")
        values = rules.get("rules", []) if isinstance(rules, dict) else []
        for route in routes:
            if not any(self._source_rule_matches(item, route["source"], route["group"]) for item in values):
                raise ValueError("Mihomo did not verify the managed source rule")

    @staticmethod
    def _is_managed_route(route):
        try:
            from ipaddress import ip_network
            source = str(ip_network(route["source"], strict=False))
            import hashlib
            expected = "rpb5-src-" + hashlib.sha256(source.encode("ascii")).hexdigest()[:20]
            return route.get("group") == expected
        except (KeyError, TypeError, ValueError, UnicodeEncodeError):
            return False

    def _verify_absent_source_routes(self, routes):
        groups = self.proxies().get("groups", {})
        values = self._request("GET", "/rules").get("rules", [])
        for route in routes:
            if route["group"] in groups or any(self._source_rule_matches(item, route["source"], route["group"]) for item in values):
                raise ValueError("Mihomo retained a newly-created managed source route after rollback")

    @staticmethod
    def _source_rule_matches(item, source, group):
        def same_source(value):
            try:
                from ipaddress import ip_network
                return str(ip_network(value, strict=False)) == source
            except (TypeError, ValueError):
                return False
        if isinstance(item, str):
            parts = item.split(",", 2)
            return len(parts) == 3 and parts[0] == "SRC-IP-CIDR" and same_source(parts[1]) and parts[2] == group
        if isinstance(item, dict):
            rule_type = item.get("type")
            payload = item.get("payload", item.get("source"))
            target = item.get("proxy", item.get("target"))
            return isinstance(rule_type, str) and rule_type.replace("-", "").lower() == "srcipcidr" and same_source(payload) and target == group
        return False

    def delay(self, node, discovered=None):
        return self._delay_unlocked(node, discovered)

    def _delay_unlocked(self, node, discovered=None):
        if not self.demo:
            discovered = discovered if discovered is not None else {item["name"] for item in self.proxies().get("nodes", [])}
            if node not in discovered:
                raise ValueError("unknown discovered node")
        if self.demo:
            found = next((item for item in DEMO_NODES if item["name"] == node), None)
            if not found:
                raise ValueError("unknown node")
            return {"name": node, "delay_ms": found["delay_ms"], "available": found["available"], "mode": "demo"}
        with self._delay_cache_lock:
            node_lock = self._node_probe_locks.setdefault(node, threading.RLock())
        with node_lock:
            now = time.monotonic()
            with self._delay_cache_lock:
                cached = self._delay_cache.get(node)
                if cached and now - cached[0] <= self.settings.delay_cache_ttl_ms / 1000:
                    return copy.deepcopy(cached[1])
            try:
                timeout_ms = self.settings.delay_timeout_ms
                path = "/proxies/" + urllib.parse.quote(node, safe="") + "/delay?" + urllib.parse.urlencode({"url": self.settings.delay_url, "timeout": timeout_ms})
                result = self._request("GET", path, timeout=max(timeout_ms / 1000, 0.1))
                delay_ms = result.get("delay") if isinstance(result, dict) else None
                if not isinstance(delay_ms, (int, float)):
                    raise ValueError("Mihomo delay response did not contain a numeric delay")
                delay_ms = round(delay_ms)
                result = {"name": node, "delay_ms": delay_ms, "available": delay_ms <= 600, "error": "latency exceeds 600ms" if delay_ms > 600 else None}
                with self._delay_cache_lock:
                    self._delay_cache[node] = (time.monotonic(), result)
                return result
            except urllib.error.HTTPError as exc:
                return {"name": node, "delay_ms": None, "available": False, "error": "HTTP error %s" % exc.code}
            except urllib.error.URLError as exc:
                reason = str(exc.reason).lower()
                return {"name": node, "delay_ms": None, "available": False, "error": "timeout" if "timed out" in reason or "timeout" in reason else "controller request failed"}
            except TimeoutError:
                return {"name": node, "delay_ms": None, "available": False, "error": "timeout"}
            except Exception:
                logger.warning("mihomo_delay_probe_failed")
                return {"name": node, "delay_ms": None, "available": False, "error": "controller request failed"}

    def nodes_with_delays(self):
        return self.batch_delay_results()

    def batch_delay_results(self):
        nodes = self.proxies().get("nodes", [])
        if self.demo:
            return nodes
        discovered = {node["name"] for node in nodes}
        if not nodes:
            return []

        def probe(node):
            try:
                if type(self).delay is not MihomoProvider.delay:
                    result = self.delay(node["name"])
                else:
                    result = self._delay_unlocked(node["name"], discovered)
                return {**node, **result}
            except Exception:
                logger.warning("mihomo_batch_delay_probe_failed")
                return {
                    **node,
                    "delay_ms": None,
                    "available": False,
                    "error": "controller request failed",
                }

        worker_count = min(max(1, int(self.settings.delay_workers)), len(nodes))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            return list(executor.map(probe, nodes))
