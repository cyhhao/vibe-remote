from modules.im.slack_modal import parse_routing_modal_selection


def test_parse_routing_modal_selection_uses_action_override():
    view = {
        "state": {
            "values": {
                "backend_block": {"backend_select": {"selected_option": {"value": "opencode"}}},
                "opencode_model_block": {"opencode_model_select": {"selected_option": {"value": "m1"}}},
            }
        }
    }
    action = {"action_id": "opencode_model_select", "selected_option": {"value": "m2"}}

    selection = parse_routing_modal_selection(view=view, action=action, default_backend="claude")

    assert selection.selected_backend == "opencode"
    assert selection.selected_opencode_model == "m2"


def test_parse_routing_modal_selection_normalizes_default_values():
    view = {
        "state": {
            "values": {
                "claude_model_block": {"claude_model_select": {"selected_option": {"value": "__default__"}}},
                "claude_reasoning_block": {"claude_reasoning_select": {"selected_option": {"value": "__default__"}}},
                "codex_reasoning_block": {"codex_reasoning_select_1": {"selected_option": {"value": "__default__"}}},
            }
        }
    }

    selection = parse_routing_modal_selection(view=view, action={}, default_backend="claude")

    assert selection.selected_backend == "claude"
    assert selection.selected_claude_model is None
    assert selection.selected_claude_reasoning is None
    assert selection.selected_codex_reasoning is None
