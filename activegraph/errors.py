"""ActiveGraphError hierarchy. CONTRACT v1.0 #3, #4 — the error format and
the class tree are public contract.

Every framework error inherits from `ActiveGraphError` and renders in the
locked format:

    <ErrorClass>: <one-line summary>

    What failed:
      <specific thing that went wrong, with names>

    Why:
      <root cause, not the symptom>

    How to fix:
      <concrete action>

    More:
      https://docs.activegraph.ai/errors/<slug>

The seven category bases are stable. Concrete leaves are migrated under
their categories one PR at a time (CONTRACT v1.0 #C1 — the rewrite ships
as a PR series, not one PR). PR-A lands the foundation plus ReplayError
as the reference category; subsequent PRs migrate the other categories
without changing the bases.

`_doc_slug` on each class is the URL slug for the error's doc page. The
base URL is the primary `docs.activegraph.ai` domain (CONTRACT v1.0 #C6,
v1.0-rc3 amendment); the constant is the single swap point.
"""

from __future__ import annotations

from typing import Any, ClassVar


# CONTRACT v1.0 #C6 (v1.0-rc3 amendment): primary doc-site domain is
# docs.activegraph.ai. Until the user-owned Pages enablement and DNS
# land, the URL renders the same 404 the rc2 user-test surfaced; the
# v1.1 #9 deploy-verification gate fails until that lands, which is
# the correct signal. One-line swap point: update this constant and
# every error message URL and `DOCS_BASE_URL`-derived f-string follows.
DOCS_BASE_URL = "https://docs.activegraph.ai"


def _indent_continuation(text: str, indent: str = "  ") -> str:
    """Re-indent a multi-line block so every line after the first sits
    under the same column as the first line. The format spec puts every
    field's body on a continuation line indented by two spaces; multi-line
    bodies (a how-to-fix with three steps, for instance) maintain that
    indent on every line so the message reads as a single block."""
    lines = text.split("\n")
    if len(lines) == 1:
        return lines[0]
    return lines[0] + "\n" + "\n".join(indent + line if line else line for line in lines[1:])


class ActiveGraphError(Exception):
    """Root of every framework error. CONTRACT v1.0 #4.

    Subclasses construct by passing a one-line ``summary`` plus the three
    structured fields (``what_failed``, ``why``, ``how_to_fix``) and any
    error-specific context. ``__str__`` produces the locked format; the
    structured fields stay accessible programmatically for tools that
    want to render errors differently (a doc-site error catalog, a
    machine-readable failure log, etc.).
    """

    # Each subclass sets its own slug. The base class slug exists so a
    # bare `ActiveGraphError` (which should not be raised in practice;
    # always reach for a concrete subclass) still produces a valid URL.
    _doc_slug: ClassVar[str] = "active-graph-error"

    def __init__(
        self,
        summary_or_message: str,
        *,
        what_failed: str | None = None,
        why: str | None = None,
        how_to_fix: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Two construction modes during the v1.0 transition:

        - **Structured** (the v1.0 target): pass ``summary`` plus the three
          named fields. ``__str__`` produces the locked format.
        - **Legacy**: pass a single positional message. Used by error
          leaves that have not yet migrated under the v1.0 PR series.
          ``__str__`` returns the message verbatim — format-noncompliant
          but valid Python, so existing raises keep working.

        PR-A converts the ReplayError leaves (the reference category).
        PR-B through PR-F convert the rest one PR at a time. The legacy
        branch goes away once every leaf is migrated; until then this
        gateway is the bridge.
        """
        self._summary = summary_or_message
        self.what_failed = what_failed or ""
        self.why = why or ""
        self.how_to_fix = how_to_fix or ""
        self.context: dict[str, Any] = dict(context) if context else {}
        if self.is_structured():
            super().__init__(self._format())
        else:
            super().__init__(summary_or_message)

    def is_structured(self) -> bool:
        """True when the three structured fields are populated. Used by
        the format snapshot tests and the docs catalog to filter out
        leaves that have not yet migrated to the v1.0 format."""
        return bool(self.what_failed and self.why and self.how_to_fix)

    @property
    def doc_url(self) -> str:
        return f"{DOCS_BASE_URL}/errors/{self._doc_slug}"

    def _format(self) -> str:
        return (
            f"{type(self).__name__}: {self._summary}\n\n"
            f"What failed:\n  {_indent_continuation(self.what_failed)}\n\n"
            f"Why:\n  {_indent_continuation(self.why)}\n\n"
            f"How to fix:\n  {_indent_continuation(self.how_to_fix)}\n\n"
            f"More:\n  {self.doc_url}"
        )

    def __str__(self) -> str:
        if self.is_structured():
            return self._format()
        return self._summary


# ---------- Category bases ----------------------------------------------
#
# The seven categories from CONTRACT v1.0 #4. Each is an abstract base —
# raise a concrete subclass, not a bare category. Until PR-B through PR-F
# land, some categories have no concrete leaves yet; the bases still
# exist so external code can `except RegistrationError:` today and have
# it cover those leaves once they migrate.


class ConfigurationError(ActiveGraphError):
    """Runtime construction problems: invalid budget, malformed store URL,
    missing required configuration.

    Fires before any work runs — configuration failures are
    exceptions at the entry point, never ``behavior.failed`` events,
    because there is no run yet to record them in (CONTRACT v1.0
    #4b's routing rule).
    """

    _doc_slug = "configuration-error"


class RegistrationError(ActiveGraphError):
    """Behavior, tool, or pack registration problems: conflicts at
    registration time, version mismatches, missing providers, unknown
    tools. Fires at registration / pack-load time."""

    _doc_slug = "registration-error"


class ExecutionError(ActiveGraphError):
    """Runtime execution problems: behavior failures, budget exhausted,
    tool failures during a goal run. Named ExecutionError (not
    RuntimeError) because Python already has builtin ``RuntimeError`` and
    shadowing the builtin produces confusing stack traces."""

    _doc_slug = "execution-error"


class ReplayError(ActiveGraphError):
    """Replay and fork problems: cache hash mismatches, type-stream
    divergence between recorded and re-run event logs. Fires only during
    replay / fork; never during a fresh run."""

    _doc_slug = "replay-error"


class StorageError(ActiveGraphError):
    """Persistence problems: failed writes, malformed event payloads on
    deserialize, schema version mismatches.

    The category base for the store seam — concrete subclasses
    (``NonSerializableEventError``, ``CorruptedEventPayloadError``,
    ``SchemaVersionMismatch``, ``InvalidStoreURL``) say which
    invariant broke. Storage failures raise rather than emit: a store
    that can't be trusted can't record its own failure.
    """

    _doc_slug = "storage-error"


class PatternError(ActiveGraphError):
    """Pattern subscription problems: invalid Cypher syntax, unsupported
    features, malformed WHERE clauses.

    Raised at registration time, when ``pattern=`` strings compile —
    a bad pattern never reaches dispatch. ``UnsupportedPatternError``
    is the subclass for syntactically valid Cypher that uses features
    outside the supported subset (CONTRACT v0.7 #8).
    """

    _doc_slug = "pattern-error"


class PackError(ActiveGraphError):
    """Pack-specific problems at runtime (not registration): schema
    violations on add_object after pack load, pack-state inconsistencies.
    Registration-time pack errors live under :class:`RegistrationError`
    instead."""

    _doc_slug = "pack-error"


class MissingOptionalDependency(RegistrationError, ImportError):
    """A subsystem requires an optional Python package that isn't installed.

    Used by the Postgres store, the Prometheus metrics backend, and the
    Pack format (which requires Pydantic). Multi-inherits :class:`ImportError`
    so user code that catches the builtin around optional-dep imports
    continues to work. v1.0 PR-E.

    Construct with the missing package name and the activegraph extras
    name that bundles it; the structured message walks the user through
    the install line.
    """

    _doc_slug = "missing-optional-dependency"

    def __init__(
        self,
        *,
        package: str,
        feature: str,
        extras: str | None = None,
    ) -> None:
        self.package = package
        self.feature = feature
        self.extras = extras
        install_line = (
            f"pip install 'activegraph[{extras}]'"
            if extras
            else f"pip install {package}"
        )
        ctx = {"package": package, "feature": feature}
        if extras is not None:
            ctx["extras"] = extras
        RegistrationError.__init__(
            self,
            f"{feature} requires the {package!r} Python package",
            what_failed=(
                f"While initializing {feature}, the import of {package!r} "
                f"failed because the package is not installed in this "
                f"environment."
            ),
            why=(
                "The framework keeps optional subsystems off the default "
                "install path so a minimal install stays small. Each "
                "optional subsystem declares its dependency explicitly; "
                "missing it produces this error rather than failing later "
                "with a confusing AttributeError or ImportError deep inside "
                "the subsystem."
            ),
            how_to_fix=(
                f"Install the optional dependency:\n"
                f"    {install_line}\n"
                f"\n"
                f"If you don't need {feature}, the bare `activegraph` "
                f"install does not depend on {package!r} — the error only "
                f"fires when the subsystem is actually used."
            ),
            context=ctx,
        )


__all__ = [
    "ActiveGraphError",
    "ConfigurationError",
    "RegistrationError",
    "ExecutionError",
    "ReplayError",
    "StorageError",
    "PatternError",
    "PackError",
    "MissingOptionalDependency",
    "internal_bug_fields",
    "GITHUB_NEW_ISSUE_URL",
    "DOCS_BASE_URL",
]


# ---------- v1.0 PR-G: shared internal-bug helper -----------------------
#
# Three sites in the framework raise exceptions for framework-bug
# conditions (the parser/evaluator sees something the framework itself
# produced incorrectly, not user input). Per PR-G consistency pass, all
# three use the same context-dict shape and the same recovery prose so
# GitHub Issues filed from these errors arrive with uniform metadata.


GITHUB_NEW_ISSUE_URL = "https://github.com/yoheinakajima/activegraph/issues/new"


def internal_bug_fields(
    *,
    summary: str,
    what_happened: str,
    why_invariant: str,
    location: str,
    extra_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Produce uniform structured fields for an internal-bug exception.

    Used by the three framework-bug raise sites (two in
    ``activegraph/runtime/patterns.py`` for the WHERE evaluator's
    unknown-operator and unrecognized-AST-node cases; one in
    ``activegraph/core/graph.py`` for the view-filter evaluator's
    unknown-operator case). Returns the kwargs dict that an
    :class:`ActiveGraphError` subclass's structured ``__init__``
    consumes.

    Uniform context dict shape:

    - ``internal``: ``True``
    - ``framework_version``: ``activegraph.__version__``
    - ``internal_error_location``: module:function pointer for triage
    - ``report_url``: the GitHub new-issue URL
    - any per-site keys from ``extra_context``

    Uniform recovery prose:

      "This is a framework bug, not a problem with your code. Please
      file an issue at <URL> with the framework version and the message
      above."

    PR-G normalization pass: the three pre-existing internal-bug
    messages had drifted into three slightly different shapes; they
    are unified here. Future internal-bug raises should call this
    helper so the pattern stays uniform.
    """
    from activegraph import __version__ as _aw_version
    ctx: dict[str, Any] = {
        "internal": True,
        "framework_version": _aw_version,
        "internal_error_location": location,
        "report_url": GITHUB_NEW_ISSUE_URL,
    }
    if extra_context:
        ctx.update(extra_context)
    return {
        "summary": summary,
        "what_failed": what_happened,
        "why": why_invariant,
        "how_to_fix": (
            "This is a framework bug, not a problem with your code.\n"
            "Please file an issue and include the framework version, the\n"
            "internal error location, and the full message above:\n"
            f"    {GITHUB_NEW_ISSUE_URL}\n"
            "\n"
            f"  framework version:   activegraph {_aw_version}\n"
            f"  internal location:   {location}"
        ),
        "context": ctx,
    }
