"""Constants for graph node and state keys."""

START_NODE = "router"
END_NODE = "end"

ROUTER_NODE = "router"
NARRATIVE_ASSISTANT_NODE = "assistant"
ASSISTANT_NODE = NARRATIVE_ASSISTANT_NODE
COMBAT_ASSISTANT_NODE = "combat_assistant"
COMBAT_START_NODE = "combat_start"
COMBAT_END_NODE = "combat_end"
COMBAT_EXECUTOR_NODE = "combat_executor"
TOOL_NODE = "tool"
COMBAT_RESOLUTION_NODE = "combat_resolution"
DEATH_SAVE_PAUSE_NODE = "death_save_pause"
SUMMARIZE_NODE = "summarize"
REACTION_RESOLUTION_NODE = "resolve_reaction"

NARRATIVE_AGENT_MODE = "narrative"
COMBAT_AGENT_MODE = "combat"

STATE_MESSAGES_KEY = "messages"
STATE_OUTPUT_KEY = "output"
STATE_DEATH_SAVE_PAUSE_TURN_KEY = "death_save_pause_turn_id"
