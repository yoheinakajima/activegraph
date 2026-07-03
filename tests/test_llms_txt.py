"""llms.txt / llms-full.txt generation gate. CONTRACT v1.0.5 #1.

Builds the mkdocs site into a temp directory and asserts that
``site/llms.txt`` and ``site/llms-full.txt`` exist after the build,
are well-formed (H1, blockquote summary, at least one H2 section),
and reference the nav-anchor pages named in CONTRACT v1.0.5 #1's
contract claim. The same build also gates the other agent-discovery
files at the site root: the ``llms.md`` markdown mirror (produced by
``scripts/mkdocs_hooks.py``) and ``robots.txt`` (copied verbatim from
``docs/robots.txt``). The "content coverage" assertion is the boundary
guard against the failure mode where the build succeeds but emits
an llms.txt that contains only the H1 — file existence alone is
not sufficient.

Standing Rule §2 shape: the test anchors on the contract boundary
the v1.0.5 #1 amendment names ("both files exist after build and
are well-formed"), not on the implementation path of the
`mkdocs-llmstxt` plugin. If a future release swaps the plugin for
a custom build script or a different plugin, this test stays
correct without modification.

Marked ``slow`` because ``mkdocs build`` takes several seconds.
Local ``pytest`` skips it by default; the ``.github/workflows/docs.yml``
workflow runs it explicitly via ``pytest -m slow tests/test_llms_txt.py``.
The precedent is ``tests/test_doc_site_reachable.py`` (CONTRACT v1.1
#9), which uses the same marker for the same reason.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
MKDOCS_CONFIG = REPO_ROOT / "mkdocs.yml"


@pytest.fixture(scope="module")
def built_site(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Run ``mkdocs build`` into a temp dir; return the site/ path.

    Module-scoped: the build is shared across every assertion in
    this file. The fixture skips the entire module if ``mkdocs`` is
    not installed — keeps the gate honest about its hard dependency
    on the docs-build environment without polluting unrelated test
    runs.
    """
    if shutil.which("mkdocs") is None:
        pytest.skip("mkdocs binary not available; install '.[docs]' to run")

    site_dir = tmp_path_factory.mktemp("llms_txt_gate") / "site"
    result = subprocess.run(
        [
            "mkdocs",
            "build",
            "--config-file",
            str(MKDOCS_CONFIG),
            "--site-dir",
            str(site_dir),
            "--clean",
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    if result.returncode != 0:
        # Surface the actual mkdocs error so a failing gate is
        # diagnosable from the CI log — not just "build failed."
        pytest.fail(
            "mkdocs build failed:\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return site_dir


@pytest.mark.slow
def test_llms_txt_exists(built_site: Path) -> None:
    """Contract: ``/llms.txt`` is generated at site root after build."""
    llms = built_site / "llms.txt"
    assert llms.is_file(), (
        f"site/llms.txt missing after mkdocs build (expected at {llms}). "
        "Check the `llmstxt` plugin block in mkdocs.yml."
    )


@pytest.mark.slow
def test_llms_full_txt_exists(built_site: Path) -> None:
    """Contract: ``/llms-full.txt`` is generated at site root after build."""
    llms_full = built_site / "llms-full.txt"
    assert llms_full.is_file(), (
        f"site/llms-full.txt missing after mkdocs build (expected at {llms_full}). "
        "Check the `full_output:` key in the `llmstxt` plugin block."
    )


@pytest.mark.slow
def test_llms_txt_h1_and_blockquote(built_site: Path) -> None:
    """Contract: ``llms.txt`` starts with the H1 and a blockquote summary.

    The plugin derives the H1 from ``site_name`` and the blockquote
    from ``site_description``. The assertion shape catches both the
    plugin-not-running case and the case where ``mkdocs.yml`` loses
    one of those keys (which would break the llms.txt convention
    described at llmstxt.org).
    """
    content = (built_site / "llms.txt").read_text(encoding="utf-8")
    lines = content.splitlines()

    assert lines[0] == "# Active Graph", (
        f"llms.txt does not start with the expected H1; got: {lines[0]!r}"
    )
    # The blockquote summary line — first non-empty line after the H1.
    blockquote = next((ln for ln in lines[1:] if ln.strip()), "")
    assert blockquote.startswith("> "), (
        f"llms.txt does not carry a blockquote summary; got: {blockquote!r}"
    )
    assert len(blockquote) > len("> "), (
        "llms.txt blockquote summary is empty after the '> ' marker"
    )


@pytest.mark.slow
def test_llms_txt_has_section_headers(built_site: Path) -> None:
    """Contract: ``llms.txt`` carries H2 sections (navigation aid shape)."""
    content = (built_site / "llms.txt").read_text(encoding="utf-8")
    h2_count = sum(1 for ln in content.splitlines() if ln.startswith("## "))
    assert h2_count >= 4, (
        f"llms.txt has {h2_count} H2 section headers; expected at least 4 "
        "(Quickstart / Concepts / Cookbook / Reference at minimum). "
        "A file with the H1 but no sections is not useful as a nav aid."
    )


@pytest.mark.slow
def test_llms_txt_references_nav_anchor_pages(built_site: Path) -> None:
    """Contract: ``llms.txt`` references the nav-anchor pages by name.

    The v1.0.5 #1 amendment names three anchors for the coverage
    check: ``concepts/graph.md``, ``quickstart.md``, and at least
    one cookbook page. These are the load-bearing entry points for
    a first-time AI reader walking the index; if any one is
    missing, the file fails to deliver on its navigation-aid
    purpose.
    """
    content = (built_site / "llms.txt").read_text(encoding="utf-8")

    assert "concepts/graph/" in content, (
        "llms.txt does not reference docs/concepts/graph.md — the "
        "primary entry point for the framework's primitive model."
    )
    assert "quickstart/" in content, (
        "llms.txt does not reference docs/quickstart.md — the "
        "ten-minute tutorial that the README points new users at."
    )
    assert "cookbook/" in content, (
        "llms.txt does not reference any cookbook page — at least "
        "one of common-patterns.md / debugging.md / multi-run-scripts.md "
        "is expected."
    )


@pytest.mark.slow
def test_llms_full_txt_well_formed(built_site: Path) -> None:
    """Contract: ``llms-full.txt`` carries the H1 + actual page bodies.

    Asserts on a distinctive prose marker from ``docs/quickstart.md``
    so a file that contains the H1 and blockquote but no concatenated
    bodies fails. The marker is the tutorial's opening sentence,
    voice-locked since v1.0-rc1 and unlikely to churn.
    """
    content = (built_site / "llms-full.txt").read_text(encoding="utf-8")

    assert content.startswith("# Active Graph"), (
        "llms-full.txt does not start with the expected H1"
    )
    # Distinctive phrase from docs/quickstart.md's first paragraph —
    # if llms-full.txt contains it, the quickstart body made it
    # into the concatenated output.
    assert "Ten minutes from install to a working custom behavior" in content, (
        "llms-full.txt does not contain the quickstart body marker; "
        "the file may be the index-only output instead of the "
        "concatenated full content."
    )


@pytest.mark.slow
def test_llms_md_mirrors_llms_txt(built_site: Path) -> None:
    """Contract: ``/llms.md`` is a byte-for-byte mirror of ``/llms.txt``.

    The mirror is the cold-discovery path for AI agents that probe
    well-known markdown root paths (``/llms.md``, ``/agents.md``, ...)
    instead of reading llms.txt first — GitHub Pages serves ``.md``
    as ``text/markdown`` but cannot answer Accept-header negotiation
    on the homepage. Asserting byte equality (not just existence)
    catches the failure mode where the hook copies a stale or empty
    file.
    """
    llms_md = built_site / "llms.md"
    assert llms_md.is_file(), (
        f"site/llms.md missing after mkdocs build (expected at {llms_md}). "
        "Check the `hooks:` block in mkdocs.yml and scripts/mkdocs_hooks.py."
    )
    assert llms_md.read_bytes() == (built_site / "llms.txt").read_bytes(), (
        "site/llms.md diverges from site/llms.txt; the post-build hook "
        "should produce an exact mirror."
    )


@pytest.mark.slow
def test_robots_txt_present_and_points_at_sitemap(built_site: Path) -> None:
    """Contract: ``/robots.txt`` ships at the site root with a Sitemap line.

    ``docs/robots.txt`` is a non-markdown file, so mkdocs copies it
    verbatim. The Sitemap directive must carry the absolute canonical
    URL — crawlers ignore relative sitemap references.
    """
    robots = built_site / "robots.txt"
    assert robots.is_file(), (
        f"site/robots.txt missing after mkdocs build (expected at {robots}). "
        "Check that docs/robots.txt exists — mkdocs copies it verbatim."
    )
    content = robots.read_text(encoding="utf-8")
    assert "User-agent: *" in content, (
        "robots.txt does not declare a wildcard User-agent block"
    )
    assert "Sitemap: https://docs.activegraph.ai/sitemap.xml" in content, (
        "robots.txt does not point at the canonical sitemap URL"
    )
