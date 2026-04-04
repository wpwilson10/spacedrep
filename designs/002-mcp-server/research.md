# MCP Server Research

## Codebase Fit

The existing three-layer architecture (db → core → CLI) makes MCP trivial.
`core.py` already exposes every operation as a function that takes `Path` +
typed args and returns a Pydantic model. The CLI layer is one thin wrapper;
the MCP layer will be another. The docstring in `core.py` already says
"Both CLI and (future) MCP call these functions."

### Core functions available to wrap

| Core function | Purpose |
|---|---|
| `init_database` | Create/migrate DB |
| `get_next_card` | Get next due card (filterable by deck/tags/state) |
| `add_card` | Add single card |
| `add_cards_bulk` | Add multiple cards |
| `list_cards` | List/filter cards with pagination |
| `get_card_detail` | Full card detail by ID |
| `update_card` | Update card fields |
| `delete_card` | Delete card |
| `suspend_card` / `unsuspend_card` | Suspend/unsuspend |
| `submit_review` | Submit a review rating |
| `preview_review` | Preview scheduling outcomes for all ratings |
| `list_decks` | List all decks |
| `import_deck` | Import .apkg file |
| `export_deck` | Export .apkg file |
| `get_due_count` | Due card counts by state |
| `get_session_stats` | Stats for a review session |
| `get_overall_stats` | Global stats |
| `optimize_parameters` | Run FSRS optimizer |
| `get_fsrs_status` | FSRS parameter status |

All return Pydantic models or dicts — already JSON-serializable.

## Technology Choice: FastMCP

**FastMCP** is the decorator-based API built into the official `mcp` Python SDK
(`pip install mcp`). It's the recommended way to build MCP servers.

Why FastMCP:
- **Minimal boilerplate**: `@mcp.tool()` decorator on a function, done
- **Type-driven**: reads function signatures and docstrings for tool schemas
- **Official**: maintained as part of the MCP SDK, not a third-party wrapper
- **Pydantic-native**: works naturally with our existing models

Alternative considered: building on the lower-level `Server` class from the SDK.
Not worth it — FastMCP covers everything we need and is less code to maintain.

### Key FastMCP patterns

- **Schema generation**: `@mcp.tool` reads function signature + type hints to
  auto-generate JSON Schema for tool parameters. Pydantic models, dicts,
  dataclasses all auto-serialize to structured output.
- **Parameter descriptions**: Use `Annotated[int, "description"]` for per-param docs.
- **Error handling**: Raise `ToolError` for user-facing errors; other exceptions
  are caught and converted to generic error responses. Use `mask_error_details=True`
  in production to hide internal error details.
- **Context injection**: Declare a parameter of type `Context` to get access to
  logging (`ctx.info()`, `ctx.warning()`), progress reporting, and lifespan context.
- **Lifespan**: `@asynccontextmanager` for resource initialization/cleanup (DB
  connections, etc.) — resources yielded are available via `ctx.request_context.lifespan_context`.

### Tool annotations

MCP spec supports annotations that hint at tool behavior:
- `readOnlyHint: True` — tool only reads, no modifications. Clients may auto-approve.
- `destructiveHint: True` — changes are irreversible. Clients may require confirmation.
- `idempotentHint: True` — repeated calls with same args produce same effect.
- `openWorldHint: False` — does not interact with external systems.

These affect how Claude and other clients decide whether to prompt for confirmation.

## MCP Primitives

MCP defines three primitives: **Tools**, **Resources**, and **Prompts**.

| Primitive | Control model | Side effects | When to use |
|---|---|---|---|
| **Tools** | LLM decides when to call | May have side effects | Actions, queries, writes |
| **Resources** | Application decides when to fetch | Read-only | Passive context, schemas |
| **Prompts** | User selects explicitly | None | Guided workflows, templates |

### For spacedrep

**Tools for everything.** Rationale:
- Resources have weaker client support — many hosts don't have good UI for
  browsing/selecting resources. Tools are universally supported.
- The MCP spec's own database example uses tools for queries, resources for
  schema only. Our "schema" is simple and stable — not worth a resource.
- Real-world MCP servers (Anki MCP, sqlite-explorer, mcp-sqlite-manager) all
  expose operations as tools, not resources.
- Resources are application-controlled (host decides when to fetch), but we want
  the agent to decide when to query cards, stats, etc.

**Decision: Tools only for v1.** Resources and Prompts are additive — can add
later if there's a use case.

## Tool Design Best Practices (from research)

### Granularity

- **Single-purpose, well-scoped tools.** Avoid combining unrelated operations.
  (Source: Speakeasy MCP tool design guide)
- **Organize around workflows, not API structure.** Group by user tasks.
  (Source: AWS Prescriptive Guidance)
- **Target 5-15 tools.** LLMs become unreliable above 30-40 tools — hallucinate
  names, make wrong selections. Real-world MCP servers average 3-15.
  (Sources: Speakeasy, Anthropic engineering blog)

### Naming

- **snake_case** — 90%+ of MCP tools use it. Multi-word names (~95%).
- **Consistent verb prefixes**: `get_*`, `add_*`, `search_*`, `delete_*`.
- **Avoid similar names** that could confuse selection.
  (Source: zazencodes.com naming conventions analysis)

### Descriptions

- **Write for agents, not humans.** Descriptions are consumed by the LLM.
- **Front-load purpose** — agents may not read the full description.
- **1-2 sentences.** Include when-to-use guidance. Operational details belong in
  the parameter schema, not the description.
- **Include prerequisites**: "Requires a valid card ID from search_cards or get_next_card."
  (Sources: Merge.dev, Anthropic advanced tool use blog)
- Tool use examples in descriptions improved accuracy from 72% to 90% on complex
  parameter handling. (Source: Anthropic engineering blog)

### Parameters

- All parameters must have clear descriptions (use `Annotated[type, "desc"]`).
- Mark optional vs required explicitly. Document defaults.
- Accept lenient variations — advertise strict schemas but execute leniently.
  (Source: Peter Steinberger MCP best practices)

### Anti-patterns

- **Too many tools** (>30): agent hallucination, wrong selections.
- **Vague descriptions**: "Execute query" vs "Search flashcards by content, tags, or deck."
- **Large intermediate results**: Filter server-side before returning. Don't dump tables.
- **Missing dependency tools**: Ensure toolset has all prerequisites to complete workflows.
- **stdout during operation**: Must not output to stdout during normal tool operation
  (disrupts MCP stdio transport). Use stderr for logging.

## Architecture

```
MCP client (Claude, etc.)
    │
    ▼
mcp_server.py          ← thin wrapper, one @mcp.tool() per core function
    │
    ▼
core.py                ← existing business logic (unchanged)
    │
    ▼
db.py / fsrs_engine.py ← existing data layer (unchanged)
```

The MCP server is a peer of the CLI — both call core, neither knows about the
other. This means:
- No code duplication
- Core changes automatically available to both interfaces
- Each wrapper handles its own concerns (CLI: flags/output, MCP: schemas/descriptions)

### DB Path Handling

The CLI takes `--db` as a flag. The MCP server takes it as:
1. Environment variable `SPACEDREP_DB` (set in MCP client config)
2. Default to `./reviews.db` (same as CLI)

Standard pattern — env vars for config since there's no interactive flag passing.

### DB Connection Strategy

Every core function does `with _open_db(db_path) as conn:` — open per call,
close after. For the MCP server, resolve `db_path` once at startup from env var,
then pass it into each tool call. This:
- Matches the CLI pattern exactly
- Allows CLI and MCP to coexist on the same DB (SQLite handles concurrent readers)
- Avoids holding a persistent connection in a long-running process
- No need for lifespan-managed connection pool for SQLite

### Error Handling

Core already raises typed `SpacedrepError` subclasses with structured error info.
The MCP layer catches these and raises `ToolError` with the same message.
Use `mask_error_details=True` to hide internal exceptions from clients.

## Real-World MCP Server Examples

| Server | Tools | Language | Pattern |
|---|---|---|---|
| sqlite-explorer | 3 | Python/FastMCP | Minimal surface, read-only, env var config |
| Anki MCP | ~15 | TypeScript | Categorized tools, safety flags, read-only mode |
| mcp-sqlite-manager | 5 | Python/FastMCP | Clean read/write separation |
| MCP SDK simple-tool | 1 | Python | Shows stdio + HTTP transport switching |

Common patterns:
- **Tool count**: 3-15 is typical. Focused servers have 3-5.
- **Configuration**: Env vars for paths/credentials, CLI flags for behavior modes.
- **Safety**: Read-only annotations, confirmation for destructive actions.
- **Structure**: Single file for simple servers; package for larger ones.

## Configuration & Deployment

### Transport

**stdio** for local tools. Client manages server lifecycle. No network overhead.
Streamable HTTP is for remote/multi-client scenarios — not needed here.

### Client Registration

Claude Desktop (`~/Library/Application Support/Claude/claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "spacedrep": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/spacedrep", "python", "-m", "spacedrep.mcp_server"],
      "env": {
        "SPACEDREP_DB": "/path/to/reviews.db"
      }
    }
  }
}
```

Claude Code:
```bash
claude mcp add spacedrep -e SPACEDREP_DB=/path/to/reviews.db -- uv run python -m spacedrep.mcp_server
```

Or project `.mcp.json` with same structure.

### Entry Point

```toml
[project.scripts]
spacedrep = "spacedrep.cli:app"
spacedrep-mcp = "spacedrep.mcp_server:main"
```

Or via `python -m spacedrep.mcp_server`. Server communicates over stdio.

## Claude Code Compatibility Issues

Critical bugs and incompatibilities discovered in Claude Code's MCP client that
directly affect tool parameter design. Originally researched early 2026;
updated April 2026 with live testing results.

### 1. Objects/Dicts/Pydantic models serialized as JSON strings

Claude Code serializes JSON objects as **strings** instead of native objects when
calling MCP tools. `{"key": "value"}` arrives as `"{\"key\": \"value\"}"`.

- [claude-code#5504](https://github.com/anthropics/claude-code/issues/5504)
- [claude-code#3084](https://github.com/anthropics/claude-code/issues/3084)
- [claude-code#7845](https://github.com/anthropics/claude-code/issues/7845) (still broken after "fix")

**Rule: Do NOT use Pydantic models, dicts, or nested objects as tool parameters.**
Flatten to primitives. Construct complex objects inside the tool function.

### 2. Array parameters serialized as strings

Same bug for arrays — `["a", "b"]` arrives as `"[\"a\", \"b\"]"`.

- [claude-code#22394](https://github.com/anthropics/claude-code/issues/22394)

**Rule: Avoid list parameters.** Accept comma-separated strings and parse them,
or accept repeated calls with single values.

### 3. Optional[X] generates incompatible schemas

`Optional[int]` generates an `anyOf` schema that Claude Code can't validate.

- [python-sdk#1402](https://github.com/modelcontextprotocol/python-sdk/issues/1402)
- [claude-code#5844](https://github.com/anthropics/claude-code/issues/5844)

**Rule: Use `param: int = None` instead of `param: Optional[int] = None`.**

### 4. title/outputSchema/toolAnnotations — reported but not confirmed broken

When a server includes `title`, `outputSchema`, or `toolAnnotations` in its
`tools/list` response, Claude Code was reported to **silently drop ALL tools**.

- [claude-code#25081](https://github.com/anthropics/claude-code/issues/25081)
  (closed by stale bot, no engineer response)
- [XcodeBuildMCP#220](https://github.com/getsentry/XcodeBuildMCP/issues/220)

**Update (April 2026):** Live testing shows this is no longer the case, at least
for `outputSchema` and `title` within `inputSchema`. FastMCP 1.27.0 auto-adds
`outputSchema` to every tool and `title` to every inputSchema/property. All 20
spacedrep tools register, call, and return results correctly through Claude Code.
No changelog entry or issue comment confirms when this was fixed — it may have
been a quiet fix or version-specific regression.

`toolAnnotations` (`readOnlyHint`, `destructiveHint`, etc.) remain untested since
FastMCP doesn't add them by default. Avoid until independently verified.

**Rule: Flat primitives for parameters remain the safe choice (see #1-3 above),
but `outputSchema` and `title` in schemas are not blockers.**

### 5. 64-character tool name limit

Claude Code prefixes tool names with `mcp__<server_name>__`. Combined name must
be under 64 characters. Property names must match `^[a-zA-Z0-9_.-]{1,64}$`.

- [claude-code#955](https://github.com/anthropics/claude-code/issues/955)

**Rule: Short server name + short tool names.** `mcp__spacedrep__` is 17 chars,
leaving 47 for tool names — plenty.

### 6. Tool descriptions capped at 2KB

Claude Code truncates descriptions at 2KB.

**Rule: Keep descriptions concise. Critical info first.**

### Summary: Defensive parameter design

```python
# YES — flat primitives, simple defaults
@mcp.tool
def search_cards(
    query: str = "",
    deck: str = "",
    tags: str = "",
    state: str = "",
    limit: int = 20,
    offset: int = 0,
) -> dict: ...

# NO — Pydantic models, Optional[], lists, dicts as params
@mcp.tool
def search_cards(
    filters: SearchFilters,              # broken: object as string
    tags: Optional[list[str]] = None,    # broken: anyOf + list
    options: dict[str, str] = {},        # broken: object as string
) -> dict: ...
```

Return values are fine as dicts/Pydantic models — the serialization issue is
only on the **input** side.

## Maintenance

Adding a new feature to spacedrep:
1. Add DB query in `db.py`
2. Add business logic in `core.py`
3. Add CLI command in `commands/`
4. Add MCP tool in `mcp_server.py` — one function, ~5 lines

The MCP tool is the thinnest layer: a function with a docstring that calls the
corresponding core function and returns the result.
