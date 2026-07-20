import json

from agent import TOOL_REGISTRY, _execute_tool


def test_registry_exposes_visual_tool():
    assert "tool_plot_size_class_distribution" in TOOL_REGISTRY
    assert TOOL_REGISTRY["tool_plot_size_class_distribution"]["enabled"] is True


def test_registry_disables_heuristic_tools():
    result = json.loads(_execute_tool("tool_calc_carbon_and_hydrology_metrics", {"subplot_id": "2816"}))
    assert "error" in result
    assert "\u4e0d\u53ef\u7528" in result["error"] or "\u672a\u6ce8\u518c" in result["error"] or "\u65e0\u6cd5\u6267\u884c" in result["error"]
