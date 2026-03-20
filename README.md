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

# Get next due card
spacedrep card next

# Submit a review (again/hard/good/easy or 1-4)
spacedrep review submit 1 good --answer "Pick 2 of consistency, availability, partition tolerance"

# Check what's due
spacedrep stats due

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
| `card list` | List cards with optional filters |
| `card get <id>` | Get full card detail by ID |
| `card update <id>` | Update question, answer, tags, or deck |
| `card delete <id>` | Delete a card and its review history |
| `card suspend <id>` | Suspend a card |
| `card unsuspend <id>` | Unsuspend a card |
| `review submit <id> <rating>` | Submit a review |
| `deck list` | List all decks |
| `deck import <path>` | Import .apkg file |
| `deck export <path>` | Export to .apkg file |
| `stats due` | Count of due cards |
| `stats session <id>` | Session statistics |
| `stats overall` | Overall statistics |

All commands accept `--db <path>` (default: `./reviews.db`).

## Agent-First Design

- **JSON to stdout** — every command outputs structured JSON
- **Meaningful exit codes** — 0=success, 2=usage error, 3=not found
- **Idempotent** — imports dedup, adds are safe to retry
- **Non-interactive** — no prompts, no confirmation dialogs
- **Self-documenting** — `--help` on every command with examples

## How It Works

- **FSRS scheduling** — the same algorithm built into Anki since v23.10, via [py-fsrs](https://github.com/open-spaced-repetition/py-fsrs)
- **SQLite storage** — single file, SQL-queryable review history
- **.apkg compatible** — import from and export to Anki

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
