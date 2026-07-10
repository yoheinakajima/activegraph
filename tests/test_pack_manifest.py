"""Provisional pack-manifest validator (CONTRACT v1.4 #1).

Schema validation with full-violation aggregation, the two-way
surface check against a live Pack, and the spec §4 content hash —
byte-exact, with the runtime's amendments (directory symlinks
rejected, non-NFC / non-UTF-8 paths rejected).
"""

from __future__ import annotations

import hashlib
import os
import unicodedata

import pytest
from pydantic import BaseModel

from activegraph.packs import ObjectType, Pack
from activegraph.packs.manifest import (
    PackManifestError,
    compute_bundle_hash,
    compute_content_hash,
    load_manifest,
    verify_bundle_hash,
    verify_content_hash,
    verify_surface,
)


GOOD_MANIFEST = """
[pack]
name = "meeting_notes"
version = "0.1.0"
description = "Extracts decisions."
license = "Apache-2.0"

[pack.provenance]
authors = ["Y <y@example.com>"]
authored_by = "human"
generator = ""
source_url = ""
created_at = "2026-07-08T00:00:00Z"

[pack.integrity]
content_hash = "sha256:%s"

[dependencies]
activegraph = ">=1.3,<2.0"
python = ">=3.11"
python-deps = []

[dependencies.packs]
core = ">=0.1"

[surface]
object_types = ["meeting"]
relation_types = []
behaviors = []
tools = []
settings_schema = ""

[[surface.capabilities]]
provider = "meeting"
capability = "export_summary"
risk_class = "medium"
credential_ref = ""

[fixtures]
entrypoint = "fixtures/run_fixtures.py"
deterministic = true
""" % ("0" * 64)


def _write_pack(tmp_path, manifest_text=GOOD_MANIFEST):
    root = tmp_path / "meeting_notes"
    root.mkdir(parents=True)
    (root / "manifest.toml").write_text(manifest_text)
    (root / "__init__.py").write_text("# pack module\n")
    return root


def test_load_manifest_round_trip(tmp_path):
    root = _write_pack(tmp_path)
    m = load_manifest(root)
    assert m.name == "meeting_notes"
    assert m.version == "0.1.0"
    assert m.activegraph_range == ">=1.3,<2.0"
    assert m.pack_deps == {"core": ">=0.1"}
    assert m.object_types == ("meeting",)
    assert m.capabilities[0].risk_class == "medium"
    assert m.fixtures_deterministic is True


def test_violations_are_aggregated_into_one_error(tmp_path):
    bad = GOOD_MANIFEST.replace('name = "meeting_notes"', 'name = "Bad-Name"')
    bad = bad.replace('version = "0.1.0"', 'version = "not-a-version"')
    bad = bad.replace('risk_class = "medium"', 'risk_class = "extreme"')
    root = _write_pack(tmp_path, bad)
    with pytest.raises(PackManifestError) as exc:
        load_manifest(root)
    joined = "\n".join(exc.value.violations)
    assert "pack.name" in joined
    assert "pack.version" in joined
    assert "risk_class" in joined
    assert len(exc.value.violations) == 3


def test_nonempty_signature_is_rejected_not_skipped(tmp_path):
    # Spec Q6: the reserved seam must not be usable for downgrade.
    signed = GOOD_MANIFEST.replace(
        'content_hash = "sha256:%s"' % ("0" * 64),
        'content_hash = "sha256:%s"\nsignature = "ed25519:abcd"' % ("0" * 64),
    )
    root = _write_pack(tmp_path, signed)
    with pytest.raises(PackManifestError, match="signature"):
        load_manifest(root)


# --------------------------------------------------- surface check


class _MeetingSchema(BaseModel):
    title: str


def _pack(object_types=("meeting",)):
    return Pack(
        name="meeting_notes",
        version="0.1.0",
        object_types=tuple(
            ObjectType(name=n, schema=_MeetingSchema) for n in object_types
        ),
    )


def test_surface_check_passes_on_agreement(tmp_path):
    from activegraph.packs.manifest import CapabilityDecl

    m = load_manifest(_write_pack(tmp_path))
    pack = Pack(
        name="meeting_notes",
        version="0.1.0",
        object_types=_pack().object_types,
        capabilities=(
            CapabilityDecl(
                provider="meeting",
                capability="export_summary",
                risk_class="medium",
            ),
        ),
    )
    verify_surface(m, pack)  # no raise


def test_surface_check_catches_both_directions(tmp_path):
    m = load_manifest(_write_pack(tmp_path))
    with pytest.raises(PackManifestError) as exc:
        verify_surface(m, _pack(object_types=("meeting", "undeclared_thing")))
    assert any("undeclared_thing" in v for v in exc.value.violations)

    with pytest.raises(PackManifestError) as exc:
        verify_surface(m, _pack(object_types=()))
    assert any(
        "declared 'meeting' not found" in v for v in exc.value.violations
    )


# ----------------------------------------------------- content hash


def test_content_hash_is_deterministic_and_byte_exact(tmp_path):
    root = _write_pack(tmp_path)
    (root / "behaviors.py").write_text("x = 1\n")

    # Hand-compute the §4 stream for the two hashed files (the
    # manifest itself is excluded).
    h = hashlib.sha256()
    for rel in [b"__init__.py", b"behaviors.py"]:
        data = (root / rel.decode()).read_bytes()
        h.update(rel)
        h.update(b"\x00")
        h.update(len(data).to_bytes(8, "big"))
        h.update(data)
    expected = "sha256:" + h.hexdigest()

    assert compute_content_hash(root) == expected
    assert compute_content_hash(root) == expected  # stable


def test_content_hash_exclusions(tmp_path):
    root = _write_pack(tmp_path)
    baseline = compute_content_hash(root)

    # Excluded artifacts don't move the hash...
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "x.cpython-311.pyc").write_bytes(b"\x00")
    (root / "module.pyc").write_bytes(b"\x00")
    (root / ".hidden").write_text("secret")
    (root / "manifest.toml").write_text(GOOD_MANIFEST + "\n# comment\n")
    assert compute_content_hash(root) == baseline

    # ...and real content does.
    (root / "tools.py").write_text("y = 2\n")
    assert compute_content_hash(root) != baseline


def test_content_hash_rejects_symlinks_including_directories(tmp_path):
    root = _write_pack(tmp_path)
    outside = tmp_path / "outside.py"
    outside.write_text("z = 3\n")
    os.symlink(outside, root / "linked.py")
    with pytest.raises(PackManifestError, match="symlink"):
        compute_content_hash(root)
    (root / "linked.py").unlink()

    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir()
    (outside_dir / "smuggled.py").write_text("s = 4\n")
    os.symlink(outside_dir, root / "linked_dir")
    with pytest.raises(PackManifestError, match="symlink"):
        compute_content_hash(root)


def test_content_hash_rejects_non_nfc_paths(tmp_path):
    root = _write_pack(tmp_path)
    # NFD-decomposed "é" — hashes differently across platforms if
    # accepted; rejected loudly instead.
    nfd_name = unicodedata.normalize("NFD", "café.py")
    assert nfd_name != unicodedata.normalize("NFC", nfd_name)
    (root / nfd_name).write_text("q = 5\n")
    with pytest.raises(PackManifestError, match="NFC"):
        compute_content_hash(root)


def test_verify_content_hash_pins_bytes(tmp_path):
    root = _write_pack(tmp_path)
    real = compute_content_hash(root)
    manifest_text = GOOD_MANIFEST.replace("sha256:" + "0" * 64, real)
    (root / "manifest.toml").write_text(manifest_text)
    m = load_manifest(root)
    verify_content_hash(m, root)  # no raise

    (root / "__init__.py").write_text("# tampered\n")
    with pytest.raises(PackManifestError, match="mismatch"):
        verify_content_hash(m, root)


def test_prompt_loader_skips_hidden_and_symlinked_files(tmp_path):
    # Alignment with the §4 hash walk: nothing load-bearing may be a
    # file the content hash excludes (hidden) or rejects (symlink).
    from activegraph.packs import load_prompts_from_dir

    d = tmp_path / "prompts"
    d.mkdir()
    body = '---\nversion = "1.0.0"\n---\nBody.\n'
    (d / "real.md").write_text(body)
    (d / ".sneaky.md").write_text(body)
    outside = tmp_path / "outside.md"
    outside.write_text(body)
    os.symlink(outside, d / "linked.md")

    prompts = load_prompts_from_dir(d)
    assert [pr.name for pr in prompts] == ["real"]


# ------------------------------------------- bundle hash (v1.4.0)


def test_bundle_hash_includes_the_manifest(tmp_path):
    root = _write_pack(tmp_path)
    content = compute_content_hash(root)
    bundle = compute_bundle_hash(root)
    assert bundle != content

    # A manifest-only edit — the approve-then-swap vector — moves the
    # bundle hash and leaves the content hash untouched.
    (root / "manifest.toml").write_text(
        GOOD_MANIFEST.replace('risk_class = "medium"', 'risk_class = "low"')
    )
    assert compute_content_hash(root) == content
    assert compute_bundle_hash(root) != bundle


def test_bundle_hash_is_byte_exact(tmp_path):
    root = _write_pack(tmp_path)
    h = hashlib.sha256()
    for rel in sorted([b"__init__.py", b"manifest.toml"]):
        data = (root / rel.decode()).read_bytes()
        h.update(rel)
        h.update(b"\x00")
        h.update(len(data).to_bytes(8, "big"))
        h.update(data)
    assert compute_bundle_hash(root) == "sha256:" + h.hexdigest()


def test_verify_bundle_hash_detects_manifest_swap(tmp_path):
    root = _write_pack(tmp_path)
    pin = compute_bundle_hash(root)
    verify_bundle_hash(pin, root)  # no raise

    (root / "manifest.toml").write_text(
        GOOD_MANIFEST.replace("[fixtures]", "consumes = []\n\n[fixtures]")
    )
    with pytest.raises(PackManifestError, match="bundle hash mismatch"):
        verify_bundle_hash(pin, root)


def test_verify_bundle_hash_rejects_malformed_pin(tmp_path):
    root = _write_pack(tmp_path)
    with pytest.raises(PackManifestError, match="external pin"):
        verify_bundle_hash("md5:abcd", root)


def test_bundle_hash_shares_the_walk_rules(tmp_path):
    root = _write_pack(tmp_path)
    baseline = compute_bundle_hash(root)
    (root / ".hidden").write_text("x")
    (root / "cached.pyc").write_bytes(b"\x00")
    assert compute_bundle_hash(root) == baseline
    os.symlink(tmp_path / "nowhere", root / "bad_link")
    with pytest.raises(PackManifestError, match="symlink"):
        compute_bundle_hash(root)


# --------------------------------- Pack.capabilities (v1.4.0, Q8)


def test_pack_capabilities_field_validates_and_lands_in_pack_loaded(tmp_path):
    from activegraph import Graph, Runtime, clear_registry
    from activegraph.packs import PackValidationError
    from activegraph.packs.manifest import CapabilityDecl

    clear_registry()
    cap = CapabilityDecl(
        provider="meeting", capability="export_summary", risk_class="medium"
    )
    pack = Pack(name="meeting_notes", version="0.1.0", capabilities=(cap,))

    rt = Runtime(Graph())
    rt.load_pack(pack)
    loaded_event = next(
        e for e in rt.graph.events if e.type == "pack.loaded"
    )
    assert loaded_event.payload["capabilities"] == [
        {
            "provider": "meeting",
            "capability": "export_summary",
            "risk_class": "medium",
            "credential_ref": "",
        }
    ]

    with pytest.raises(PackValidationError, match="risk_class"):
        Pack(
            name="bad",
            version="0.1.0",
            capabilities=(
                CapabilityDecl(
                    provider="x", capability="y", risk_class="extreme"
                ),
            ),
        )
    with pytest.raises(PackValidationError, match="CapabilityDecl"):
        Pack(name="bad", version="0.1.0", capabilities=({"provider": "x"},))


def test_surface_check_covers_capabilities_two_way(tmp_path):
    from activegraph.packs.manifest import CapabilityDecl

    m = load_manifest(_write_pack(tmp_path))  # declares meeting.export_summary/medium

    # Agreement passes.
    ok = _pack()
    ok = Pack(
        name="meeting_notes",
        version="0.1.0",
        object_types=ok.object_types,
        capabilities=(
            CapabilityDecl(
                provider="meeting",
                capability="export_summary",
                risk_class="medium",
            ),
        ),
    )
    verify_surface(m, ok)

    # Pack silent about a declared capability.
    with pytest.raises(PackManifestError) as exc:
        verify_surface(m, _pack())
    assert any("not on Pack" in v for v in exc.value.violations)

    # Risk-class relabel is caught.
    relabeled = Pack(
        name="meeting_notes",
        version="0.1.0",
        object_types=_pack().object_types,
        capabilities=(
            CapabilityDecl(
                provider="meeting",
                capability="export_summary",
                risk_class="low",
            ),
        ),
    )
    with pytest.raises(PackManifestError) as exc:
        verify_surface(m, relabeled)
    assert any("risk_class mismatch" in v for v in exc.value.violations)


# ------------------------------------------- action_class (CONTRACT v1.9)


def test_action_class_is_optional_and_closed_set(tmp_path):
    from activegraph.packs.manifest import CapabilityDecl

    # Absent → undeclared ("").
    m = load_manifest(_write_pack(tmp_path))
    assert m.capabilities[0].action_class == ""

    # Present and valid → parsed as-is.
    declared = GOOD_MANIFEST.replace(
        'risk_class = "medium"', 'risk_class = "medium"\naction_class = "R2"'
    )
    m = load_manifest(_write_pack(tmp_path / "declared", declared))
    assert m.capabilities[0].action_class == "R2"
    assert m.capabilities[0] == CapabilityDecl(
        provider="meeting",
        capability="export_summary",
        risk_class="medium",
        credential_ref="",
        action_class="R2",
    )


def test_action_class_outside_closed_set_is_a_violation(tmp_path):
    # Including legacy risk labels: there is no mapping between the
    # vocabularies, so "medium" is as invalid as "R9" (ADR 0016).
    for bad in ("R9", "r2", "medium"):
        text = GOOD_MANIFEST.replace(
            'risk_class = "medium"',
            f'risk_class = "medium"\naction_class = "{bad}"',
        )
        root = tmp_path / f"bad_{bad.lower()}"
        with pytest.raises(PackManifestError, match="action_class"):
            load_manifest(_write_pack(root, text))


def _pack_with_capability(action_class=""):
    from activegraph.packs.manifest import CapabilityDecl

    return Pack(
        name="meeting_notes",
        version="0.1.0",
        object_types=_pack().object_types,
        capabilities=(
            CapabilityDecl(
                provider="meeting",
                capability="export_summary",
                risk_class="medium",
                action_class=action_class,
            ),
        ),
    )


def test_surface_check_requires_action_class_agreement(tmp_path):
    declared = GOOD_MANIFEST.replace(
        'risk_class = "medium"', 'risk_class = "medium"\naction_class = "R2"'
    )
    m = load_manifest(_write_pack(tmp_path, declared))

    # Agreement on both sides: clean.
    verify_surface(m, _pack_with_capability(action_class="R2"))

    # A relabel between manifest and Pack is caught...
    with pytest.raises(PackManifestError) as exc:
        verify_surface(m, _pack_with_capability(action_class="R3"))
    assert any("action_class mismatch" in v for v in exc.value.violations)

    # ...and so is a class present on only one side (either side).
    with pytest.raises(PackManifestError) as exc:
        verify_surface(m, _pack_with_capability(action_class=""))
    assert any("action_class mismatch" in v for v in exc.value.violations)

    undeclared = load_manifest(_write_pack(tmp_path / "undeclared"))
    with pytest.raises(PackManifestError) as exc:
        verify_surface(undeclared, _pack_with_capability(action_class="R2"))
    assert any("action_class mismatch" in v for v in exc.value.violations)
