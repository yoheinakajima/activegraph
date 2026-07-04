"""Cypher subset parser + matcher. CONTRACT v0.7 #8 / #9 / #11 / #12,
+ CONTRACT v1.0 PR-B (error format migration).

A strict subset of Cypher. Anything outside the subset raises
`UnsupportedPatternError` pointing at the offending token. A clean
subset is more useful than a fuzzy superset.

Supported (the LOCKED subset):
  * Node patterns:           (var:type {prop: value, ...})
  * Relationship patterns:   (a)-[var:rel_type]->(b)
                             (a)<-[var:rel_type]-(b)
  * Multi-hop:               (a)-[:r1]->(b)-[:r2]->(c)
  * WHERE clauses:           WHERE expr
      - comparisons:         a.confidence > 0.7
      - AND                  (NO OR — see CONTRACT v0.7 #8)
      - NOT                  NOT a.confidence > 0.5
      - NOT EXISTS { ... }   negation over a sub-pattern
  * Node `{prop: value}` is EQUALITY ONLY. Comparisons go in WHERE.
  * Identifiers:             ASCII letters/digits/underscore, leading letter.

Refused (will raise UnsupportedPatternError, pointing at the token):
  * RETURN — patterns don't return; bindings come out via ctx.matches
  * OPTIONAL MATCH
  * Variable-length paths -[*1..3]-
  * Aggregation (count, sum, ...)
  * WITH / pipeline composition
  * Subqueries beyond NOT EXISTS
  * OR in WHERE
  * UNION / UNWIND / CREATE / MERGE / SET / DELETE / DETACH

Parser produces a `Pattern` AST; the runtime calls
`Pattern.compile().matches(event, graph) -> list[Match]`. Compilation
happens once, at behavior registration (CONTRACT v0.7 #9).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Iterator, Optional

from activegraph.errors import DOCS_BASE_URL, PatternError

if TYPE_CHECKING:
    from activegraph.core.event import Event
    from activegraph.core.graph import Graph, Object, Relation


# CONTRACT v0.7 #8 voice notes for v1.0 PR-B:
# The pattern subset is small on purpose. Every refusal in this module
# protects two invariants — (a) patterns are exhaustively testable, so
# a behavior either fires or does not based on a small finite spec; and
# (b) the trace's audit trail stays meaningful, because a fuzzy match
# would let a behavior appear to fire on input it did not actually
# match. Recovery prose almost always points at "register two behaviors"
# (for OR-like semantics) or "do it in the behavior body" (for mutation,
# pipelines, etc.).


class UnsupportedPatternError(PatternError, SyntaxError):
    """Pattern uses syntax outside the v0.7 Cypher subset.

    Multi-inherits :class:`SyntaxError` so existing user code that
    catches ``SyntaxError`` around pattern compilation keeps working.
    The v1.0 structured-format superclass is :class:`PatternError`,
    which is itself an :class:`ActiveGraphError`.

    Construct via :meth:`refused_feature` or :meth:`syntax_error` —
    the factory class methods produce the canonical voice for the two
    failure modes (a refused-but-recognized Cypher feature vs. a
    parser-level syntax error). Direct construction with the structured
    fields is supported for one-off cases.
    """

    _doc_slug = "unsupported-pattern-error"

    def __init__(
        self,
        summary: str,
        *,
        what_failed: str,
        why: str,
        how_to_fix: str,
        at: Optional[str] = None,
        context: Optional[dict[str, Any]] = None,
    ) -> None:
        ctx: dict[str, Any] = dict(context) if context else {}
        if at is not None:
            ctx["at"] = at
        self.at = at
        super().__init__(
            summary,
            what_failed=what_failed,
            why=why,
            how_to_fix=how_to_fix,
            context=ctx,
        )

    @classmethod
    def refused_feature(
        cls,
        *,
        feature: str,
        workaround: str,
        at: Optional[str] = None,
        why: Optional[str] = None,
    ) -> "UnsupportedPatternError":
        """The canonical case: a recognized Cypher feature that the v0.7
        subset deliberately refuses, with a documented workaround.

        Use for OR, OPTIONAL MATCH, variable-length paths, undirected
        relationships, WITH, RETURN, CREATE, MERGE, etc. The ``feature``
        argument is included in the summary verbatim, so the substring
        is the same string an operator would search a log for.
        """
        return cls(
            f"{feature} is not supported in the v0.7 Cypher subset",
            what_failed=(
                f"The pattern uses {feature}. The v0.7 subset refuses this "
                f"feature at registration time — long before any match runs."
            ),
            why=(
                why
                if why
                else (
                    "The pattern subset is deliberately small and exhaustively "
                    "testable. A fuzzy superset of Cypher would let patterns "
                    "appear to match input they did not actually match, which "
                    "would break the audit trail that pattern-driven behaviors "
                    "preserve. See CONTRACT v0.7 #8 for the locked subset."
                )
            ),
            how_to_fix=workaround,
            at=at,
        )

    @classmethod
    def syntax_error(
        cls,
        *,
        what: str,
        at: Optional[str] = None,
        expected: Optional[str] = None,
        got: Optional[str] = None,
    ) -> "UnsupportedPatternError":
        """Parser-level error: the pattern does not parse at all (vs.
        parses-but-uses-refused-feature). Recovery points the developer
        at the offending token / position.
        """
        if expected is not None and got is not None:
            summary = f"pattern does not parse: expected {expected}, got {got}"
            body_top = (
                f"While parsing the pattern, the parser expected {expected} "
                f"but found {got}."
            )
        else:
            summary = f"pattern does not parse: {what}"
            body_top = f"While parsing the pattern: {what}."
        if at:
            body_top += f"\n  at: {at!r}"
        return cls(
            summary,
            what_failed=body_top,
            why=(
                "Behaviors register their pattern subscriptions at startup, "
                "so the parser refuses ambiguous syntax now rather than risk "
                "matching a pattern the developer did not actually write. An "
                "unparseable pattern is a configuration bug; matching is the "
                "next concern."
            ),
            how_to_fix=(
                f"Fix the syntax. The supported subset is documented in CONTRACT "
                f"v0.7 #8 and at\n"
                f"    {DOCS_BASE_URL}/concepts/patterns\n"
                f"If the syntax looks right, check for unbalanced brackets, a "
                f"missing relationship type, or a missing arrow direction."
            ),
            at=at,
        )


# Per-keyword recovery prose. CONTRACT v0.7 #8 enumerates the refused
# keywords; each one has a specific "do this in the behavior body
# instead" answer.
_KEYWORD_WORKAROUNDS: dict[str, str] = {
    "RETURN": (
        "Pattern subscriptions do not return values. Bindings reach the\n"
        "behavior body via `ctx.matches` — read them there."
    ),
    "OPTIONAL": (
        "OPTIONAL MATCH expresses 'match if present, else null.' The runtime\n"
        "does not have a null binding. Register a second behavior whose\n"
        "pattern is the optional sub-pattern."
    ),
    "WITH": (
        "WITH composes a pipeline of matches. The runtime evaluates each\n"
        "pattern as a flat match; pipelines are expressed as multiple\n"
        "behaviors chained through emitted events."
    ),
    "MATCH": (
        "Multiple MATCH clauses compose a pipeline. Flatten the pattern\n"
        "or register one behavior per clause and chain them through\n"
        "emitted events."
    ),
    "UNWIND": (
        "UNWIND iterates a collection. Iterate in the behavior body\n"
        "instead — `for row in ctx.matches: ...` — and express the source\n"
        "collection as a sub-pattern."
    ),
    "UNION": (
        "UNION takes the union of two queries. Register two behaviors,\n"
        "one per branch, and let both fire."
    ),
    "CREATE": (
        "Patterns observe the graph; they do not mutate it. Mutations go\n"
        "in the behavior body via `graph.add_object` / `graph.add_relation`."
    ),
    "MERGE": (
        "Same as CREATE — patterns do not mutate. Use\n"
        "`graph.add_object` (with idempotency handled by the behavior) in\n"
        "the body instead."
    ),
    "SET": (
        "Same as CREATE — patterns do not mutate. Use\n"
        "`graph.patch_object` in the behavior body."
    ),
    "DELETE": (
        "Same as CREATE — patterns do not mutate. Use\n"
        "`graph.remove_object` / `graph.remove_relation` in the behavior\n"
        "body."
    ),
    "DETACH": (
        "Same as DELETE — patterns do not mutate."
    ),
    "REMOVE": (
        "Same as DELETE — patterns do not mutate."
    ),
    "FOREACH": (
        "FOREACH iterates inside the pattern. Iterate in the behavior\n"
        "body instead."
    ),
    "CALL": (
        "CALL invokes a procedure. The runtime has no procedure registry;\n"
        "call your function from the behavior body."
    ),
    "LIMIT": (
        "LIMIT caps result count. Apply the cap in the behavior body by\n"
        "slicing `ctx.matches`."
    ),
    "SKIP": (
        "SKIP offsets results. Apply the offset in the behavior body."
    ),
    "ORDER": (
        "ORDER BY sorts results. Sort in the behavior body."
    ),
}


# ---------- AST -------------------------------------------------------------


@dataclass
class NodePat:
    var: Optional[str]
    type: Optional[str]
    properties: dict[str, Any] = field(default_factory=dict)


@dataclass
class RelPat:
    var: Optional[str]
    type: str
    # 'right' = (a)-[]->(b); 'left' = (a)<-[]-(b). Undirected not supported.
    direction: str


@dataclass
class MatchClause:
    """Linear chain of nodes connected by relationships.

    `rels[i]` connects `nodes[i]` to `nodes[i+1]`. v0.7 patterns are
    always linear chains — branching is "register two patterns" (same
    spirit as "no OR in WHERE: register two behaviors").
    """

    nodes: list[NodePat]
    rels: list[RelPat]


@dataclass
class Comparison:
    """Either path-vs-literal or path-vs-path."""

    left_path: list[str]
    op: str
    right_path: Optional[list[str]] = None  # one of right_path / right_value
    right_value: Any = None


@dataclass
class NotExpr:
    inner: "BoolExpr"


@dataclass
class NotExists:
    sub_match: MatchClause


@dataclass
class AndExpr:
    parts: list["BoolExpr"] = field(default_factory=list)


BoolExpr = Any  # Union[Comparison, NotExpr, NotExists, AndExpr]


@dataclass
class Pattern:
    match: MatchClause
    where: Optional[BoolExpr]
    source: str  # original pattern string, for error messages and tracing

    def compile(self) -> "PatternMatcher":
        return PatternMatcher(self)


# ---------- Lexer -----------------------------------------------------------


_TOKEN_RE = re.compile(
    r"""
    \s+                      # whitespace (skipped)
  | (?P<STRING>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')
  | (?P<NUMBER>-?\d+\.\d+|-?\d+)
  # Arrows BEFORE OP so '<-' tokenizes as ARROW_L, not OP('<') + DASH.
  | (?P<ARROW_R>->)
  | (?P<ARROW_L><-)
  | (?P<OP><=|>=|<>|!=|=|<|>)
  | (?P<DASH>-)
  | (?P<STAR>\*)
  | (?P<LPAREN>\()
  | (?P<RPAREN>\))
  | (?P<LBRACK>\[)
  | (?P<RBRACK>\])
  | (?P<LBRACE>\{)
  | (?P<RBRACE>\})
  | (?P<COMMA>,)
  | (?P<COLON>:)
  | (?P<DOT>\.)
  | (?P<IDENT>[A-Za-z_][A-Za-z0-9_]*)
    """,
    re.VERBOSE,
)


# Tokens that the parser inspects by .kind. Keywords are recognized
# at parse time from IDENT (case-insensitive).
@dataclass(frozen=True)
class Tok:
    kind: str
    text: str
    pos: int


_KEYWORDS = {"WHERE", "AND", "OR", "NOT", "EXISTS", "TRUE", "FALSE", "NULL"}
_FORBIDDEN_KEYWORDS = {
    "RETURN", "OPTIONAL", "WITH", "MATCH", "UNWIND", "UNION", "CREATE",
    "MERGE", "SET", "DELETE", "DETACH", "REMOVE", "FOREACH", "CALL",
    "LIMIT", "SKIP", "ORDER",
}


def _tokenize(s: str) -> list[Tok]:
    out: list[Tok] = []
    pos = 0
    while pos < len(s):
        m = _TOKEN_RE.match(s, pos)
        if not m:
            raise UnsupportedPatternError.syntax_error(
                what=f"unexpected character at position {pos}",
                at=s[pos : pos + 8],
            )
        if m.lastgroup is None:
            pos = m.end()
            continue
        kind = m.lastgroup
        text = m.group(kind)
        if kind == "IDENT":
            upper = text.upper()
            if upper in _FORBIDDEN_KEYWORDS:
                workaround = _KEYWORD_WORKAROUNDS.get(
                    upper,
                    f"Remove the {upper} keyword. The v0.7 subset refuses it; "
                    f"no equivalent in-subset expression exists for this case.",
                )
                raise UnsupportedPatternError.refused_feature(
                    feature=f"the {upper} keyword",
                    workaround=workaround,
                    at=text,
                )
            if upper in _KEYWORDS:
                kind = "KW_" + upper
                text = upper
        out.append(Tok(kind=kind, text=text, pos=pos))
        pos = m.end()
    out.append(Tok(kind="EOF", text="", pos=pos))
    return out


# ---------- Parser ----------------------------------------------------------


class _Parser:
    def __init__(self, tokens: list[Tok], source: str) -> None:
        self.tokens = tokens
        self.source = source
        self.i = 0

    def peek(self, offset: int = 0) -> Tok:
        return self.tokens[min(self.i + offset, len(self.tokens) - 1)]

    def eat(self, kind: str, text: Optional[str] = None) -> Tok:
        t = self.peek()
        if t.kind != kind or (text is not None and t.text != text):
            expected = kind + (f" {text!r}" if text else "")
            raise UnsupportedPatternError.syntax_error(
                what=f"expected {expected}, got {t.kind} {t.text!r}",
                expected=expected,
                got=f"{t.kind} {t.text!r}",
                at=t.text or self.source[t.pos : t.pos + 8],
            )
        self.i += 1
        return t

    def consume_if(self, kind: str, text: Optional[str] = None) -> Optional[Tok]:
        t = self.peek()
        if t.kind == kind and (text is None or t.text == text):
            self.i += 1
            return t
        return None

    # ---- top level ----

    def parse_pattern(self) -> Pattern:
        # Optional leading MATCH keyword is not in our grammar (forbidden
        # by lexer), so we just go straight into nodes/rels.
        match_clause = self.parse_match()
        where: Optional[BoolExpr] = None
        if self.consume_if("KW_WHERE"):
            where = self.parse_bool_expr()
        # Anything after WHERE (or after the match if no WHERE) is junk.
        if self.peek().kind != "EOF":
            t = self.peek()
            raise UnsupportedPatternError.syntax_error(
                what=f"unexpected trailing tokens after pattern: {t.kind} {t.text!r}",
                at=t.text,
            )
        return Pattern(match=match_clause, where=where, source=self.source)

    # ---- match (linear chain) ----

    def parse_match(self) -> MatchClause:
        first = self.parse_node()
        nodes: list[NodePat] = [first]
        rels: list[RelPat] = []
        while self.peek().kind in ("DASH", "ARROW_L"):
            rel = self.parse_rel()
            nxt = self.parse_node()
            rels.append(rel)
            nodes.append(nxt)
        return MatchClause(nodes=nodes, rels=rels)

    def parse_node(self) -> NodePat:
        self.eat("LPAREN")
        var: Optional[str] = None
        type_: Optional[str] = None
        if self.peek().kind == "IDENT":
            var = self.eat("IDENT").text
        if self.consume_if("COLON"):
            type_ = self.eat("IDENT").text
        props: dict[str, Any] = {}
        if self.consume_if("LBRACE"):
            props = self.parse_props()
            self.eat("RBRACE")
        self.eat("RPAREN")
        return NodePat(var=var, type=type_, properties=props)

    def parse_rel(self) -> RelPat:
        t = self.peek()
        if t.kind == "DASH":
            # -[...]-> or -[...]<- — the second is illegal (we don't
            # support undirected); the first is `direction=right`.
            self.eat("DASH")
            rel_var, rel_type = self._parse_edge_brackets()
            # Now expect ARROW_R (->); DASH (undirected -) is refused.
            arrow = self.peek()
            if arrow.kind == "ARROW_R":
                self.eat("ARROW_R")
                return RelPat(var=rel_var, type=rel_type, direction="right")
            if arrow.kind == "DASH":
                raise UnsupportedPatternError.refused_feature(
                    feature="undirected-relationship syntax",
                    workaround=(
                        "Pick a direction. Use `(a)-[:rel]->(b)` for source→target\n"
                        "or `(a)<-[:rel]-(b)` for target→source. The pattern\n"
                        "matcher needs the direction so the audit trail knows which\n"
                        "endpoint produced the binding."
                    ),
                    at="-",
                )
            raise UnsupportedPatternError.syntax_error(
                what=f"expected '->' after relationship, got {arrow.kind} {arrow.text!r}",
                expected="'->'",
                got=f"{arrow.kind} {arrow.text!r}",
                at=arrow.text,
            )
        if t.kind == "ARROW_L":
            self.eat("ARROW_L")
            rel_var, rel_type = self._parse_edge_brackets()
            self.eat("DASH")
            return RelPat(var=rel_var, type=rel_type, direction="left")
        raise UnsupportedPatternError.syntax_error(
            what=f"expected relationship between nodes, got {t.kind} {t.text!r}",
            expected="a relationship",
            got=f"{t.kind} {t.text!r}",
            at=t.text,
        )

    def _parse_edge_brackets(self) -> tuple[Optional[str], str]:
        self.eat("LBRACK")
        # Reject variable-length path syntax explicitly so the error
        # message points at the right place.
        if self.consume_if("STAR") is not None:
            raise UnsupportedPatternError.refused_feature(
                feature="variable-length path syntax (-[*]-)",
                workaround=(
                    "Express the path as N separate one-hop patterns and register\n"
                    "one behavior per length you care about. If the path length is\n"
                    "unbounded, the matcher would have unbounded cost — that's\n"
                    "the reason for the refusal, not just policy."
                ),
                at="*",
            )
        var: Optional[str] = None
        if self.peek().kind == "IDENT":
            var = self.eat("IDENT").text
        # Type is required: per the locked subset, relationships always
        # have an explicit type.
        if not self.consume_if("COLON"):
            t = self.peek()
            raise UnsupportedPatternError.syntax_error(
                what="relationship type required (e.g. [:supports] or [r:supports])",
                at=t.text,
            )
        type_tok = self.eat("IDENT")
        self.eat("RBRACK")
        return var, type_tok.text

    def parse_props(self) -> dict[str, Any]:
        # `{key: literal, key: literal, ...}` — equality only.
        out: dict[str, Any] = {}
        if self.peek().kind == "RBRACE":
            return out
        while True:
            key = self.eat("IDENT").text
            self.eat("COLON")
            out[key] = self.parse_literal()
            if not self.consume_if("COMMA"):
                break
        return out

    # ---- where ----

    def parse_bool_expr(self) -> BoolExpr:
        parts: list[BoolExpr] = [self.parse_unary()]
        while self.consume_if("KW_AND"):
            parts.append(self.parse_unary())
        if self.consume_if("KW_OR"):
            raise UnsupportedPatternError.refused_feature(
                feature="OR",
                workaround=(
                    "Register two behaviors, one per branch of the disjunction.\n"
                    "Both fire independently; if both branches are true for the\n"
                    "same event, both behaviors fire (which is usually what you\n"
                    "want — OR-then-dedup is not).\n"
                    "\n"
                    "Example:\n"
                    "  Instead of: WHERE c.confidence > 0.7 OR c.severity = 'high'\n"
                    "  Register:   one behavior with WHERE c.confidence > 0.7\n"
                    "              one behavior with WHERE c.severity = 'high'"
                ),
                why=(
                    "OR in WHERE clauses can produce match-set ambiguity at the "
                    "trace level: it's hard to tell, after the fact, which branch "
                    "of the OR actually triggered. Registering two behaviors keeps "
                    "every fire attributable to a specific pattern in the audit "
                    "trail. See CONTRACT v0.7 #8."
                ),
                at="OR",
            )
        if len(parts) == 1:
            return parts[0]
        return AndExpr(parts=parts)

    def parse_unary(self) -> BoolExpr:
        if self.consume_if("KW_NOT"):
            # NOT EXISTS { sub-pattern }
            if self.consume_if("KW_EXISTS"):
                self.eat("LBRACE")
                sub = self.parse_match()
                self.eat("RBRACE")
                return NotExists(sub_match=sub)
            return NotExpr(inner=self.parse_unary())
        if self.consume_if("LPAREN"):
            inner = self.parse_bool_expr()
            self.eat("RPAREN")
            return inner
        return self.parse_comparison()

    def parse_comparison(self) -> Comparison:
        left = self.parse_path()
        t = self.peek()
        if t.kind != "OP":
            raise UnsupportedPatternError.syntax_error(
                what=f"expected comparison operator, got {t.kind} {t.text!r}",
                expected="a comparison operator (=, <>, <, <=, >, >=)",
                got=f"{t.kind} {t.text!r}",
                at=t.text,
            )
        op = self.eat("OP").text
        # Right side: literal or another path
        nxt = self.peek()
        if nxt.kind in ("NUMBER", "STRING", "KW_TRUE", "KW_FALSE", "KW_NULL"):
            value = self.parse_literal()
            return Comparison(left_path=left, op=op, right_value=value)
        if nxt.kind == "IDENT":
            right_path = self.parse_path()
            return Comparison(left_path=left, op=op, right_path=right_path)
        raise UnsupportedPatternError.syntax_error(
            what=f"expected literal or path on rhs of comparison, got {nxt.kind} {nxt.text!r}",
            expected="a literal (number, string, true/false/null) or a binding path (a.field)",
            got=f"{nxt.kind} {nxt.text!r}",
            at=nxt.text,
        )

    def parse_path(self) -> list[str]:
        first = self.eat("IDENT").text
        parts = [first]
        while self.consume_if("DOT"):
            parts.append(self.eat("IDENT").text)
        return parts

    def parse_literal(self) -> Any:
        t = self.peek()
        if t.kind == "NUMBER":
            self.i += 1
            text = t.text
            return float(text) if ("." in text) else int(text)
        if t.kind == "STRING":
            self.i += 1
            # Strip surrounding quotes; minimal escape support.
            inner = t.text[1:-1]
            return inner.encode("utf-8").decode("unicode_escape")
        if t.kind == "KW_TRUE":
            self.i += 1
            return True
        if t.kind == "KW_FALSE":
            self.i += 1
            return False
        if t.kind == "KW_NULL":
            self.i += 1
            return None
        raise UnsupportedPatternError.syntax_error(
            what=f"expected literal, got {t.kind} {t.text!r}",
            expected="a literal (number, string, true, false, null)",
            got=f"{t.kind} {t.text!r}",
            at=t.text,
        )


def parse(pattern: str) -> Pattern:
    """Public entry point. Raises UnsupportedPatternError on any
    parse failure with the offending token."""
    tokens = _tokenize(pattern)
    return _Parser(tokens, pattern).parse_pattern()


# ---------- Matcher ---------------------------------------------------------


@dataclass
class Match:
    """A single pattern binding.

    Maps variable names to object_ids (for nodes) or relation_ids (for
    rels). Variables for unbound nodes/rels (e.g. `(:claim)`, `[:supports]`)
    are absent. Handlers iterate `ctx.matches` to consume; the runtime
    fires the behavior once per event, not once per match
    (CONTRACT v0.7 #12).
    """

    bindings: dict[str, str]

    def __getitem__(self, key: str) -> str:
        return self.bindings[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.bindings.get(key, default)


class PatternMatcher:
    """Pre-compiled matcher. Apply to (event, graph) → list[Match]."""

    def __init__(self, pattern: Pattern) -> None:
        self.pattern = pattern

    def matches(self, event: "Optional[Event]", graph: "Graph") -> list[Match]:
        # `event` is currently unused — patterns evaluate against the
        # post-event graph state. Future extensions may bind event
        # properties (e.g. `$event.payload.x`). v0.7 spec keeps it
        # graph-only.
        return list(self._enumerate_matches(self.pattern.match, graph))

    def _enumerate_matches(
        self, match_clause: MatchClause, graph: "Graph"
    ) -> Iterator[Match]:
        if not match_clause.nodes:
            return
        # Resolve the whole structural chain in one push-down call: node types
        # + relation types/directions go to the store (a single Cypher query
        # on FalkorDB; the in-memory default reproduces the same depth-first
        # walk). What stays in Python is exactly what can't be pushed down:
        # node {prop: value} equality (it lives in the JSON data blob),
        # variable-consistency, and WHERE.
        node_types = [n.type for n in match_clause.nodes]
        rels: list[tuple[Optional[str], str]] = [
            (r.type, r.direction) for r in match_clause.rels
        ]
        for chain in graph.match_chain(node_types, rels):
            objs = chain.objects
            if not all(
                _node_matches(o, np)
                for o, np in zip(objs, match_clause.nodes)
            ):
                continue
            bindings = _bind_chain(match_clause, objs, chain.relations)
            if bindings is None:
                continue  # a variable would bind two different ids
            if self.pattern.where is None or _eval_where(
                self.pattern.where, bindings, graph
            ):
                yield Match(bindings=dict(bindings))


def _bind_chain(
    match_clause: MatchClause, objs: "list[Object]", rels: "list[Relation]"
) -> Optional[dict[str, str]]:
    """Build the binding dict for one structural chain, enforcing that a
    repeated variable resolves to a single id. Returns ``None`` on conflict
    (the chain reuses an object/relation under a variable already bound to a
    different id, e.g. ``(a)-[:r]->(a)`` over two distinct objects).
    """
    bindings: dict[str, str] = {}
    for node_pat, obj in zip(match_clause.nodes, objs):
        if node_pat.var:
            existing = bindings.get(node_pat.var)
            if existing is not None and existing != obj.id:
                return None
            bindings[node_pat.var] = obj.id
    for rel_pat, rel in zip(match_clause.rels, rels):
        if rel_pat.var:
            existing = bindings.get(rel_pat.var)
            if existing is not None and existing != rel.id:
                return None
            bindings[rel_pat.var] = rel.id
    return bindings


def _node_matches(obj: "Object", node_pat: NodePat) -> bool:
    if node_pat.type is not None and obj.type != node_pat.type:
        return False
    for k, v in node_pat.properties.items():
        if obj.data.get(k) != v:
            return False
    return True


# ---------- WHERE evaluator -------------------------------------------------


_OPS: dict[str, Callable[[Any, Any], bool]] = {
    "=": lambda a, b: a == b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<>": lambda a, b: a != b,
    ">": lambda a, b: a is not None and b is not None and a > b,
    "<": lambda a, b: a is not None and b is not None and a < b,
    ">=": lambda a, b: a is not None and b is not None and a >= b,
    "<=": lambda a, b: a is not None and b is not None and a <= b,
}


def _eval_where(expr: BoolExpr, bindings: dict[str, str], graph: "Graph") -> bool:
    if isinstance(expr, Comparison):
        left = _resolve_path(expr.left_path, bindings, graph)
        if expr.right_path is not None:
            right = _resolve_path(expr.right_path, bindings, graph)
        else:
            right = expr.right_value
        fn = _OPS.get(expr.op)
        if fn is None:
            # Internal: the parser accepted an operator the evaluator does
            # not recognize. Either the parser drifted from _OPS or the
            # AST was constructed externally. PR-G normalization: uses the
            # shared internal_bug_fields helper for uniform prose.
            from activegraph.errors import internal_bug_fields
            fields = internal_bug_fields(
                summary=f"unknown comparison operator {expr.op!r}",
                what_happened=(
                    f"The WHERE evaluator received a comparison with operator "
                    f"{expr.op!r}, but the operator table has no handler for it."
                ),
                why_invariant=(
                    "The operator table (_OPS in this module) is the source of "
                    "truth for which comparison operators the runtime accepts. "
                    "If the parser produces an operator the evaluator does not "
                    "know about, the audit trail would silently mis-evaluate "
                    "the pattern — refuse instead."
                ),
                location="activegraph/runtime/patterns.py:_eval_where (unknown comparison operator)",
                extra_context={"operator": expr.op},
            )
            raise UnsupportedPatternError(
                fields["summary"],
                what_failed=fields["what_failed"],
                why=fields["why"],
                how_to_fix=fields["how_to_fix"],
                context=fields["context"],
            )
        return fn(left, right)
    if isinstance(expr, NotExpr):
        return not _eval_where(expr.inner, bindings, graph)
    if isinstance(expr, AndExpr):
        return all(_eval_where(p, bindings, graph) for p in expr.parts)
    if isinstance(expr, NotExists):
        # NOT EXISTS { sub_match } — sub_match shares variable bindings
        # with the outer match: any variable used in both refers to the
        # same object id. If any binding of the sub_match exists that's
        # consistent with the outer bindings, NOT EXISTS is False.
        sub_matcher = PatternMatcher(
            Pattern(match=expr.sub_match, where=None, source="<sub>")
        )
        for m in sub_matcher.matches(event=None, graph=graph):
            # Outer bindings constrain sub-match bindings (shared vars).
            consistent = True
            for k, v in bindings.items():
                if k in m.bindings and m.bindings[k] != v:
                    consistent = False
                    break
            if consistent:
                return False
        return True
    # Internal: an AST node the evaluator does not recognize. Should not
    # happen given the parser produces a closed set of node types. PR-G
    # normalization: uses the shared internal_bug_fields helper.
    from activegraph.errors import internal_bug_fields
    _fields = internal_bug_fields(
        summary=f"unrecognized WHERE AST node {type(expr).__name__}",
        what_happened=(
            f"The WHERE evaluator received an AST node of type "
            f"{type(expr).__name__}, but the evaluator only handles "
            f"Comparison, NotExpr, AndExpr, and NotExists."
        ),
        why_invariant=(
            "The AST node set is closed and produced by this module's parser. "
            "An unrecognized node means either the parser drifted from the "
            "evaluator or the AST was constructed externally — both would "
            "produce silent mis-evaluation, so the runtime refuses."
        ),
        location="activegraph/runtime/patterns.py:_eval_where (unrecognized AST node)",
        extra_context={"ast_node_type": type(expr).__name__},
    )
    raise UnsupportedPatternError(
        _fields["summary"],
        what_failed=_fields["what_failed"],
        why=_fields["why"],
        how_to_fix=_fields["how_to_fix"],
        context=_fields["context"],
    )


def _resolve_path(path: list[str], bindings: dict[str, str], graph: "Graph") -> Any:
    """Resolve `a.confidence` etc. against bindings + graph."""
    if not path:
        return None
    head, *rest = path
    obj_id = bindings.get(head)
    if obj_id is None:
        # Could be a relation binding (NOT supported for property access
        # in v0.7 — relations have no .data attribute lookup syntax).
        return None
    obj = graph.get_object(obj_id)
    if obj is None:
        return None
    if not rest:
        # `a` alone → just return the object id; useful for equality.
        return obj.id
    # Walk: a.data.x.y or a.confidence (shorthand for a.data.confidence)
    cur: Any = obj.data
    # Allow `a.type` and `a.id` as direct attribute access.
    first = rest[0]
    if first in ("id", "type", "version"):
        cur = getattr(obj, first, None)
        rest = rest[1:]
    elif first == "data":
        cur = obj.data
        rest = rest[1:]
    # Anything else is treated as `a.<field>` → `a.data.<field>` (the
    # common case: `c.confidence` rather than `c.data.confidence`).
    for p in rest:
        if isinstance(cur, dict):
            cur = cur.get(p)
        else:
            return None
        if cur is None:
            return None
    return cur
