"""Check retained packet hashes and conservative IEEE format; no runtime calls."""
from __future__ import annotations
import argparse
import hashlib
from importlib import metadata
import json
from pathlib import Path
import re
import subprocess

BASELINE = '7070112c63b7cffc4b3aef8b7e907c8f2f9a9165'
PACKET = Path(__file__).resolve().parents[1]
REPO = PACKET.parents[1]
SKIP = {'.scratch', '.private', '__pycache__'}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def files():
    return [p for p in sorted(PACKET.rglob('*')) if p.is_file()
            and not (set(p.relative_to(PACKET).parts) & SKIP)
            and p.name != 'manifest.json'
            and p.suffix not in ('.aux', '.log', '.out', '.pyc')]


def plain(tex):
    return subprocess.check_output(['pandoc', '-f', 'latex', '-t', 'plain'],
                                   input=tex, text=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write-manifest', action='store_true')
    parser.add_argument('--saved-only', action='store_true', help='verify retained hashes only; no Git, Pandoc, or runtime required')
    args = parser.parse_args()
    if args.saved_only:
        assert not args.write_manifest
        manifest = json.loads((PACKET / 'manifest.json').read_text())
        assert {r['path'] for r in manifest['files']} == {str(p.relative_to(PACKET)) for p in files()}
        for record in manifest['files']:
            assert digest(PACKET / record['path']) == record['sha256'], record['path']
        print(json.dumps({'result': 'PASS', 'mode': 'retained hashes only', 'files': len(manifest['files']),
                          'no_git_pandoc_or_runtime_required': True}, indent=2))
        return
    text = (PACKET / 'article.tex').read_text()
    abstract = re.search(r'\\begin\{abstract\}(.*?)\\end\{abstract\}', text, re.S).group(1)
    abstract_words = len(plain(abstract).split())
    all_visible_words = len(plain(text).split())
    figures = text.count(r'\begin{figure}')
    tables = text.count(r'\begin{table}')
    adjusted_conservative = all_visible_words + 250 * (figures + tables)
    refs = text.count(r'\bibitem')
    insights = text.split(r'\section*{Three actionable insights}', 1)[1].split(r'\end{itemize}', 1)[0].count(r'\item')
    assert abstract_words <= 150, abstract_words
    assert adjusted_conservative <= 4200, adjusted_conservative
    assert refs <= 15 and insights == 3
    assert figures == 1 and tables == 1
    receipt = json.loads((PACKET / 'evidence/identity-checked/verification.json').read_text())
    assert receipt['imported_runtime_identity_verified']
    assert digest(PACKET / 'verification/runtime-inputs.json') == receipt['runtime_pin_sha256']
    for rel, key in [('fixture-events.jsonl', 'full_event_jsonl_sha256'),
                     ('fixture-events-second.jsonl', 'second_full_event_jsonl_sha256'),
                     ('fixture-events-normalized.jsonl', 'normalized_event_jsonl_sha256'),
                     ('fixture-projection.json', 'projection_sha256')]:
        assert digest(PACKET / 'evidence/identity-checked' / rel) == receipt[key]
    assert receipt['two_fixture_event_jsonl_equal'] is False
    assert receipt['two_normalized_fixture_event_jsonl_equal']
    assert receipt['strict_payload_only_drift_accepted']
    for field in receipt['differing_event_fields']:
        assert field['type'] == 'tool.responded'
        assert field['fields'] == ['payload.latency_seconds']
    changed_runtime = subprocess.check_output(
        ['git', 'diff', BASELINE, '--', 'activegraph', 'tests', 'pyproject.toml', 'CONTRACT.md', 'docs'],
        cwd=REPO, text=True)
    assert not changed_runtime, 'runtime/native-contract/docs baseline changed'
    original = PACKET / 'archive/arxiv-v1'
    assert digest(original / '2605.21997v1.pdf') == '8e8ba7df94f27f0ab9fa105f038dc9cd4ceaa6679657fc456d81dbbb689a0266'
    assert digest(original / 'activegraph.tex') == 'b8fb447bad03b55ca326083df8948cf5dd31b4ff5e33762ebedbaeb47055e610'
    format_receipt = dict(abstract_words=abstract_words,
                          all_visible_words_including_references_header_visuals=all_visible_words,
                          figures=figures, tables=tables,
                          conservative_total_with_250_per_visual=adjusted_conservative,
                          references=refs, actionable_insights=insights,
                          method='Pandoc LaTeX-to-plain whitespace tokens, all visible text included, plus 250 per figure/table; intentionally overcounts exclusions')
    if args.write_manifest:
        inventory = [{'path': str(p.relative_to(PACKET)), 'bytes': p.stat().st_size,
                      'sha256': digest(p)} for p in files()]
        runtime_inputs = ['pyproject.toml', 'LICENSE', 'NOTICE', 'activegraph/runtime/runtime.py',
                          'activegraph/core/graph.py', 'activegraph/core/event.py',
                          'activegraph/packs/diligence/fixtures/__init__.py',
                          'activegraph/packs/diligence/fixtures/companies.py']
        installed = {d.metadata['Name']: d.version for d in metadata.distributions() if d.metadata['Name']}
        result = {'schema_version': 1, 'observed_date': '2026-11-02', 'timezone': 'America/Los_Angeles',
                  'runtime_baseline_commit': BASELINE,
                  'source_branch': 'codex/ieee-software-prep-20261102',
                  'manuscript': 'article.tex', 'pdf': 'article.pdf',
                  'supplement': 'supplement.tex', 'supplement_pdf': 'supplement.pdf',
                  'readiness': 'manager/author review; author facts and final action gates pending',
                  'primary_verification_receipt': 'evidence/identity-checked/verification.json',
                  'earlier_retained_verification_receipt': 'evidence/verification.json',
                  'actual_submission_history': 'unknown; no journal submission by preparation worker',
                  'format': format_receipt,
                  'runtime_inputs': [{'path': p, 'sha256': digest(REPO / p)} for p in runtime_inputs],
                  'environment_versions': dict(sorted(installed.items())),
                  'files': inventory,
                  'inventory_exclusions': ['manifest.json itself', '.scratch/', '.private/',
                                           '__pycache__/', 'aux/log/out/pyc build files'],
                  'verification_scope': 'retained artifact hashes/static format; no new runtime,model,or service call'}
        (PACKET / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
    else:
        manifest = json.loads((PACKET / 'manifest.json').read_text())
        assert manifest['format'] == format_receipt, 'format receipt stale'
        known = {r['path'] for r in manifest['files']}
        assert known == {str(p.relative_to(PACKET)) for p in files()}, 'inventory changed'
        for record in manifest['files']:
            assert digest(PACKET / record['path']) == record['sha256'], record['path']
    print(json.dumps({'result': 'PASS', 'format': format_receipt,
                      'files_in_packet': len(files()), 'manifest_written': args.write_manifest}, indent=2))


if __name__ == '__main__':
    main()
