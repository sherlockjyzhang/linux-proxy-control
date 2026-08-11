LATENCY_LIMIT_MS = 600
PREFERRED_NODE_LATENCY_LIMIT_MS = 300
DIRECT_NODE_NAME = "DIRECT"


def _is_usable(node, limit=LATENCY_LIMIT_MS):
    return (
        node.get("name") != DIRECT_NODE_NAME
        and node.get("available")
        and isinstance(node.get("delay_ms"), (int, float))
        and node["delay_ms"] <= limit
    )


def _lowest(nodes, limit=LATENCY_LIMIT_MS):
    usable = [node for node in nodes if _is_usable(node, limit)]
    return min(usable, key=lambda node: (node["delay_ms"], node["name"])) if usable else None


def _direct(reason):
    return {"name": DIRECT_NODE_NAME, "region": None, "delay_ms": 0, "available": True, "type": "direct"}, reason


def choose_node(nodes, mapping=None, requested_ip=None, manual_node=None):
    """Choose a node from a canonical mapping, or DIRECT when policy requires it.

    ``manual_node`` remains supported for the existing admin assignment endpoint. It
    behaves as a temporary node-mode selection with cross-region fallback enabled.
    """
    mapping = mapping or {}
    by_name = {node.get("name"): node for node in nodes if isinstance(node, dict) and isinstance(node.get("name"), str) and node.get("name") != DIRECT_NODE_NAME}

    is_manual_selection = bool(manual_node)
    if is_manual_selection:
        mapping = {
            "selection": {"kind": "node", "value": manual_node},
            "allow_cross_region_fallback": True,
        }

    selection = mapping.get("selection")
    if not selection:
        chosen = _lowest(nodes)
        if chosen:
            return chosen, "automatic selection chose global lowest latency"
        return _direct("no usable nodes; selected DIRECT")

    kind = selection.get("kind")
    value = selection.get("value")
    allow_global = mapping.get("allow_cross_region_fallback") is True

    if kind == "region":
        chosen = _lowest((node for node in nodes if node.get("region") == value))
        if chosen:
            return chosen, "automatic selection chose lowest latency in mapped region"
        chosen = _lowest(nodes) if allow_global else None
        if chosen:
            return chosen, "mapped region has no usable nodes; selected global fallback"
        return _direct("mapped region has no usable nodes; selected DIRECT")

    if kind == "node":
        requested = by_name.get(value)
        if requested and _is_usable(requested, PREFERRED_NODE_LATENCY_LIMIT_MS):
            if is_manual_selection:
                return requested, "manual node has acceptable latency"
            return requested, "requested node has acceptable latency"
        region = requested.get("region") if requested else None
        chosen = _lowest((node for node in nodes if region is not None and node.get("region") == region))
        if chosen:
            if is_manual_selection:
                return chosen, "manual node unavailable; selected same-region fallback in its catalog region"
            return chosen, "requested node unavailable; selected same-region fallback in its catalog region"
        chosen = _lowest(nodes) if allow_global else None
        if chosen:
            if is_manual_selection:
                return chosen, "manual node and catalog region unavailable; selected global fallback"
            return chosen, "requested node and catalog region unavailable; selected global fallback"
        if is_manual_selection:
            return _direct("manual node and catalog region unavailable; selected DIRECT")
        return _direct("requested node and catalog region unavailable; selected DIRECT")

    raise ValueError("mapping selection is invalid")
