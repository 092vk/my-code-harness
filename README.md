# opencode-graph-harness

Graph-first code navigation for [OpenCode](https://opencode.ai) and any
MCP-capable coding agent.

Large repositories make agents expensive. A single "where is this implemented?"
question can turn into `grep`, a directory walk, and a dozen full-file reads.
The agent pays for the same discovery work on every session, and most of the
context it burns is unrelated to the task.

This harness fixes that by making a **persistent code graph** the agent's first
stop instead of raw file search:

- a local MCP server indexes symbols, calls, imports, and files once;
- the graph answers structural questions in a few hundred tokens;
- `AGENTS.md` and a skill make graph-first navigation a hard rule;
- raw file reads become a narrow fallback, not the default workflow.

The harness itself is small. Heavy indexes live outside the repository
(`~/.cache/codebase-memory-mcp` for the primary backend).

## Quick start

```bash
# 1. Get the harness somewhere permanent
git clone <this-repo> ~/tools/opencode-graph-harness

# 2. Install it into the repository you want to navigate
cd ~/src/linux
~/tools/opencode-graph-harness/scripts/setup.sh --backend cbm .

# 3. Build the graph (or use: setup.sh --index .)
~/tools/opencode-graph-harness/scripts/index.sh --project .

# 4. Open OpenCode in the repository and ask a structural question
opencode
```

Replace `<this-repo>` with the clone URL you use. If the harness is already in a
submodule or a zip, point the commands at that directory instead.

Then try prompts such as:

- "Who calls `drm_atomic_commit`? Show the direct callers."
- "How does a page fault reach the page allocator?"
- "What breaks if I change this function's signature?"
- "Where is this config option parsed, and which files consume it?"

The agent should answer with graph calls, not a repository-wide grep. If it
starts with `grep -R`, the rules are not loaded or the MCP server is not
connected; see [Troubleshooting](#troubleshooting).

## What gets installed

`setup.sh` installs four things into the target repository:

| Path | Purpose |
|---|---|
| `AGENTS.md` | A managed block of mandatory graph-first rules. Existing content is preserved. |
| `.opencode/skills/code-navigation/SKILL.md` | Workflow recipes, tool mapping, good/bad examples, and token budgeting. |
| `opencode.json` or an existing `opencode.json(c)` | MCP server entry for the chosen backend, merged without destroying comments. |
| `.opencode/graph-harness/manifest.json` | Install record, backend choice, and backups. |

The harness checkout stays outside the target. Setup is idempotent: re-running
it updates managed files and backs up anything it replaces. `--dry-run` shows
the plan; `--force` replaces a conflicting MCP server entry.

Uninstall with:

```bash
~/tools/opencode-graph-harness/scripts/uninstall.sh ~/src/linux
```

Indexes are intentionally left in place. Remove `~/.cache/codebase-memory-mcp`
or run `codegraph uninit` if you want the data gone too.

## How it works

```text
                    one-time / incremental indexing
 source tree  ───────────────────────────────────────▶  local graph
 (Linux, U-Boot,                                           │
  firmware, app)                                           │  stdio MCP
                                                           ▼
                                              ┌─────────────────────────┐
                                              │  OpenCode agent         │
                                              │                         │
  grep / find / read many files  ◀── fallback │  1. graph query         │
                                              │  2. snippet for match   │
                                              │  3. bounded read if     │
                                              │     the graph can't     │
                                              └─────────────────────────┘
```

The graph is the structural source of truth. Think of it as a whole-repository
header index: it tells you which symbols exist, where they are declared, and how
they connect. You still open the implementation you need, but you no longer
open twenty files to find out which one matters.

### Why the rules are mandatory

A polite suggestion ("consider using the graph") loses to habit. The installed
`AGENTS.md` states explicit MUST rules:

1. graph query before `grep`, `find`, `rg`, `git grep`, or bulk reads;
2. the graph is authoritative for structure;
3. raw file access is allowed only for a defined set of fallbacks;
4. negative claims require a freshness/coverage check;
5. queries must be batched, bounded, and paginated;
6. after edits, callers/impact must be re-queried.

The skill supplies the *how*: concrete tool mappings, recipes, and examples of
good versus bad usage.

## Backends

### Primary: codebase-memory-mcp

- local, pure-C MCP server with a persistent SQLite graph;
- 17 graph tools (`search_graph`, `trace_path`, `detect_changes`,
  `get_code_snippet`, `get_file_outline`, `get_architecture`, `search_code`,
  `check_index_coverage`, and more);
- 162 languages and file formats;
- full Linux kernel index measured at about **3 minutes** (28M LOC, 75K files,
  4.81M nodes, 7.72M edges);
- indexes under `~/.cache/codebase-memory-mcp`;
- no telemetry.

Install (either is supported):

```bash
curl -fsSL https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/install.sh | bash
# or
npm install -g codebase-memory-mcp
```

The generated config uses `npx -y codebase-memory-mcp`, so a global install is
optional. The first MCP start downloads and caches the native runtime.

### Secondary: codegraph

- local Rust kernel with a SQLite + FTS5 graph in the project;
- one strong MCP tool, `codegraph_explore`, which returns relevant source, call
  paths, and blast radius in one call;
- 38 languages; automatic file watching and incremental sync;
- native OpenCode integration (`codegraph install`);
- optional `opencode-codegraph-bridge` plugin for automatic setup and first
  indexing.

Install:

```bash
curl -fsSL https://raw.githubusercontent.com/colbymchenry/codegraph/main/install.sh | sh
# or
npm install -g @colbymchenry/codegraph

cd /path/to/repo
codegraph init
```

Select it with `setup.sh --backend codegraph`. The generated entry is:

```json
{
  "type": "local",
  "command": ["npx", "-y", "@colbymchenry/codegraph", "serve", "--mcp"],
  "codemode": false,
  "environment": { "CODEGRAPH_TELEMETRY": "off" }
}
```

If you use the optional plugin, do not also register a manual `codegraph` MCP
entry; the plugin owns registration and indexing.

See [docs/backends.md](docs/backends.md) for a detailed comparison.

## Using it on a real large repository

Linux kernel example:

```bash
# Shallow clone is enough for code navigation
git clone --depth 1 https://github.com/torvalds/linux.git ~/src/linux
cd ~/src/linux

~/tools/opencode-graph-harness/scripts/setup.sh --backend cbm .
~/tools/opencode-graph-harness/scripts/index.sh --project .
```

Indexing runs once and is reused across sessions. For a repository that size,
expect the first index to take minutes (the backend's published measurement is
about 3 minutes on an M3 Pro) and to need substantial disk space. Later queries
are sub-second graph lookups.

Useful first questions:

```text
Find the definition of schedule_work and list its direct callers.
How does a block I/O request flow from submit_bio to the device driver?
Which functions call into this subsystem from outside its directory?
What is the blast radius of changing this struct field?
```

What a good session looks like:

1. one `search_graph` or `codegraph_explore` call to locate candidates;
2. one `trace_path` for callers/callees or flow;
3. one `get_code_snippet` for the implementation you actually need;
4. a bounded `read` only when editing or when the graph reports a gap.

What a bad session looks like:

1. `grep -R "schedule_work" .` returns thousands of lines;
2. the agent reads a header, a source file, and five call sites;
3. the answer is correct but the context is mostly noise;
4. the next session repeats the process.

## Configuration

The harness writes OpenCode **V2** configuration under `mcp.servers`. A minimal
example is in [templates/opencode.json](templates/opencode.json), and a complete
commented example is in [templates/opencode.jsonc](templates/opencode.jsonc).

Key points:

- `codemode: false` keeps graph tools on the native tool list. Remove it to use
  OpenCode's Code Mode namespaces (`tools.codebase_memory.search_graph(...)`).
- `timeout.startup` is raised because the first `npx` start may download a
  runtime.
- CodeGraph's template entry is disabled by default; enable exactly one backend
  per repository unless you are deliberately comparing them.
- If you are on OpenCode V1, translate the entry to `mcp.<name>` with
  `enabled: true`; V2 uses `mcp.servers.<name>` with `disabled: false`.

Check that the server is connected:

```bash
opencode mcp list
```

OpenCode reloads configuration on save. Use `/reload` if a running session does
not pick up the new tools.

### Environment variables

| Variable | Effect |
|---|---|
| `CBM_CACHE_DIR` | Move the codebase-memory-mcp index cache. |
| `CBM_ALLOWED_ROOT` | Restrict which paths the indexer may read. |
| `CODEGRAPH_DIR` | Override the CodeGraph data directory name. |
| `CODEGRAPH_TELEMETRY=off` | Disable CodeGraph telemetry (set by the template). |

## Keeping the index fresh

- **codebase-memory-mcp** can auto-sync while an MCP session owns the project.
  A graph built by the CLI, or a different project, may need a re-run after a
  large pull.
- **codegraph** watches the working tree while its MCP server runs and
  incrementally syncs changes.
- Both backends expose freshness/coverage information. The agent is instructed
  to check it before claiming that something does not exist.

After a large rebase, a branch switch, or a long period with no MCP session:

```bash
~/tools/opencode-graph-harness/scripts/index.sh --project .
```

Use `--status` to inspect the index without rebuilding it.

## Token savings

The mechanism is simple: replace repeated discovery with a compact structural
query, then read only the code that matters.

Published measurements from the backends:

| Backend | Reported result | Scope |
|---|---|---|
| codebase-memory-mcp | ~10× lower token use, 2.1× fewer tool calls | 31-repository preprint |
| codebase-memory-mcp | ~3,400 vs ~412,000 tokens for structural queries | earlier five-query comparison |
| codegraph | 62% fewer tokens, 88% fewer tool calls, zero file reads | 7 open-source repositories, median of 4 runs |

These are workload-specific results, not guarantees. Small single-file edits,
generated code, and brand-new unindexed code will see little benefit. The gains
come from structural discovery in medium and large repositories.

See [docs/token-savings.md](docs/token-savings.md) for the cost model, caveats,
and a measurement protocol you can run on your own tasks.

## Repository layout

```text
opencode-graph-harness/
├── README.md
├── AGENTS.md                      # rules installed into target repos
├── LICENSE
├── .opencode/skills/code-navigation/SKILL.md
├── templates/
│   ├── opencode.json              # minimal V2 config
│   └── opencode.jsonc             # complete, commented V2 config
├── scripts/
│   ├── setup.sh                   # install / update
│   ├── index.sh                   # index / re-index / status
│   ├── uninstall.sh               # clean removal
│   └── lib/
│       ├── install.py             # installer, manifest, backups
│       └── opencode_config.py     # comment-preserving JSONC merge
├── tests/test_opencode_config.py
└── docs/
    ├── backends.md
    └── token-savings.md
```

## Using it as a submodule

A submodule keeps the harness versioned with the repository but out of the
source tree's way:

```bash
cd ~/src/my-project
git submodule add <this-repo> tools/opencode-graph-harness
./tools/opencode-graph-harness/scripts/setup.sh .
./tools/opencode-graph-harness/scripts/index.sh --project .
```

Commit `.opencode/graph-harness/manifest.json` if you want teammates to share
the backend choice; ignore `backups/` (the installer creates a local
`.gitignore` for it). Do not commit index data.

## Requirements

- Bash 3.2+ (macOS default is fine).
- Python 3.8+ for the installer and JSONC merge (standard library only).
- One backend runtime:
  - `codebase-memory-mcp` on `PATH`, or Node.js + `npx`;
  - `codegraph` on `PATH`, or Node.js + `npx`.
- Git is recommended but not required.
- Linux and macOS are supported. Windows users should use WSL or Git Bash and
  a platform supported by the chosen backend.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Agent still greps first | Check that `AGENTS.md` contains the managed block and the skill exists. Reload OpenCode. |
| No MCP tools | Run `opencode mcp list`; check the server entry and `npx` availability. Use `/reload`. |
| Server starts slowly the first time | `npx` is downloading the runtime. Wait for the 120s startup timeout, or install globally and change the command to the native binary. |
| "Not indexed" / empty results | Run `scripts/index.sh --project <repo>` and retry. |
| Config conflict on setup | An existing server with the same name differs. Re-run with `--force`, or merge manually. |
| Comments disappeared (should not happen) | The installer preserves JSONC comments. Check the backup under `.opencode/graph-harness/backups/`. |
| CodeGraph index is large | Add `.codegraph/` to `.gitignore`. Use `codegraph uninit` to remove it. |
| Tool approval prompts | Add permission rules from the commented example in `templates/opencode.jsonc`. |

## Security and privacy

- Both backends run locally over stdio. Source code is not sent to a hosted
  service for indexing or querying.
- codebase-memory-mcp collects no telemetry.
- CodeGraph telemetry is disabled by the generated config. Re-enable it only if
  you want to support that project.
- Graph databases contain source snippets and symbol names. Treat the cache and
  `.codegraph/` as source-code artifacts: do not publish them or place them on
  shared storage.
- The MCP server inherits the agent's filesystem permissions. Use
  `CBM_ALLOWED_ROOT` if you need to confine indexing.

## Development

```bash
python3 -m unittest discover -s tests -v
bash -n scripts/setup.sh scripts/index.sh scripts/uninstall.sh
shellcheck scripts/*.sh   # optional
```

The JSONC editor is intentionally tested against comments, trailing commas,
nested objects, conflicts, and idempotent merges. Add a test with any change to
`scripts/lib/opencode_config.py`.

## License

MIT. See [LICENSE](LICENSE).

The backends are separate projects with their own licenses: codebase-memory-mcp
is MIT; CodeGraph is MIT (check the version you install).
