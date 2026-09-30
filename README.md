# Codex Thread Reader

**English** · [简体中文](README.zh-CN.md)

A local, read-only Codex plugin for finding and reading the visible parts of earlier conversations. Give Codex a `codex://threads/<uuid>` link, a thread ID, or a search phrase; the plugin reads the corresponding rollout files on your computer and returns the result to the agent.

## What it provides

| Tool | Purpose |
| --- | --- |
| `read_thread` | Read a thread by deep link or UUID. Returns the newest text within a character budget and supports paging backward. |
| `list_threads` | List recent local threads, including archived threads by default. |
| `search_threads` | Search visible user and assistant text in the 200 most recent local threads. |
| `thread_reader_status` | Show the Codex data directory, rollout counts, Python version, and Zstandard decoder availability. |

The plugin includes an MCP server and a skill that guides Codex to use these tools when you ask about past local conversations.

## Requirements

- A Codex installation with `codex plugin` support and local conversation history.
- Python available as the `python` command. The server uses the standard library for ordinary `.jsonl` files.
- For compressed `.jsonl.zst` history, one of Python's `compression.zstd` module, the `zstandard` Python package, or the `zstd` command-line program.

The server reads `CODEX_HOME` if set; otherwise it uses `~/.codex`. It looks under `sessions/` and `archived_sessions/`.

## Install

Register this repository as a Git marketplace, then install the plugin:

```sh
codex plugin marketplace add https://github.com/JonasWooh/codex-thread-reader.git
codex plugin add codex-thread-reader@codex-thread-reader
```

Check the result with `codex plugin list`. Start a new Codex conversation if the new tools do not appear in an already-open one.

## Use

Ask Codex in ordinary language, for example:

```text
Read this earlier conversation: codex://threads/01234567-89ab-7cde-8f01-23456789abcd
List my five most recent local Codex threads.
Search my local Codex threads for "browser extension".
```

For a long conversation, `read_thread` returns the newest portion within `max_chars`. If its response says `truncated: true`, pass `next_older_end_item` back as `end_item` to retrieve the previous portion. A thread can be found successfully while returning an empty transcript if its rollout contains no visible messages.

## Privacy and scope

- The server reads local rollout files and does not modify Codex history or SQLite state. It makes no network requests; the Git URL above is used by Codex only to install the plugin.
- Hidden reasoning records are excluded. By default, tool traces and system/developer messages are also excluded.
- `include_tools=true` can expose tool arguments and outputs; `include_instructions=true` can expose system/developer messages from the selected local rollout. Use these options only when needed.
- Returned conversations may contain personal or confidential material. Only ask the agent to read threads you intend to share in the current conversation.
- This plugin works with local rollout files available to the Codex process. It does not retrieve conversations stored only in another installation or cloud account.

## Development

Run the bundled tests with Python:

```sh
python -m unittest discover -s tests -v
```

Repository layout:

```text
.agents/plugins/marketplace.json  Codex marketplace entry
plugin.json                       Plugin metadata
mcp.json                          Local MCP server configuration
server/                           Rollout reader and MCP server
skills/read-codex-thread/         Codex usage guidance
tests/                            Reader and protocol tests
```

The package is released under the [MIT License](LICENSE).
