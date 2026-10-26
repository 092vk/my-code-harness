# Backends

opencode-graph-harness supports two local MCP backends. Both build a persistent
graph and answer structural questions; they differ in engine, storage, indexing
speed, and tool surface.

The harness defaults to **codebase-memory-mcp**. Use **codegraph** when its
single-call exploration or framework-aware features fit your stack better.

The version numbers and commands below reflect the projects as of **October
2026**. Check each project's release notes before pinning versions in a team
environment.

## At a glance

| | codebase-memory-mcp (primary) | codegraph (secondary) |
|---|---|---|
| Project | [DeusData/codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp) | [colbymchenry/codegraph](https://github.com/colbymchenry/codegraph) |
| Engine | Pure C, vendored tree-sitter grammars | Rust kernel |
| Index storage | SQLite under `~/.cache/codebase-memory-mcp` | SQLite + FTS5 under `<repo>/.codegraph/` |
| Languages | 162 languages and file formats | 38 languages |
| MCP surface | 17 tools (`search_graph`, `trace_path`, `detect_changes`, `get_code_snippet`, `get_file_outline`, `get_architecture`, `search_code`, `query_graph`, coverage, indexing) | One primary tool, `codegraph_explore`; narrower tools are opt-in |
| Query model | Structural, BM25, semantic, Cypher subset, call/data-flow traces | One broad explore call returning relevant source, call paths, blast radius |
| Linux kernel index | ~3 min for 28M LOC / 75K files (published benchmark) | Not the primary target; use incremental sync |
| Freshness | Watcher/auto-sync for an active MCP project; CLI indexes may need a re-run | Auto-watch and incremental sync while the MCP server runs |
| OpenCode integration | Manual `mcp.servers` entry (written by setup.sh) | `codegraph install`, manual entry, or the optional bridge plugin |
| Telemetry | None | Opt-out; the harness sets `CODEGRAPH_TELEMETRY=off` |
| License | MIT | MIT |
| Best for | Very large C/C++ repos, deep call graphs, impact analysis, architecture queries | Fast one-call exploration, JS/TS/framework routes, low-friction setup |

### Name warning

Several unrelated projects are called "CodeGraph". This harness targets
`@colbymchenry/codegraph`, the Rust/SQLite project that exposes
`codegraph_explore` and integrates with OpenCode. It is not
`codegraph-ai/CodeGraph` (42 tools, RocksDB) and not the `codegraph.ru` security
platform. The npm package name and the `codegraph_explore` tool are the reliable
identifiers.

## codebase-memory-mcp

### Why it is the default

- It is built for very large codebases. The published full Linux kernel index
  (28M LOC, 75K files) produces 4.81M nodes and 7.72M edges in about three
  minutes on an M3 Pro.
- The tool surface is designed for precise, bounded queries: name search,
  call-path tracing, source snippets, file outlines, change impact, coverage,
  and read-only Cypher.
- The cache lives outside the repository, so indexing a huge checkout does not
  bloat the working tree or Git status.
- No telemetry and no hosted dependency.

### Install

```bash
# Official installer (macOS/Linux)
curl -fsSL https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/install.sh | bash

# Or npm (downloads and caches the native runtime on first run)
npm install -g codebase-memory-mcp
```

The harness template uses `npx -y codebase-memory-mcp`, so a global install is
optional. The first MCP start can take longer while the native runtime is
downloaded.

### Index

```bash
# Through the harness
~/tools/opencode-graph-harness/scripts/index.sh --backend cbm --project /path/to/repo

# Directly
codebase-memory-mcp cli index_repository --repo-path /path/to/repo
```

Status and projects:

```bash
codebase-memory-mcp cli list_projects --format json --detail stats
codebase-memory-mcp cli index_status --project <name>
```

### Configuration

OpenCode V2:

```json
{
  "mcp": {
    "servers": {
      "codebase-memory": {
        "type": "local",
        "command": ["npx", "-y", "codebase-memory-mcp"],
        "codemode": false,
        "timeout": { "startup": 120000, "catalog": 60000 }
      }
    }
  }
}
```

OpenCode V1 uses the same object directly under `mcp`:

```json
{
  "mcp": {
    "codebase-memory": {
      "type": "local",
      "command": ["npx", "-y", "codebase-memory-mcp"],
      "enabled": true
    }
  }
}
```

### Useful query patterns

```text
search_graph(name_pattern="drm_atomic_commit", label="Function", limit=5)
trace_path(function_name="drm_atomic_commit", direction="callers", depth=2)
trace_path(function_name="submit_bio", direction="callees", depth=4)
detect_changes()
get_code_snippet(qualified_name="...")
get_file_outline(path="drivers/gpu/drm/drm_atomic.c")
get_architecture()
check_index_coverage(paths=["drivers/gpu/drm"])
```

### Caveats

- Very large indexes consume disk and RAM during construction. Plan for it and
  keep the cache off network filesystems.
- A CLI-built graph is not automatically watched by every running session.
  Re-run indexing after large changes, or keep an MCP session on the project.
- Some languages are parsed structurally without full type resolution; treat
  dynamic dispatch results as candidates, not proof.

## codegraph

### Why choose it

- One `codegraph_explore` call is often enough for "how does X work" and
  "how does X reach Y" questions.
- It watches the working tree and incrementally syncs while its MCP server is
  running, so the index stays fresh during long sessions.
- It has framework-aware route edges for Next.js, Vue/Nuxt, SvelteKit, and
  related stacks.
- `codegraph install` gives native OpenCode wiring, including the correct
  `mcp.servers.codegraph` shape with `codemode: false`.

### Install

```bash
# Official installer (macOS/Linux)
curl -fsSL https://raw.githubusercontent.com/colbymchenry/codegraph/main/install.sh | sh

# Or npm
npm install -g @colbymchenry/codegraph

# Build the per-project index
cd /path/to/repo
codegraph init
```

The harness template uses `npx -y @colbymchenry/codegraph serve --mcp` for
portability. If startup latency matters, install globally and change the
command to `["codegraph", "serve", "--mcp"]`.

### Index

```bash
# Through the harness
~/tools/opencode-graph-harness/scripts/index.sh --backend codegraph --project /path/to/repo

# Directly
codegraph init /path/to/repo        # first time
codegraph index /path/to/repo       # full re-index
codegraph index /path/to/repo --force
codegraph status /path/to/repo
codegraph uninit /path/to/repo      # remove the project index
```

Add `.codegraph/` to the repository's `.gitignore`.

### Configuration

OpenCode V2:

```json
{
  "mcp": {
    "servers": {
      "codegraph": {
        "type": "local",
        "command": ["npx", "-y", "@colbymchenry/codegraph", "serve", "--mcp"],
        "codemode": false,
        "environment": { "CODEGRAPH_TELEMETRY": "off" },
        "timeout": { "startup": 120000, "catalog": 60000 }
      }
    }
  }
}
```

OpenCode V1 uses `mcp.codegraph` with `"enabled": true`.

CodeGraph's own installer writes the V2 entry and sets `codemode: false` so
`codegraph_explore` stays on the native tool list.

### Optional OpenCode plugin

`opencode-codegraph-bridge` can register the MCP server and build the first
index automatically:

```jsonc
{
  "plugins": ["opencode-codegraph-bridge"]
}
```

Use the plugin **or** the manual MCP entry from this harness, not both. The
plugin owns registration, indexing, and session guidance; a second manual entry
creates duplicate tools.

The plugin expects a safe Git project root and uses the `@colbymchenry/codegraph`
runtime. See its documentation for v1/v2 plugin shapes and update behavior.

### Useful query patterns

```text
codegraph_explore(query="how does authentication middleware protect routes?")
codegraph_explore(query="trace the request from this handler to the database write")
codegraph_explore(query="where is rate limiting configured?")
```

If enabled with `CODEGRAPH_MCP_TOOLS=explore,node,search,callers,callees,impact`:

```text
codegraph_search(query="parse config")
codegraph_node(symbol="load_config")
codegraph_callers(symbol="load_config", depth=2)
codegraph_callees(symbol="load_config", depth=2)
codegraph_impact(symbol="load_config", depth=3)
```

### Caveats

- The index lives in `.codegraph/` inside the repository. It can be large and
  must be ignored by Git.
- A single live writer per project is enforced. Multiple MCP hosts should share
  the daemon; do not run two direct writers against the same index.
- `codegraph_explore` returns substantial source context by design. It is still
  far cheaper than a search-and-read loop, but it is not a minimal metadata
  query. Budget for it accordingly.

## Which should I use?

| Situation | Recommendation |
|---|---|
| Linux kernel, U-Boot, firmware, large C/C++ | codebase-memory-mcp |
| Very large monorepo, deep call graphs, impact analysis | codebase-memory-mcp |
| You want precise metadata queries and pagination | codebase-memory-mcp |
| TypeScript/JavaScript app with framework routes | codegraph |
| You want one exploration call and automatic sync | codegraph |
| You already use CodeGraph's OpenCode installer | codegraph |
| You want to compare both on your own tasks | install both, enable one at a time |

Running both backends enabled for the same repository duplicates tool surfaces
and splits the agent's attention. If you benchmark them, disable one server
between runs and use the same prompts.

## Configuration shapes: V1 vs V2

| | V1 | V2 |
|---|---|---|
| Location of server key | `mcp.<name>` | `mcp.servers.<name>` |
| Enabled flag | `"enabled": true` | no field; use `"disabled": true` to disable |
| Plugins key | `plugin` | `plugins` |

The harness writes V2. Use `templates/opencode.json` or
`templates/opencode.jsonc` as reference, and translate the shape if you are
still on V1.

## Operational notes

- **One index per repository.** Do not point two MCP servers at the same
  project index.
- **Keep indexes out of Git.** codebase-memory-mcp already does; add
  `.codegraph/` for codegraph.
- **Index after big changes.** Watchers handle normal edits, but a rebase,
  branch switch, or long offline period may leave the graph behind.
- **Check coverage before negative claims.** Both backends can report gaps;
  neither can prove that something is absent outside the indexed scope.
- **Do not index secrets.** Graph databases can contain source snippets. Treat
  caches as sensitive.
