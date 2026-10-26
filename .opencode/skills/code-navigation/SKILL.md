---
name: code-navigation
description: Graph-first navigation for indexed repositories. Use when exploring, understanding, modifying, debugging, or reviewing code so the persistent knowledge graph answers structural questions before grep, find, or bulk file reads. Covers symbol search, callers, callees, impact, flows, snippets, architecture, freshness, and token budgeting.
license: MIT
metadata:
  harness: opencode-graph-harness
  version: "1.0.0"
---

# Code navigation with a persistent graph

<!-- managed by opencode-graph-harness; re-run setup.sh to update -->

The repository has a pre-built code graph exposed through MCP. The graph already
knows the symbols, call edges, imports, and file locations that a search loop
would rebuild from scratch. Query it first.

## The one rule

**For any structural question, call a graph tool before any file-search tool.**

Structure includes: where a symbol is defined, who calls it, what it calls,
what a change affects, how a feature flows, which tests cover it, and how a
module is organized. If the question is about any of that, the first discovery
call is a graph call.

Raw reads are for the final mile: exact current text, a line-range edit, or a
path the graph explicitly reports as unindexed.

## Tool map

Two backends are supported. Use whichever one is present; do not configure or
call both for the same question.

### codebase-memory-mcp (primary)

| Goal | Tool |
|---|---|
| Find symbols by name / pattern / semantic intent | `search_graph` |
| Trace callers or callees | `trace_path` with `direction: "callers"` or `"callees"` |
| Follow a flow / cross-service path | `trace_path` with `mode` (`calls`, `data_flow`, `cross_service`, `topology`, `deploy`, `run`) |
| Map a diff to affected symbols | `detect_changes` |
| Read one symbol's source | `get_code_snippet` |
| List declarations in a file | `get_file_outline` |
| Architecture, hotspots, entry points | `get_architecture` |
| Text search inside indexed code | `search_code` |
| Custom structural query | `query_graph` (read-only Cypher subset) |
| Verify a path is indexed and fresh | `check_index_coverage` |
| Index / inspect index | `index_repository`, `index_status`, `list_projects` |

Useful arguments: `name_pattern`, `label` (e.g. `Function`, `Class`),
`qualified_name`, `file_path`, `depth` (1–15), `limit`, `source_max_lines`,
`max_output_tokens`, `result_limit` / `result_offset`.

### codegraph (secondary)

| Goal | Tool |
|---|---|
| Almost any structural question | `codegraph_explore` |
| One symbol + callers | `codegraph_node` (opt-in) |
| Symbol search | `codegraph_search` (opt-in) |
| Callers / callees | `codegraph_callers`, `codegraph_callees` (opt-in) |
| Blast radius | `codegraph_impact` (opt-in) |
| Index status | `codegraph_status` (opt-in) |

`codegraph_explore` is the primary tool by design. One call can return the
relevant symbols' source grouped by file, the call paths between them, and a
blast-radius summary. Trust that output; do not re-verify it with `grep`.
The narrower tools only appear when `CODEGRAPH_MCP_TOOLS` enables them.

### Invocation form

The tool tables use the backends' logical names. OpenCode adds context:

- **Native tool list** (V2 config with `codemode: false`): tools are prefixed
  with the server name, for example `codebase_memory_search_graph`,
  `codebase_memory_trace_path`, and `codegraph_codegraph_explore`.
- **Code Mode** (V2 default): `tools.codebase_memory.search_graph({...})`,
  `tools.codegraph.codegraph_explore({...})`.
- **Other clients**: usually the unprefixed names in the tables.

Use the form that appears in your tool list. If a tool is missing, check whether
the backend is disabled in `opencode.json(c)` before falling back to file
search.

## Workflow recipes

### 1. "Where is X implemented?"

1. `search_graph(name_pattern="X", label="Function")` or one
   `codegraph_explore(query="where is X implemented")`.
2. Read only the best match with `get_code_snippet` / `codegraph_node`.
3. Answer with the qualified name and file:line. Stop.

Do **not** grep for `X`, list directories, and open every match.

### 2. "How does feature Y work?"

1. One broad query: `search_graph(query="Y")` or
   `codegraph_explore(query="how does Y work")`.
2. Use `trace_path` from the entry symbol to the outcome symbol when the flow
   is not already in the result.
3. Fetch snippets only for the two or three symbols on the path that matter.
4. Summarize the path; cite each hop.

### 3. "Who calls this function?"

1. `trace_path(function_name="foo", direction="callers", depth=2)` (or
   `codegraph_callers` if enabled).
2. If the answer is used for a risky change, repeat with `depth=3` or use
   `detect_changes`.
3. Only if the result is empty **and** coverage is uncertain, inspect the
   callers manually.

Saying "no callers" after reading one file is a correctness bug.

### 4. "What breaks if I change this signature?"

1. `trace_path(function_name="foo", direction="callers", depth=3)`.
2. `detect_changes` on the working tree once edits exist.
3. `search_graph` for interface/implementor relationships if the language uses
   dynamic dispatch.
4. Summarize direct callers, transitive impact, and tests. Read call sites only
   when you are about to edit them.

### 5. "Where should I add this code?"

1. `get_architecture` / `codegraph_explore` for the owning module.
2. `get_file_outline` for candidate files instead of opening them.
3. `search_graph` for sibling patterns to imitate.
4. Read the one file you will edit.

### 6. "Which tests should I run?"

1. `detect_changes` for affected symbols, or CodeGraph's blast-radius section.
2. `search_graph` for test symbols referencing the changed symbols.
3. Fall back to the project's test discovery only if the graph has no test
   relationships for this language.

### 7. Debugging a failure

1. Locate the failing symbol from the stack trace with `search_graph`.
2. `trace_path(direction="callers", depth=2)` to find the entry path.
3. Read only the implicated snippets and their immediate callees.
4. Do not scan the repository for log strings; use `search_code` (indexed) or a
   path-scoped search.

## Good vs bad

### Good: locate a function (about 300–800 tokens)

```text
search_graph(name_pattern="drm_atomic_commit", label="Function", limit=5)
get_code_snippet(qualified_name="drm_atomic_commit")
```

One name query, one snippet, answer with file:line.

### Bad: rediscover the function (often 30k–150k tokens)

```text
grep -R "drm_atomic_commit" .
# then read drivers/gpu/drm/drm_atomic.c
# then read include/drm/drm_atomic.h
# then read three callers
```

The graph already had the definition and call edges. The search loop paid for
the same discovery again and polluted the context with unrelated matches.

### Good: impact analysis (one or two calls)

```text
trace_path(function_name="foo", direction="callers", depth=3)
detect_changes()
```

### Bad: impact analysis by inspection

```text
rg "foo\\(" .
# open 12 files to see whether each call site matters
```

### Good: architecture survey

```text
get_architecture()
get_file_outline(path="src/core/engine.c")
```

### Bad: architecture survey by directory walk

```text
find . -maxdepth 4 -type f | head -200
cat README.md ARCHITECTURE.md src/core/engine.c
```

## Budgeting and pagination

- Start narrow: name search before source, signatures before bodies.
- Use `limit`/`result_limit` first; page only if the answer is not in the top
  results.
- Use `source_max_lines` and `max_output_tokens` for source-bearing calls.
- Prefer `get_file_outline` over reading a file to learn its contents.
- One broad `explore`/`search_graph` call usually beats several narrow ones.
- Stop when the answer is supported. Extra verification reads are not free.

## Freshness and coverage

The graph is a snapshot plus, for some backends, a live watcher.

Before a negative or destructive conclusion:

1. Check the index: `check_index_coverage` (CBM), the staleness banner in
   `codegraph_explore`, or `codegraph_status`.
2. Re-index once if the working tree changed substantially:
   `path/to/opencode-graph-harness/scripts/index.sh` (the harness path is
   recorded as `harness_dir` in `.opencode/graph-harness/manifest.json`).
3. State limits precisely: "no callers in the indexed scope" is honest;
   "nothing calls this" may be false.

Do not re-index before every question. Do not run a full re-index while another
indexing job is active.

## Permitted raw fallbacks

Use ordinary tools only when:

- the path is reported unindexed, skipped, or unsupported;
- the content is not code (CI config, docs, generated/vendored files);
- you need exact current lines for an edit and the snippet is insufficient;
- you must resolve a contradiction before a destructive change; or
- it is a single-file, line-local edit with no structural question.

Even then:

- read one file at a time with offset/limit;
- scope searches to a known path;
- keep reads around 200 lines;
- do not repeat a read the graph already answered.

## Failure handling

| Situation | Action |
|---|---|
| Tool not found | Check backend config; if disabled, use the other backend or report the limitation. |
| "Not indexed" | Run the harness index script once, then retry. |
| Empty result with suspicious confidence | Check coverage; try a broader name pattern or a different label; then a bounded file search. |
| Stale/missing symbols after edits | Let the watcher catch up or re-index; mention the freshness limit. |
| Graph server crash/timeout | Fall back to bounded reads, and tell the user the graph was unavailable. |

## Reporting

When you answer from the graph:

- cite the qualified symbol and `path:line`;
- distinguish direct callers from transitive impact;
- state the query depth and any coverage gaps;
- keep the summary shorter than the evidence you consumed.
