# PackManifestError

A `manifest.toml` failed validation — schema violations, a two-way
surface mismatch against the live `Pack` object, or a content-hash
problem (mismatch, symlink in the walk, non-NFC path). Raised by the
**provisional** validator in `activegraph.packs.manifest`
(CONTRACT v1.4 #1); `load_pack` does not enforce manifests yet.

Since v1.6, `load_pack` runs this validation as a **warning tier**
(CONTRACT v1.6 #1): when a `manifest.toml` is discoverable at the
pack root, violations are logged as one structured WARNING per pack
per process on the `activegraph.packs.manifest` logger — the record
carries the full `violations` list and
`reason="pack.manifest_invalid"` — and the pack loads anyway. A
missing manifest stays silent. This never becomes an error before
activegraph 2.0.

Every violation is collected and raised together — `.violations` is
the full list — so one fix pass suffices, matching `load_pack`'s
pre-mutation posture.

## Quick fix

```python
from activegraph.packs.manifest import load_manifest, PackManifestError

try:
    m = load_manifest("packs/my_pack")
except PackManifestError as e:
    for v in e.violations:
        print(v)
```

Each violation names the offending field or path. The field rules
live in the pack manifest spec (activegraph-packs,
`docs/manifest-spec.md`, DRAFT); this validator is its reference
implementation, including the §4 content-hash canonicalization.

Common causes:

- **Schema**: `pack.name` outside `^[a-z][a-z0-9_]{1,63}$`, a
  non-PEP-440 `version`, a `risk_class` outside
  `low|medium|high|critical`, a non-empty `signature` (reserved —
  rejected, never skipped, so the seam can't be used for downgrade).
- **Surface**: a name declared in `[surface]` that the `Pack(...)`
  object doesn't register, or vice versa. The check is two-way by
  design — a pack that opts in, opts all the way in.
- **Hash**: directory bytes differ from `content_hash` (something
  changed after the hash was computed), a symlink anywhere under the
  pack root (rejected outright, files and directories both), or a
  path that isn't NFC-normalized UTF-8.

## Provisional status

The manifest spec stays DRAFT until its first two consumers (the vc
extraction and the evolution pack) have built against it. Expect one
round of breaking edits to `activegraph.packs.manifest` — import it
from that module path, not from the top-level `activegraph`
namespace, which deliberately does not re-export it yet.

## What's related

- [Authoring packs](../../guides/authoring-packs.md) — the `Pack(...)`
  object the surface check verifies against.
- [`pack-schema-violation`](pack-schema-violation.md) — typed-data
  violations at `add_object` (and, since v1.4, promote-apply) time.
