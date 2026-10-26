# Graph-first code navigation (mandatory)

This repository has a persistent, pre-built code knowledge graph. It is the
primary source of truth for **structure**: symbols, definitions, call chains,
dependencies, tests, and change impact.

Use the graph the way C and C++ use headers: read the declaration index first,
then open only the implementation you actually need. Do not rediscover the
codebase with `grep`, `find`, `rg`, or bulk file reads.

These rules are not suggestions. They apply to every task: exploration,
implementation, debugging, refactoring, review, and test selection.

## Non-negotiable rules

1. **Graph query first.** For any question about where code lives, what calls
   what, what a change affects, how a feature flows, or which tests to run, the
   **first** code-discovery action MUST be a graph MCP tool call. Do not start
   with `grep`, `rg`, `find`, `ls -R`, `git grep`, or reading files.
2. **The graph is authoritative for structure.** Prefer its symbol names,
   qualified names, paths, line numbers, edges, and call paths over guesses
   derived from filenames or partial reads.
3. **Raw file access is a fallback, not a default.** Read files only when the
   graph cannot answer the question (see *Permitted fallbacks*). When it can
   answer, stop querying and answer.
4. **Never scan the repository to discover code.** Recursive searches from the
   repository root are forbidden while an index exists. After a graph query,
   a bounded search inside one known file or directory is allowed if needed.
5. **Resolve before you read.** Find the symbol with `search_graph` /
   `codegraph_explore`, then fetch the exact snippet with `get_code_snippet` /
   `codegraph_node`. Do not open whole files to find a function.
6. **Verify before negative claims.** "No callers", "unused", "dead code",
   "nothing depends on this", and "safe to delete" require a graph result plus
   a coverage/freshness check. If coverage is incomplete, say so explicitly.
7. **Batch and bound every query.** Prefer one broad `explore` / `search_graph`
   call over five narrow ones. Use `limit`, `source_max_lines`,
   `max_output_tokens`, and pagination. Never request a whole-repository dump.
8. **Re-query after edits.** After changing a signature, moving code, or
   deleting a symbol, re-run callers/impact on the affected symbols. Do not
   re-read the entire file tree to "check".
9. **Keep the index fresh, but do not thrash it.** Do not re-index inside a
   query loop. If results are stale or a path is missing, run the harness
   `scripts/index.sh` once (or the backend's index tool) and retry.
10. **Cite graph evidence.** In summaries and reviews, name the qualified
    symbol and `path:line` returned by the graph. This keeps conclusions
    auditable and prevents invented structure.

## Required first move

| Question | Primary graph call | Backend |
|---|---|---|
| Where is `foo` defined? | `search_graph(name_pattern="foo")` | codebase-memory-mcp |
| Understand a feature or flow | `search_graph(...)` then `trace_path(...)` | codebase-memory-mcp |
| Who calls `foo`? | `trace_path(function_name="foo", direction="callers")` | codebase-memory-mcp |
| What does `foo` call? | `trace_path(function_name="foo", direction="callees")` | codebase-memory-mcp |
| What breaks if I change this? | `detect_changes(...)` or `trace_path(direction="callers", depth=3)` | codebase-memory-mcp |
| Show only the body of `foo` | `get_code_snippet(qualified_name="...")` | codebase-memory-mcp |
| Outline a file | `get_file_outline(path="...")` | codebase-memory-mcp |
| Architecture / entry points | `get_architecture(...)` | codebase-memory-mcp |
| Text inside indexed code | `search_code(query="...")` | codebase-memory-mcp |
| Any structural question | `codegraph_explore(query="...")` | codegraph |

`codegraph_explore` is intentionally broad: it returns relevant source grouped
by file, call paths, and blast radius in one call. Prefer one strong
`codegraph_explore` call over many narrow searches. The narrower CodeGraph
tools (`codegraph_callers`, `codegraph_callees`, `codegraph_impact`,
`codegraph_search`, `codegraph_node`) are opt-in; use them only if they are
present in the tool list.

### Tool invocation

The tables above use the backends' logical tool names. How they appear depends
on the client:

- **OpenCode V2 with `codemode: false`** (the harness default): MCP tools are on
  the native tool list and are prefixed with the server name. Call
  `codebase_memory_search_graph`, `codebase_memory_trace_path`,
  `codebase_memory_detect_changes`, or `codegraph_codegraph_explore`.
- **OpenCode Code Mode** (the default when `codemode` is omitted): call through
  the grouped namespace, for example
  `tools.codebase_memory.search_graph({...})` or
  `tools.codegraph.codegraph_explore({...})`.
- **Other MCP clients** commonly expose the unprefixed names used in the tables,
  such as `search_graph` and `codegraph_explore`.

Use whichever form your tool list actually shows. If the graph server is
unavailable or returns an explicit "not indexed" response, follow the fallback
protocol below and state that the graph was unavailable. Never pretend a graph
call happened.

## Permitted fallbacks

Fall back to raw file operations only when at least one of these is true:

1. The graph reports the path/file/symbol as **not indexed**, skipped, or
   outside its supported languages.
2. The question is about **non-code content**: build scripts, CI YAML, docs,
   generated files, vendored code, binary assets, or config not in the graph.
3. The graph returned a symbol but you need the **exact current text or line
   range** for an edit, and the snippet tool did not return enough context.
4. You must verify a **suspicious or contradictory** graph result before making
   a destructive change.
5. The task is a **single-file, line-local edit** where the file is already
   known and the graph adds no discovery value. Still use the graph to check
   callers/impact before changing a public interface.

When falling back, stay narrow:

- Read one file with an explicit offset/limit, not the whole tree.
- Use a path-scoped search (`rg "pattern" path/to/file.c`), never `rg pattern .`.
- Keep raw reads under roughly 200 lines per call unless the file is genuinely
  the subject of the task.
- Do not repeat the same read after a successful graph query.

## Negative claims and freshness

Before saying that something does not exist, is unused, or is safe to delete:

1. Query the graph with a depth and scope that matches the claim.
2. Check index freshness/coverage (`check_index_coverage`, `index_status`, or
   the CodeGraph staleness banner).
3. Prefer precise wording: "The graph reports no callers within the indexed
   scope" instead of "nothing calls this".
4. If coverage is incomplete, say which paths were not indexed and what you
   verified manually.

## Index lifecycle

- **codebase-memory-mcp** keeps indexes under `~/.cache/codebase-memory-mcp`
  by default. It can auto-sync an active project session; a CLI index or a
  different project may need a re-run.
- **codegraph** stores `.codegraph/` in the project and auto-syncs while its
  MCP server runs. Add `.codegraph/` to `.gitignore`.
- Re-index with the harness: `path/to/opencode-graph-harness/scripts/index.sh`.
  If you do not know the harness path, read `harness_dir` from
  `.opencode/graph-harness/manifest.json`.
- Do not run a full re-index as a substitute for a precise query, and do not
  delete the index directory while an MCP server is running.

## Token discipline

The graph exists to reduce context use. Protect that advantage:

- Ask for the smallest result that answers the question. Start with a name
  search; add source only for the matching symbols.
- Prefer signatures, outlines, and call paths over full function bodies.
- Do not paste large graph outputs into the conversation. Summarize and cite.
- Stop querying once the answer is supported. Extra "just to be sure" reads
  are how token savings disappear.

## Anti-patterns (do not do these)

- `grep -R "foo" .` followed by reading ten candidate files.
- `find . -name "*.c"` to locate a subsystem.
- Reading a 5,000-line header to find one declaration.
- Claiming "no callers" after inspecting two files.
- Re-indexing on every question.
- Using the graph once and then falling back to a full repository scan.
- Dumping the entire architecture or graph into the response.

## Definition of done for structural work

A structural answer is complete only when:

- the relevant symbols and call paths came from the graph;
- every cited file/line was returned by the graph or verified with a bounded
  read;
- impact/callers were checked before interface changes;
- freshness limitations are stated if any; and
- no unnecessary repository-wide search was performed.
