import pytest
from unittest.mock import AsyncMock, MagicMock
from app.agent.loop import AgentLoop


@pytest.mark.asyncio
async def test_loop_calls_done_immediately():
    gw = MagicMock()
    gw.call = AsyncMock(return_value={
        "message": {
            "content": "",
            "tool_calls": [
                {"function": {"name": "done", "arguments": '{"answer": "AC is 15.", "cites": [{"path": "x.md", "page": 42, "quote": "AC 15"}]}'}}
            ],
        }
    })
    toolbox = MagicMock()
    loop = AgentLoop(gw, toolbox)
    result = await loop.run([], "What is AC?")
    assert result["answer"] == "AC is 15."
    assert result["cites"][0]["page"] == 42
    assert result["iterations"] == 1


@pytest.mark.asyncio
async def test_loop_searches_then_done():
    call_count = [0]
    responses = [
        {"message": {"content": "", "tool_calls": [{"function": {"name": "fts_search", "arguments": '{"query": "goblin"}'}}]}},
        {"message": {"content": "", "tool_calls": [{"function": {"name": "done", "arguments": '{"answer": "Found goblin.", "cites": []}'}}]}},
    ]
    async def mock_call(role, prompt, tools=None, messages=None):
        r = responses[call_count[0]]
        call_count[0] += 1
        return r
    gw = MagicMock()
    gw.call = mock_call
    toolbox = MagicMock()
    toolbox.execute = MagicMock(return_value='[{"path": "x.md", "title": "Goblin", "snippet": "AC 15"}]')
    loop = AgentLoop(gw, toolbox)
    result = await loop.run([], "Find goblin stats")
    assert result["answer"] == "Found goblin."
    assert result["iterations"] == 2
    toolbox.execute.assert_called_once()


@pytest.mark.asyncio
async def test_loop_force_terminates_after_max():
    gw = MagicMock()
    gw.call = AsyncMock(return_value={
        "message": {"content": "", "tool_calls": [{"function": {"name": "fts_search", "arguments": '{"query": "x"}'}}]}
    })
    toolbox = MagicMock()
    toolbox.execute = MagicMock(return_value="[]")
    loop = AgentLoop(gw, toolbox, max_iterations=3)
    result = await loop.run([], "loop forever")
    assert "could not find" in result["answer"].lower() or "couldn't" in result["answer"].lower()
    assert result["iterations"] == 3