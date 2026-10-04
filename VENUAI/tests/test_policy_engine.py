"""
Real tests for the Tool Policy Engine (VENUAI/policy_engine.py) and its
integration into VENUAI/agent_tools.build_agent_tools() — deny-by-default
tool access, independent of the document-level retrieval filtering tested
in test_agent_tools_authorization.py.
"""

from VENUAI import agent_tools, policy_engine, tool_registry
from VENUAI.agents import registry as agent_registry


def test_unknown_tool_is_denied():
    decision = policy_engine.check_tool_call(user_role="admin", agent_id="react_agent", tool_id="nonexistent_tool")
    assert decision.allowed is False
    assert "unknown" in decision.reason.lower() or "Unknown" in decision.reason


def test_unknown_agent_is_denied():
    decision = policy_engine.check_tool_call(user_role="admin", agent_id="nonexistent_agent", tool_id="drdo_calculator")
    assert decision.allowed is False


def test_agent_not_in_allowed_agents_is_denied():
    # general_agent is a real registered agent but is not in
    # web_search_drdo's allowed_agents (only react_agent is).
    decision = policy_engine.check_tool_call(user_role="admin", agent_id="general_agent", tool_id="web_search_drdo")
    assert decision.allowed is False


def test_role_without_permission_is_denied(monkeypatch):
    # Temporarily strip WEB_SEARCH from the "user" role to prove the check
    # is real, not a no-op that always allows registered agent+tool pairs.
    monkeypatch.setitem(policy_engine.ROLE_PERMISSIONS, "user", {"READ_DOCUMENTS"})
    decision = policy_engine.check_tool_call(user_role="user", agent_id="react_agent", tool_id="web_search_drdo")
    assert decision.allowed is False
    assert "permission" in decision.reason.lower()


def test_valid_call_is_allowed():
    decision = policy_engine.check_tool_call(user_role="user", agent_id="react_agent", tool_id="drdo_kb_search")
    assert decision.allowed is True


def test_every_registered_tool_lists_at_least_one_allowed_agent():
    for tool in tool_registry.all_tools():
        assert len(tool.allowed_agents) > 0, f"{tool.tool_id} has no allowed_agents — would be unusable"


def test_every_tool_allowed_agent_is_a_registered_agent():
    for tool in tool_registry.all_tools():
        for agent_id in tool.allowed_agents:
            assert agent_registry.get(agent_id) is not None, f"{tool.tool_id} allows unregistered agent '{agent_id}'"


# ── Integration: build_agent_tools actually enforces this, not just the engine alone ──

def test_build_agent_tools_denies_web_search_when_role_lacks_permission(monkeypatch):
    monkeypatch.setitem(policy_engine.ROLE_PERMISSIONS, "user", {"READ_DOCUMENTS"})
    tools = agent_tools.build_agent_tools(user_id="u1", allowed_doc_ids=None, user_role="user")
    web_tool = next(t for t in tools if t.name == "web_search_drdo")
    result = web_tool.invoke({"query": "anything"})
    assert result.startswith("Tool denied:")


def test_build_agent_tools_allows_calculator_for_default_user_role():
    tools = agent_tools.build_agent_tools(user_id="u1", allowed_doc_ids=None, user_role="user")
    calc_tool = next(t for t in tools if t.name == "drdo_calculator")
    result = calc_tool.invoke({"expression": "2 + 2"})
    assert "Result: 4" in result


def test_build_agent_tools_defaults_user_role_to_user():
    """Callers that don't pass user_role (defensive default) still get a
    real, deny-by-default policy check, not an open pass-through."""
    tools = agent_tools.build_agent_tools(user_id="u1", allowed_doc_ids=None)
    calc_tool = next(t for t in tools if t.name == "drdo_calculator")
    result = calc_tool.invoke({"expression": "3 * 3"})
    assert "Result: 9" in result
