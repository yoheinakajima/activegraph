# Active Graph — Design

> The graph is the world. Behaviors are physics. The trace is the proof.

This document is the canonical product, UX, and design reference for
**Active Graph** — the open-source runtime, the marketing site that
promotes it, and the cloud control plane built on top of it. One
brand, one name, one voice across every surface.

A human designer or AI coding agent should be able to read this file
and ship a coherent marketing site, dashboard, or feature without
asking "what is this product for?" Anything that contradicts this
file is drift — fix the implementation or update this file in the
same change.

This file lives in the OSS repo but is **for the company side**
(marketing site and cloud product). The OSS runtime has its own
references — `README.md`, `CONTRACT.md`, and the doc site at
`docs.activegraph.ai`. This document does not duplicate them; it
defines the layer that *promotes and extends* them.

---

## 1. Project Overview

**Project name.** Active Graph.

**Package name.** `activegraph` on PyPI. Lower-case, one word, no
hyphen. Same name everywhere the brand appears: domain
(`activegraph.ai`), CLI (`activegraph quickstart`), docs
(`docs.activegraph.ai`), cloud (`app.activegraph.ai`),
GitHub (`github.com/yoheinakajima/activegraph`).

**One-liner.** Active Graph is the event-sourced reactive graph
runtime for long-running, auditable agentic systems — and the
cloud control plane for teams that run it in production.

**Tagline (marketing).** *Agents you can audit.*

**Two surfaces, one product:**

- **The runtime (OSS).** Python, MIT-licensed.
  `pip install activegraph`. The runtime, the CLI, the pack format,
  the SQLite and Postgres stores, the recorded-provider testing
  primitives. Source of truth: the repo and the doc site.
- **The cloud (commercial, planned).** Hosted event store, web UI
  for `inspect / replay / fork / diff`, team approval inboxes,
  pack registry, observability dashboards. Everything the OSS
  deliberately doesn't ship.

The cloud is an Active Graph product — same name, same logo, same
voice. There is no second brand. Marketing speaks of "Active Graph"
and "Active Graph Cloud" when the distinction matters; otherwise
just "Active Graph."

**Target users.**

1. **Builders of agentic systems** — Python developers writing
   long-running agents (research agents, diligence agents,
   incident-response agents, document-extraction pipelines) who
   have outgrown a chat loop and need durable state, replay, and
   audit.
2. **Operators** — staff/senior engineers running an agent system
   in production for someone else (PM, compliance, ops). They need
   inspect, replay, fork, diff, approval workflows, and metrics
   without writing them.
3. **Decision-makers** evaluating agent infrastructure — eng leads,
   CTOs, heads of ML platform — who care about auditability,
   determinism, vendor lock-in, and total cost.

**Core problem.** Agent frameworks optimized for chat fall apart
when the work is long-running, multi-step, and consequential. There
is no durable world state, no audit trail, no way to ask "what
would have happened if I'd done X differently," no way for a human
to approve a particular action before it lands. Teams end up
rebuilding the same plumbing — event log, replay, fork, approval,
trace — on top of a framework that fights them.

**Core value proposition.**

- **For builders:** A small, sharp runtime where the event log is
  the source of truth. Behaviors react to a shared graph instead of
  talking to each other. Forks are first-class. Errors point to
  fixes.
- **For operators:** Inspect, replay, fork, diff, metrics, and
  structured logs out of the box. Postgres-backed store. Cloud
  console for the things a CLI can't make ergonomic (approval
  queues, pack discovery, multi-run dashboards).
- **For decision-makers:** MIT license, no lock-in, no proprietary
  graph format. Cloud is convenience, not gravity. Move runs in or
  out by copying an event log.

**Why this should exist.** Two reasons.

First, the OSS will not reach its audience without a site that
explains, in 30 seconds, what it does and who it's for. The repo
README is the right artifact for someone who already typed
`pip install`; it is the wrong artifact for someone who has not yet
decided whether to. The marketing site is the funnel.

Second, the operator surface that the runtime deliberately doesn't
ship (web UI, HTTP server, multi-tenant team workspace) is exactly
the surface a sustainable company is built on. The OSS stays small
and sharp. The cloud adds the things a team-sized adopter needs and
a solo developer doesn't. The economic line is clean.

---

## 2. Product Principles

These rules guide every product decision on the marketing site and
the cloud console. When two principles conflict, the higher-numbered
one yields.

1. **The graph is the world; the trace is the proof.** Every screen
   that shows a run should make the graph and the event log
   reachable in one click. The trace is not a debug panel; it is
   the canonical record. The UI surfaces it as such.
2. **Show, then explain.** The landing page runs the framework in
   the visitor's eyes (animated diligence demo, or interactive
   pyodide REPL) before it talks about the framework. Same rule
   for cloud features: the screenshot leads the copy.
3. **Inspectability over magic.** When the system does something on
   the user's behalf — an LLM call, a tool fire, a pack-policy
   approval gate — show the evidence inline. Hidden behavior is
   the failure mode the framework was built to prevent.
4. **Audit before action.** Any action that mutates a run, a pack,
   or a team setting shows the diff before it commits. "Apply"
   buttons are two-step: preview, then confirm.
5. **Cloud is convenience, not gravity.** Every cloud feature must
   have a 1-paragraph "how to do this with just the OSS" answer
   visible somewhere in the docs. We sell ergonomics and team
   workflow; we do not sell escape-tax.
6. **Operators are first-class.** The cloud console is a debugger
   for people whose job is keeping a run alive. Optimize for fast
   triage (5 seconds from alert to event_id), not for product
   tours.
7. **One canonical voice.** Marketing, docs, errors, UI strings —
   all read as if from one author. Active, declarative, name the
   invariant. (See § 8 and § 14.)
8. **Fast review beats clever automation.** When an AI proposes a
   change (auto-link evidence, draft a memo, classify a risk), the
   UI optimizes for a 5-second human review. No bulk-accept
   without inspection.
9. **MIT today, MIT tomorrow.** The OSS stays MIT. No
   business-source license, no commons clause, no relicensing of
   already-released code. Cloud is a separate codebase.
10. **No dark patterns.** No false urgency, no required credit
    card to try, no countdown timers, no "10 people viewing this
    plan." The product earns trust by being inspectable; the
    pricing page should match.

---

## 3. User Personas

Four personas. Every workflow in § 4 maps to at least one. If a
proposed feature doesn't serve any of these, it doesn't ship.

### 3.1 The Builder (primary)

**Who.** Tessa, senior Python developer at a 40-person startup. Was
hacking on a research-agent prototype with LangChain; ran into the
"agent kept rewriting the doc and I can't tell why" wall. Found
Active Graph from a Hacker News post.

**Goal.** Ship a research agent her PM can demo to a customer this
month. Wants the agent to be debuggable when (not if) it goes
sideways.

**Pain points.**
- Existing frameworks treat state as an in-memory dict that vanishes
  when the script exits.
- "What did the agent decide and why" requires reading verbose logs.
- Rerunning to reproduce a bug costs API tokens.

**Typical workflow.**
1. Lands on `activegraph.ai`.
2. Runs the demo without leaving the page.
3. `pip install activegraph && activegraph quickstart`.
4. Walks the 10-minute tutorial.
5. Writes a custom behavior. Hits an error. Clicks the `More:` link.
6. Joins the Discord. Stars the repo.
7. *(Maybe later)* Signs up for cloud when she puts the agent in
   front of customers.

**Success state.** A reproducible agent run, an event log she can
fork to test variants, and a teammate who can pick up the project
because the trace is self-describing.

### 3.2 The Operator (primary for cloud)

**Who.** Marcus, staff engineer at a 200-person fintech. Inherited
an agent system another team built. Now responsible for keeping it
running and explainable to the compliance team.

**Goal.** Inspect any run. Replay any failure. Approve memos before
they reach a regulator. Show an auditor "here is exactly why the
system did this."

**Pain points.**
- The team that built the agent left no observability behind.
- Compliance needs a paper trail; the current one is `print()`.
- He doesn't want to write a dashboard. He wants to use one.

**Typical workflow.**
1. Signs into Active Graph Cloud. Sees all active runs in his
   workspace.
2. Filters to runs that hit a `behavior.failed` event in the last 24h.
3. Opens a run. Sees the graph, the event log, the trace, the
   metrics — one page.
4. Identifies the bad behavior. Forks the run with a corrected
   pack setting. Diffs the fork against the parent.
5. Approves the memo from his phone before bed.

**Success state.** Mean-time-to-diagnose drops from "an afternoon"
to "five minutes." Compliance gets a JSONL trace export with a
signature.

### 3.3 The Decision-Maker (marketing-only)

**Who.** Priya, VP of Engineering at a 500-person legal-tech
company. Evaluating agent platforms for a 6-person internal team.

**Goal.** Pick infrastructure that won't lock her in, won't break
when LLM vendors change, and that her audit team can sign off on.

**Pain points.**
- Most agent platforms are proprietary endpoints.
- "Auditability" is usually marketing copy with nothing behind it.
- Pricing is opaque.

**Typical workflow.**
1. Lands on `activegraph.ai/why`.
2. Reads the architecture page. Confirms MIT.
3. Reads pricing. Confirms there is no per-seat tax.
4. Forwards the link to her tech-lead with "let's prototype with
   this next sprint."

**Success state.** A 15-minute internal pitch document she can
write from the marketing site alone.

### 3.4 The Pack Author (long-tail, post-MVP)

**Who.** Alex, independent consultant who has built three Active
Graph packs for past clients and wants to publish one.

**Goal.** Distribute a pack on the registry. Earn reputation. Maybe
monetize a premium variant later.

**Pain points.**
- No place to publish. No way for users to discover.
- Versioning and prompt-hash compatibility are hard to communicate.

**Typical workflow.**
1. Publishes a pack via `activegraph pack publish` (post-MVP CLI).
2. Pack appears on `activegraph.ai/packs`.
3. Users install via `activegraph pack install <name>`.
4. Pack page shows downloads, compatible runtime versions, and a
   live fixture demo.

**Success state.** A pack with 200 weekly installs, a clear
versioning story, and a community of users filing issues.

---

## 4. Core User Workflows

Eight workflows define the product. Each maps to specific screens
(see § 5) and personas (§ 3).

### 4.1 First-touch: visitor evaluates the OSS

- **Persona.** Builder, Decision-Maker.
- **Trigger.** Visitor lands on `activegraph.ai` from HN /
  Twitter / Discord / a friend.
- **User goal.** Decide in under 60 seconds whether this is worth
  10 minutes.
- **Flow.**
  1. Sees the headline, the one-liner, and an above-the-fold
     interactive demo (the Diligence pack running on fixtures, with
     the graph rendering as events land).
  2. Scrolls past three "what makes this different" beats:
     event-sourced graph, fork-and-diff, errors that link to fixes.
  3. Reaches the `pip install activegraph` block with the
     `activegraph quickstart` command underneath.
- **System behavior.** The demo runs in-browser (pyodide or a
  pre-recorded canvas animation; see § 13). No backend call.
  Telemetry: did they reach the install block? Did they copy it?
- **Success state.** They copy the install command.
- **Failure state.** They leave above the fold. Track scroll depth;
  if median session bounces above the demo, the demo isn't doing
  its job.

### 4.2 Install-to-tutorial: builder follows the funnel

- **Persona.** Builder.
- **Trigger.** Visitor copies `pip install activegraph` and runs
  `activegraph quickstart`.
- **User goal.** See the framework work, then write a custom
  behavior.
- **Flow.**
  1. The CLI prints a "what just happened" block with a link back
     to `docs.activegraph.ai/quickstart`.
  2. They walk the 10-minute tutorial.
  3. They write a behavior. They might fail; the error message
     links to a fix page.
- **System behavior.** This is mostly OSS territory. The marketing
  site's job is to *not get in the way* — the install command and
  the docs link must be one click apart on the landing page.
- **Success state.** They reach the fork-and-diff step at the end
  of the tutorial.

### 4.3 OSS-to-cloud: operator signs up

- **Persona.** Operator.
- **Trigger.** Operator already runs Active Graph in production
  with SQLite or self-hosted Postgres. Wants a dashboard.
- **User goal.** Connect their run to the cloud console without
  rewriting code.
- **Flow.**
  1. Signs up on `activegraph.ai/cloud` (email + GitHub).
  2. Creates a workspace. Gets a connection string + token.
  3. Sets `ACTIVEGRAPH_STORE_URL=activegraph+cloud://...` and re-runs.
     Existing event log streams to cloud as events emit.
     *(Alternative: `activegraph migrate <local> <cloud>` to ship a
     historical run.)*
  4. Opens the cloud console. Sees their run live.
- **System behavior.** The cloud store implements the same
  `EventStore` protocol as SQLite and Postgres. No code changes.
  Migration is one-directional and explicit per OSS contract.
- **Success state.** Operator can `inspect`, `replay`, `fork`, and
  `diff` from the web UI.
- **Failure state.** Auth fails, or the store URL format is wrong.
  The error message links to a fix page (same discipline as OSS
  errors).

### 4.4 Run inspection: operator triages a failure

- **Persona.** Operator.
- **Trigger.** An alert fires (Slack, email, or in-app
  notification) referencing a `behavior.failed` event.
- **User goal.** Understand the failure in under five minutes.
- **Flow.**
  1. Click alert. Lands on the **Run Detail** screen, scrolled to
     the failing event.
  2. Sees the event payload, the behavior that failed, the events
     that led to it, and the graph state at that moment.
  3. Toggles **Replay Mode** to see the run reconstruct
     event-by-event.
  4. Decides: fix the pack, fork to test the fix, or escalate.
- **System behavior.** All state is projected from the event log.
  No expensive query — the same `Runtime.load()` that the OSS CLI
  uses, behind a web request.
- **Success state.** Operator can describe the failure in plain
  language and has a next action.

### 4.5 Fork-and-diff in the browser

- **Persona.** Operator, Builder.
- **Trigger.** Operator wants to test a config change against a
  real historical run.
- **User goal.** Run "what if I'd done X differently?" without a
  terminal.
- **Flow.**
  1. From any event on the Run Detail screen, click **Fork from
     here**.
  2. Modal opens: name the fork, optionally set pack settings
     (key/value, with autocomplete from the pack's settings
     schema), choose `--record` if intentional.
  3. Click **Create Fork**. The fork runs; the page splits into a
     side-by-side **Diff View**.
  4. Differences in graph state, claims, memos, and risks are
     highlighted. Costs and event counts compare in a header bar.
- **System behavior.** Backed by the OSS `fork` and `diff`
  primitives, exposed as cloud RPCs. Cache replay (no LLM
  re-execution for the shared prefix) is preserved.
- **Success state.** Operator sees the structural diff and exports
  it to share with their team.

### 4.6 Approval inbox

- **Persona.** Operator (or any person the pack policy designates
  as an approver).
- **Trigger.** A pack policy gates an action (memo, risk, large
  spend) and emits `approval.requested`.
- **User goal.** Approve or reject with enough context to be
  confident.
- **Flow.**
  1. Approver gets a notification (email, Slack, in-app).
  2. Opens the **Inbox** screen. Pending approvals are sorted by
     age and priority.
  3. Clicks one. Sees the proposed action, the evidence chain
     (which events triggered it, which claims it cites, which
     prompt produced it), and the cost.
  4. Clicks **Approve** or **Reject**. Optionally adds a note.
- **System behavior.** Backed by the OSS approval primitive
  (`runtime.approve()` / `runtime.deny()`). The decision lands as
  an event in the run's log.
- **Success state.** Approval round-trip in under 30 seconds. The
  pack's policy decides what happens next.

### 4.7 Pack discovery and install

- **Persona.** Builder, Pack Author.
- **Trigger.** Builder wants a pre-built pack for their domain.
- **User goal.** Find a pack, evaluate it, install it.
- **Flow.**
  1. Browses `activegraph.ai/packs`. Filters by domain (finance,
     research, sales, ops).
  2. Pack page shows: description, install command, settings
     schema, version compatibility, downloads-per-week, link to
     source.
  3. Optionally clicks **Try in browser** — pyodide-backed live
     fixture demo.
  4. Copies `pip install activegraph-<pack>` and runs locally.
- **System behavior.** Registry is a metadata index over PyPI for
  v1; can become first-party hosting post-MVP if there's demand.
- **Success state.** Pack installs and the included fixture runs.

### 4.8 Compliance export

- **Persona.** Operator + their compliance counterpart.
- **Trigger.** Audit. End-of-quarter review. Regulator request.
- **User goal.** Hand the auditor a signed, complete, replayable
  artifact for any historical run.
- **Flow.**
  1. From the **Runs** screen, multi-select runs in the audit
     window.
  2. Click **Export for Audit**. Choose JSONL or a signed bundle
     (event log + pack manifest + content hashes).
  3. Download or email to the auditor with a verification link
     (`activegraph.ai/verify/<hash>`).
- **System behavior.** Backed by OSS `export-trace`. The signed
  bundle includes pack content hashes so the auditor can verify
  prompts didn't drift.
- **Success state.** Auditor signs off without follow-up.

---

## 5. Information Architecture

### 5.1 Marketing site (`activegraph.ai`)

```
/                         Home
/why                      Why event-sourced agents
/demo                     Interactive in-browser demo
/quickstart               (redirect to docs.activegraph.ai/quickstart)
/packs                    Pack registry index
/packs/<name>             Pack detail
/cloud                    Cloud product
/cloud/pricing            Pricing
/cloud/security           Security and compliance
/docs                     (redirect to docs.activegraph.ai)
/blog                     Engineering blog (case studies, RFCs)
/changelog                Aggregated: OSS + cloud
/about                    Company, team, principles
/contact                  Contact / sales
/verify/<hash>            Audit-bundle verification (deep link only)
```

**Domain layout.**
- `activegraph.ai` — marketing site (this section).
- `docs.activegraph.ai` — OSS docs (already live).
- `app.activegraph.ai` — cloud console (§ 5.2).
- `status.activegraph.ai` — status page.

**Navigation.**
- Top bar: `Why`, `Demo`, `Packs`, `Docs`, `Cloud`, `Pricing`,
  `GitHub` (icon), `Sign in` (right-aligned).
- Footer: company, docs, OSS license, status page, security, X,
  Discord, RSS.

**Search/filter.**
- Pack registry: filter by domain tag, sort by downloads-per-week,
  compatible runtime version, last updated.
- Blog: filter by tag (`tutorial`, `case-study`, `release`, `RFC`).
- Docs search lives at `docs.activegraph.ai` (not duplicated).

### 5.2 Cloud console (`app.activegraph.ai`)

```
/                                 Workspace overview
/runs                             Runs list (filterable)
/runs/<run_id>                    Run detail
/runs/<run_id>/graph              Graph view
/runs/<run_id>/trace              Event-log view
/runs/<run_id>/events/<event_id>  Single event detail
/runs/<run_id>/fork               Fork wizard
/runs/<run_id>/diff/<other_id>    Diff view
/inbox                            Pending approvals
/packs                            Installed packs in workspace
/metrics                          Metrics dashboard
/team                             Team and permissions
/settings                         Workspace settings, API tokens
/audit                            Audit log (workspace-level)
```

**Navigation.**
- Left sidebar (collapsible): `Runs`, `Inbox` (badge with pending
  count), `Packs`, `Metrics`, `Audit`, `Settings`. Sticky.
- Top bar: workspace switcher (left), command palette (`⌘K`),
  notifications, user menu.

**Search/filter.**
- Runs list: filter by status (running, completed, failed, idle),
  pack, date range, has-pending-approval, has-error. Free-text on
  run label and goal text.
- Trace view: filter by event type, behavior name, time window;
  jump to event by id.
- Command palette: navigate to any run by id or label; jump to any
  pending approval; open any pack page.

**Relationship between areas.**
- A `Run` belongs to a workspace, may have many forks (themselves
  runs).
- An `Event` belongs to a run, can trigger many behaviors, may
  produce a pending `Approval`.
- A `Pack` is installed in a workspace and used by runs.
- The `Inbox` is a view across runs in the workspace, scoped to
  the signed-in user's approval rights.

---

## 6. Core Objects

Six objects define the cloud data model. Three (Run, Event, Pack)
are projections of OSS concepts; three (Workspace, Approval, User)
are cloud-native.

### 6.1 Workspace

**Purpose.** The unit of billing, permissions, and isolation. A
workspace owns runs, packs, approvals, and team members.

**Key fields.**
- `id` (UUID)
- `name`
- `slug` (URL-safe)
- `plan` (`free`, `team`, `enterprise`)
- `created_at`
- `store_region` (`us-east`, `eu-west`, …)

**Relationships.** Has many users (via membership), many runs, many
installed packs, many approval policies.

**Status lifecycle.** `active` → `suspended` → `deleted` (soft, 30
days). No other states.

### 6.2 User

**Purpose.** A person who can sign in. Belongs to one or more
workspaces with a role per workspace.

**Key fields.**
- `id`
- `email`
- `name`
- `github_id` (optional)
- `default_workspace_id`

**Relationships.** Many workspace memberships, many approval
decisions authored.

**Status lifecycle.** `invited` → `active` → `deactivated`.

### 6.3 Run

**Purpose.** A single execution of an Active Graph runtime —
projection of an event log. The user-facing unit of work.

**Key fields.**
- `id` (the OSS `run_id`)
- `workspace_id`
- `label` (human-readable)
- `goal_text` (the originating `goal.created` payload)
- `pack_name` and `pack_version`
- `status` (see lifecycle)
- `parent_run_id` (nullable; set on forks)
- `forked_at_event_id` (nullable; set on forks)
- `created_at`, `last_event_at`
- `metrics_summary` (events, cost USD, duration)

**Relationships.** One workspace; many events; zero-or-one parent
run; zero-to-many child forks.

**Status lifecycle.**
`pending` → `running` → (`completed` | `failed` | `idle` | `budget_exhausted`)

**Example.**
```json
{
  "id": "run_01H...",
  "workspace_id": "ws_01H...",
  "label": "Diligence: Northwind Robotics — alt thesis",
  "goal_text": "Diligence: Northwind Robotics",
  "pack_name": "diligence",
  "pack_version": "0.1.0",
  "status": "completed",
  "parent_run_id": "run_01G...",
  "forked_at_event_id": "evt_00042",
  "metrics_summary": {
    "event_count": 287,
    "cost_usd": 0.42,
    "duration_seconds": 91
  }
}
```

### 6.4 Event

**Purpose.** One entry in the append-only log. The atomic record of
what happened.

**Key fields.**
- `id` (monotonic per run)
- `run_id`
- `type` (e.g. `object.created`, `behavior.failed`, `approval.requested`)
- `payload` (JSONB)
- `caused_by_event_id` (nullable; the event whose behavior emitted this)
- `behavior_name` (nullable; the behavior that produced this)
- `ts`

**Relationships.** Belongs to one run; may be caused by one event;
may cause many events; may produce one approval.

**Status lifecycle.** Immutable. Events are never edited or
deleted from a run's history (deleting a run deletes its events
together; nothing else touches them).

### 6.5 Pack

**Purpose.** A bundle of object types, behaviors, tools, prompts,
and policies for a specific domain (the OSS pack primitive).
Cloud-side, a pack is a workspace-installed resource and a
public registry entry.

**Key fields.**
- `name` (registry-unique)
- `version`
- `description`
- `author`
- `homepage`
- `settings_schema` (Pydantic JSON Schema)
- `object_types`, `relation_types`, `behavior_names`, `tool_names`,
  `prompt_hashes`
- `compatible_runtime_versions` (semver range)

**Relationships.** Installed in many workspaces; used by many
runs; authored by one user (or org).

**Status lifecycle (registry).**
`draft` → `published` → `deprecated` → `yanked`

### 6.6 Approval

**Purpose.** A pending decision a human must make before a gated
action lands.

**Key fields.**
- `id`
- `run_id`, `event_id` (the `approval.requested` event)
- `workspace_id`
- `policy_name` (e.g. `memo_approval`)
- `target_object_type` (e.g. `memo`)
- `proposed_payload`
- `evidence_chain` (ordered list of event ids back to the goal)
- `status` (see lifecycle)
- `decision_by_user_id`, `decision_at`, `decision_note`
- `expires_at` (optional)

**Relationships.** Belongs to one run, one workspace; decided by
one user.

**Status lifecycle.** `pending` → (`approved` | `rejected` | `expired`)

**Example.**
```json
{
  "id": "apv_01H...",
  "run_id": "run_01H...",
  "event_id": "evt_00231",
  "policy_name": "memo_approval",
  "target_object_type": "memo",
  "proposed_payload": {
    "title": "Northwind Robotics — Investment Memo",
    "summary": "...",
    "claims_cited": 14,
    "risks_surfaced": 3
  },
  "evidence_chain": ["evt_00001", "evt_00012", "evt_00203", "evt_00231"],
  "status": "pending",
  "expires_at": "2026-05-25T00:00:00Z"
}
```

---

## 7. UX Patterns

Reusable patterns. Every screen composes these — no one-offs unless
the pattern can't stretch.

### 7.1 The Run Card

The atomic "this is a run" tile. Used on the workspace home, the
runs list, and embedded in inbox items.

- Status pill (color-coded: green completed, blue running, red
  failed, gray idle, yellow budget-exhausted).
- Run label (1 line, truncated with title attribute).
- Pack name + version (small, monospace).
- Last event time (relative: "2 min ago").
- Metrics row: event count · cost · duration.
- Hover: subtle elevation. Click: navigate to Run Detail.

### 7.2 The Event Row

One line per event in the trace view.

- Monospace event id, left-aligned.
- Event type pill (color-coded by category: graph mutation, LLM
  call, tool call, behavior fire, approval, error).
- Inline payload summary (the one field that matters for this type;
  full payload on click).
- Behavior name (if applicable), right-aligned.
- Timestamp + duration (since previous event).
- Click expands inline; shift-click opens detail in a side panel
  without navigating.

### 7.3 Graph View

Force-directed (or DAG-laid-out for diligence-style runs) view of
the graph projection at the selected event.

- Nodes colored by object type; edges colored by relation type;
  legend top-right.
- Scrubber at the bottom: drag back through events; the graph
  reconstructs to that point. Always-visible "now" marker.
- Single-click: select node, show its data in a side panel.
- Double-click: jump to the event that created the object.
- Search box: jump to object by id or label.

### 7.4 Diff View

Side-by-side comparison of two runs (typically parent + fork).

- Header bar with metrics delta (`+12 events`, `−$0.07 cost`,
  `+0.3s duration`).
- Two columns. Each row is a structural diff entry:
  - Object added/removed/changed (with the field diff inline).
  - Relation added/removed.
  - Memo or risk content diff (unified diff for prose).
- Filter chips above: `Only changed`, `Object type`, `Behavior`.

### 7.5 Approval Card

The atomic "decide this" tile.

- Pack policy badge.
- Proposed action summary (object type + truncated payload).
- Evidence chain: a horizontal scroll of event chips, leftmost is
  the root goal, rightmost is the proposed action. Click any chip
  to inspect.
- Two-step Approve / Reject buttons (click → confirm).
- Optional note field.
- Age + expiry pill.

### 7.6 Review Queue (Inbox)

The list of approvals. Defaults to "oldest first, mine first."

- Sort: age, priority, run, pack.
- Filter: status, pack, approver.
- Bulk select for reject-with-reason only. **Approve is never
  bulk** — principle 8 ("fast review beats clever automation").

### 7.7 Empty States

Every list screen has an empty state with: a one-sentence
description, an illustration (a small line-art graph), one primary
CTA, and a doc link.

- Runs empty: "No runs yet. Connect a runtime to start streaming."
  CTA: "Get a connection string."
- Inbox empty: "Nothing waiting on you." Illustration: a calm
  empty graph.
- Packs empty: "No packs installed. Browse the registry." CTA:
  "Browse packs."

### 7.8 Loading States

- **Skeletons** for predictable-shape content (lists, cards).
- **Indeterminate progress** for the graph view rebuild during
  event scrub.
- **Streaming** for live runs: the event row list appends new
  rows from the bottom with a 200 ms ease-in; the new-event
  indicator pulses in the top right if the user has scrolled away
  from "now."

### 7.9 Error States

Match the OSS error voice: state the problem, then the fix, then
a doc link.

- Toast for transient failures (network blip, retried
  automatically). Auto-dismiss after 4s.
- Inline error for form failures (e.g. invalid pack setting on
  fork wizard); field-level, red border, helper text.
- Full-page error for hard failures (404, 403, run not found);
  always include a "More: <doc-link>" line — same shape as OSS
  errors.

### 7.10 Detail / Side Panel

Many lists open a detail in a right-side panel rather than
navigating. Used for event detail, object detail, approval
detail. ESC closes; URL updates so the panel is shareable.

---

## 8. Visual Design System

### 8.1 Brand feel

Engineering-grade. Calm. Inspectable. Visually closer to Linear,
Vercel's docs, and Honeycomb than to a typical AI startup
(no aurora gradients, no glassmorphism, no "we believe in the
future" hero photo). The aesthetic communicates: *this product
treats your audit trail with respect.*

The animated graph is the brand mark. It moves only on the home
hero and on cloud loading states; it does not chrome other pages.

### 8.2 Layout

- **Grid.** 12-column, 1200 px max content width on marketing.
  Cloud console is flexible to viewport with a 240 px sidebar and
  fluid content area.
- **Density.** Marketing: airy. Cloud: dense by default with a
  per-user "comfortable / compact" toggle. Operators triage
  faster on compact.
- **Whitespace.** Generous on marketing (let the demo breathe).
  Disciplined on cloud (one screen, one job).

### 8.3 Typography

- **Sans.** Inter (or system sans fallback). 400 / 500 / 600.
- **Mono.** JetBrains Mono. Used for: event ids, run ids, code
  snippets, payload previews, CLI commands.
- **Scale (marketing).** 56 / 40 / 28 / 20 / 16 / 14.
- **Scale (cloud).** 24 / 20 / 16 / 14 / 13 / 12. Tighter.
- **Line height.** 1.5 for prose; 1.3 for headlines; 1.4 for
  tabular data.

### 8.4 Color

- **Neutral.** Six-step gray ramp (`gray-50` … `gray-900`).
  Background `gray-50` (light) / `gray-900` (dark). Dark mode is
  first-class for the cloud console; marketing defaults to light.
- **Accent.** A single ink-leaning indigo (`#3D52F5`). Used for
  primary CTAs, links, and selected state. Sparingly.
- **Status palette** (used for event-type pills, run status, diff
  highlights):
  - Graph mutation: `#0ea5e9` (sky)
  - LLM call: `#8b5cf6` (violet)
  - Tool call: `#10b981` (emerald)
  - Behavior fire: `#6b7280` (gray)
  - Approval: `#f59e0b` (amber)
  - Error: `#ef4444` (red)
- **Diff colors.** Added: emerald background `#10b98115`. Removed:
  red background `#ef444415`. Changed: amber border.

No gradients except a single 1-step shimmer on the graph mark.

### 8.5 Spacing

8-px base unit. Allowed values: 4, 8, 12, 16, 24, 32, 48, 64.
Anything else is a bug.

### 8.6 Icons

- Library: Lucide. Stroke 1.5, 16 px in cloud, 20 px on marketing.
- No emoji in product UI.
- The graph-mark logo is custom: three nodes, two edges, one of
  the nodes is colored.

### 8.7 Motion

- **Default.** None. Static reveals.
- **Allowed.** Subtle hover elevation (1 → 4 px shadow), 150 ms
  ease-out fades on panel open/close, 200 ms ease-in for streaming
  event rows, scrubber drag is real-time.
- **Banned.** Parallax, scroll-jacking, autoplay video with
  sound, "look at our animated wordmark" intros.
- **Respect `prefers-reduced-motion`.** All non-essential motion
  is suppressed when the OS preference is set.

### 8.8 Mobile

- **Marketing.** Fully responsive. Demo collapses to a recorded
  canvas animation below 768 px (pyodide is heavy on mobile).
- **Cloud console.** Read-mostly on mobile. Operators can:
  - View any run, event, graph, diff.
  - **Approve / reject** from the Inbox. (Phone-from-bed
    workflow is intentional.)
  - Cannot edit team / workspace settings, cannot fork — these
    redirect to "open on desktop."
- **Sidebar.** Bottom tab bar on mobile (Runs / Inbox / Packs /
  Profile).

---

## 9. Interaction Rules

### 9.1 Confirmation

Two-step confirm required for:

- Approving or rejecting an approval (with the proposed payload
  visible in the confirm step).
- Creating a fork.
- Deleting a run, a pack installation, or a team member.
- Changing workspace plan or billing.
- Generating an audit-export bundle (because it includes payloads).

No confirmation for:

- Navigation.
- Filter / sort changes.
- Toggling display preferences (density, dark mode).
- Opening side panels.

### 9.2 Undo

- **Soft-delete** for runs, packs, team members: 30-day undo from
  a `Trash` view in workspace settings.
- **No undo** for approval decisions. The decision lands as an
  event in the run log; reversing it requires a fresh decision via
  a follow-up approval, which is itself an event. This matches the
  OSS event-sourced model — you don't edit history, you append.
- **Undo toast** for non-destructive bulk actions (e.g. reject
  many approvals with reason) — 10-second window.

### 9.3 Logging

Every consequential action emits a workspace-audit-log entry
(separate from per-run event logs):

- Sign-in / sign-out.
- Approval decisions.
- Run create / delete.
- Fork create.
- Pack install / uninstall.
- Team-member invite / remove / role change.
- API token create / revoke.
- Billing / plan change.
- Export bundle generation.

Audit log is visible to workspace admins at `/audit`. Per-run
event logs are immutable and complete; the workspace audit log is
append-only.

### 9.4 Keyboard shortcuts

Cloud console:

- `⌘K` — command palette (jump to anything by id or name).
- `g` then `r` — go to Runs.
- `g` then `i` — go to Inbox.
- `g` then `p` — go to Packs.
- `j` / `k` — next / previous item in lists.
- `e` — expand current event row.
- `f` — open Fork wizard from current event.
- `?` — keyboard-shortcut cheat sheet.
- `esc` — close side panel.

All shortcuts honor a textbox-focus check (no triggering while
typing).

### 9.5 Notifications

Three channels: in-app, email, Slack (post-MVP). Per-user prefs
per workspace.

Default triggers:

- A run in your workspace fails (`behavior.failed` with `reason`
  in a deny-list of stable failure modes).
- An approval lands in your queue.
- An approval you authored is escalated or expires.
- A run finishes that you started.

Anti-noise rules:

- Coalesce: 5+ events of the same type within 2 minutes collapse
  to one summary notification.
- Quiet hours: per-user setting; respected for email and Slack
  (in-app always lands).
- No marketing inside notifications. Ever.

---

## 10. AI / Automation Behavior

Active Graph is built around AI behaviors. The product's stance on
how to *display* AI work is as important as how to *invoke* it.

### 10.1 What AI can do (in the runtime)

- Execute LLM-backed behaviors that mutate the graph.
- Call tools (functions or fixtures) on behalf of behaviors.
- Generate proposed objects (memos, risks, claims) for human
  approval, gated by pack policies.

### 10.2 What requires human approval

- Anything a pack policy declares (`requires_approval=("memo",)`).
- By default, the cloud console proposes (but does not enforce)
  human approval for: writing a memo, generating a risk artifact,
  any tool call whose `cost_usd_estimate` exceeds a workspace
  threshold.
- Workspace admins can configure additional approval gates per
  pack or per object type without writing code (UI-driven).

### 10.3 What evidence AI must show

Every AI-produced artifact in the UI exposes, one click away:

- The prompt content hash (matches the pack's recorded hash, or is
  flagged as drifted).
- The model name and version.
- The full prompt and the full response.
- Token counts and cost.
- The event chain back to the root goal.
- Tool calls invoked while producing the response.

If any of these are missing for a displayed AI artifact, that is a
bug.

### 10.4 How confidence is displayed

The OSS does not force a confidence field on every behavior. The
cloud UI surfaces it *only when the pack provides it*:

- If the object schema (Pydantic) has a `confidence: float` field,
  the UI renders a probability bar.
- Otherwise the field is absent — no fabricated confidence.

The cloud never displays an AI-generated confidence score
prominently in a way that suggests certainty the model didn't
claim. Confidence is shown as "the model said 0.7" — never as
"this is 70% reliable."

### 10.5 How corrections are handled

- A reject-with-note in the Approval inbox lands as an event in
  the run log. Subsequent behaviors that subscribe to
  `approval.denied` can react (re-prompt, fall back, escalate).
- Operators can fork a run with corrected pack settings to test a
  fix without disrupting the parent run.
- Corrections never edit prior events. They append. (Same as the
  OSS event-sourced contract.)

### 10.6 What is never allowed automatically

- **No autoplay of paid actions.** Tool calls flagged as having
  external side effects (sending an email, executing a trade,
  calling a paid API beyond a workspace-configured threshold)
  always require approval, regardless of pack default. The cloud
  enforces this even if the pack doesn't.
- **No prompt-injection blind trust.** Tool responses and external
  documents that flow into LLM prompts are visibly marked as
  external in the trace view; behaviors can opt into a
  prompt-injection-detection middleware (post-MVP).
- **No silent auto-merge of forks back into a parent.** Fork
  results are diffed, reviewed, then explicitly applied — never
  auto-merged.
- **No model swapping without notice.** If the configured model
  changes (e.g. a default upgrade), the workspace admin is
  notified before runs use the new model, and the change is
  logged.

---

## 11. System Behavior

### 11.1 Ingestion (cloud event stream)

- A runtime configured with
  `ACTIVEGRAPH_STORE_URL=activegraph+cloud://...` streams events to
  the cloud as they emit. Transport: HTTPS POST per-event, with
  batch fallback if backpressure builds.
- The cloud appends to the run's event log. The log is immutable
  per OSS contract.
- Events arrive in monotonic order per run; out-of-order arrivals
  are rejected with a fix-link error (likely a misconfigured ID
  generator).

### 11.2 Syncing (between OSS and cloud)

- `activegraph migrate <source-url> <activegraph+cloud://...>` ships
  a historical run from a local SQLite or self-hosted Postgres into
  cloud. Idempotent.
- `activegraph migrate <activegraph+cloud://...> <local-url>` ships
  a run *out* of cloud. **Equally first-class.** The export is the
  exact event log; replaying it locally produces the same
  projection.

### 11.3 Background jobs

- **Replay-on-demand.** When a user opens a run, the projection is
  rebuilt server-side (cached for the session) and streamed to the
  client.
- **Diff computation.** Triggered on fork completion or on diff-view
  open; cached per (run_a, run_b) pair.
- **Notification fan-out.** Per-workspace queue; never blocks event
  ingestion.
- **Approval expiry sweep.** Hourly. Marks expired approvals;
  emits a workspace audit-log entry.

### 11.4 Permissions

Three roles per workspace:

- **Owner.** Billing, team management, pack management, all
  approvals, all runs.
- **Member.** All runs, all approvals, pack browse and install.
  Cannot delete a workspace or change billing.
- **Viewer.** Read-only on runs and approvals. Cannot approve or
  fork.

Approval policies can additionally name specific approvers per
policy (e.g. "memo_approval requires a user in the `analysts`
group"). Configured per workspace, stored as workspace settings.

### 11.5 Integrations

MVP:

- **GitHub OAuth** for sign-in.
- **Slack** for notifications (post-MVP for two-way approvals).
- **Webhooks** out: on run-completed, run-failed, approval-pending,
  approval-decided. Signed with HMAC, retry with backoff.

Post-MVP:

- **Linear / Jira** issue creation from a failed run.
- **PagerDuty** for hard runtime failures.
- **SSO** (SAML / OIDC) on the team plan and above.

### 11.6 Error handling

- All cloud API errors return `{ "error": { "code", "message", "fix_url" } }`.
  Same shape as OSS error messages.
- The UI surfaces these per § 7.9.
- Internal errors log to the workspace audit log with a request id
  the user can give support.

### 11.7 Audit log

Workspace-level (§ 9.3). Append-only. Exportable as JSONL.
Retention: indefinite on team plan and above; 90 days on free.

---

## 12. MVP Scope

The MVP is what ships to make the first 50 paying operators
successful. Anything beyond is deferred.

### 12.1 Must-have (marketing site)

- Home with above-the-fold animated demo.
- `/why`, `/cloud`, `/pricing`, `/security`.
- Pack registry index + 3 launch packs visible (Diligence + two
  partner-built).
- Blog (5 launch posts).
- Sign-up flow with GitHub OAuth.
- Cohesive light/dark, mobile-responsive.

### 12.2 Must-have (cloud console)

- Workspace + team (Owner, Member, Viewer roles).
- Runs list, Run Detail (graph, trace, event detail).
- Cloud event store (Postgres-backed; same protocol as OSS).
- Approval Inbox (per-pack policies, in-app + email
  notifications).
- Fork-and-diff in the browser.
- Audit export (JSONL, signed bundle).
- Per-error fix-link toasts.
- `⌘K` palette + keyboard shortcuts.

### 12.3 Nice-to-have (post-MVP, ranked)

1. Slack two-way approvals.
2. Pack publishing UI for authors (vs the CLI-only path at MVP).
3. Multi-region store (EU residency).
4. SSO (SAML / OIDC).
5. Live collaboration cursors on Run Detail.
6. Metrics dashboard (per-pack cost trends, behavior-failure
   rates).
7. Replay-as-test fixtures: record a run, replay in CI.
8. Prompt-injection middleware toggle.
9. Linear / Jira / PagerDuty integrations.

### 12.4 Deferred (explicit no-not-yet)

- Distributed runtime / multi-process execution.
- Real-time multi-user editing of behaviors.
- A "no-code behavior builder."
- Marketplace billing for paid packs.
- A mobile app (mobile web suffices).
- Self-hosted cloud edition (only after the SaaS economics work).

### 12.5 Launch criteria

The cloud reaches GA when:

1. Five external operators are running a real workload through it
   for 30 days with no critical incidents.
2. The pack registry hosts three packs that aren't from the
   Active Graph team.
3. Audit-export bundles verify against a third-party tool (proves
   the signature scheme is honest).
4. Mean time from `behavior.failed` notification to landed event
   detail page (signed-in user, in-app) is under 5 seconds at
   the 95th percentile.
5. Documentation parity: every cloud feature has a corresponding
   "how to do this with just the OSS" answer at
   `docs.activegraph.ai`.

---

## 13. Non-Goals

The product will not become any of the following. If a feature
request reads like one of these, decline it and link here.

- **A general-purpose web UI for arbitrary databases.** The cloud
  console is shaped to event-sourced graph runs. It is not a
  Retool replacement.
- **An IDE.** Behaviors are written in Python, in the user's
  editor. The cloud does not offer a code editor. (Pyodide on the
  marketing page is for demo, not authoring.)
- **A model gateway.** No multi-model routing, no LLM proxy, no
  "OpenAI-compatible endpoint." Use the OSS providers; bring
  your own keys.
- **A workflow engine.** No DAG builder, no visual flow editor.
  The graph is the world state, not the control flow.
- **A vector DB.** Embeddings are an implementation detail of
  particular packs, not a platform primitive.
- **A LangSmith competitor for non-Active-Graph runs.** We do not
  observe arbitrary LLM apps. The cloud is specifically for Active
  Graph runtimes.
- **A consumer product.** Operators and builders, not end
  consumers. No social features, no public profiles, no
  gamification.

---

## 14. Guidance for AI Coding Agents

If you are an AI agent modifying the marketing site, the cloud
console, or any related artifact, read this section before doing
anything else. The rules are short on purpose.

1. **Read `design.md` (this file) first.** Then read the section
   relevant to your change.
2. **Preserve the product principles in § 2.** When a proposed
   change conflicts with a principle, surface the conflict to the
   user — do not silently override.
3. **Use existing UX patterns from § 7 before inventing new ones.**
   Three similar one-off cards is a smell; reach for the Run Card
   or Approval Card.
4. **Do not add new core objects (§ 6) without updating this file
   in the same change.** A new object means new lifecycles, new
   permissions, new audit-log entries. Document them or don't ship
   them.
5. **Do not add new core workflows (§ 4) without documenting
   them.** A "quick feature" that doesn't map to a documented
   workflow is technical debt the design doc can't catch.
6. **Update lifecycle, permissions, and logging rules when
   behavior changes.** § 6 (lifecycles), § 9.3 (audit log), § 11.4
   (permissions) must stay in sync with the code.
7. **Match the voice.** Active, declarative, name the invariant.
   The OSS docs at `docs.activegraph.ai` are the voice canon —
   read three concept pages before writing copy. Avoid marketing
   adverbs ("seamlessly," "effortlessly," "delightful").
8. **Match the OSS error voice in cloud errors.** State the
   problem, then the fix, then a `More:` link. The same shape as
   `docs.activegraph.ai/reference/errors/*`.
9. **One brand: Active Graph.** Lowercase `activegraph` in code,
   URLs, CLI, package names. "Active Graph" (two words, both
   capitalized) in prose. "Active Graph Cloud" when the
   distinction from the OSS matters. Never invent a second brand.
10. **Mobile is read-mostly + Inbox-approve.** Don't ship
    write-heavy flows on mobile; redirect to desktop with a clear
    message.
11. **Flag conflicts between implementation and this document.**
    If you discover the code is doing something different from
    what this file says, file it (or surface it to the user). Do
    not silently reconcile by editing this file to match buggy
    behavior.
12. **Never invent confidence scores, completion percentages, or
    "AI reliability" badges.** Per § 10.4, only show what the
    model claimed.
13. **No new analytics events without listing them in the audit
    log (§ 9.3) or the privacy page (§ 11.7).** If you're tempted
    to add silent telemetry, stop.

---

## 15. Open Questions

These are unresolved as of this version. Each needs a decision
before the related work ships. Track these in the company doc
tracker, not in this file long-term.

### Product

- **Q1 (cloud pricing model).** Per-event, per-run, per-seat, or
  flat tier? Need 5 customer-development conversations before
  locking. MVP launches with a single $X/month team tier and
  metered overage TBD.
- **Q2 (free tier).** How generous? Need to support the builder
  persona (Tessa) using cloud for personal projects without
  abusing it. Working hypothesis: 1 user, 1 workspace, 1k events
  per day, 7-day retention.
- **Q3 (cloud naming).** "Active Graph Cloud" is the working name;
  consider "Active Graph for Teams" if user testing shows "Cloud"
  reads too infrastructure-y for the operator buyer. Decide before
  the `/cloud` page ships.

### UX

- **Q4 (graph layout).** Force-directed by default vs DAG-laid-out
  vs pack-specified-layout. Diligence runs look great as a DAG;
  free-form research graphs need force-directed. Likely
  resolution: per-pack hint, default to force-directed.
- **Q5 (event-row density).** Compact vs comfortable as the
  default. Lean compact for operators (the primary cloud
  persona), provide a one-click toggle.
- **Q6 (live updates UX).** When new events stream in while the
  user is reading older events: pulse-and-stay, or stick to "now"?
  Lean pulse-and-stay (don't yank context out from under them).

### AI / automation

- **Q7 (proactive AI in the console).** Should the cloud propose
  fixes when a run fails ("the prompt drifted from the pack's
  recorded hash — here's the diff")? Compelling but risks
  violating principle 3 (inspectability) if the proposal is
  black-box. Defer to post-MVP; require evidence panel if shipped.
- **Q8 (auto-rerun on transient failure).** Configurable per-pack
  policy? Or always require an operator action? Lean
  require-action for MVP; the failure model says "events,
  not exceptions" — operators decide.

### Technical

- **Q9 (cloud store backend).** Postgres is the obvious answer for
  MVP. At what scale does it stop being enough? Set an alert at
  10k events/sec per workspace; revisit then.
- **Q10 (audit-export signature scheme).** Detached signature
  with workspace-rotating Ed25519 keys is the working plan; a
  third-party verification tool is part of GA criteria. Confirm
  with one design-partner auditor before locking.
- **Q11 (multi-region).** EU residency from day 1 or punt to
  post-MVP? Punt unless an early design-partner blocks on it.

### Scope

- **Q12 (pack monetization).** If pack authors want to charge,
  do we host billing for them or let them DIY via Stripe? Decline
  at MVP; revisit if 3+ authors ask in the first 90 days.
- **Q13 (self-hosted cloud).** Enterprise will ask. Likely answer
  is "after 20 SaaS customers." Document the answer publicly so
  sales doesn't promise it early.

---

## Appendix A — Page-by-page intent

A compact directory of every public page on the marketing site,
with the one job each page must do. The marketing AI should treat
this as the brief.

| Page | Primary job | Persona | Primary CTA |
|---|---|---|---|
| `/` | Convince a visitor in 60 s that this is worth 10 m | Builder, Decision-Maker | `pip install activegraph` |
| `/why` | Explain event-sourced agents vs alternatives | Builder, Decision-Maker | Read the quickstart |
| `/demo` | Run the framework in their browser | Builder | Install the OSS |
| `/packs` | Discover a pre-built pack | Builder, Pack Author | Install pack X |
| `/packs/<name>` | Evaluate one pack | Builder | Install / Try in browser |
| `/cloud` | Convince an operator the console saves them a week | Operator | Sign up |
| `/cloud/pricing` | Make the price knowable in 30 s | Decision-Maker, Operator | Start free trial |
| `/cloud/security` | Pass a 30-min security review | Decision-Maker | Talk to sales |
| `/blog` | Establish technical credibility | Builder, Decision-Maker | Subscribe |
| `/changelog` | Show velocity, build trust | Operator | Subscribe |
| `/about` | Humanize the team | All | (no CTA) |
| `/contact` | Route inbound | All | Send |
| `/verify/<hash>` | Verify an audit-export bundle | Auditor (third party) | (functional) |

---

## Appendix B — Copy do's and don'ts

**Do.**

- "The graph is the world. Behaviors are physics. The trace is the proof."
- "Agents you can audit."
- "Inspect any run. Replay any failure. Approve any action."
- "MIT today, MIT tomorrow."
- "Cloud is convenience, not gravity."

**Don't.**

- "Revolutionary AI platform."
- "Seamlessly orchestrate your agents."
- "10x your agent velocity."
- "AI-powered" (everything is AI-powered; the phrase carries no
  information).
- "Enterprise-grade" (used by every B2B startup; means nothing).
- "Delightful developer experience."
- Anything that an AI startup landing page generator would write.

---

## Appendix C — Naming rules

Because the brand is one word and we will be asked again:

- **Package / CLI / URL / code.** Lowercase, one word: `activegraph`.
  `pip install activegraph`, `activegraph quickstart`,
  `activegraph.ai`, `docs.activegraph.ai`, `app.activegraph.ai`.
- **Prose.** Two words, both capitalized: "Active Graph."
- **The cloud offering.** "Active Graph Cloud" (when distinguishing
  from the OSS) or just "the cloud / the console" in context.
- **Never used.** ActiveGraph (camelCase), Active-Graph (hyphen),
  activegraph.ai/cloud as a separate brand, or any second name.

---

*The graph is the world. Behaviors are physics. The trace is the proof.*
