FORCED_DONE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the full content of a markdown file. Only use for files you have NOT already read.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the markdown file"},
                    "lines": {"type": "string", "description": "Optional line range, e.g. '10-30'"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "done",
            "description": "Signal that the answer is complete. Provide the final answer, citations, and 3 suggested follow-up questions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "answer": {"type": "string", "description": "The final answer to the user's question"},
                    "cites": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "page": {"type": "integer"},
                                "quote": {"type": "string"},
                            },
                        },
                        "description": "Citations to source pages",
                    },
                    "suggestions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "3 suggested follow-up questions for deeper exploration",
                    },
                },
                "required": ["answer"],
            },
        },
    },
]

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "fts_search",
            "description": "Full-text search across all documents in the current collection. Returns ranked matches with path, title, summary, and a snippet. Use this first to find relevant sections.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "FTS5 query string, e.g. 'goblin AC' or 'grapple prone'"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the full content of a markdown file. Use for leaf sections found via fts_search or list_index. For large files, pass a line range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the markdown file"},
                    "lines": {"type": "string", "description": "Optional line range, e.g. '10-30'"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_index",
            "description": "Read an index.md file and return its child entries (title + path). Use to navigate the document hierarchy.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to an index.md file"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "Regex search across all markdown files in the user's tree. Returns matching lines with path and line number. Use for cross-references like 'every mention of advantage'. Max 20 results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern to search for"},
                    "path": {"type": "string", "description": "Optional: limit search to a specific directory"},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "table_extract",
            "description": "Parse markdown tables in a file into structured JSON rows. Use for stat blocks, equipment lists, spell tables.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to a markdown file containing tables"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calc",
            "description": "Evaluate an arithmetic expression. Supports dice notation (e.g. '2d6+3', '1d20+5'), addition, subtraction, multiplication, division, comparisons.",
            "parameters": {
                "type": "object",
                "properties": {
                    "expr": {"type": "string", "description": "Expression to evaluate, e.g. '2d6+3' or '15+2'"},
                },
                "required": ["expr"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ls",
            "description": "List files in a directory within the user's document tree.",
            "parameters": {
                "type": "object",
                "properties": {
                    "dir": {"type": "string", "description": "Directory path to list"},
                },
                "required": ["dir"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "done",
            "description": "Signal that the answer is complete. Provide the final answer, citations, and 3 suggested follow-up questions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "answer": {"type": "string", "description": "The final answer to the user's question"},
                    "cites": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "page": {"type": "integer"},
                                "quote": {"type": "string"},
                            },
                        },
                        "description": "Citations to source pages",
                    },
                    "suggestions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "3 suggested follow-up questions for deeper exploration",
                    },
                },
                "required": ["answer"],
            },
        },
    },
]