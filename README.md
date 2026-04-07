# spacedrep

[![PyPI version](https://badge.fury.io/py/spacedrep.svg)](https://pypi.org/project/spacedrep/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)](https://pypi.org/project/spacedrep/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Agent-first flashcard CLI with FSRS scheduling and .apkg support.

A standalone spaced repetition tool that AI coding agents can drive — no Anki desktop required. Import/export .apkg files, schedule reviews with FSRS, manage cards via JSON-native CLI or MCP server.

## Install

```bash
pip install spacedrep

# With MCP server support
pip install spacedrep[mcp]

# With FSRS optimizer (requires torch)
pip install spacedrep[optimizer]
```

## Quick Start

```bash
# Initialize database
spacedrep db init

# Add a card
spacedrep card add "What is CAP theorem?" "Pick 2 of 3: consistency, availability, partition tolerance" --deck AWS

# Bulk add from JSON
echo '[{"question":"Q1","answer":"A1","deck":"AWS"}]' | spacedrep card add-bulk

# Study: get next due card, preview outcomes, submit rating
spacedrep card next
spacedrep review preview 1
spacedrep review submit 1 good

# Check what's due
spacedrep stats due

# Import/export Anki decks
spacedrep deck import ~/Downloads/deck.apkg
spacedrep deck export ./export.apkg --deck AWS

# Pipe card IDs for scripting
spacedrep card list -q | xargs -I{} spacedrep card get {}

# Preview destructive operations
spacedrep card delete 42 --dry-run
```

Run `spacedrep --help` for all commands and options.

## Agent-First Design

- **JSON to stdout, errors to stderr** — stdout is the API contract
- **Meaningful exit codes** — 0=success, 2=usage error, 3=not found
- **Idempotent** — imports dedup, adds are safe to retry
- **Non-interactive** — no prompts, no confirmation dialogs
- **`--quiet` mode** — bare values for piping into `xargs` or `while read`
- **`--dry-run`** — preview destructive operations without side effects

## How It Works

- **FSRS scheduling** — the same algorithm built into Anki since v23.10, via [py-fsrs](https://github.com/open-spaced-repetition/py-fsrs)
- **Leech detection** — cards rated "again" 8+ times while in Review/Relearning are auto-suspended
- **Review preview** — see what each rating would produce before committing
- **Parameter optimization** — personalize FSRS from your review history (`pip install spacedrep[optimizer]`)
- **SQLite storage** — single file, SQL-queryable review history
- **.apkg compatible** — import from and export to Anki

## MCP Server

An MCP server exposes all 20 spacedrep operations as tools for AI agents (Claude Code, Claude Desktop, etc.) over the MCP protocol.

```bash
pip install spacedrep[mcp]
```

**Claude Code:**
```bash
claude mcp add spacedrep -e SPACEDREP_DB=/path/to/reviews.db -- uv run --directory /path/to/spacedrep spacedrep-mcp
```

**Claude Desktop** (`claude_desktop_config.json`):
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

Set `SPACEDREP_DB` to configure the database path (default: `./reviews.db`).

## Development

```bash
git clone https://github.com/wpwilson10/spacedrep.git && cd spacedrep
uv venv && uv sync --all-extras
pre-commit install

uv run pytest              # Test
uv run pyright .           # Type check
uv run ruff check .        # Lint
```

## License

MIT
