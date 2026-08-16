import pytest
from unittest.mock import AsyncMock, MagicMock
from app.agent.loop import AgentLoop, SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_system_prompt_mentions_search_strategy():
    assert "fts_search" in SYSTEM_PROMPT
    assert "ONE distinctive keyword" in SYSTEM_PROMPT
    assert "grep" in SYSTEM_PROMPT


def test_cites_extracted_from_read_file():
    from app.agent.loop import _extract_cites_from_history
    messages = [
        {"role": "assistant", "content": 'read_file {"path": "abc/01_goblin.md"}'},
        {"role": "tool", "name": "read_file",
         "content": "# Goblin\n\nAC 15, HP 7. A small humanoid.\n\nmore text" * 40},
    ]
    cites = _extract_cites_from_history(messages)
    assert any(c["path"] == "abc/01_goblin.md" for c in cites)


def test_cites_extracted_from_grep_json():
    """grep results are stored as JSON — citations must be extracted from
    the JSON structure, not from text lines."""
    from app.agent.loop import _extract_cites_from_history
    import json
    messages = [
        {"role": "assistant", "content": ""},
        {"role": "tool", "name": "grep",
         "content": json.dumps([
             {"path": "d1/01_chapter/01_goblin.md", "line": 12, "text": "AC 15"},
             {"path": "d1/01_chapter/02_knight.md", "line": 3, "text": "AC 16"},
         ])},
    ]
    cites = _extract_cites_from_history(messages)
    assert any(c["path"] == "d1/01_chapter/01_goblin.md" for c in cites), \
        "grep JSON results must produce citations"


def test_system_prompt_no_book_preference_instruction():
    assert "prefer matches from that book" not in SYSTEM_PROMPT


def test_system_prompt_no_duplicated_mechanics():
    assert "AND-combined" not in SYSTEM_PROMPT


def test_system_prompt_too_broad_guidance():
    assert "more specific" in SYSTEM_PROMPT


def test_system_prompt_mentions_match_mode():
    assert "match_mode" in SYSTEM_PROMPT


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
    assert result["done_called"] is True


@pytest.mark.asyncio
async def test_loop_reports_done_called_false_when_no_done_tool():
    """A run that ends without calling the done tool must report done_called=False."""
    gw = MagicMock()
    gw.call = AsyncMock(return_value={
        "message": {"content": "Here is the answer.", "tool_calls": []}
    })
    toolbox = MagicMock()
    loop = AgentLoop(gw, toolbox)
    result = await loop.run([], "What is AC?")
    assert result["done_called"] is False


@pytest.mark.asyncio
async def test_loop_reports_done_called_false_on_budget_exhaustion():
    """A run that hits the iteration budget must report done_called=False."""
    gw = MagicMock()
    gw.call = AsyncMock(return_value={
        "message": {"content": "", "tool_calls": [{"function": {"name": "fts_search", "arguments": '{"query": "x"}'}}]}
    })
    toolbox = MagicMock()
    toolbox.execute = MagicMock(return_value='[{"path": "x.md", "title": "X", "snippet": "y"}]')
    loop = AgentLoop(gw, toolbox, max_iterations=2)
    result = await loop.run([], "Find x")
    assert result["done_called"] is False


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
async def test_loop_bounds_search_attempts():
    """The SEARCHING cap must bind on distinct search count, not the
    reset-on-every-search iteration counter (M1)."""
    import json
    search_count = [0]
    seen_messages = []

    async def mock_call(role, prompt, tools=None, messages=None):
        seen_messages.extend(messages or [])
        if search_count[0] < 5:
            search_count[0] += 1
            return {"message": {"content": "", "tool_calls": [
                {"function": {"name": "fts_search", "arguments": json.dumps({"query": f"q{search_count[0]}"})}}
            ]}}
        return {"message": {"content": "", "tool_calls": [
            {"function": {"name": "done", "arguments": '{"answer": "found", "cites": [], "suggestions": ["a", "b", "c"]}'}}
        ]}}

    gw = MagicMock()
    gw.call = mock_call
    toolbox = MagicMock()
    toolbox.execute = MagicMock(return_value='[{"path": "x.md", "title": "X", "snippet": "y"}]')
    loop = AgentLoop(gw, toolbox)
    result = await loop.run([], "Find x")
    # Exactly 5 distinct searches in SEARCHING, then the cap forces the nudge
    # before the model may call done.
    assert search_count[0] == 5
    assert result["done_called"] is True
    assert any("searched enough" in m.get("content", "") for m in seen_messages)


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


def test_clean_answer_preserves_legitimate_page_text():
    from app.agent.loop import clean_answer
    text = "The **Page** of Swords is a tarot card.\n\n**Path**: d1/goblin.md\n**Page**: 42"
    result = clean_answer(text)
    assert "**Page** of Swords" in result
    assert "tarot card" in result
    assert "**Path**" not in result
    assert "**Page**:" not in result


def test_clean_answer_strips_citation_lines():
    from app.agent.loop import clean_answer
    text = "Goblins have AC 15.\n\n**Path**: d1/goblin.md\n**Page**: 42\n**Quote**: AC 15"
    result = clean_answer(text)
    assert "Goblins have AC 15" in result
    assert "**Path**" not in result
    assert "**Page**" not in result


def test_synthesize_answer_handles_frontmatter_only():
    from app.agent.loop import _synthesize_answer
    messages = [
        {"role": "tool", "name": "read_file", "content": "---\nsummary: test\npage: 1\n---\n\nActual content here with goblins."},
    ]
    result = _synthesize_answer(messages, "goblins content")
    assert "Actual content" in result


def test_synthesize_answer_handles_horizontal_rules():
    from app.agent.loop import _synthesize_answer
    messages = [
        {"role": "tool", "name": "read_file", "content": "---\nsummary: test\n---\n\nContent with --- horizontal rule about goblins.\n\nMore text."},
    ]
    result = _synthesize_answer(messages, "goblins horizontal")
    assert "horizontal rule" in result


def test_synthesize_answer_short_terms():
    from app.agent.loop import _synthesize_answer
    messages = [
        {"role": "tool", "name": "read_file", "content": "Goblins have AC 15 and HP 7. They are small creatures."},
    ]
    result = _synthesize_answer(messages, "What is the AC and HP of goblins?")
    assert "AC" in result or "HP" in result or "Goblins" in result