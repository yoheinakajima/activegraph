"""Version-sync CI gate. CONTRACT v1.0 PR-C follow-on.

Stale ``__version__`` constants produce confusing GitHub Issues six
months later when a bug reported "in 0.9.0" was actually 0.9.1. This
test asserts the runtime constant and the packaging metadata agree.
"""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path

import activegraph


_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def test_runtime_version_matches_pyproject() -> None:
    with _PYPROJECT.open("rb") as f:
        data = tomllib.load(f)
    pyproject_version = data["project"]["version"]
    assert activegraph.__version__ == pyproject_version, (
        f"activegraph.__version__ = {activegraph.__version__!r} but "
        f"pyproject.toml version = {pyproject_version!r}. "
        f"Bump one to match the other before merging — every error "
        f"message that embeds the version (see PR-B internal-error "
        f"contexts, PR-C SchemaVersionMismatch) reads activegraph."
        f"__version__, so a drift produces wrong-version error reports."
    )


def test_tagged_release_version_matches_runtime_version() -> None:
    """Tagged CI should publish the version named by the tag.

    Branch builds intentionally skip this check; feature branches often have
    no release tag reachable. GitHub Actions exposes tag builds via
    GITHUB_REF_TYPE/GITHUB_REF_NAME or refs/tags/... in GITHUB_REF.
    """
    tag = _ci_release_tag(os.environ)
    if tag is None:
        return
    assert _normalise_release_version(tag) == _normalise_release_version(
        activegraph.__version__
    ), (
        f"release tag {tag!r} does not match activegraph.__version__ "
        f"{activegraph.__version__!r}. Bump the package version or move "
        f"the tag before publishing."
    )


def test_release_tag_detection_from_github_ref_name() -> None:
    env = {"GITHUB_REF_TYPE": "tag", "GITHUB_REF_NAME": "v1.2.3"}
    assert _ci_release_tag(env) == "v1.2.3"


def test_release_tag_detection_from_github_ref() -> None:
    env = {"GITHUB_REF": "refs/tags/1.2.3+local.7"}
    assert _ci_release_tag(env) == "1.2.3+local.7"


def test_release_version_normalisation() -> None:
    assert _normalise_release_version("v1.2.3+local.7") == "1.2.3"


def _ci_release_tag(env: os._Environ | dict[str, str]) -> str | None:
    if env.get("GITHUB_REF_TYPE") == "tag":
        tag = env.get("GITHUB_REF_NAME")
        if tag:
            return tag
    ref = env.get("GITHUB_REF", "")
    prefix = "refs/tags/"
    if ref.startswith(prefix):
        return ref[len(prefix):]
    return None


def _normalise_release_version(value: str) -> str:
    version = value.strip()
    if re.match(r"^[vV]\d", version):
        version = version[1:]
    return version.split("+", 1)[0]
