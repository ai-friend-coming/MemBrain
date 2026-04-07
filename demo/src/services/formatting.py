"""Shared message formatting utilities."""

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    UserPromptPart,
)


def extract_text(parts) -> str:
    return "".join(p.text for p in parts if p.type == "text" and p.text)


def format_conversation(
    messages: list[dict],
    user_label: str = "User",
    assistant_label: str = "Assistant",
) -> str:
    """Format a list of {role, content} dicts into a readable conversation string."""
    lines = []
    for m in messages:
        name = user_label if m["role"] == "user" else assistant_label
        lines.append(f"{name}: {m['content']}")
    return "\n".join(lines)


def render_response(output) -> str:
    return output


def format_judge_prompt(
    messages: list[dict],
    user_label: str = "User",
    assistant_label: str = "Assistant",
) -> str:
    """Format a list of {role, content} dicts into judge prompt format.

    Last user message → <current_message>; everything before it → <conversation_history>.
    """
    # Find last user message index
    last_user_idx = None
    for i in range(len(messages) - 1, -1, -1):
        if messages[i]["role"] == "user":
            last_user_idx = i
            break

    if last_user_idx is None:
        return ""

    history = messages[:last_user_idx]
    current = messages[last_user_idx]["content"]

    lines: list[str] = []
    if history:
        lines.append("<conversation_history>")
        for m in history:
            prefix = user_label if m["role"] == "user" else assistant_label
            lines.append(f"{prefix}: {m['content']}")
        lines.append("</conversation_history>")
        lines.append("")
    lines.append(f"<current_message>{current}</current_message>")
    return "\n".join(lines)


def build_pydantic_history(messages: list[dict]) -> list[ModelRequest | ModelResponse]:
    """Convert a list of {role, content} dicts into PydanticAI message history."""
    history: list[ModelRequest | ModelResponse] = []
    for msg in messages:
        if msg["role"] == "user":
            history.append(ModelRequest(parts=[UserPromptPart(content=msg["content"])]))
        else:
            history.append(ModelResponse(parts=[TextPart(content=msg["content"])]))
    return history


def compute_trunk_window(total: int, max_window: int, trunk_size: int) -> int:
    """Compute how many recent messages to keep with trunk-based sliding window.

    Instead of sliding by 1 each turn (invalidates prefix cache), the window
    stays stable and only drops messages in trunk-sized chunks.
    """
    if total <= max_window or trunk_size <= 0:
        return total
    excess = total - max_window
    trunks_dropped = (excess + trunk_size - 1) // trunk_size
    return total - trunks_dropped * trunk_size


def wrap_history_with_prompts(
    history: list[ModelRequest | ModelResponse],
    system_prompt: str,
    memory_prompt: str = "",
) -> list[ModelRequest | ModelResponse]:
    """Wrap history with system prompt at start and memory prompt at end.

    For chat-reply: places system.md before chat history and memory.md after,
    so the stable prefix (system.md + history) stays cached.
    """
    result: list[ModelRequest | ModelResponse] = [
        ModelRequest(parts=[SystemPromptPart(content=system_prompt)])
    ]
    result.extend(history)
    if memory_prompt.strip():
        result.append(ModelRequest(parts=[SystemPromptPart(content=memory_prompt)]))
    return result
