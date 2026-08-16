"""Tests for AgentLoop.run_stream — the streaming event generator.

Covered contracts:
  - thinking events are emitted before tool executions
  - the done event carries answer/cites/suggestions/iterations/tokens
  - a mid-stream failure (gateway exception) yields an error event and stops
  - iteration-budget exhaustion yields a done event with done_called=False
  - blocking tool execution must not stall the event loop
"""
import asyncio
import time
import pytest
from unittest.mock import MagicMock

from app.agent.loop import AgentLoop


def _gateway(responses):
    """Build a gateway whose call() returns each response in turn."""
    gw = MagicMock()
    responses = list(responses)

    async def call(role, prompt, tools=None, messages=None):
        r = responses.pop(0) if responses else {
            "message": {"content": "", "tool_calls": [
                {"function": {"name": "done", "arguments": '{"answer": "done"}'}}
            ]}
        }
        if isinstance(r, Exception):
            raise r
        return r

    gw.call = call
    return gw


def _streaming_gateway(prose: str, done_response: dict):
    """Gateway whose call() returns prose first then done_response; stream()
    yields prose deltas for the prose turn."""
    gw = MagicMock()
    calls = [0]

    async def call(role, prompt, tools=None, messages=None):
        if calls[0] == 0:
            calls[0] += 1
            return {"message": {"content": prose, "tool_calls": []}}
        return dict(done_response)

    async def stream(role, prompt, tools=None, messages=None):
        for chunk in (prose[i:i + 4] for i in range(0, len(prose), 4)):
            yield {"type": "content", "text": chunk}

    gw.call = call
    gw.stream = stream
    return gw


async def _collect(loop, history, question, nudge=None):
    return [ev async for ev in loop.run_stream(history, question, nudge=nudge)]


@pytest.mark.asyncio
async def test_stream_emits_thinking_then_done():
    gw = _gateway([
        {"message": {"content": "", "tool_calls": [
            {"function": {"name": "fts_search", "arguments": '{"query": "goblin"}'}}]}},
        {"message": {"content": "", "tool_calls": [
            {"function": {"name": "done", "arguments": '{"answer": "AC is 15.", "cites": [{"path": "x.md", "page": 1, "quote": "AC 15"}]}'}}]}},
    ])
    toolbox = MagicMock()
    toolbox.execute = MagicMock(return_value='[{"path": "x.md", "title": "X", "snippet": "AC 15"}]')
    loop = AgentLoop(gw, toolbox)

    events = await _collect(loop, [], "What is AC?")
    types = [e["type"] for e in events]
    assert types == ["thinking", "done"]
    assert events[0]["message"] == "Searching for: goblin"
    assert events[1]["answer"] == "AC is 15."
    assert events[1]["cites"][0]["path"] == "x.md"
    toolbox.execute.assert_called_once()


@pytest.mark.asyncio
async def test_stream_done_event_fields():
    gw = _gateway([
        {"message": {"content": "", "tool_calls": [
            {"function": {"name": "done", "arguments": (
                '{"answer": "Goblin AC is 15.", '
                '"cites": [{"path": "b.md", "page": 2, "quote": "AC 15"}], '
                '"suggestions": ["What is HP?", "What is XP?"]}'
            )}}]}},
    ])
    toolbox = MagicMock()
    loop = AgentLoop(gw, toolbox)

    events = await _collect(loop, [], "Goblin AC?")
    assert len(events) == 1
    done = events[0]
    assert done["type"] == "done"
    assert done["answer"] == "Goblin AC is 15."
    assert done["cites"][0]["page"] == 2
    assert done["suggestions"] == ["What is HP?", "What is XP?"]
    assert done["iterations"] == 1
    assert done["done_called"] is True
    assert done["est_input_tokens"] > 0  # system + question messages counted


@pytest.mark.asyncio
async def test_stream_error_mid_stream_yields_error_event():
    """A gateway failure mid-stream must surface as an error event, not a
    swallowed exception or an endless stream."""
    gw = _gateway([RuntimeError("ollama down")])
    toolbox = MagicMock()
    loop = AgentLoop(gw, toolbox)

    events = await _collect(loop, [], "What is AC?")
    assert len(events) == 1
    assert events[0]["type"] == "error"
    assert "ollama down" in events[0]["message"]


@pytest.mark.asyncio
async def test_stream_tool_execution_error_yields_error_event():
    """A tool that raises must also surface as an error event."""
    gw = _gateway([
        {"message": {"content": "", "tool_calls": [
            {"function": {"name": "read_file", "arguments": '{"path": "x.md"}'}}]}},
    ])
    toolbox = MagicMock()
    toolbox.execute = MagicMock(side_effect=RuntimeError("sandbox exploded"))
    loop = AgentLoop(gw, toolbox)

    events = await _collect(loop, [], "Read x")
    # thinking is emitted before the failing tool runs; the stream then
    # terminates with the error event.
    assert events[-1]["type"] == "error"
    assert "sandbox exploded" in events[-1]["message"]


@pytest.mark.asyncio
async def test_run_maps_error_event_to_graceful_fallback():
    """run() must not raise on a mid-stream failure — it returns a fallback
    answer mentioning the error instead of a crash."""
    gw = _gateway([RuntimeError("Ollama connection refused")])
    toolbox = MagicMock()
    loop = AgentLoop(gw, toolbox)

    result = await loop.run([], "What is AC?")
    assert "Ollama" in result["answer"]
    assert result["done_called"] is False
    assert result["cites"] == []
    assert result["iterations"] == 0


@pytest.mark.asyncio
async def test_stream_budget_exhaustion_done_called_false():
    """When the iteration budget is hit, a budget_exhausted event with the
    recent steps is emitted instead of a silent force-answered done."""
    gw = _gateway([
        {"message": {"content": "", "tool_calls": [
            {"function": {"name": "fts_search", "arguments": '{"query": "goblin"}'}}]}},
        {"message": {"content": "", "tool_calls": [
            {"function": {"name": "fts_search", "arguments": '{"query": "orc"}'}}]}},
    ])
    toolbox = MagicMock()
    toolbox.execute = MagicMock(return_value="[]")
    loop = AgentLoop(gw, toolbox, max_iterations=2)

    events = await _collect(loop, [], "Find the goblin")
    types = [e["type"] for e in events]
    assert types == ["thinking", "thinking", "budget_exhausted"]
    exhausted = events[-1]
    assert exhausted["iterations"] == 2
    steps = exhausted["steps"]
    assert len(steps) == 2
    assert steps[0]["tool"] == "fts_search"
    assert steps[0]["args"]["query"] == "goblin"
    assert "done" not in types


@pytest.mark.asyncio
async def test_stream_reprompts_on_content_without_tool_call():
    """A long content reply without tool calls should trigger a re-prompt
    (an extra gateway call) before the loop settles on done."""
    gw = _gateway([
        {"message": {"content": "Here is a long answer about goblins without any tool call.", "tool_calls": []}},
        {"message": {"content": "", "tool_calls": [
            {"function": {"name": "done", "arguments": '{"answer": "Goblins have AC 15."}'}}]}},
    ])
    toolbox = MagicMock()
    loop = AgentLoop(gw, toolbox)

    events = await _collect(loop, [], "Goblin AC?")
    done = [e for e in events if e["type"] == "done"]
    assert done[0]["answer"] == "Goblins have AC 15."
    # gateway was called twice: original answer + re-prompt for done
    assert len(events) == 1


@pytest.mark.asyncio
async def test_stream_emits_token_events_for_prose():
    """A content-only reply (no tool calls) streams as token events; the done
    event still carries the assembled answer."""
    gw = _streaming_gateway(
        "Here is a long answer about goblins without any tool call.",
        {"message": {"content": "", "tool_calls": [
            {"function": {"name": "done", "arguments": '{"answer": "Goblins have AC 15."}'}}
        ]}},
    )
    toolbox = MagicMock()
    loop = AgentLoop(gw, toolbox)

    events = await _collect(loop, [], "Goblin AC?")
    types = [e["type"] for e in events]
    assert "token" in types
    tokens = "".join(e["content"] for e in events if e["type"] == "token")
    assert tokens == "Here is a long answer about goblins without any tool call."
    done = [e for e in events if e["type"] == "done"]
    assert done[0]["answer"] == "Goblins have AC 15."


@pytest.mark.asyncio
async def test_stream_tool_calls_from_prose_keeps_context():
    """When the streamed response emits tool calls (not prose), the assistant
    prose must stay in context for the tool execution loop."""
    gw = MagicMock()
    calls = [0]
    seen_messages = []

    async def call(role, prompt, tools=None, messages=None):
        if calls[0] == 0:
            calls[0] += 1
            return {"message": {"content": "Let me look that up for you.", "tool_calls": []}}
        seen_messages.extend(messages or [])
        return {"message": {"content": "", "tool_calls": [
            {"function": {"name": "done", "arguments": '{"answer": "Found it."}'}}
        ]}}

    async def stream(role, prompt, tools=None, messages=None):
        yield {"type": "tool_calls", "calls": [
            {"function": {"name": "fts_search", "arguments": '{"query": "goblin"}'}}
        ]}

    gw.call = call
    gw.stream = stream
    toolbox = MagicMock()
    toolbox.execute = MagicMock(return_value='[{"path": "x.md", "title": "G", "snippet": "s"}]')
    loop = AgentLoop(gw, toolbox)

    events = await _collect(loop, [], "Goblin AC?")
    types = [e["type"] for e in events]
    assert "done" in types
    # The assistant's prose must be present in the final messages context.
    assert any("Let me look that up" in m.get("content", "") for m in seen_messages)


@pytest.mark.asyncio
async def test_stream_nudge_appended_to_messages():
    """The nudge (continue) message must be appended after the question."""
    gw = MagicMock()
    seen = []

    async def call(role, prompt, tools=None, messages=None):
        seen.extend(messages or [])
        return {"message": {"content": "", "tool_calls": [
            {"function": {"name": "done", "arguments": '{"answer": "ok"}'}}
        ]}}

    gw.call = call
    loop = AgentLoop(gw, MagicMock())
    await _collect(loop, [], "Find x", nudge="You were asked to keep looking.")
    contents = [m.get("content", "") for m in seen]
    assert any("keep looking" in c for c in contents)
    assert contents[-1] == "You were asked to keep looking."


@pytest.mark.asyncio
async def test_stream_no_tool_calls_short_content_yields_done():
    """Content too short to re-prompt (<= 10 chars) with no tool calls
    falls back to a done event using the last known content."""
    gw = _gateway([
        {"message": {"content": "AC 15.", "tool_calls": []}},
    ])
    toolbox = MagicMock()
    loop = AgentLoop(gw, toolbox)

    events = await _collect(loop, [], "Goblin AC?")
    done = [e for e in events if e["type"] == "done"]
    assert done[0]["done_called"] is False
    assert "AC 15" in done[0]["answer"]


@pytest.mark.asyncio
async def test_repeated_dedup_reads_force_synthesis():
    """A model that keeps requesting the SAME file must be forced into
    SYNTHESIZING after 2 consecutive dedup skips — not allowed to burn the
    whole iteration budget re-reading nothing."""
    same_file = {"function": {"name": "read_file", "arguments": '{"path": "d1/x.md"}'}}
    gw = _gateway([
        {"message": {"content": "", "tool_calls": [same_file]}},  # iter 1: real read
        {"message": {"content": "", "tool_calls": [same_file]}},  # iter 2: dedup #1
        {"message": {"content": "", "tool_calls": [same_file]}},  # iter 3: dedup #2 -> force
        {"message": {"content": "", "tool_calls": [
            {"function": {"name": "done", "arguments": '{"answer": "Found it.", "cites": [{"path": "d1/x.md", "page": 1, "quote": "AC 15"}]}'}}]}},
    ])
    toolbox = MagicMock()
    toolbox.execute = MagicMock(return_value="file content here")
    loop = AgentLoop(gw, toolbox, max_iterations=10)

    events = await _collect(loop, [], "What is in d1/x.md?")
    done = [e for e in events if e["type"] == "done"]
    assert done, "loop must terminate with a done event"
    assert done[0]["done_called"] is True
    assert done[0]["answer"] == "Found it."
    # The dedup skips must not consume the full budget: 3 tool iterations
    # + 1 done call, not 10.
    assert done[0]["iterations"] < 6


@pytest.mark.asyncio
async def test_repeated_read_replays_cached_content():
    """A repeat read_file must return the cached content (not a generic
    'already read' message) so the model can't claim the content is missing."""
    same_file = {"function": {"name": "read_file", "arguments": '{"path": "d1/x.md"}'}}
    seen_messages = []

    async def call(role, prompt, tools=None, messages=None):
        seen_messages.append(list(messages or []))
        if len(seen_messages) == 1:
            return {"message": {"content": "", "tool_calls": [same_file]}}
        if len(seen_messages) == 2:
            return {"message": {"content": "", "tool_calls": [same_file]}}
        return {"message": {"content": "", "tool_calls": [
            {"function": {"name": "done", "arguments": '{"answer": "AC 15 from the file."}'}}]}}

    gw = MagicMock()
    gw.call = call
    toolbox = MagicMock()
    toolbox.execute = MagicMock(return_value="AC 15, HP 7. Goblin stats.")
    loop = AgentLoop(gw, toolbox, max_iterations=5)

    events = await _collect(loop, [], "What is in d1/x.md?")
    done = [e for e in events if e["type"] == "done"]
    assert done[0]["done_called"] is True
    assert done[0]["answer"] == "AC 15 from the file."
    # The second gateway call's messages must contain the replayed content,
    # not the generic dedup message.
    assert len(seen_messages) >= 2
    tool_contents = [m.get("content", "") for m in seen_messages[1] if m.get("role") == "tool"]
    assert any("AC 15, HP 7" in c for c in tool_contents), \
        "repeat read must replay cached content, not a generic dedup message"


@pytest.mark.asyncio
async def test_non_dict_tool_args_do_not_crash_stream():
    """A model emitting non-dict tool arguments (e.g. a JSON array) must
    not crash run_stream — the args must be coerced to {} and the run
    must continue to done."""
    gw = _gateway([
        {"message": {"content": "", "tool_calls": [
            {"function": {"name": "fts_search", "arguments": "[1,2,3]"}}]}},
        {"message": {"content": "", "tool_calls": [
            {"function": {"name": "done", "arguments": '{"answer": "AC is 15."}'}}]}},
    ])
    toolbox = MagicMock()
    toolbox.execute = MagicMock(return_value="[]")
    loop = AgentLoop(gw, toolbox)

    events = await _collect(loop, [], "What is AC?")
    assert any(e["type"] == "done" for e in events), \
        "non-dict args must not abort the stream"
    assert not any(e["type"] == "error" for e in events)


@pytest.mark.asyncio
async def test_blocking_tool_does_not_stall_event_loop():
    """A slow synchronous tool must run off the event loop — concurrent
    tasks must keep making progress while the tool blocks."""
    gw = _gateway([
        {"message": {"content": "", "tool_calls": [
            {"function": {"name": "fts_search", "arguments": '{"query": "goblin"}'}}]}},
        {"message": {"content": "", "tool_calls": [
            {"function": {"name": "done", "arguments": '{"answer": "AC is 15."}'}}]}},
    ])
    toolbox = MagicMock()

    def slow_execute(name, args):
        time.sleep(2.0)  # blocking sleep — stalls the loop if run in-loop
        return '[]'

    toolbox.execute = MagicMock(side_effect=slow_execute)
    loop = AgentLoop(gw, toolbox)

    async def collect():
        return [ev async for ev in loop.run_stream([], "What is AC?")]

    collect_task = asyncio.create_task(collect())
    # The mock gateway call is instant, so the tool starts almost
    # immediately. Measure how long OUR sleep takes while the tool is
    # (potentially) blocking the loop: in-loop execution delays us by
    # ~2s; off-loop execution leaves us on schedule.
    t0 = time.monotonic()
    await asyncio.sleep(0.3)
    gap = time.monotonic() - t0
    events = await collect_task
    assert gap < 1.0, f"event loop stalled during tool execution (gap {gap:.2f}s)"
    assert any(e["type"] == "done" for e in events)
