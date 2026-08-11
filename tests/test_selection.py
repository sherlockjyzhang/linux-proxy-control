from backend.selection import DIRECT_NODE_NAME, choose_node


def nodes():
    return [
        {"name": "manual", "region": "East", "delay_ms": 700, "available": True},
        {"name": "east-fast", "region": "East", "delay_ms": 120, "available": True},
        {"name": "east-slow", "region": "East", "delay_ms": 350, "available": True},
        {"name": "west", "region": "West", "delay_ms": 80, "available": True},
    ]


def test_manual_node_wins():
    source = [{**x, "delay_ms": 100} if x["name"] == "manual" else x for x in nodes()]
    n, reason = choose_node(source, {"region": "East"}, manual_node="manual")
    assert n["name"] == "manual" and "manual" in reason


def test_manual_falls_back_same_region():
    n, reason = choose_node(nodes(), {"region": "East"}, manual_node="manual")
    assert n["name"] == "east-fast" and "same-region" in reason


def test_manual_falls_back_global_when_region_has_no_candidate():
    source = [{"name":"manual", "region":"East", "delay_ms":700, "available":True}, {"name":"west", "region":"West", "delay_ms":80, "available":True}]
    n, reason = choose_node(source, {"region": "East"}, manual_node="manual")
    assert n["name"] == "west" and "global" in reason


def test_region_mode_uses_same_region_then_respects_global_toggle():
    n, reason = choose_node(nodes(), {"selection": {"kind": "region", "value": "East"}, "allow_cross_region_fallback": False})
    assert n["name"] == "east-fast" and "mapped region" in reason
    n, reason = choose_node(nodes(), {"selection": {"kind": "region", "value": "Missing"}, "allow_cross_region_fallback": False})
    assert n["name"] == DIRECT_NODE_NAME and "DIRECT" in reason
    n, reason = choose_node(nodes(), {"selection": {"kind": "region", "value": "Missing"}, "allow_cross_region_fallback": True})
    assert n["name"] == "west" and "global" in reason


def test_node_mode_uses_300ms_then_catalog_region_fallback():
    n, _ = choose_node(nodes(), {"selection": {"kind": "node", "value": "east-slow"}, "allow_cross_region_fallback": False})
    assert n["name"] == "east-fast"
    n, _ = choose_node(nodes(), {"selection": {"kind": "node", "value": "east-fast"}, "allow_cross_region_fallback": False})
    assert n["name"] == "east-fast"


def test_node_mode_regional_fallback_includes_requested_node_and_breaks_ties():
    mapping = {"selection": {"kind": "node", "value": "requested"}, "allow_cross_region_fallback": False}
    n, reason = choose_node([
        {"name": "requested", "region": "East", "delay_ms": 350, "available": True},
        {"name": "peer", "region": "East", "delay_ms": 400, "available": True},
    ], mapping)
    assert n["name"] == "requested"
    assert "same-region fallback" in reason and "acceptable latency" not in reason

    n, _ = choose_node([
        {"name": "requested", "region": "East", "delay_ms": 350, "available": True},
        {"name": "alpha", "region": "East", "delay_ms": 350, "available": True},
    ], mapping)
    assert n["name"] == "alpha"


def test_direct_is_not_a_discovered_candidate():
    n, _ = choose_node([{"name": "DIRECT", "region": "Local", "delay_ms": 1, "available": True}], {"selection": {"kind": "region", "value": "Local"}, "allow_cross_region_fallback": True})
    assert n["name"] == DIRECT_NODE_NAME


def test_no_usable_nodes_selects_direct():
    n, reason = choose_node([{"name":"x", "region":"East", "delay_ms":700, "available":False}], {}, manual_node="x")
    assert n["name"] == DIRECT_NODE_NAME and "DIRECT" in reason
