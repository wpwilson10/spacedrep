# spacedrep

[![PyPI version](https://badge.fury.io/py/spacedrep.svg)](https://pypi.org/project/spacedrep/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)](https://pypi.org/project/spacedrep/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Spaced repetition you can script. A CLI and [MCP server](https://modelcontextprotocol.io/) for flashcards — so an AI agent can generate cards from what you're learning, quiz you in conversation, and manage your deck alongside you.

Import your existing Anki decks to get started. Scheduling uses [FSRS](https://github.com/open-spaced-repetition/py-fsrs) (same algorithm as Anki 23.10+). Everything is JSON in, JSON out — pipe it, script it, or let an agent drive it. [Why build a CLI this way.](https://dev.to/uenyioha/writing-cli-tools-that-ai-agents-actually-want-to-use-39no)

## Install

```bash
pip install spacedrep
```

Optional extras: `spacedrep[mcp]` for the MCP server, `spacedrep[optimizer]` for FSRS parameter optimization (requires torch).

## Quick Start

```bash
spacedrep db init
spacedrep card add "What is CAP theorem?" "Pick 2 of 3: consistency, availability, partition tolerance" --deck AWS
spacedrep card next
spacedrep review submit 1 good
```

Import an existing Anki deck:

```bash
spacedrep deck import ~/Downloads/deck.apkg
```

See `spacedrep --help` and `spacedrep <command> --help` for all options.

## Features

- **FSRS scheduling** — the same algorithm Anki uses since v23.10, via [py-fsrs](https://github.com/open-spaced-repetition/py-fsrs)
- **Anki import/export** — bring your `.apkg` decks (see compatibility below)
- **Leech detection** — cards rated "again" 8+ times are auto-suspended
- **Review preview** — see what each rating would produce before committing
- **Parameter optimization** — personalize FSRS from your review history
- **Deck and tag hierarchy** — `Science::Chemistry` and `aws::s3` with prefix filtering
- **SQLite storage** — single file, SQL-queryable

## Anki Compatibility

Import handles the common card types: **Basic**, **Basic (and reversed)**, and **Cloze** deletions. Suspended cards, deck hierarchy, and tags are preserved.

This is not a full Anki reimplementation. Things that are **not supported**: media files (images/audio are stripped to text), templates with JavaScript or conditional sections, nested cloze deletions, and scheduling data (imported cards start fresh with FSRS). Export writes basic two-field cards only.

Re-importing the same `.apkg` updates existing cards rather than creating duplicates.

## MCP Server

An MCP server exposes spacedrep as tools for AI agents. Once connected, you can ask an agent to:

- "Quiz me on my AWS deck" — it calls `get_next_card`, shows the question, evaluates your answer, and calls `submit_review`
- "Make flashcards from these notes" — it calls `add_cards_bulk` with cards it generates from your material
- "Which cards am I struggling with?" — it calls `list_cards` with the leeches filter and suggests rewrites

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