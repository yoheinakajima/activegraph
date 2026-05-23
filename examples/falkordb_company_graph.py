"""FalkorDB example — software company graph.

Creates a realistic graph of a small software company:
  - 5 departments, 25 engineers, 6 projects, 40+ tasks
  - Typed relations: works_in, leads, assigned_to, depends_on, part_of
  - Behaviors that react to events and populate the graph reactively

activegraph uses FalkorDB as the event store (Run + Event nodes).
After the runtime finishes, the script also writes the derived business
objects (person, department, project, task) and their relationships as
real Cypher nodes/rels into the **same** FalkorDB graph.  Different labels
coexist without interference, so you can query business data directly.

Requires FalkorDB running locally (see docs/guides/testing-falkordb-locally.md):

    docker run -d --name falkordb -p 6379:6379 falkordb/falkordb:latest
    python examples/falkordb_company_graph.py

After it runs, query the business graph:

    redis-cli -p 6379
    GRAPH.QUERY company "MATCH (p:person)-[:works_in]->(d:department) RETURN p.name, d.name"
    GRAPH.QUERY company "MATCH (t:task) WHERE t.priority = 'high' RETURN t.title, t.project ORDER BY t.story_points DESC"
    GRAPH.QUERY company "MATCH (t:task)-[:depends_on]->(b:task) RETURN t.title, b.title"
"""

from __future__ import annotations

import json
import os

from activegraph import Graph, Runtime, behavior, clear_registry, relation_behavior

FALKOR_URL = os.environ.get(
    "ACTIVEGRAPH_FALKORDB_URL", "falkor://localhost:6379/company"
)

# ── seed data ──────────────────────────────────────────────────────────────

DEPARTMENTS = [
    {"name": "Platform Engineering", "budget_usd": 2_400_000},
    {"name": "Product",              "budget_usd": 1_800_000},
    {"name": "Data & ML",            "budget_usd": 2_100_000},
    {"name": "Security",             "budget_usd": 900_000},
    {"name": "Developer Experience", "budget_usd": 750_000},
]

# (name, department_index, seniority, skills)
ENGINEERS = [
    # Platform Engineering (0)
    ("Alice Chen",     0, "staff",    ["kubernetes", "go", "terraform"]),
    ("Bob Nguyen",     0, "senior",   ["go", "kafka", "postgres"]),
    ("Carla Reyes",    0, "mid",      ["python", "docker", "prometheus"]),
    ("David Kim",      0, "senior",   ["rust", "networking", "eBPF"]),
    ("Eva Johansson",  0, "mid",      ["go", "grpc", "kubernetes"]),
    # Product (1)
    ("Frank Li",       1, "staff",    ["typescript", "react", "figma"]),
    ("Grace Obi",      1, "senior",   ["typescript", "graphql", "node"]),
    ("Hiro Tanaka",    1, "mid",      ["typescript", "react", "css"]),
    ("Isabelle Morel", 1, "senior",   ["product", "analytics", "sql"]),
    ("James Webb",     1, "mid",      ["typescript", "testing", "storybook"]),
    # Data & ML (2)
    ("Kiran Patel",    2, "staff",    ["python", "pytorch", "spark"]),
    ("Luna Ferreira",  2, "senior",   ["python", "sklearn", "sql"]),
    ("Marco Russo",    2, "senior",   ["python", "dbt", "airflow"]),
    ("Nadia Hassan",   2, "mid",      ["python", "pandas", "jupyter"]),
    ("Oscar Nielsen",  2, "mid",      ["python", "mlflow", "kubernetes"]),
    # Security (3)
    ("Priya Singh",    3, "staff",    ["security", "threat-modeling", "go"]),
    ("Quentin Blake",  3, "senior",   ["security", "pentest", "python"]),
    ("Rosa Park",      3, "mid",      ["security", "compliance", "aws"]),
    ("Sam Torres",     3, "senior",   ["security", "devsecops", "terraform"]),
    # Developer Experience (4)
    ("Tina Walker",    4, "staff",    ["python", "dx", "cli"]),
    ("Umar Al-Farsi",  4, "senior",   ["typescript", "vscode", "testing"]),
    ("Vera Schmidt",   4, "mid",      ["python", "docs", "mkdocs"]),
    ("Will Okoye",     4, "senior",   ["python", "ci/cd", "github-actions"]),
    ("Xiu Long",       4, "mid",      ["typescript", "rollup", "dx"]),
    ("Yael Cohen",     4, "senior",   ["python", "observability", "opentelemetry"]),
]

PROJECTS = [
    {"name": "Orion",   "status": "active",  "quarter": "Q3-2026", "department": 0},
    {"name": "Nebula",  "status": "active",  "quarter": "Q3-2026", "department": 2},
    {"name": "Pulsar",  "status": "active",  "quarter": "Q4-2026", "department": 1},
    {"name": "Quasar",  "status": "planned", "quarter": "Q4-2026", "department": 3},
    {"name": "Rigel",   "status": "active",  "quarter": "Q3-2026", "department": 4},
    {"name": "Sirius",  "status": "planned", "quarter": "Q1-2027", "department": 0},
]

# (title, project_index, priority, story_points)
TASKS = [
    # Orion — platform
    ("Migrate Kafka cluster to KRaft mode",   0, "high",   13),
    ("Add distributed tracing to API gateway",0, "high",    8),
    ("Autoscale worker pools on p99 latency", 0, "medium",  5),
    ("Upgrade Postgres to 17",                0, "medium",  3),
    ("Reduce cold-start time < 200ms",        0, "high",    8),
    ("Write runbook for DR procedure",        0, "low",     2),
    ("Kubernetes node pool cost audit",       0, "medium",  5),
    # Nebula — data/ML
    ("Build feature store v2",                1, "high",   21),
    ("Automate model retraining pipeline",    1, "high",   13),
    ("Add A/B testing framework",             1, "medium",  8),
    ("Migrate legacy ETL to dbt",             1, "medium",  8),
    ("ML latency SLA < 50ms p95",             1, "high",   13),
    ("Data lineage dashboard",                1, "low",     5),
    # Pulsar — product
    ("Redesign onboarding flow",              2, "high",   13),
    ("Dark mode support",                     2, "medium",  8),
    ("Accessibility audit (WCAG 2.1 AA)",     2, "high",    8),
    ("Real-time notification centre",         2, "high",   13),
    ("Export to CSV / Excel",                 2, "medium",  3),
    ("Infinite scroll for feed",              2, "medium",  5),
    ("Component library migration to v2",     2, "high",   21),
    # Quasar — security
    ("Threat model new auth service",         3, "high",   13),
    ("Automate CVE scanning in CI",           3, "high",    8),
    ("Rotate all secrets quarterly",          3, "medium",  5),
    ("SOC 2 evidence collection",             3, "high",   21),
    ("Penetration test Q3",                   3, "high",   13),
    # Rigel — developer experience
    ("Improve local dev setup to < 5 min",    4, "high",   13),
    ("Add code coverage gate to CI",          4, "medium",  5),
    ("Revamp internal docs site",             4, "medium",  8),
    ("Publish activegraph FalkorDB guide",    4, "low",     2),
    ("OpenTelemetry instrumentation guide",   4, "medium",  5),
    # Sirius — next platform iteration
    ("Architecture spike: multi-region",      5, "high",   21),
    ("Cost model for new infra",              5, "medium",  8),
    ("PoC: Rust-based routing layer",         5, "high",   13),
]

# task dependencies: (task_index_a, task_index_b) means b depends on a
TASK_DEPS = [
    (0, 3),   # Kafka KRaft blocks Postgres upgrade
    (7, 8),   # feature store v2 blocks retraining pipeline
    (7, 9),   # feature store v2 blocks A/B testing
    (13, 14), # onboarding blocks dark mode (same surface)
    (19, 13), # component lib migration unblocks onboarding
    (20, 21), # threat model blocks automate CVE
    (29, 30), # architecture spike blocks cost model
    (29, 31), # architecture spike blocks Rust PoC
]

# ── behaviors ──────────────────────────────────────────────────────────────


def register_behaviors() -> None:
    clear_registry()

    # Shared state: populated during graph construction so behaviors can
    # cross-reference objects by ID without scanning the full graph.
    # (BehaviorGraph exposes only add/patch/emit; all_objects is on Graph.)
    eng_id_by_name: dict[str, str] = {}    # name → object ID
    dept_id_by_index: dict[int, str] = {}  # dept_index → object ID

    @behavior(name="seed_departments", on=["goal.created"])
    def seed_departments(event, graph, ctx):
        """Emit one dept.created event per department."""
        for dept in DEPARTMENTS:
            graph.emit("dept.created", {"dept": dept})

    @behavior(name="seed_engineers", on=["dept.created"])
    def seed_engineers(event, graph, ctx):
        """When a department is created, add its engineers."""
        dept_data = event.payload["dept"]
        dept_obj = graph.add_object("department", dept_data)
        dept_index = DEPARTMENTS.index(dept_data)
        dept_id_by_index[dept_index] = dept_obj.id
        for name, di, seniority, skills in ENGINEERS:
            if di != dept_index:
                continue
            eng = graph.add_object("person", {
                "name": name,
                "seniority": seniority,
                "skills": skills,
                "status": "active",
            })
            eng_id_by_name[name] = eng.id
            graph.add_relation(eng.id, dept_obj.id, "works_in")
            if seniority == "staff":
                graph.add_relation(eng.id, dept_obj.id, "leads")

    @behavior(name="seed_projects", on=["goal.created"])
    def seed_projects(event, graph, ctx):
        """Emit one project.created event per project."""
        for proj in PROJECTS:
            graph.emit("project.created", {"project": proj, "index": PROJECTS.index(proj)})

    @behavior(name="build_project", on=["project.created"])
    def build_project(event, graph, ctx):
        """Create the project node, attach tasks, and assign engineers."""
        proj_data = event.payload["project"]
        proj_index = event.payload["index"]
        proj_obj = graph.add_object("project", {
            "name": proj_data["name"],
            "status": proj_data["status"],
            "quarter": proj_data["quarter"],
        })

        # Wire staff engineer as project lead (ID looked up from shared dict)
        dept_idx = proj_data["department"]
        staff_name = next(
            name for name, di, sen, _ in ENGINEERS
            if di == dept_idx and sen == "staff"
        )
        if staff_name in eng_id_by_name:
            graph.add_relation(eng_id_by_name[staff_name], proj_obj.id, "leads")

        # Add tasks that belong to this project
        task_objs: dict[int, str] = {}  # task_index → object ID
        for task_idx, (title, pi, priority, sp) in enumerate(TASKS):
            if pi != proj_index:
                continue
            task = graph.add_object("task", {
                "title": title,
                "priority": priority,
                "story_points": sp,
                "status": "open" if proj_data["status"] == "active" else "planned",
                "project": proj_data["name"],
            })
            task_objs[task_idx] = task.id
            graph.add_relation(task.id, proj_obj.id, "part_of")

        # Wire task dependencies
        for (a, b) in TASK_DEPS:
            if a in task_objs and b in task_objs:
                graph.add_relation(task_objs[b], task_objs[a], "depends_on")

        # Assign engineers to tasks (round-robin across department engineers)
        dept_engs = [
            eng_id_by_name[name]
            for name, di, _, _ in ENGINEERS
            if di == dept_idx and name in eng_id_by_name
        ]
        for i, task_id in enumerate(task_objs.values()):
            if dept_engs:
                graph.add_relation(task_id, dept_engs[i % len(dept_engs)], "assigned_to")

    @relation_behavior(
        name="unblock_tasks",
        relation_type="depends_on",
        on=["task.completed"],
    )
    def unblock_tasks(relation, event, graph, ctx):
        """When a blocking task completes, open the dependent task."""
        if event.payload.get("task_id") == relation.target:
            graph.patch_object(relation.source, {"status": "open"})


# ── materialise derived graph to FalkorDB ─────────────────────────────────


# ── materialise derived graph into the same FalkorDB graph ────────────────


def materialize_to_falkordb(ag_graph, url: str) -> None:
    """Write activegraph objects/relations as labelled nodes & Cypher rels.

    Uses the same FalkorDB graph as the event store.  Business nodes
    (person, department, task …) coexist with Run/Event nodes because they
    carry different labels — no interference with the event log.
    """
    import falkordb as fdb

    # Parse falkor[db]://host:port/graph-name
    raw = url.replace("falkordb://", "falkor://")
    if raw.startswith("falkor://"):
        raw = raw[len("falkor://"):]
    host_port, _, graph_name = raw.partition("/")
    host, _, port_str = host_port.partition(":")
    port = int(port_str) if port_str else 6379
    graph_name = graph_name or "company"

    db = fdb.FalkorDB(host=host, port=port)
    g = db.select_graph(graph_name)

    print(f"\n⬆  Writing business nodes/rels into FalkorDB graph '{graph_name}'")

    # nodes
    for obj in ag_graph.all_objects():
        props = {"ag_id": obj.id}
        for k, v in obj.data.items():
            if isinstance(v, (str, int, float, bool)):
                props[k] = v
            elif isinstance(v, list):
                props[k] = json.dumps(v)
        prop_str = ", ".join(f"{k}: {json.dumps(v)}" for k, v in props.items())
        g.query(f"CREATE (:{obj.type} {{{prop_str}}})")

    # relations
    for rel in ag_graph.all_relations():
        g.query(
            f"MATCH (a {{ag_id: {json.dumps(rel.source)}}}), "
            f"(b {{ag_id: {json.dumps(rel.target)}}}) "
            f"CREATE (a)-[:{rel.type}]->(b)"
        )

    total_nodes = g.query("MATCH (n) RETURN count(n)").result_set[0][0]
    print(f"   total nodes in '{graph_name}': {total_nodes}  "
          f"(Run/Event + {len(list(ag_graph.all_objects()))} business nodes)")
    print(f"\n   Sample queries:")
    print(f'   GRAPH.QUERY {graph_name} "MATCH (p:person)-[:works_in]->(d:department) RETURN p.name, d.name"')
    print(f'   GRAPH.QUERY {graph_name} "MATCH (t:task) WHERE t.priority = \'high\' RETURN t.title, t.project"')
    print(f'   GRAPH.QUERY {graph_name} "MATCH (t:task)-[:depends_on]->(b:task) RETURN t.title, b.title"')



def main() -> None:
    print(f"\n{'='*60}")
    print("activegraph × FalkorDB — company graph demo")
    print(f"store: {FALKOR_URL}")
    print(f"{'='*60}\n")

    register_behaviors()
    graph = Graph()
    rt = Runtime(
        graph,
        persist_to=FALKOR_URL,
        budget={"max_events": 2000, "max_seconds": 120},
    )

    rt.run_goal("Build company knowledge graph")

    # ── summary ──────────────────────────────────────────────────────────
    all_objs = list(graph.all_objects())
    by_type: dict[str, list] = {}
    for obj in all_objs:
        by_type.setdefault(obj.type, []).append(obj)

    print("\n📊 Graph summary")
    print(f"  Total objects   : {len(all_objs)}")
    for t, objs in sorted(by_type.items()):
        print(f"    {t:<15} {len(objs):>3}")

    all_rels = list(graph.all_relations())
    by_rel: dict[str, int] = {}
    for rel in all_rels:
        by_rel[rel.type] = by_rel.get(rel.type, 0) + 1
    print(f"\n  Total relations : {len(all_rels)}")
    for t, n in sorted(by_rel.items()):
        print(f"    {t:<15} {n:>3}")

    print(f"\n  Total events    : {len(rt.graph.events)}")

    # ── sample queries ───────────────────────────────────────────────────
    print("\n👤 Staff engineers (department leads):")
    for obj in by_type.get("person", []):
        if obj.data.get("seniority") == "staff":
            dept = next(
                (
                    graph.get_object(r.target)
                    for r in graph.get_relations(obj.id, "leads")
                    if graph.get_object(r.target) is not None
                ),
                None,
            )
            dept_name = dept.data.get("name", "?") if dept else "?"
            print(f"  {obj.data['name']:<20} leads  {dept_name}")

    print("\n🔴 High-priority open tasks:")
    high = [
        obj for obj in by_type.get("task", [])
        if obj.data.get("priority") == "high" and obj.data.get("status") == "open"
    ]
    for obj in sorted(high, key=lambda o: -o.data.get("story_points", 0))[:8]:
        print(
            f"  [{obj.data['story_points']:>2}sp] {obj.data['title'][:52]}"
            f"  ({obj.data['project']})"
        )

    print("\n🔗 Task dependency chains:")
    for rel in all_rels:
        if rel.type != "depends_on":
            continue
        task = graph.get_object(rel.source)
        blocker = graph.get_object(rel.target)
        if task and blocker:
            print(
                f"  {task.data['title'][:40]:<40}"
                f" ← blocks ←  {blocker.data['title'][:35]}"
            )

    # ── materialise business nodes/rels into the same FalkorDB graph ─────
    materialize_to_falkordb(graph, FALKOR_URL)

    print("\n✅ Done. Query the graph above with redis-cli.")
    print()


if __name__ == "__main__":
    main()
