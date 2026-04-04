# Tasks: MCP Server

## Implementation tasks

1. **Add `mcp` dependency to `pyproject.toml`**
   Add `mcp>=1.0` as optional dependency (`[project.optional-dependencies] mcp`),
   add to dev deps, and add `spacedrep-mcp` script entry point.
   Done when: `uv sync --all-extras` installs the `mcp` package, `pyright` can
   resolve `fastmcp` imports, `spacedrep-mcp` entry point is registered.

2. **Create `mcp_server.py` with FastMCP server scaffold**
   Create `src/spacedrep/mcp_server.py` with FastMCP app, `DB_PATH` from env var,
   error handling wrapper, `main()` function, and `if __name__ == "__main__"` block.
   No tools yet — just the skeleton.
   Done when: `spacedrep-mcp` starts, responds to MCP `initialize`, and returns
   empty `tools/list`.

3. **Add card tools (9 tools)**
   Implement `add_card`, `add_cards_bulk`, `get_next_card`, `list_cards`,
   `get_card`, `update_card`, `delete_card`, `suspend_card`, `unsuspend_card`.
   All parameters flat primitives. Sentinel conversion (`""` → `None`) in each tool.
   `add_cards_bulk` parses JSON string and validates with Pydantic.
   Done when: all 9 card tools appear in `tools/list` with correct schemas, manual
   smoke test via `mcp dev` works.

4. **Add review tools (2 tools)**
   Implement `submit_review` and `preview_review`.
   `rating` is `int` (1-4), optional string params use `""` sentinel.
   Done when: both tools appear in `tools/list`, rating validation works.

5. **Add deck tools (3 tools)**
   Implement `list_decks`, `import_deck`, `export_deck`.
   Path params are `str`, converted to `Path` inside tool.
   Done when: all 3 tools appear in `tools/list`.

6. **Add stats tools (3 tools)**
   Implement `get_due_count`, `get_session_stats`, `get_overall_stats`.
   Done when: all 3 tools appear in `tools/list`.

7. **Add system tools (3 tools)**
   Implement `init_database`, `get_fsrs_status`, `optimize_fsrs`.
   `optimize_fsrs` catches `OptimizerNotInstalledError` and returns `ToolError`.
   Done when: all 3 tools appear in `tools/list`, `init_database` creates DB.

8. **Write tool function tests**
   Create `tests/test_mcp_server.py`. Call each `@mcp.tool` function directly.
   Temp DB per test. Cover: happy paths for all 20 tools, error cases (card not
   found, invalid rating, missing DB), bulk add (valid JSON, invalid JSON, empty
   array), sentinel conversion (empty string → `None`).
   Done when: all tests pass.

9. **Write schema validation test**
   Connect to the server via FastMCP test utilities. Inspect `tools/list` response.
   Verify: 20 tools present, no `annotations` field, all parameter types are
   primitives (no `anyOf`, `object`, `array` in schemas). `title` and
   `outputSchema` from FastMCP are OK (confirmed working April 2026).
   Done when: schema regression test passes.

10. **Run full validation suite**
    `ruff check --fix . && ruff format . && pyright . && uv run pytest -x`
    Done when: zero errors across linter, formatter, type checker, and all tests
    (existing 139 + new MCP tests).

11. **Update README.md**
    Add MCP server section: what it is, install (`pip install spacedrep[mcp]`),
    configure (Claude Code, Claude Desktop), available tools summary.
    Done when: README has MCP section with working config examples.
