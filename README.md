# spacedrep

Agent-first flashcard CLI with FSRS scheduling and .apkg support.

A standalone spaced repetition tool that AI coding agents can drive — no Anki desktop required. Import/export .apkg files, schedule reviews with FSRS, manage cards via JSON-native CLI.

## Install

```bash
# From PyPI (once published)
uvx spacedrep --help

# From source
git clone <repo> && cd spacedrep
uv venv && uv sync --all-extras
uv run spacedrep --help
```

## Quick Start

```bash
# Initialize database
spacedrep db init

# Add a card
spacedrep card add "What is CAP theorem?" "Pick 2 of 3: consistency, availability, partition tolerance" --deck AWS

# Bulk add cards from JSON
echo '[{"question":"Q1","answer":"A1","deck":"AWS"},{"question":"Q2","answer":"A2"}]' | spacedrep card add-bulk

# Get next due card
spacedrep card next

# Preview what each rating would produce
spacedrep review preview 1

# Submit a review (again/hard/good/easy or 1-4)
spacedrep review submit 1 good --answer "Pick 2 of consistency, availability, partition tolerance"

# Check what's due
spacedrep stats due

# List leech cards (cards that keep failing)
spacedrep card list --leeches

# Check FSRS scheduler status
spacedrep fsrs status

# Import an Anki deck
spacedrep deck import ~/Downloads/deck.apkg

# Export back to Anki
spacedrep deck export ./export.apkg --deck AWS
```

## Commands

| Command | Description |
|---------|-------------|
| `db init` | Initialize the database |
| `card next` | Get the next due card |
| `card add <q> <a>` | Add a new card |
| `card add-bulk` | Add multiple cards from JSON on stdin |
| `card list` | List cards with optional filters |
| `card list --leeches` | Show only leech cards (8+ lapses) |
| `card get <id>` | Get full card detail by ID |
| `card update <id>` | Update question, answer, tags, or deck |
| `card delete <id>` | Delete a card and its review history |
| `card suspend <id>` | Suspend a card |
| `card unsuspend <id>` | Unsuspend a card |
| `review submit <id> <rating>` | Submit a review |
| `review preview <id>` | Preview all 4 rating outcomes |
| `deck list` | List all decks |
| `deck import <path>` | Import .apkg file |
| `deck export <path>` | Export to .apkg file |
| `stats due` | Count of due cards |
| `stats session <id>` | Session statistics |
| `stats overall` | Overall statistics |
| `fsrs status` | Show scheduler parameters and review count |
| `fsrs optimize [--reschedule]` | Optimize FSRS parameters from review history |

All commands accept `--db <path>` (default: `./reviews.db`).

### Global Flags

| Flag | Available on | Behavior |
|------|-------------|----------|
| `--quiet` / `-q` | card, deck, review submit | Output bare values (one per line) for piping |
| `--dry-run` | card delete/suspend/unsuspend, deck import, fsrs optimize | Preview what would happen without making changes |

```bash
# Pipe card IDs
spacedrep card list -q | xargs -I{} spacedrep card get {}

# Preview a delete
spacedrep card delete 42 --dry-run

# Preview an import
spacedrep deck import deck.apkg --dry-run
```

## Agent-First Design

- **JSON to stdout, errors to stderr** — stdout is the API contract, stderr has structured error JSON
- **Meaningful exit codes** — 0=success, 2=usage error, 3=not found
- **Idempotent** — imports dedup, adds are safe to retry
- **Non-interactive** — no prompts, no confirmation dialogs
- **Self-documenting** — `--help` on every command with examples
- **`--quiet` mode** — bare values for piping into `xargs` or `while read`
- **`--dry-run`** — preview destructive operations without side effects

## How It Works

- **FSRS scheduling** — the same algorithm built into Anki since v23.10, via [py-fsrs](https://github.com/open-spaced-repetition/py-fsrs)
- **Leech detection** — cards rated "again" 8+ times while in Review/Relearning are auto-suspended
- **Review preview** — see what each rating would produce before committing
- **Parameter optimization** — personalize FSRS scheduling from your review history (requires `pip install spacedrep[optimizer]`)
- **SQLite storage** — single file, SQL-queryable review history
- **.apkg compatible** — import from and export to Anki

## MCP Server

An MCP server exposes all spacedrep operations as tools for AI agents (Claude Code, Claude Desktop, etc.) without shelling out to the CLI.

### Install

```bash
# From source with MCP support
uv sync --extra mcp

# Or via pip
pip install spacedrep[mcp]
```

### Configure

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

### Available Tools

| Tool | Description |
|------|-------------|
| `init_database` | Initialize the database |
| `add_card` | Add a single flashcard |
| `add_cards_bulk` | Add multiple cards from JSON string |
| `get_next_card` | Get next due card for review |
| `list_cards` | List/filter cards with pagination |
| `get_card` | Get full card detail by ID |
| `update_card` | Update card fields |
| `delete_card` | Delete a card |
| `suspend_card` | Suspend a card from reviews |
| `unsuspend_card` | Unsuspend a card |
| `submit_review` | Submit a review rating (1-4) |
| `preview_review` | Preview all 4 rating outcomes |
| `list_decks` | List all decks |
| `import_deck` | Import .apkg file |
| `export_deck` | Export to .apkg file |
| `get_due_count` | Due card counts by state |
| `get_session_stats` | Session review statistics |
| `get_overall_stats` | Overall database statistics |
| `get_fsrs_status` | FSRS scheduler status |
| `optimize_fsrs` | Optimize FSRS parameters |

All tools use flat primitive parameters (string, int, bool) for maximum client compatibility. Set `SPACEDREP_DB` to configure the database path (default: `./reviews.db`).

## Development

```bash
uv venv && uv sync --all-extras
pre-commit install

# Run tests
uv run pytest

# Type check + lint
uv run pyright .
uv run ruff check .
```

## License

MIT
