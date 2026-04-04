# Plan: MCP Server

## What we're building and why

An MCP server that exposes spacedrep's core functions as MCP tools, so AI agents
(Claude Code, Claude Desktop, etc.) can manage flashcards directly over the MCP
protocol without shelling out to the CLI.

The architecture was designed for this from day one — `core.py` is already a
clean orchestration layer with no CLI dependencies. The MCP server is a peer
of the CLI: both call core, neither knows about the other.

## Architecture

```
MCP client (Claude Code, Claude Desktop, etc.)
    │  stdio (JSON-RPC)
    ▼
mcp_server.py          ← new file, one @mcp.tool per core function
    │
    ▼
core.py                ← unchanged
    │
    ▼
db.py / fsrs_engine.py ← unchanged
```

### Technology

- **FastMCP** from the official `mcp` Python SDK (`pip install mcp`)
- **stdio transport** — client manages server lifecycle, no network
- **`mcp` added as a dependency** in `pyproject.toml`

### DB path

Resolved once at module level from `SPACEDREP_DB` env var, default `./reviews.db`.
Passed into each core function call. Same open-per-call pattern as the CLI.

### Error handling

Core raises typed `SpacedrepError` subclasses. The MCP layer catches these and
raises `ToolError` with the structured error message. Leave `mask_error_details`
at default (False) — this is a local server, full errors help debugging.

## Tool inventory

20 tools. 1:1 mapping with core functions (minus `get_next_due_time` which is an
internal helper used only by the CLI's "no cards due" message).

All parameters are **flat primitives** (str, int, float, bool) due to Claude Code
serialization bugs with objects, lists, and `Optional[X]`. Complex inputs (bulk
cards) are accepted as JSON strings and parsed inside the tool function.

No tool annotations (`readOnlyHint`, `destructiveHint`, etc.) — Claude Code
silently drops all tools from servers that include them
([claude-code#25081](https://github.com/anthropics/claude-code/issues/25081)).

### Card tools

| Tool | Parameters | Returns | Notes |
|---|---|---|---|
| `add_card` | `question: str`, `answer: str`, `deck: str = "Default"`, `tags: str = ""` | `{"card_id": int, "deck": str}` | |
| `add_cards_bulk` | `cards_json: str` | `BulkAddResult` | JSON string of `[{question, answer, deck?, tags?}]`, parsed inside tool |
| `get_next_card` | `deck: str = ""`, `tags: str = ""`, `state: str = ""` | `CardDue \| null message` | Empty string = no filter. Tags comma-separated. |
| `list_cards` | `deck: str = ""`, `tags: str = ""`, `state: str = ""`, `leeches_only: bool = False`, `limit: int = 50`, `offset: int = 0` | `CardListResult` | |
| `get_card` | `card_id: int` | `CardDetail` | |
| `update_card` | `card_id: int`, `question: str = ""`, `answer: str = ""`, `tags: str = ""`, `deck: str = ""` | `CardDetail` | Empty string = no change. **Limitation**: cannot clear a field to empty via MCP — set to desired value instead. |
| `delete_card` | `card_id: int`, `dry_run: bool = False` | delete result dict | |
| `suspend_card` | `card_id: int`, `dry_run: bool = False` | suspend result | |
| `unsuspend_card` | `card_id: int`, `dry_run: bool = False` | unsuspend result | |

### Review tools

| Tool | Parameters | Returns | Notes |
|---|---|---|---|
| `submit_review` | `card_id: int`, `rating: int`, `answer: str = ""`, `feedback: str = ""`, `session_id: str = ""` | `ReviewResult` | rating 1-4 (again/hard/good/easy) |
| `preview_review` | `card_id: int` | `ReviewPreview` | Shows all 4 rating outcomes |

### Deck tools

| Tool | Parameters | Returns | Notes |
|---|---|---|---|
| `list_decks` | (none) | `list[DeckInfo]` | |
| `import_deck` | `apkg_path: str`, `question_field: str = ""`, `answer_field: str = ""`, `dry_run: bool = False` | `ImportResult` | |
| `export_deck` | `output_path: str`, `deck: str = ""` | `{"exported": int, "file": str}` | |

### Stats tools

| Tool | Parameters | Returns | Notes |
|---|---|---|---|
| `get_due_count` | (none) | `DueCount` | |
| `get_session_stats` | `session_id: str` | `SessionStats` | |
| `get_overall_stats` | (none) | `OverallStats` | |

### System tools

| Tool | Parameters | Returns | Notes |
|---|---|---|---|
| `init_database` | (none) | `{"status": "ok", ...}` | Idempotent |
| `get_fsrs_status` | (none) | `FsrsStatus` | |
| `optimize_fsrs` | `reschedule: bool = False`, `dry_run: bool = False` | `OptimizeResult` | Requires optimizer extra |

## Tool descriptions

Written for agents, not humans. Front-load purpose, include when-to-use guidance.
Keep to 1-2 sentences. Example:

```python
@mcp.tool
def get_next_card(deck: str = "", tags: str = "", state: str = "") -> dict:
    """Get the next flashcard due for review. Use this at the start of a study
    session or when the user wants to practice. Filter by deck name, comma-separated
    tags, or state (new/learning/review/relearning). Returns null message if no
    cards are due."""
```

## Parameter design rules

Due to Claude Code compatibility issues (see research.md):

1. **Flat primitives only** — `str`, `int`, `float`, `bool`. No Pydantic models,
   dicts, lists, or nested objects as parameters.
2. **No `Optional[X]`** — use `param: str = ""` or `param: int = 0` with sentinel
   defaults. Empty string means "not provided".
3. **No tool annotations** — `readOnlyHint`, `destructiveHint`, etc. are untested
   with Claude Code. Avoid until verified.
4. **`title` and `outputSchema` are OK** — FastMCP 1.27.0 auto-generates both.
   Live testing (April 2026) confirms Claude Code handles them correctly despite
   earlier reports (claude-code#25081, now closed).
5. **Lists as strings** — `tags` is comma-separated, `cards_json` is a JSON string.
   Parse inside the tool function.
6. **Tool names under 47 chars** — `mcp__spacedrep__` prefix is 17 chars, 64 max.
7. **Descriptions under 2KB** — critical info first.

## Sentinel convention

Empty string `""` means "not provided" for optional string params. This replaces
`None` which would require `Optional[str]` and generate broken schemas.

Inside each tool, convert sentinels before calling core:

```python
@mcp.tool
def get_next_card(deck: str = "", tags: str = "", state: str = "") -> dict:
    result = core.get_next_card(
        DB_PATH,
        deck=deck or None,
        tags=[t.strip() for t in tags.split(",") if t.strip()] if tags else None,
        state=state or None,
    )
    ...
```

## File changes

| File | Change |
|---|---|
| `src/spacedrep/mcp_server.py` | **New.** FastMCP server with 20 tools + `main()` entry point. Module is directly runnable (`python -m spacedrep.mcp_server`). |
| `pyproject.toml` | Add `mcp` optional dependency. Add `mcp` to dev deps (for pyright). Add `spacedrep-mcp` script entry point. |
| `tests/test_mcp_server.py` | **New.** Tests for each MCP tool. |
| `README.md` | Add MCP server section (setup, configuration, usage). |

### Not changing

- `core.py` — no modifications needed
- `db.py` — no modifications needed
- `models.py` — no modifications needed
- `commands/` — no modifications needed
- `cli.py` — no modifications needed

## Entry points

```toml
[project.scripts]
spacedrep = "spacedrep.cli:app"
spacedrep-mcp = "spacedrep.mcp_server:main"

[project.optional-dependencies]
mcp = ["mcp>=1.0"]
optimizer = ["fsrs[optimizer]"]
dev = ["pytest>=8.0", "pyright>=1.1", "ruff>=0.11", "pre-commit>=4.0", "mcp>=1.0"]
```

The MCP server is an optional runtime dependency — install with
`pip install spacedrep[mcp]`. This keeps the core CLI lightweight.
Dev deps include `mcp` so pyright can type-check `mcp_server.py`.

`mcp_server.py` includes an `if __name__ == "__main__": main()` block for
`python -m spacedrep.mcp_server` support.

## Client configuration

Claude Code:
```bash
claude mcp add spacedrep -e SPACEDREP_DB=/path/to/reviews.db -- uv run --directory /path/to/spacedrep spacedrep-mcp
```

Claude Desktop (`claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "spacedrep": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/spacedrep", "spacedrep-mcp"],
      "env": {
        "SPACEDREP_DB": "/path/to/reviews.db"
      }
    }
  }
}
```

## Testing strategy

Two layers:

**1. Tool function tests** — call the `@mcp.tool` functions directly as Python
functions (they're just wrappers). Temp DB per test, same pattern as `test_cli.py`.
- Each tool gets at least one happy-path test
- Error cases: card not found, invalid rating, missing DB
- Bulk add: valid JSON string, invalid JSON string, empty array
- Sentinel conversion: empty string params correctly become `None` in core calls

**2. Schema validation test** — connect to the server via FastMCP's test utilities
and inspect the `tools/list` response:
- Verify all 20 tools appear
- Verify no tool annotations, `title`, or `outputSchema` in tool definitions
- Verify all parameter types are primitives (no `anyOf`, no `object`, no `array`)
- This is a regression guard against FastMCP version upgrades silently adding
  fields that break Claude Code

## Acceptance criteria

1. `spacedrep-mcp` starts and responds to MCP `initialize` handshake
2. All 20 tools appear in `tools/list` response with correct schemas
3. Every tool calls the corresponding core function and returns serialized results
4. Error cases return `ToolError` with structured messages
5. `SPACEDREP_DB` env var configures database path
6. No tool annotations in tool definitions (`title` and `outputSchema` from
   FastMCP are OK — confirmed working April 2026)
7. All parameters are flat primitives (str, int, float, bool)
8. Existing 139 tests still pass
9. New MCP tests pass
10. `ruff check`, `ruff format`, `pyright` all clean
