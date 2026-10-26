# Token savings

The harness exists to make structural questions cheap. This document explains
the mechanism, the published evidence, where the savings come from, and where
they do not.

## The short version

A coding agent exploring an unfamiliar repository usually pays twice:

1. **Discovery cost** — search, directory walks, and reading candidate files to
   find the code that matters.
2. **Context cost** — every candidate file stays in the context window and is
   re-sent on later turns.

A persistent graph moves discovery to a one-time indexing pass. At query time
the agent asks a compact structural question and receives a ranked, bounded
answer: symbol names, paths, line numbers, call edges, and only the source
snippets it needs.

The savings are largest on structural questions in medium and large
repositories. They are small or negative on tiny, single-file, or unindexed
work.

## Why raw exploration is expensive

Consider "who calls `foo` and what breaks if I change it?" without a graph:

```text
grep -R "foo" .                    # thousands of matches in a large repo
find . -name "*.h"                 # locate candidate declarations
read include/foo.h                 # 1,500 lines
read src/foo.c                     # 3,000 lines
read src/a.c src/b.c src/c.c       # verify call sites
grep again for a variant name
read two more files because the first
  search missed a macro-generated call
```

Each read is billed once when produced and again as part of the context on
every subsequent model turn. A 3,000-line file at roughly 8–12 tokens per line
is 24k–36k tokens. Three such files plus search output can exceed 100k tokens
before the agent has written a single line of code.

With a graph:

```text
trace_path(function_name="foo", direction="callers", depth=2)
get_code_snippet(qualified_name="foo", source_max_lines=80)
```

The result is typically hundreds to a few thousand tokens: a bounded list of
callers plus the one body you asked for.

## The cost model

A rough model for a discovery task:

```text
raw cost   ≈ search_output
           + Σ(candidate_files × average_file_tokens)
           + replayed_context_on_later_turns

graph cost ≈ index_query_result
           + Σ(selected_snippets × snippet_tokens)
           + small tool-schema overhead
```

The important term is the middle one in the raw path. Agents are bad at knowing
which candidate is relevant before reading it, so they read several. The graph
ranks candidates by structure and relationships first, which shrinks the set
that must be read to one or two.

### Hypothetical example

A medium C repository, one "where is this implemented?" question:

| Step | Raw exploration | Graph-first |
|---|---:|---:|
| Search output | 18,000 tokens | — |
| Candidate file reads (6 files) | 72,000 tokens | — |
| Graph name query | — | 500 tokens |
| Call-path trace | — | 900 tokens |
| Focused snippet (1 function) | — | 1,200 tokens |
| **Discovery total** | **~90,000** | **~2,600** |

That is roughly a 30× reduction for this step. Real tasks vary; the point is
the shape of the curve, not the exact multiplier. The graph wins because it
replaces unordered candidates with a ranked structural answer.

## Published evidence

These numbers come from the backend projects, not from this harness. They are
useful as an order-of-magnitude guide, not a guarantee for your workload.

### codebase-memory-mcp

The project's preprint reports, across 31 repositories:

- **~10× lower token use**
- **2.1× fewer tool calls**

An earlier five-query comparison reported roughly **3,400 tokens versus
412,000 tokens** for structural queries.

The same project publishes indexing performance:

| Operation | Time | Scope |
|---|---:|---|
| Linux kernel full index | ~3 min | 28M LOC, 75K files, 4.81M nodes, 7.72M edges |
| Linux kernel fast index | ~1m 12s | 1.88M nodes |
| Cypher query | <1 ms | relationship traversal |
| Call-path trace (depth 5) | <10 ms | BFS traversal |

Sources:

- <https://github.com/DeusData/codebase-memory-mcp#performance>
- <https://arxiv.org/html/2603.27277v1>
- <https://github.com/DeusData/codebase-memory-mcp/blob/main/docs/MEASURING_SAVINGS.md>

### codegraph

Across seven open-source repositories (VS Code, Excalidraw, Django, Tokio,
OkHttp, Gin, Alamofire), with a median of four headless runs per arm:

- **62% fewer tokens**
- **88% fewer tool calls**
- **53% faster**
- **44% cheaper**
- **zero file reads** in the graph arm on all seven repositories

The methodology blocked the CLI in both arms so the control could not
accidentally use the graph. The largest gains were on repositories where the
control arm needed 28–43 tool calls and 12–19 file reads; the smallest were on
small repositories where plain exploration was already cheap.

Sources:

- <https://github.com/colbymchenry/codegraph#benchmark-results>

### How to read these numbers

- They measure **structural questions**, where a graph has the most leverage.
- They compare a graph-enabled agent with a competent file-search agent, not
  with a perfect oracle.
- They include the cost of the graph tools themselves.
- They do not include the one-time indexing time, which is amortized across
  sessions.
- Different models, prompts, and repository layouts produce different results.

## Where the savings are small

The graph is not a universal token reducer. Expect little or no benefit when:

| Situation | Why |
|---|---|
| Tiny repository (a few files) | Search and read are already cheap; tool schemas add overhead. |
| Single known file, line-local edit | No discovery to replace. Use the graph only for impact checks. |
| Brand-new code not yet indexed | The graph has nothing to return; bounded reads are correct. |
| Generated or vendored code | Often excluded or unhelpful to index. |
| Exact line-level debugging | You eventually need the current text; the graph only narrows the file. |
| Overly broad `explore` calls | Returning every related symbol defeats the purpose. Ask narrower questions. |
| Stale index | Wrong answers force re-verification and can cost more than searching. |
| Both backends enabled | Duplicate tools and duplicate results waste context. |

## Measuring it on your own repository

Use a small, repeatable protocol:

1. **Pick 5–10 representative prompts** across categories: locate, understand a
   flow, callers/callees, impact, architecture, tests.
2. **Run each prompt in a fresh session** with a fixed model and no prior
   context.
3. **Record** total input + output + cache tokens, tool calls, file reads, wall
   time, and whether the answer was correct.
4. **Run a baseline** with the graph disabled (or on a checkout without an
   index).
5. **Run the graph arm** with the harness rules and skill loaded.
6. **Repeat 3–5 times** and use the median. Single runs are noisy.
7. **Separate task types.** Aggregate numbers hide the fact that graphs help
   discovery far more than editing.
8. **Check correctness.** A cheap wrong answer is not a saving.

If you use a provider dashboard or API usage logs, compare total tokens per
completed task, not tokens per individual call. Agent loops change the number
of calls, and that is part of the effect.

## Maximizing the savings

- **Keep the rules mandatory.** A single "just grep it" habit can erase the
  advantage on a large repository.
- **Prefer name search and call traces over broad source dumps.** Ask for the
  smallest result that answers the question.
- **Use snippets, outlines, and call paths.** Read full files only for editing
  or verification.
- **Bound every call.** Use `limit`, `source_max_lines`, `max_output_tokens`,
  and pagination.
- **Stop when the answer is supported.** Extra verification reads are the most
  common source of avoidable cost.
- **Keep the index fresh.** Re-index after large pulls, rebases, or long gaps.
- **Use one backend per repository.** Disable the other server.
- **Do not paste raw graph output into the final answer.** Summarize and cite.
- **Measure.** Intuition about token use is unreliable; the protocol above is
  not.

## Index cost and amortization

Indexing is not free:

- codebase-memory-mcp's full Linux kernel index takes about three minutes and a
  substantial amount of disk/RAM during construction.
- codegraph indexes incrementally and watches the working tree; the first index
  is the expensive part.
- A graph database can be large because it stores structure and, depending on
  the backend, source snippets.

The cost is paid once per index generation and reused across many sessions. If
you ask one question and never return to the repository, indexing may not pay
off. If you work in the repository for days, it usually does.

## A realistic expectation

For structural discovery on a large repository, a well-behaved graph-first
agent commonly spends **40–80% fewer discovery tokens** than a search-and-read
loop, with larger gains on call-chain and impact questions and smaller gains on
simple lookups. Published backend benchmarks show results from roughly 40% to
90% reductions depending on the repository and question.

Treat those as a range to validate, not a promise. The harness's contribution is
making the graph the default path and preventing the agent from silently
falling back to full-repository search.
