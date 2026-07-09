"""Pack manifest schema, validation, and content hashing. PROVISIONAL.

CONTRACT v1.4 #1 (provisional). Reference implementation of the pack
manifest spec (activegraph-packs `docs/manifest-spec.md`) so the
consumers that recompute the same checks — this loader, downstream CI,
and the evolution pack's static gates — share one implementation
instead of drifting.

**PROVISIONAL API.** Expect one round of breaking edits to this module
before the API is contract-stable. Since v1.6, ``load_pack`` runs
these checks itself as a WARNING tier (CONTRACT v1.6 #1): when a
``manifest.toml`` is discoverable at the pack root, the loader runs
:func:`load_manifest` + :func:`verify_surface` and logs any violations
once per pack — it never raises before 2.0, and a pack without a
manifest still loads exactly as before. You may also call these
functions directly (host tooling, CI, and the trial sandbox's pin
chain all do).

Entry points:

  * :func:`load_manifest` — parse ``manifest.toml`` and validate the
    schema. All violations are collected and raised together in one
    :class:`PackManifestError` (nothing partial, matching
    ``load_pack``'s pre-mutation posture).
  * :func:`verify_surface` — the loader-verifiable two-way check
    between a manifest's ``[surface]`` and a live :class:`Pack`
    object, per the spec's identity mapping. Since ``Pack`` grew a
    declarative ``capabilities`` field (spec Q8), declared
    ``capabilities`` ARE checked here too — two-way by
    ``(provider, capability)`` with ``risk_class`` agreement (a
    relabeled risk class is exactly the swap the decision surface must
    catch). Only ``consumes`` stays out of scope, being imperative
    gateway wiring the loader cannot observe.
  * :func:`compute_content_hash` / :func:`verify_content_hash` — the
    spec §4 canonical byte stream over the pack directory EXCLUDING
    ``manifest.toml``, byte-exact, with this implementation's
    amendments (also flagged in the spec review): directory symlinks
    are rejected like file symlinks; paths that are not
    UTF-8-encodable or not NFC-normalized are rejected loudly rather
    than hashed ambiguously.
  * :func:`compute_bundle_hash` / :func:`verify_bundle_hash` — the
    same §4 walk but WITH ``manifest.toml`` included. This is the
    external pin recorded in a proposal and verified before a
    candidate is trialed or adopted, since the manifest is part of
    what the owner approves.
"""

from __future__ import annotations

import hashlib
import re
import tomllib
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from activegraph.errors import PackError


_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
# PEP 440 core grammar (syntactic check only; semantic range
# resolution is load-time enforcement, not this cycle).
_VERSION_RE = re.compile(
    r"^v?\d+(\.\d+)*((a|b|rc)\d+)?(\.post\d+)?(\.dev\d+)?"
    r"(\+[a-z0-9]+(\.[a-z0-9]+)*)?$"
)
_SPECIFIER_RE = re.compile(
    r"^\s*(~=|==|!=|<=|>=|<|>|===)\s*[\w.*+!-]+\s*(,\s*(~=|==|!=|<=|>=|<|>|===)\s*[\w.*+!-]+\s*)*$"
)
_RISK_CLASSES = frozenset({"low", "medium", "high", "critical"})
_AUTHORED_BY = frozenset({"human", "agent"})
_HASH_PREFIX = "sha256:"


class PackManifestError(PackError, ValueError):
    """A manifest failed validation. Carries every violation at once.

    One error, full list, computed before any mutation — the
    ``load_pack`` atomicity posture applied to validation (spec Q1).
    ``violations`` is a list of human-readable strings, each naming
    the offending field or path.
    """

    _doc_slug = "pack-manifest-error"

    def __init__(self, *, source: str, violations: list[str]) -> None:
        self.violations = list(violations)
        preview = "\n".join(f"    - {v}" for v in self.violations[:12])
        more = (
            f"\n    ... and {len(self.violations) - 12} more"
            if len(self.violations) > 12
            else ""
        )
        PackError.__init__(
            self,
            f"{len(self.violations)} manifest violation(s) in {source}",
            what_failed=(
                f"Validation of {source} found "
                f"{len(self.violations)} violation(s):\n{preview}{more}"
            ),
            why=(
                "The manifest is the static, declarative half of a pack "
                "(pack manifest spec, DRAFT); consumers — the loader, CI, "
                "the evolution pack's gates — trust it only after it "
                "validates. All violations are reported together so one "
                "fix pass suffices."
            ),
            how_to_fix=(
                "Fix every listed violation in manifest.toml. The spec "
                "document in activegraph-packs (docs/manifest-spec.md) "
                "defines each field's rules; the PROVISIONAL validator "
                "in activegraph.packs.manifest is the reference "
                "implementation."
            ),
            context={"source": source, "violations": self.violations},
        )


@dataclass(frozen=True)
class CapabilityDecl:
    """One ``[[surface.capabilities]]`` entry: a gateway capability
    this pack's host wiring registers, with its risk class."""

    provider: str
    capability: str
    risk_class: str
    credential_ref: str = ""


@dataclass(frozen=True)
class PackManifest:
    """A parsed, schema-valid ``manifest.toml``. PROVISIONAL shape.

    Field names mirror the spec's tables; ``raw`` preserves the full
    parsed TOML for consumers that need keys this dataclass doesn't
    surface yet.
    """

    name: str
    version: str
    description: str
    license: str
    authored_by: str
    content_hash: str
    activegraph_range: str
    python_range: str
    python_deps: tuple[str, ...]
    pack_deps: dict[str, str]
    optional_pack_deps: dict[str, str]
    object_types: tuple[str, ...]
    relation_types: tuple[str, ...]
    behaviors: tuple[str, ...]
    tools: tuple[str, ...]
    settings_schema: str
    capabilities: tuple[CapabilityDecl, ...]
    consumes: tuple[str, ...]
    fixtures_entrypoint: str
    fixtures_deterministic: bool
    raw: dict[str, Any] = field(repr=False, default_factory=dict)


def load_manifest(path: str | Path) -> PackManifest:
    """Parse and schema-validate a ``manifest.toml``. PROVISIONAL.

    ``path`` is the manifest file or the pack root containing it.
    Raises :class:`PackManifestError` carrying EVERY violation found;
    returns the parsed :class:`PackManifest` when clean. Version and
    range fields are checked syntactically (PEP 440 shape); semantic
    range resolution against a running runtime is load-time
    enforcement and deliberately not part of this cycle.
    """
    p = Path(path)
    if p.is_dir():
        p = p / "manifest.toml"
    violations: list[str] = []
    try:
        raw_bytes = p.read_bytes()
    except OSError as e:
        raise PackManifestError(
            source=str(p), violations=[f"cannot read manifest: {e}"]
        ) from e
    try:
        data = tomllib.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        raise PackManifestError(
            source=str(p), violations=[f"not valid UTF-8 TOML: {e}"]
        ) from e

    def table(key: str) -> dict[str, Any]:
        v = data.get(key)
        if not isinstance(v, dict):
            violations.append(f"missing required table [{key}]")
            return {}
        return v

    pack = table("pack")
    provenance = pack.get("provenance")
    if not isinstance(provenance, dict):
        violations.append("missing required table [pack.provenance]")
        provenance = {}
    integrity = pack.get("integrity")
    if not isinstance(integrity, dict):
        violations.append("missing required table [pack.integrity]")
        integrity = {}
    dependencies = table("dependencies")
    surface = table("surface")
    fixtures = table("fixtures")

    name = str(pack.get("name", ""))
    if not _NAME_RE.match(name):
        violations.append(
            f"pack.name {name!r} must match ^[a-z][a-z0-9_]{{1,63}}$"
        )
    version = str(pack.get("version", ""))
    if not _VERSION_RE.match(version):
        violations.append(f"pack.version {version!r} is not PEP 440")
    description = str(pack.get("description", ""))
    if not description:
        violations.append("pack.description must be nonempty")
    license_ = str(pack.get("license", ""))

    authored_by = str(provenance.get("authored_by", ""))
    if authored_by not in _AUTHORED_BY:
        violations.append(
            f"pack.provenance.authored_by {authored_by!r} must be "
            f"'human' or 'agent'"
        )

    content_hash = str(integrity.get("content_hash", ""))
    if not content_hash.startswith(_HASH_PREFIX) or not re.fullmatch(
        r"[0-9a-f]{64}", content_hash[len(_HASH_PREFIX):]
    ):
        violations.append(
            f"pack.integrity.content_hash {content_hash!r} must be "
            f"'sha256:' + 64 lowercase hex chars"
        )
    signature = integrity.get("signature", "")
    # Spec Q6: an unknown algorithm prefix is REJECTED, never skipped —
    # the reserved seam must not be usable for downgrade.
    if signature:
        violations.append(
            f"pack.integrity.signature is reserved; no algorithm is "
            f"implemented yet, and a non-empty value ({str(signature)[:40]!r}) "
            f"must be rejected rather than skipped"
        )

    ag_range = str(dependencies.get("activegraph", ""))
    if not ag_range or not _SPECIFIER_RE.match(ag_range):
        violations.append(
            f"dependencies.activegraph {ag_range!r} must be a PEP 440 "
            f"specifier set"
        )
    py_range = str(dependencies.get("python", ""))
    if py_range and not _SPECIFIER_RE.match(py_range):
        violations.append(
            f"dependencies.python {py_range!r} must be a PEP 440 "
            f"specifier set"
        )
    python_deps = dependencies.get("python-deps", [])
    if not isinstance(python_deps, list) or not all(
        isinstance(x, str) for x in python_deps
    ):
        violations.append("dependencies.python-deps must be a list of strings")
        python_deps = []

    def dep_table(key: str) -> dict[str, str]:
        t = dependencies.get(key, {})
        if not isinstance(t, dict):
            violations.append(f"dependencies.{key} must be a table")
            return {}
        out: dict[str, str] = {}
        for k, v in t.items():
            if not isinstance(v, str) or not _SPECIFIER_RE.match(v):
                violations.append(
                    f"dependencies.{key}.{k} {v!r} must be a PEP 440 "
                    f"specifier set"
                )
                continue
            out[str(k)] = v
        return out

    pack_deps = dep_table("packs")
    optional_pack_deps = dep_table("optional-packs")

    def str_list(key: str) -> tuple[str, ...]:
        v = surface.get(key, [])
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            violations.append(f"surface.{key} must be a list of strings")
            return ()
        return tuple(v)

    object_types = str_list("object_types")
    relation_types = str_list("relation_types")
    behaviors = str_list("behaviors")
    tools = str_list("tools")
    settings_schema = str(surface.get("settings_schema", ""))

    capabilities: list[CapabilityDecl] = []
    for i, cap in enumerate(surface.get("capabilities", []) or []):
        if not isinstance(cap, dict):
            violations.append(f"surface.capabilities[{i}] must be a table")
            continue
        risk = str(cap.get("risk_class", ""))
        if risk not in _RISK_CLASSES:
            violations.append(
                f"surface.capabilities[{i}].risk_class {risk!r} must be "
                f"one of low|medium|high|critical"
            )
        capabilities.append(
            CapabilityDecl(
                provider=str(cap.get("provider", "")),
                capability=str(cap.get("capability", "")),
                risk_class=risk,
                credential_ref=str(cap.get("credential_ref", "")),
            )
        )
    consumes_v = surface.get("consumes", [])
    if not isinstance(consumes_v, list) or not all(
        isinstance(x, str) for x in consumes_v
    ):
        violations.append("surface.consumes must be a list of strings")
        consumes_v = []

    fixtures_entrypoint = str(fixtures.get("entrypoint", ""))
    if not fixtures_entrypoint:
        violations.append("fixtures.entrypoint must be nonempty")
    fixtures_deterministic = fixtures.get("deterministic")
    if not isinstance(fixtures_deterministic, bool):
        violations.append("fixtures.deterministic must be a boolean")
        fixtures_deterministic = False

    if violations:
        raise PackManifestError(source=str(p), violations=violations)

    return PackManifest(
        name=name,
        version=version,
        description=description,
        license=license_,
        authored_by=authored_by,
        content_hash=content_hash,
        activegraph_range=ag_range,
        python_range=py_range,
        python_deps=tuple(python_deps),
        pack_deps=pack_deps,
        optional_pack_deps=optional_pack_deps,
        object_types=object_types,
        relation_types=relation_types,
        behaviors=behaviors,
        tools=tools,
        settings_schema=settings_schema,
        capabilities=tuple(capabilities),
        consumes=tuple(consumes_v),
        fixtures_entrypoint=fixtures_entrypoint,
        fixtures_deterministic=fixtures_deterministic,
        raw=data,
    )


def verify_surface(manifest: PackManifest, pack: Any) -> None:
    """Two-way surface check between a manifest and a live ``Pack``.

    PROVISIONAL. Per the spec's identity mapping: every declared name
    must exist on the ``Pack`` object and every registered name must
    be declared, for ``object_types`` / ``relation_types`` /
    ``behaviors`` / ``tools`` / ``settings_schema``. Raises
    :class:`PackManifestError` with the full mismatch list.

    ``capabilities`` ARE verified here (spec Q8, runtime half): since
    ``Pack`` carries a declarative ``capabilities`` field, this runs
    the same two-way check keyed by ``(provider, capability)`` and
    additionally requires ``risk_class`` agreement on any pair
    declared on both sides. Only ``consumes`` stays out of scope —
    it is imperative gateway wiring invisible at ``load_pack`` time,
    left to static CI / evolution gates.
    """
    violations: list[str] = []

    def two_way(kind: str, declared: tuple[str, ...], actual: set[str]) -> None:
        for name in sorted(set(declared) - actual):
            violations.append(f"{kind}: declared {name!r} not found on Pack")
        for name in sorted(actual - set(declared)):
            violations.append(f"{kind}: Pack registers {name!r} undeclared")

    if manifest.name != getattr(pack, "name", None):
        violations.append(
            f"pack.name {manifest.name!r} != Pack(name={getattr(pack, 'name', None)!r})"
        )
    if manifest.version != getattr(pack, "version", None):
        violations.append(
            f"pack.version {manifest.version!r} != "
            f"Pack(version={getattr(pack, 'version', None)!r})"
        )
    two_way(
        "object_types",
        manifest.object_types,
        {ot.name for ot in pack.object_types},
    )
    two_way(
        "relation_types",
        manifest.relation_types,
        {rt.name for rt in pack.relation_types},
    )
    two_way("behaviors", manifest.behaviors, {b.name for b in pack.behaviors})
    two_way("tools", manifest.tools, {t.name for t in pack.tools})
    from activegraph.packs import EmptySettings

    actual_settings = (
        ""
        if pack.settings_schema is EmptySettings
        else pack.settings_schema.__name__
    )
    if manifest.settings_schema != actual_settings:
        violations.append(
            f"settings_schema: declared {manifest.settings_schema!r}, "
            f"Pack has {actual_settings!r}"
        )
    # v1.4 (spec Q8, runtime half): Pack.capabilities is declarative,
    # so the loader can two-way check it exactly like behaviors/tools.
    # Keyed by (provider, capability); a declared pair must also agree
    # on risk_class — a relabeled risk class is precisely the swap the
    # decision surface needs to catch.
    declared_caps = {
        (c.provider, c.capability): c.risk_class for c in manifest.capabilities
    }
    actual_caps = {
        (c.provider, c.capability): c.risk_class
        for c in getattr(pack, "capabilities", ())
    }
    for key in sorted(set(declared_caps) - set(actual_caps)):
        violations.append(
            f"capabilities: declared {key[0]}.{key[1]} not on Pack"
        )
    for key in sorted(set(actual_caps) - set(declared_caps)):
        violations.append(
            f"capabilities: Pack declares {key[0]}.{key[1]} undeclared "
            f"in manifest"
        )
    for key in sorted(set(declared_caps) & set(actual_caps)):
        if declared_caps[key] != actual_caps[key]:
            violations.append(
                f"capabilities: {key[0]}.{key[1]} risk_class mismatch — "
                f"manifest {declared_caps[key]!r}, Pack {actual_caps[key]!r}"
            )
    if violations:
        raise PackManifestError(
            source=f"{manifest.name}@{manifest.version}", violations=violations
        )


def _hash_pack_dir(pack_root: str | Path, *, include_manifest: bool) -> str:
    """The shared §4 canonical byte stream over a pack directory.

    ``include_manifest=False`` is the manifest-internal content hash
    (a hash cannot cover itself); ``True`` is the bundle hash external
    pins use (spec amendment: the manifest is the very document the
    reviewer approves, so an external pin that excludes it pins
    nothing that matters). Everything else is identical:

    1. Every regular file under the root, recursively, excluding
       ``__pycache__`` directories, ``*.pyc``, and hidden
       (``.``-prefixed) files and directories.
    2. Symlinks — file OR directory — are rejected loudly (directory
       symlinks could otherwise smuggle unhashed content).
    3. Paths must be UTF-8-encodable and NFC-normalized; anything
       else is rejected loudly rather than hashed ambiguously across
       platforms.
    4. Relative POSIX paths sorted lexicographically by UTF-8 bytes;
       per file: path bytes, NUL, 8-byte big-endian length, raw bytes.
    """
    root = Path(pack_root)
    if not root.is_dir():
        raise PackManifestError(
            source=str(root), violations=["pack root is not a directory"]
        )
    entries: list[tuple[bytes, Path]] = []
    violations: list[str] = []

    def walk(d: Path) -> None:
        for child in sorted(d.iterdir(), key=lambda c: c.name):
            rel = child.relative_to(root).as_posix()
            if child.name.startswith("."):
                continue
            if child.is_symlink():
                violations.append(f"symlink rejected: {rel}")
                continue
            if child.is_dir():
                if child.name == "__pycache__":
                    continue
                walk(child)
                continue
            if not child.is_file():
                continue  # sockets, fifos: not regular files
            if child.name.endswith(".pyc"):
                continue
            if rel == "manifest.toml" and not include_manifest:
                continue
            if unicodedata.normalize("NFC", rel) != rel:
                violations.append(f"path not NFC-normalized: {rel!r}")
                continue
            try:
                rel_bytes = rel.encode("utf-8")
            except UnicodeEncodeError:
                violations.append(f"path not UTF-8-encodable: {rel!r}")
                continue
            entries.append((rel_bytes, child))

    walk(root)
    if violations:
        raise PackManifestError(source=str(root), violations=violations)

    h = hashlib.sha256()
    for rel_bytes, f in sorted(entries, key=lambda e: e[0]):
        data = f.read_bytes()
        h.update(rel_bytes)
        h.update(b"\x00")
        h.update(len(data).to_bytes(8, "big"))
        h.update(data)
    return _HASH_PREFIX + h.hexdigest()


def compute_content_hash(pack_root: str | Path) -> str:
    """The spec §4 content hash over a pack directory, byte-exact.

    PROVISIONAL — this function is the normative reference for every
    recomputation site (loader, downstream CI, evolution gates).
    ``manifest.toml`` is EXCLUDED (a hash cannot cover itself); see
    :func:`_hash_pack_dir` for the full canonicalization, including
    this implementation's amendments over the spec draft. This is the
    hash the manifest's own ``[pack.integrity].content_hash`` pins —
    internal consistency only, never authenticity. External pins use
    :func:`compute_bundle_hash`.

    Returns ``"sha256:" + 64 hex chars``.
    """
    return _hash_pack_dir(pack_root, include_manifest=False)


def compute_bundle_hash(pack_root: str | Path) -> str:
    """The bundle hash external pins use: the §4 walk WITHOUT the
    manifest exclusion. PROVISIONAL.

    Spec-review finding, accepted downstream: the content hash
    excludes ``manifest.toml`` by necessity (self-reference), so an
    external pin over it leaves the manifest — the very document a
    reviewer approves, carrying risk classes, ``consumes``, and
    ``authored_by`` — swappable without detection. ``[load.pins]``
    entries, the evolution pack's proposal pins, and any resolver
    verifying a fetched pack pin THIS hash instead. Same
    canonicalization, same amendments, one file more.

    Returns ``"sha256:" + 64 hex chars``.
    """
    return _hash_pack_dir(pack_root, include_manifest=True)


def verify_bundle_hash(expected: str, pack_root: str | Path) -> None:
    """Recompute the bundle hash over ``pack_root`` and compare to an
    EXTERNAL pin. Raises :class:`PackManifestError` on mismatch.

    PROVISIONAL. ``expected`` comes from outside the pack directory —
    a ``[load.pins]`` entry, a proposal record, an approval surface —
    which is what makes this an authenticity check rather than the
    internal-consistency check :func:`verify_content_hash` performs.
    """
    if not expected.startswith(_HASH_PREFIX) or not re.fullmatch(
        r"[0-9a-f]{64}", expected[len(_HASH_PREFIX):]
    ):
        raise PackManifestError(
            source=str(pack_root),
            violations=[
                f"external pin {expected!r} must be 'sha256:' + 64 "
                f"lowercase hex chars"
            ],
        )
    actual = compute_bundle_hash(pack_root)
    if actual != expected:
        raise PackManifestError(
            source=str(pack_root),
            violations=[
                f"bundle hash mismatch: external pin is {expected}, "
                f"directory (manifest included) hashes to {actual}"
            ],
        )


def verify_content_hash(
    manifest: PackManifest, pack_root: str | Path
) -> None:
    """Recompute §4's hash over ``pack_root`` and compare to the
    manifest's pin. Raises :class:`PackManifestError` on mismatch.

    PROVISIONAL. Note the spec's honest scoping: this proves internal
    consistency (the bytes match what the manifest claims), never
    authenticity — the manifest travels with the pack. Authenticity
    needs an external pin.
    """
    actual = compute_content_hash(pack_root)
    if actual != manifest.content_hash:
        raise PackManifestError(
            source=str(pack_root),
            violations=[
                f"content hash mismatch: manifest pins "
                f"{manifest.content_hash}, directory hashes to {actual}"
            ],
        )
