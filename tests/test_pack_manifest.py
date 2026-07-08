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
    compute_content_hash,
    load_manifest,
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
    root.mkdir()
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
    m = load_manifest(_write_pack(tmp_path))
    verify_surface(m, _pack())  # no raise


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
