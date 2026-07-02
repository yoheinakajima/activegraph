"""Broken-link CI precursor. v1.0 doc-site phase.

Extracts every URL appearing in error message snapshots, CONTRACT
cross-references, and README links. Maps each to the doc-site source
file it would resolve to under the locked docs/ structure (CONTRACT
v1.0 #5). Reports missing pages as test failures so the doc-site
phase has a measurable burndown — each page that lands turns a red
check green.

The link checker becomes the progress meter for the doc-site phase.
Initial state: every URL targeting docs.activegraph.dev /
yoheinakajima.github.io fails loud because no doc pages exist yet.

This file deliberately does NOT depend on a built mkdocs site. It
checks the source paths (e.g. ``docs/reference/errors/<slug>.md``)
because those are what mkdocs renders. When mkdocs is set up later,
a separate check can verify the rendered URLs resolve over HTTP; for
now, the source-path check is the necessary precondition.

Also includes an orphan check that reports doc pages no error
message, CONTRACT section, or README link references. Orphans are
emitted as warnings (printed during the test run) rather than test
failures — some pages are landing pages reached only by navigation,
and failing CI on them would be too strict.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from activegraph.errors import DOCS_BASE_URL


REPO_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOTS_DIR = REPO_ROOT / "tests" / "snapshots" / "errors"
DOCS_DIR = REPO_ROOT / "docs"
CONTRACT_PATH = REPO_ROOT / "CONTRACT.md"
README_PATH = REPO_ROOT / "README.md"


# Any of three base URLs maps to the same docs/ source tree:
#   - DOCS_BASE_URL (the current primary, docs.activegraph.ai per
#     CONTRACT v1.0 #C6 v1.0-rc3 amendment)
#   - docs.activegraph.dev (the previous primary; user-owned redirect
#     to .ai is a separate operational step, but historical CHANGELOG
#     entries and old user-facing links still point here and must
#     still pass the source-presence check)
#   - github.io fallback (the pre-DNS fallback shape from the
#     original CONTRACT v1.0 #C6, kept recognized so any legacy
#     reference still resolves)
# This checker is source-tree-scoped, not HTTP-scoped — the v1.1 #9
# deploy-verification gate covers live URL reachability.
_KNOWN_DOCS_BASES = (
    DOCS_BASE_URL,
    "https://docs.activegraph.ai",
    "https://docs.activegraph.dev",
    "https://yoheinakajima.github.io/activegraph",
)

_DOCS_URL_RE = re.compile(
    r"https?://(?:docs\.activegraph\.(?:ai|dev)|yoheinakajima\.github\.io/activegraph)"
    r"(/[^\s)\"'>`]*)?"
)


# URLs that are generated at build time and do NOT correspond to a
# `docs/*.md` source file. These bypass the source-presence check
# because their existence is verified by a dedicated build-time gate
# rather than by source-tree presence.
#
# `/llms.txt` and `/llms-full.txt` (CONTRACT v1.0.5 #1) are emitted by
# the `mkdocs-llmstxt` plugin during `mkdocs build`; `/llms.md` is the
# markdown mirror of `/llms.txt` copied by the post-build hook in
# `scripts/mkdocs_hooks.py`. Their existence and well-formedness are
# verified by `tests/test_llms_txt.py`. Any URL added here must have a
# paired build-time test asserting the file lands at the site root.
_BUILD_GENERATED_URLS = frozenset({
    "/llms.txt",
    "/llms-full.txt",
    "/llms.md",
})


def _strip_trailing_punct(s: str) -> str:
    """URLs in prose often end at sentence boundaries; trim trailing
    punctuation that isn't part of the URL."""
    return s.rstrip(".,:;!?`")


def _url_to_source_path(url_path: str) -> Path | None:
    """Map a docs URL path to its mkdocs source file.

    `/errors/<slug>` -> `docs/reference/errors/<slug>.md`
    `/concepts/<page>` -> `docs/concepts/<page>.md`
    `/guides/<page>` -> `docs/guides/<page>.md`
    `/cookbook/<page>` -> `docs/cookbook/<page>.md`
    `/about/<page>` -> `docs/about/<page>.md`
    `/packs/<page>` -> `docs/packs/<page>.md`
    `/reference/<path>` -> `docs/reference/<path>.md`
    `/` (or empty) -> `docs/index.md`

    Returns None if the URL doesn't match a known docs section.
    """
    p = url_path.strip("/")
    if not p:
        return DOCS_DIR / "index.md"
    parts = p.split("/")
    section = parts[0]
    rest = parts[1:]
    if section == "errors":
        # error-class slug URLs always render under reference/errors/
        # (CONTRACT v1.0 #5: reference/errors/ is the canonical home)
        return DOCS_DIR / "reference" / "errors" / f"{'/'.join(rest)}.md"
    if section in ("concepts", "guides", "cookbook", "about", "packs"):
        return DOCS_DIR / section / f"{'/'.join(rest)}.md"
    if section == "reference":
        return DOCS_DIR / "reference" / f"{'/'.join(rest)}.md"
    if section == "quickstart":
        return DOCS_DIR / "quickstart.md"
    return None


def _collect_urls_from(path: Path) -> set[str]:
    """Extract every docs-site URL from a file, returning the URL path
    portion (after the base) without leading slash.

    URL paths containing placeholders like ``<slug>`` or
    ``<error-class-slug>`` are skipped — those appear in format-spec
    documentation prose, not as real URLs to verify.
    """
    text = path.read_text()
    out: set[str] = set()
    for m in _DOCS_URL_RE.finditer(text):
        url_path = _strip_trailing_punct(m.group(1) or "")
        if "<" in url_path:
            continue
        out.add(url_path)
    return out


def _collect_all_referenced_urls() -> dict[str, list[Path]]:
    """Walk snapshots + CONTRACT + README; return a dict from
    URL path -> list of source files that reference it."""
    referenced: dict[str, list[Path]] = {}
    sources = []
    if SNAPSHOTS_DIR.exists():
        sources.extend(sorted(SNAPSHOTS_DIR.glob("*.txt")))
    if CONTRACT_PATH.exists():
        sources.append(CONTRACT_PATH)
    if README_PATH.exists():
        sources.append(README_PATH)
    # Also walk the docs/ dir itself for inter-page links.
    if DOCS_DIR.exists():
        sources.extend(sorted(DOCS_DIR.rglob("*.md")))
    for src in sources:
        for url_path in _collect_urls_from(src):
            referenced.setdefault(url_path, []).append(src)
    return referenced


def _format_missing_report(missing: dict[Path, list[tuple[str, list[Path]]]]) -> str:
    """Format a human-readable report of missing doc pages."""
    if not missing:
        return ""
    lines = [
        f"{len(missing)} doc page(s) referenced but not present in docs/:",
        "",
    ]
    for expected_path, refs in sorted(missing.items()):
        rel = expected_path.relative_to(REPO_ROOT)
        lines.append(f"  MISSING: {rel}")
        for url_path, src_files in refs:
            for src in src_files:
                src_rel = src.relative_to(REPO_ROOT)
                lines.append(f"    referenced as {url_path!r} in {src_rel}")
        lines.append("")
    lines.append(
        "Each missing page is a doc-site phase deliverable. As pages land "
        "under docs/, this report shrinks. When empty, every error message "
        "URL and CONTRACT cross-reference resolves to a real page."
    )
    return "\n".join(lines)


def test_every_referenced_docs_url_has_a_source_page() -> None:
    """The broken-link CI gate. Every URL appearing in error message
    snapshots, CONTRACT cross-references, README links, or other doc
    pages must point at a real `docs/<section>/<page>.md` file.

    Initial state when this test lands: ~27 missing pages (one per
    error class slug + concepts/failure-model + concepts/patterns).
    Each doc-site phase PR turns red checks green. When the test
    passes, the doc site is complete enough to deploy.
    """
    referenced = _collect_all_referenced_urls()
    missing: dict[Path, list[tuple[str, list[Path]]]] = {}
    unmapped: list[tuple[str, list[Path]]] = []
    for url_path, sources in referenced.items():
        if url_path in _BUILD_GENERATED_URLS:
            # Existence guaranteed by tests/test_llms_txt.py at build
            # time; no docs/*.md source to check.
            continue
        expected = _url_to_source_path(url_path)
        if expected is None:
            unmapped.append((url_path, sources))
            continue
        if not expected.exists():
            missing.setdefault(expected, []).append((url_path, sources))

    report_lines: list[str] = []
    if unmapped:
        report_lines.append(
            f"{len(unmapped)} URL(s) did not map to a known docs section. "
            f"Either the section is new (add it to _url_to_source_path) or "
            f"the URL is a typo:"
        )
        for url_path, sources in sorted(unmapped):
            for src in sources:
                report_lines.append(
                    f"  {url_path!r} referenced in {src.relative_to(REPO_ROOT)}"
                )
        report_lines.append("")
    if missing:
        report_lines.append(_format_missing_report(missing))

    if report_lines:
        pytest.fail("\n" + "\n".join(report_lines), pytrace=False)


def test_docs_orphans_are_reported_as_warnings(capsys) -> None:
    """Walk `docs/` and report any pages that no error message,
    CONTRACT section, README link, or other doc page references.

    Orphans are reported as warnings (printed to stdout) rather than
    test failures — some pages are landing pages reached only by
    navigation, and failing CI on them would be too strict. The
    maintainer sees the warning during CI runs and decides whether
    each orphan is intentional or doc-rot.
    """
    if not DOCS_DIR.exists():
        # No docs/ tree yet — this test is a no-op until mkdocs lands.
        return

    # Build set of referenced source paths.
    referenced = _collect_all_referenced_urls()
    referenced_paths: set[Path] = set()
    for url_path in referenced:
        expected = _url_to_source_path(url_path)
        if expected is not None:
            referenced_paths.add(expected.resolve())

    # Discover all doc pages, excluding pre-v1.0 placeholders that the
    # doc-site phase will replace (operating.md, pack_authoring.md).
    legacy = {
        (DOCS_DIR / "operating.md").resolve(),
        (DOCS_DIR / "pack_authoring.md").resolve(),
    }
    orphans: list[Path] = []
    for page in sorted(DOCS_DIR.rglob("*.md")):
        resolved = page.resolve()
        if resolved in legacy:
            continue
        if resolved not in referenced_paths:
            orphans.append(page)

    if orphans:
        print(
            f"\n[doc-orphan warning] {len(orphans)} doc page(s) are not "
            f"referenced by any error message, CONTRACT section, README "
            f"link, or other doc page:"
        )
        for page in orphans:
            print(f"  {page.relative_to(REPO_ROOT)}")
        print(
            "Orphans are not test failures (some are landing pages reached "
            "only by navigation). Review to confirm each is intentional."
        )


def test_docs_base_url_is_centralized() -> None:
    """Every error message that includes a doc-site URL constructs it
    from the DOCS_BASE_URL constant — not hardcoded. Catches drift
    where a future PR pastes the URL directly.

    Scans the activegraph package for hardcoded ``https://...docs...``
    strings outside the canonical constant declaration and any
    documentation prose that shows the URL pattern as an example
    (marked with ``<slug>``-style placeholders).
    """
    package_root = REPO_ROOT / "activegraph"
    hardcoded: list[tuple[Path, int, str]] = []
    for py in sorted(package_root.rglob("*.py")):
        for lineno, line in enumerate(py.read_text().splitlines(), start=1):
            # The canonical declaration is allowed.
            if "DOCS_BASE_URL =" in line:
                continue
            # Documentation prose showing the URL pattern with a
            # placeholder is not a real URL, it's a template.
            if "<slug>" in line or "<path>" in line or "<page>" in line:
                continue
            for base in _KNOWN_DOCS_BASES:
                if base in line:
                    hardcoded.append((py.relative_to(REPO_ROOT), lineno, line.strip()))
                    break
    if hardcoded:
        report = "\n".join(
            f"  {path}:{lineno}: {line}" for path, lineno, line in hardcoded
        )
        pytest.fail(
            "\n" + (
                f"{len(hardcoded)} hardcoded docs-site URL(s) in activegraph/. "
                f"Use `from activegraph.errors import DOCS_BASE_URL` and "
                f"interpolate via f-string so the cutover to "
                f"docs.activegraph.ai is a one-line change.\n"
                + report
            ),
            pytrace=False,
        )
