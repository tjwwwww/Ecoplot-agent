import json

from agent import TOOL_REGISTRY, _execute_tool


def test_registry_exposes_visual_tool():
    assert "tool_plot_size_class_distribution" in TOOL_REGISTRY
    assert TOOL_REGISTRY["tool_plot_size_class_distribution"]["enabled"] is True


def test_registry_disables_heuristic_tools():
    result = json.loads(_execute_tool("tool_calc_carbon_and_hydrology_metrics", {"subplot_id": "2816"}))
    assert "error" in result
    assert "?????" in result["error"]
