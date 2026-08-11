import threading
from contextlib import nullcontext
from ipaddress import ip_address, ip_network

from flask import Flask, jsonify, request, send_from_directory
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import Settings
from .provider import MihomoProvider
from .selection import DIRECT_NODE_NAME, choose_node
from .storage import Storage


def create_app(settings=None):
    settings = settings or Settings()
    app = Flask(__name__, static_folder="../frontend", static_url_path="")
    # Flask is loopback-only; nginx supplies the one trusted client address.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)
    app.config["MAX_CONTENT_LENGTH"] = settings.max_body_bytes
    provider = MihomoProvider(settings)
    storage = Storage(settings)
    switch_lock = threading.Lock()

    def json_error(exc, code=400):
        return jsonify(error=str(exc)), code

    def request_body():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise ValueError("request body must be a JSON object")
        return body

    def mapping_node(mapping):
        selection = mapping.get("selection", {})
        return selection.get("value") if selection.get("kind") == "node" else None

    def source_for_mapping(mapping):
        return str(ip_network(mapping["ip"], strict=False))

    def decision_payload(chosen, reason, mode, applied, simulated, probed):
        return {
            "node": chosen.get("name") if chosen else None,
            "reason": reason,
            "mode": mode,
            "applied": applied,
            "simulated": simulated,
            "probed": probed,
        }

    def unavailable_decision(reason="real node latency is unprobed"):
        return decision_payload(None, reason, "unavailable", False, False, False)

    def resolve_decision(mapping, requested_ip, mode=None, applied=None, simulated=None):
        if not provider.demo and mode is None:
            return unavailable_decision()
        if mode is None:
            mode = "demo"
        if applied is None:
            applied = False
        if simulated is None:
            simulated = mode == "demo"
        nodes = provider.nodes_with_delays()
        chosen, reason = choose_node(nodes, mapping, requested_ip)
        return decision_payload(chosen, reason, mode, applied, simulated, True)

    def decisions_for_mappings(mappings, decisions=None):
        decisions = decisions or {}
        result = []
        for mapping in mappings:
            source = source_for_mapping(mapping)
            decision = decisions.get(source)
            if decision is None:
                decision = resolve_decision(mapping, str(ip_network(mapping["ip"], strict=False).network_address))
            result.append({"ip": mapping["ip"], "decision": decision})
        return result

    def public_apply_result(result):
        return {key: value for key, value in result.items() if key != "_decision_by_source"}

    def validate_mapping_targets(normalized, catalog):
        nodes = catalog.get("nodes", [])
        known = {node["name"] for node in nodes if isinstance(node, dict) and isinstance(node.get("name"), str)}
        regions = {
            node["region"]
            for node in nodes
            if isinstance(node, dict) and isinstance(node.get("region"), str) and node["region"]
        }
        regions.update(region for region in catalog.get("regions", []) if isinstance(region, str) and region)
        for item in normalized:
            selection = item["selection"]
            if selection["kind"] == "region":
                if selection["value"] not in regions:
                    raise ValueError("mapping region was not discovered")
            elif selection["value"] == DIRECT_NODE_NAME:
                raise ValueError("DIRECT is reserved and cannot be selected as a mapping node")
            elif selection["value"] not in known:
                raise ValueError("mapping node was not discovered")
        return catalog

    def catalog_payload(catalog=None):
        catalog = provider.proxies() if catalog is None else catalog
        nodes = []
        for item in catalog.get("nodes", []):
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                continue
            node = {"name": item["name"]}
            if isinstance(item.get("region"), str) and item["region"]:
                node["region"] = item["region"]
            nodes.append(node)
        nodes.sort(key=lambda node: node["name"])
        regions = {node["region"] for node in nodes if "region" in node}
        regions.update(region for region in catalog.get("regions", []) if isinstance(region, str) and region)
        regions = sorted(regions)
        return {"regions": regions, "nodes": nodes, "catalog": {"regions": regions, "nodes": nodes}}

    def apply_mappings(normalized, catalog=None):
        """Persist the mapping set, applying all source routes as one operation when enabled."""
        if catalog is None:
            catalog = provider.proxies()
        validate_mapping_targets(normalized, catalog)
        known = {node["name"] for node in catalog["nodes"]}
        if provider.demo:
            decision_by_source = {}
            for item in normalized:
                address = str(ip_network(item["ip"], strict=False).network_address)
                decision_by_source[source_for_mapping(item)] = resolve_decision(item, address, "demo", False, True)
            storage.save_mappings(normalized)
            return {"applied": False, "simulated": True, "mode": "demo", "_decision_by_source": decision_by_source}
        if not settings.allow_source_ip_routes:
            decision_by_source = {
                source_for_mapping(item): unavailable_decision("real source routes are disabled; node latency is unprobed")
                for item in normalized
            }
            storage.save_mappings(normalized)
            return {"applied": False, "disabled": True, "mode": "real", "_decision_by_source": decision_by_source}
        if settings.mihomo_config_path is None or not settings.mihomo_config_path.exists():
            raise ValueError("active Mihomo configuration path is unavailable")

        delayed_nodes = provider.nodes_with_delays()
        routes = {}
        decision_by_source = {}
        for item in normalized:
            source = str(ip_network(item["ip"], strict=False))
            address = str(ip_network(item["ip"], strict=False).network_address)
            chosen, reason = choose_node(delayed_nodes, item, address)
            routes[source] = chosen["name"]
            decision_by_source[source] = decision_payload(chosen, reason, "real", True, False, True)
        prior_routes = []
        for source, group in storage.managed_route_sources(settings.mihomo_config_path.read_text(encoding="utf-8")):
            current = catalog.get("groups", {}).get(group)
            if current and current.get("now"):
                prior_routes.append({"group": group, "source": source, "node": current["now"], "rule": storage.source_rule(source, group)})
        # DIRECT is a generated fallback target, never a selectable catalog node.
        metadata = storage.apply_source_route_set(routes, known | {DIRECT_NODE_NAME}, normalized, provider.reload_and_verify_source_route, prior_routes)
        return {"applied": True, "mode": "real", "routes": metadata["routes"], "_decision_by_source": decision_by_source}

    def exact_mapping(mappings, client_ip):
        source = str(ip_network(client_ip + ("/32" if ip_address(client_ip).version == 4 else "/128"), strict=False))
        return next((item for item in mappings if str(ip_network(item.get("ip", ""), strict=False)) == source), None)

    @app.get("/api/health")
    def health():
        status = provider.health()
        status.update({"allow_config_write": settings.allow_config_write, "allow_profile_activate": settings.allow_profile_activate, "allow_source_ip_routes": settings.allow_source_ip_routes, "proxy_port": 7890})
        return jsonify(status)

    @app.get("/api/proxies")
    def proxies():
        try:
            return jsonify(provider.proxies())
        except Exception as exc:
            return json_error(exc, 502)

    @app.post("/api/proxy-groups/<path:group>/select")
    def select(group):
        body = request.get_json(silent=True) or {}
        node = body.get("node")
        if not isinstance(node, str) or len(node) > 128:
            return json_error("node is required")
        if not switch_lock.acquire(blocking=False):
            return json_error("another proxy switch is in progress", 409)
        try:
            return jsonify(provider.select(group, node))
        except ValueError as exc:
            return json_error(exc)
        except Exception as exc:
            return json_error(exc, 502)
        finally:
            switch_lock.release()

    @app.post("/api/proxies/<path:node>/delay")
    def delay(node):
        try:
            return jsonify(provider.delay(node))
        except ValueError as exc:
            return json_error(exc)
        except Exception as exc:
            return json_error(exc, 502)

    @app.post("/api/proxies/delay")
    def batch_delay():
        try:
            return jsonify({"nodes": provider.batch_delay_results()})
        except Exception as exc:
            return json_error(exc, 502)

    @app.get("/api/profiles")
    def profiles():
        return jsonify({"profiles": storage.profiles(), "profiles_dir": str(settings.profiles_dir), "read_only": not settings.allow_profile_activate})

    @app.get("/api/profiles/<path:name>")
    def profile(name):
        try:
            return jsonify(storage.read_profile(name))
        except (ValueError, FileNotFoundError) as exc:
            return json_error(exc, 404)

    @app.post("/api/profiles/<path:name>/activate")
    def activate(name):
        try:
            return jsonify(storage.activate_profile(name))
        except PermissionError as exc:
            return json_error(exc, 403)
        except (ValueError, FileNotFoundError) as exc:
            return json_error(exc, 400)

    @app.get("/api/config")
    def config():
        return jsonify({**storage.read_config(), "read_only": not settings.allow_config_write})

    @app.put("/api/config")
    def save_config():
        try:
            body = request_body()
            return jsonify(storage.save_config(body.get("content", "")))
        except PermissionError as exc:
            return json_error(exc, 403)
        except ValueError as exc:
            return json_error(exc)
        except Exception as exc:
            return json_error(exc, 422)

    @app.get("/api/mappings")
    def mappings():
        try:
            saved = storage.mappings()
            return jsonify({
                "mappings": saved,
                "effective_decisions": decisions_for_mappings(saved),
                **catalog_payload(),
            })
        except ValueError as exc:
            return json_error(exc, 422)

    @app.put("/api/mappings")
    def save_mappings():
        if not switch_lock.acquire(blocking=False):
            return json_error("another source route update is in progress", 409)
        try:
            with storage.mapping_transaction():
                mappings = request_body().get("mappings")
                normalized = storage.normalize_mappings(mappings)
                result = apply_mappings(normalized)
                return jsonify({
                    "mappings": normalized,
                    "effective_decisions": decisions_for_mappings(normalized, result.get("_decision_by_source")),
                    **catalog_payload(),
                    **public_apply_result(result),
                })
        except PermissionError as exc:
            return json_error(exc, 403)
        except (AttributeError, ValueError) as exc:
            return json_error(exc)
        finally:
            switch_lock.release()

    @app.get("/api/me")
    def me():
        try:
            client_ip = str(ip_address(request.remote_addr))
            mappings = storage.mappings()
            effective = _mapping_for_ip(mappings, client_ip)
            try:
                catalog = catalog_payload()
            except Exception:
                catalog = {"regions": [], "nodes": [], "catalog": {"regions": [], "nodes": []}}
            return jsonify({
                "ip": client_ip,
                "mapping": exact_mapping(mappings, client_ip),
                "effective": effective,
                "effective_decision": resolve_decision(effective or {}, client_ip),
                **catalog,
            })
        except ValueError as exc:
            return json_error(exc, 422)

    @app.put("/api/me")
    def save_me():
        if not switch_lock.acquire(blocking=False):
            return json_error("another source route update is in progress", 409)
        try:
            body = request_body()
            if set(body) - {"selection", "allow_cross_region_fallback"}:
                raise ValueError("user mapping fields are invalid")
            client_ip = str(ip_address(request.remote_addr))
            fallback = body.get("allow_cross_region_fallback", False)
            with storage.mapping_transaction():
                prior_mappings = storage.mappings()
                candidate = storage.normalize_mappings([{
                    "ip": client_ip,
                    "selection": body.get("selection"),
                    "allow_cross_region_fallback": fallback,
                }])[0]
                catalog = provider.proxies()
                validate_mapping_targets([candidate], catalog)
                normalized = storage.replace_exact_host_mapping(client_ip, candidate["selection"], fallback)
                own_mapping = exact_mapping(normalized, client_ip)
                try:
                    result = apply_mappings(normalized, catalog)
                except Exception:
                    # Keep restoration inside the cross-process transaction.
                    storage.save_mappings(prior_mappings)
                    raise
                effective_decision = result.get("_decision_by_source", {}).get(source_for_mapping(own_mapping))
                if effective_decision is None:
                    effective_decision = resolve_decision(own_mapping, client_ip)
                return jsonify({
                    "ip": client_ip,
                    "mapping": own_mapping,
                    "effective": own_mapping,
                    "effective_decision": effective_decision,
                    **catalog_payload(catalog),
                    **public_apply_result(result),
                })
        except (AttributeError, ValueError) as exc:
            return json_error(exc)
        finally:
            switch_lock.release()

    @app.post("/api/assign")
    def assign():
        body = request_body()
        requested_ip = body.get("ip")
        manual_supplied = "manual_node" in body
        manual_node = body.get("manual_node")
        if not switch_lock.acquire(blocking=False):
            return json_error("another source route update is in progress", 409)
        try:
            source_scope = None
            if requested_ip:
                if not isinstance(requested_ip, str) or not requested_ip.strip():
                    raise ValueError("ip must be a valid address or CIDR")
                raw_ip = requested_ip.strip()
                if "/" in raw_ip:
                    source_network = ip_network(raw_ip, strict=False)
                    requested_ip = str(source_network.network_address)
                else:
                    address = ip_address(raw_ip)
                    requested_ip = str(address)
                    source_network = ip_network(address.exploded + ("/32" if address.version == 4 else "/128"), strict=False)
                source_scope = str(source_network)
            transaction = storage.mapping_transaction() if source_scope else nullcontext()
            with transaction:
                catalog = None
                if manual_supplied:
                    if not isinstance(manual_node, str) or not manual_node.strip() or len(manual_node) > 128:
                        raise ValueError("manual_node must be a discovered node")
                    catalog = provider.proxies()
                    known = {node.get("name") for node in catalog.get("nodes", []) if isinstance(node, dict)}
                    if manual_node not in known:
                        raise ValueError("manual node was not discovered")
                mapping = _mapping_for_ip(storage.mappings(), requested_ip)
                chosen, reason = choose_node(provider.nodes_with_delays(), mapping, requested_ip, manual_node if manual_supplied else None)
                if manual_supplied:
                    reason = "manual node: " + reason
                if not source_scope:
                    return jsonify({"node": chosen, "reason": reason, "ip": None, "applied": False, "source_scope": None, "mode": "recommendation"})
                if mapping:
                    source_scope = str(ip_network(mapping.get("ip", source_scope), strict=False))
                if provider.demo:
                    return jsonify({"node": chosen, "reason": reason, "ip": requested_ip, "applied": False, "simulated": True, "source_scope": source_scope, "mode": "demo"})
                catalog = catalog or provider.proxies()
                prior_routes = []
                if settings.mihomo_config_path is not None and settings.mihomo_config_path.exists():
                    for prior_source, group in storage.managed_route_sources(settings.mihomo_config_path.read_text(encoding="utf-8")):
                        current = catalog.get("groups", {}).get(group)
                        if current and current.get("now"):
                            prior_routes.append({"group": group, "source": prior_source, "node": current["now"], "rule": storage.source_rule(prior_source, group)})
                metadata = storage.apply_source_route(source_scope, chosen["name"], [node["name"] for node in catalog["nodes"]] + [DIRECT_NODE_NAME], provider.reload_and_verify_source_route, prior_routes)
                metadata.update({"node": chosen["name"]})
                return jsonify({"node": chosen, "reason": reason, "ip": requested_ip, "applied": True, "source_scope": source_scope, "application": metadata, "mode": "real"})
        except ValueError as exc:
            return json_error(exc)
        except Exception as exc:
            return json_error(exc, 502)
        finally:
            switch_lock.release()

    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.get("/admin")
    def admin_index():
        return send_from_directory(app.static_folder, "index.html")

    return app


def _mapping_for_ip(mappings, requested_ip):
    if not requested_ip:
        return None
    address = ip_address(requested_ip)
    matches = []
    for mapping in mappings:
        try:
            network = ip_network(mapping.get("ip", ""), strict=False)
        except (TypeError, ValueError):
            continue
        if address.version == network.version and address in network:
            matches.append((network.prefixlen, mapping))
    return max(matches, key=lambda item: item[0])[1] if matches else None


app = create_app()

if __name__ == "__main__":
    settings = Settings()
    app.run(host=settings.host, port=settings.port)
