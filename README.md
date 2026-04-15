# spacedrep

[![PyPI version](https://badge.fury.io/py/spacedrep.svg)](https://pypi.org/project/spacedrep/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)](https://pypi.org/project/spacedrep/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Spaced repetition you can script. A CLI and [MCP server](https://modelcontextprotocol.io/) for flashcards — so an AI agent can generate cards from what you're learning, quiz you in conversation, and manage your deck alongside you.

Import your existing Anki decks to get started. Scheduling uses [FSRS](https://github.com/open-spaced-repetition/py-fsrs) (same algorithm as Anki 23.10+). Everything is JSON in, JSON out — pipe it, script it, or let an agent drive it. [Why build a CLI this way.](https://dev.to/uenyioha/writing-cli-tools-that-ai-agents-actually-want-to-use-39no)

> **You:** Quiz me on my AWS deck.
>
> **Agent:** What does S3 stand for, and what is it used for?
>
> **You:** Simple Storage Service, it's object storage.
>
> **Agent:** Correct. Rated **good** — next review in 4 days. What is the CAP theorem?
>
> **You:** Something about distributed systems... I forget.
>
> **Agent:** CAP: pick 2 of 3 — consistency, availability, partition tolerance. Rated **again** — it'll come back shortly. 12 cards left.

## Install

```bash
pip install spacedrep
```

Optional extras: `spacedrep[mcp]` for the MCP server, `spacedrep[optimizer]` for FSRS parameter optimization (requires torch).

## Features

- **FSRS scheduling** — same algorithm as Anki 23.10+, with parameter optimization from your review history
- **Cloze deletions** — `{{c1::answer}}` syntax, auto-expands into multiple cards
- **Anki-native storage** — single SQLite file using Anki's schema for full round-trip compatibility
- **Rich filtering** — by deck, tags, state, date ranges, FSRS properties, and more
- **Agent-friendly** — duplicate detection, leech detection, review preview, bury/unbury, review history

## MCP Server

Connect spacedrep to an AI agent as an MCP tool server:

```bash
pip install spacedrep[mcp]
```

**Claude Code:**
```bash
claude mcp add spacedrep -e SPACEDREP_DB=/path/to/collection.anki21 -- uv run --directory /path/to/spacedrep spacedrep-mcp
```

**Claude Desktop** (`claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "spacedrep": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/spacedrep", "spacedrep-mcp"],
      "env": {
        "SPACEDREP_DB": "/path/to/collection.anki21"
      }
    }
  }
}
```

Set `SPACEDREP_DB` to configure the database path for the MCP server (default: `./collection.anki21`). The CLI uses `--db` instead.

## CLI Quick Start

```bash
spacedrep db init                            # create the database
spacedrep deck import ~/Downloads/deck.apkg  # or import an existing Anki deck
spacedrep card add "What is CAP theorem?" "Pick 2 of 3: consistency, availability, partition tolerance" --deck AWS
spacedrep card add-cloze "{{c1::Ottawa}} is the capital of {{c2::Canada}}" --deck Geo
spacedrep card next                          # get the next due card
spacedrep review submit <card_id> good       # again | hard | good | easy
```

See `spacedrep --help` and `spacedrep <command> --help` for all options.

## Cloze Deletions

Each `{{c1::answer}}` becomes a separate card. Hints are supported: `{{c1::Ottawa::capital city}}` shows `[capital city]` as the blank.

```bash
spacedrep card update-cloze 42 "{{c1::Ottawa}} is in {{c2::Canada}}, {{c3::North America}}"
```

## Search and Filters

```bash
spacedrep card list --search "Lambda" --deck AWS --state review
spacedrep card list --due-before "2026-12-31" --leeches
spacedrep card bury 42 --hours 4
spacedrep deck export out.apkg
```

Filters work on `card list` and `card next`. See `--help` for the full list.

## Anki Compatibility

spacedrep operates directly on Anki's native SQLite schema. Importing an `.apkg` replaces the working database; exporting writes it back. FSRS scheduling state survives the round-trip — cards reviewed in spacedrep show correct due dates when opened in Anki.

Media files, JavaScript templates, and nested cloze deletions are not supported.
