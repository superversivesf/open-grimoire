from app.agent.tools_schema import TOOL_DEFINITIONS


def test_all_tools_defined():
    names = {t["function"]["name"] for t in TOOL_DEFINITIONS}
    assert names == {"fts_search", "read_file", "list_index", "grep", "table_extract", "calc", "ls", "done"}


def test_each_has_required_fields():
    for t in TOOL_DEFINITIONS:
        assert t["type"] == "function"
        assert "name" in t["function"]
        assert "description" in t["function"]
        assert "parameters" in t["function"]
        assert "properties" in t["function"]["parameters"]


def test_done_schema():
    done = next(t for t in TOOL_DEFINITIONS if t["function"]["name"] == "done")
    props = done["function"]["parameters"]["properties"]
    assert "answer" in props
    assert "cites" in props