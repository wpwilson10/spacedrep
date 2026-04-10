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

Cloze deletions — one note, multiple cards:

```bash
spacedrep card add-cloze "{{c1::Ottawa}} is the capital of {{c2::Canada}}" --deck Geo
```

Import an existing Anki deck:

```bash
spacedrep deck import ~/Downloads/deck.apkg
```

See `spacedrep --help` and `spacedrep <command> --help` for all options.

## Features

- **FSRS scheduling** — same algorithm as Anki 23.10+, with parameter optimization from your review history
- **Cloze deletions** — `{{c1::answer}}` syntax, auto-expands into multiple cards
- **Anki import/export** — round-trip `.apkg` support including cloze notes (see compatibility below)
- **Rich filtering** — by deck, tags, state, date ranges, FSRS properties, and more
- **Agent-friendly** — duplicate detection, leech detection, review preview, bury/unbury, review history
- **SQLite storage** — single file, SQL-queryable

## Cloze Deletions

Create cloze notes with `{{c1::answer}}` syntax. Each cloze number becomes a separate flashcard:

```bash
# Single note → 2 cards (one blanks Ottawa, the other blanks Canada)
spacedrep card add-cloze "{{c1::Ottawa}} is the capital of {{c2::Canada}}" --deck Geo

# Update a cloze note (provide any card ID from it)
spacedrep card update-cloze 42 "{{c1::Ottawa}} is in {{c2::Canada}}, {{c3::North America}}"

# Bulk add supports cloze too
echo '[{"question":"{{c1::Ottawa}} is capital of {{c2::Canada}}","type":"cloze","deck":"Geo"}]' \
  | spacedrep card add-bulk
```

Hints are supported: `{{c1::Ottawa::capital city}}` shows `[capital city]` as the blank.

## Search and Filters

```bash
spacedrep card list --search "Lambda" --deck AWS --state review
spacedrep card list --due-before "2026-12-31" --min-difficulty 5.0
spacedrep card list --leeches
spacedrep card history 42
spacedrep card bury 42 --hours 4
spacedrep deck export out.apkg --tags aws --state review
```

Filters work on `card list`, `card next`, and `deck export`. See `--help` for the full list.

## Anki Compatibility

Import handles the common card types: **Basic**, **Basic (and reversed)**, and **Cloze** deletions. Suspended cards, deck hierarchy, and tags are preserved.

This is not a full Anki reimplementation. Things that are **not supported**: media files (images/audio are stripped to text), templates with JavaScript or conditional sections, nested cloze deletions, and scheduling data (imported cards start fresh with FSRS). Export writes basic and cloze notes. Suspended cards are tagged `suspended` on export.

Re-importing the same `.apkg` updates existing cards rather than creating duplicates.

## MCP Server

An MCP server exposes spacedrep as tools for AI agents. Once connected, you can ask an agent to:

- "Quiz me on my AWS deck" — fetches due cards, evaluates answers, submits reviews
- "Make flashcards from these notes" — bulk-creates cards from source material
- "Which cards am I struggling with?" — filters by leeches and suggests rewrites
- "Skip this for now" — buries a card to revisit later

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