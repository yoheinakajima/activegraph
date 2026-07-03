# Active Graph

An event-sourced reactive graph runtime for long-running, auditable,
agentic systems. Behaviors react to events, mutate the graph, emit
more events. The event log is the source of truth — every run is
replayable, forkable, and diff-able from its log.

You're already past `pip install activegraph` and you want to know
where to start. **Run the framework first, read second.**

```
activegraph quickstart
```

That runs the bundled Diligence pack on fixtures (no API key, no
configuration, under 30 seconds) and prints a memo. You'll see what
the framework does before you read about how it does it. The command
ends with a "what just happened" section pointing back here.

## Start here

- **[Quickstart](quickstart.md)** — 10 minutes from install to a
  working custom behavior. Every example is a runnable file. Reading
  this is the canonical first thing.
- **[Concepts: Failure model](concepts/failure-model.md)** — read this
  second. The framework's stance on what counts as a recoverable
  failure governs how every error message reads and how every behavior
  should be written. Short on its own; load-bearing across everything
  else.

## When something specific breaks

Every error message ends with a `More:` link to a page that explains
the specific failure in detail — when it fires, what causes it, how
to diagnose, how to fix. The error reference catalog is under
[Reference: Errors](reference/errors/). You should rarely need to
visit it directly; the error message that fired tells you which page.

## When you're building something

- **[Guides](guides/writing-behaviors.md)** — concrete how-tos for
  writing behaviors, LLM behaviors, tools, pattern subscriptions,
  packs, and operating a run in production.
- **[Concepts](concepts/graph.md)** — the model's primitives (graph,
  events, behaviors, relations, patches, views, frames, policies,
  replay, forking) explained one at a time.
- **[Cookbook](cookbook/common-patterns.md)** — recurring patterns
  with copy-pasteable code.

## Machine-readable docs

AI agents and coding assistants can skip the HTML entirely:

- **[/llms.txt](https://docs.activegraph.ai/llms.txt)** — structured
  markdown index of every page, following the
  [llms.txt convention](https://llmstxt.org/).
- **[/llms-full.txt](https://docs.activegraph.ai/llms-full.txt)** — the
  full documentation corpus concatenated into one markdown file
  (~100K tokens), sized for large-context ingestion.
- **[/llms.md](https://docs.activegraph.ai/llms.md)** — markdown mirror
  of `llms.txt`, served with `Content-Type: text/markdown` for agents
  that probe `.md` root paths.

## Source and issues

- [GitHub repository](https://github.com/yoheinakajima/activegraph)
- [Issue tracker](https://github.com/yoheinakajima/activegraph/issues)
- [Changelog](about/changelog.md) — v0.5 → v1.0 with migration notes.
