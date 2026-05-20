# RAG agent on Active Graph

*Retrieve → rerank → generate with citations.
Companion to `minimalist-coding-agent.md`.*

## The pattern

The most-shipped LLM application in production: ground the model in a
corpus so it stops hallucinating, and force it to cite. The shape is
boring on purpose:

1. **Retrieve** k candidates from a vector store / BM25 / hybrid.
2. **Rerank** them with a stronger (often LLM-based) scorer.
3. **Generate** the answer constrained to the top-N, with citations.
4. (Optional) **Verify** every citation actually supports its claim.

Most production RAG stacks bury this in a chain of opaque function
calls; when answers go wrong you don't know whether retrieval missed
the source, the reranker dropped it, or the generator ignored it.
Active Graph keeps each stage as an event, so the postmortem is just
walking the trace.

## Schema

```
query    { text }
chunk    { doc_id, text, score }        # one per retrieved snippet
ranked   { chunk_id, rerank_score }     # rerank verdict per chunk
answer   { content, citations }         # citations = list of chunk ids

query  --[retrieved]----> chunk
chunk  --[ranked_as]----> ranked
answer --[cites]--------> chunk
```

## Behaviors and tools

One `@tool` for the vector store, two `@llm_behavior`s for rerank +
generate. ~100 lines.

```python
from pydantic import BaseModel, Field
from activegraph import Frame, Graph, Runtime, behavior, llm_behavior, tool
from activegraph.llm import AnthropicProvider


# ---- the only non-LLM piece: retrieval ----

class RetrieveIn(BaseModel):
    query: str
    k: int = 20

class Hit(BaseModel):
    doc_id: str
    text: str
    score: float

class RetrieveOut(BaseModel):
    hits: list[Hit]

@tool(name="retrieve", input_schema=RetrieveIn, output_schema=RetrieveOut,
      description="Hybrid BM25+dense retrieval over the corpus.",
      deterministic=True)
def retrieve(args, ctx):
    # ...your vector store call...
    return RetrieveOut(hits=[...])


# ---- behaviors ----

@behavior(name="kickoff", on=["goal.created"])
def kickoff(event, graph, ctx):
    q = graph.add_object("query", {"text": event.payload["goal"]})
    ctx.dispatch_tool(q.id, "retrieve", {"query": q.data["text"], "k": 20})


@behavior(name="ingest_hits", on=["tool.responded"],
          where={"tool.name": "retrieve"})
def ingest_hits(event, graph, ctx):
    q = graph.view().objects(type="query")[-1]
    for h in event.payload["output"]["hits"]:
        c = graph.add_object("chunk", h)
        graph.add_relation(q.id, c.id, "retrieved")


class Rerank(BaseModel):
    rankings: list[dict] = Field(
        description="List of {chunk_id, score 0-1, reason}. "
                    "Score 1 = directly answers the query; "
                    "0 = irrelevant. Include all chunks.")

@llm_behavior(
    name="reranker",
    on=["object.created"],
    where={"object.type": "chunk"},
    description=(
        "You are a relevance scorer. Read each retrieved chunk and "
        "score how well it answers the query. The query is in the "
        "frame goal."),
    output_schema=Rerank,
    creates=["ranked"],
    # Debounce: fire once per batch, not once per chunk. The runtime
    # collapses consecutive matching events when debounce_ms is set.
    debounce_ms=500,
)
def reranker(event, graph, ctx, out: Rerank):
    for r in out.rankings:
        rk = graph.add_object("ranked", r)
        graph.add_relation(r["chunk_id"], rk.id, "ranked_as")


class Answer(BaseModel):
    content: str = Field(description="The answer to the query.")
    citations: list[str] = Field(
        description="chunk_id values used. Every nontrivial claim "
                    "must be backed by at least one cited chunk.")

@llm_behavior(
    name="generator",
    on=["object.created"],
    where={"object.type": "ranked"},
    description=(
        "Answer the query using ONLY the top-5 reranked chunks. "
        "If the chunks don't contain the answer, say so. "
        "Cite chunk ids inline like [chunk_abc123]."),
    output_schema=Answer,
    creates=["answer"],
    debounce_ms=500,
)
def generator(event, graph, ctx, out: Answer):
    if _already_answered(graph):
        return
    a = graph.add_object("answer", {"content": out.content,
                                    "citations": out.citations})
    for chunk_id in out.citations:
        graph.add_relation(a.id, chunk_id, "cites")
```

The `debounce_ms` knob is the small bit of cleverness: retrieval emits
20 chunks back-to-back; without debouncing, `reranker` would fire 20
times. With it, you get one rerank pass per batch — which is what you
wanted all along.

## Optional: citation verifier

A second `@llm_behavior` on `object.created where object.type=answer`
that re-reads each cited chunk and emits a `behavior.failed` event when
a citation doesn't actually support the claim. Three reasons this is
the right shape:

1. The failure is in the trace; you can grep traces for unsupported
   citations across a whole eval set.
2. You can fork a run after the failure and rerun the generator with a
   stricter prompt — without re-running retrieval or rerank.
3. The verifier is a hard "no" without bloating the generator's
   prompt with verification instructions.

## What you get for free

- **Cached retrieval.** `@tool(deterministic=True)` means the tool
  cache keys on arguments; identical queries are free. Cheap eval
  loops.
- **Replay-without-spend.** Fork after `tool.responded(retrieve)`,
  change reranker or generator, re-run with `replay_llm_cache=True`.
  The retrieval round trip is reused; only the LLM stages re-execute.
  This is the cheapest way to iterate on prompt design over a fixed
  candidate set.
- **Citation graph is real.** `(answer)-[cites]->(chunk)<-[retrieved]
  -(query)` lets you ask "which chunks does my system over-rely on?"
  or "which sources never get cited despite being retrieved?" directly
  via graph queries.
- **Per-stage cost.** `llm.responded` cost on rerank vs. generate is
  visible in the trace. RAG cost overruns almost always trace to the
  rerank model being too big; the trace tells you that without a
  separate profiler.

## Variations

- **Multi-query expansion.** Add a `query_expander` `@llm_behavior`
  before retrieval that emits N reformulated queries; each becomes its
  own `tool.requested(retrieve)`. The rest of the pipeline is unchanged.
- **Hierarchical retrieval.** Two `retrieve` tools (`coarse`, `fine`);
  a `where` filter on each behavior selects which it cares about.
- **Conversational RAG.** Persist the run; on the next turn, load the
  trace and emit a new `goal.created`. Prior chunks and citations
  remain in the graph as context.

## TL;DR

One tool, two LLM behaviors, four object types. Every retrieval hit,
every rerank score, every citation is a node or edge you can query
later. The pattern stays a 100-line file; the audit trail and replay
cache are what make it operable.
