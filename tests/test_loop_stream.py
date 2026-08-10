"""Tests for AgentLoop.run_stream — the streaming event generator.

Covered contracts:
  - thinking events are emitted before tool executions
  - the done event carries answer/cites/suggestions/iterations/tokens
  - a mid-stream failure (gateway exception) yields an error event and stops
  - iteration-budget exhaustion yields a done event with done_called=False
"""
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


async def _collect(loop, history, question):
    return [ev async for ev in loop.run_stream(history, question)]


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
    """When the iteration budget is hit, a done event with done_called=False
    and a fallback answer must be emitted."""
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
    thinking = [e for e in events if e["type"] == "thinking"]
    done = [e for e in events if e["type"] == "done"]
    assert len(thinking) == 2
    assert len(done) == 1
    assert done[0]["done_called"] is False
    assert done[0]["iterations"] == 2
    assert done[0]["answer"], "fallback answer must be non-empty"


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
