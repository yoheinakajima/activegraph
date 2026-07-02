"""MkDocs build hooks for the Active Graph documentation site.

Registered via the top-level ``hooks:`` key in ``mkdocs.yml``. MkDocs
runs hooks after every plugin, so ``on_post_build`` here fires after
the ``llmstxt`` plugin has written ``llms.txt`` to the site root.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any


def on_post_build(config: Any, **kwargs: Any) -> None:
    """Mirror ``llms.txt`` to ``llms.md`` at the site root.

    ``llms.txt`` is already markdown; the ``.md`` mirror exists because
    AI agents arriving cold (from web search, without reading llms.txt
    first) probe well-known markdown root paths (``/llms.md``,
    ``/agents.md``, ...) and a static GitHub Pages host cannot answer
    ``Accept: text/markdown`` negotiation on the homepage. GitHub Pages
    serves ``.md`` files with ``Content-Type: text/markdown``, so the
    mirror is the static-host answer to markdown content negotiation.

    Fails the build loudly if ``llms.txt`` is missing rather than
    silently shipping a site root without the mirror; the
    ``tests/test_llms_txt.py`` gate asserts the mirror exists and
    matches, but the build is the earlier, cheaper failure point.
    """
    site_dir = Path(config["site_dir"])
    source = site_dir / "llms.txt"
    if not source.is_file():
        raise FileNotFoundError(
            f"{source} missing at post-build; the llmstxt plugin did not "
            "run (check the `plugins:` block in mkdocs.yml)."
        )
    shutil.copyfile(source, site_dir / "llms.md")
