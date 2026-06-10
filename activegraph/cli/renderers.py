"""Shared CLI renderers.

Small human-readable sections live here when more than one command needs
the same formatting. Keeping them out of individual command modules avoids
the "two renderers diverged" class of doc and transcript drift.
"""

from __future__ import annotations


def company_name_for_memo(rt, memo) -> str:
    """Resolve a memo's company_id to a human name for CLI prose."""
    cid = (memo.data or {}).get("company_id")
    if cid is None:
        return "<unknown>"
    co = rt.graph.get_object(cid)
    if co is None:
        return cid
    return (co.data or {}).get("name") or cid


def print_memo_section(write, rt, memo) -> None:
    """Render one memo object using the quickstart/operator format."""
    write("-" * 76)
    company = company_name_for_memo(rt, memo)
    write(f" Memo: {company}")
    write("-" * 76)
    write("")
    data = memo.data or {}

    summary = data.get("summary", "")
    if summary:
        write("Summary:")
        for line in _wrap_indented(summary, indent="  ", width=74):
            write(line)
        write("")

    claims = data.get("key_claims") or []
    if claims:
        write("Key claims:")
        for c in claims:
            text = c.get("text") or c.get("claim", "")
            evidence_ids = c.get("evidence_ids") or []
            ev_clause = (
                f" (evidence: {', '.join(evidence_ids)})" if evidence_ids else ""
            )
            write(f"  - {text}{ev_clause}")
        write("")

    contradictions = data.get("open_contradictions") or []
    note = data.get("contradictions_note")
    write("Open contradictions:")
    if contradictions:
        for c in contradictions:
            write(f"  - {c}")
    elif note:
        write(f"  ({note})")
    else:
        write("  (none surfaced for this company)")
    write("")

    risks = data.get("risks") or []
    write("Risks:")
    if risks:
        for r in risks:
            title = r.get("title", "")
            severity = r.get("severity", "")
            related = r.get("related_claim_ids") or []
            sev_clause = f"; severity: {severity}" if severity else ""
            rel_clause = (
                f" (related claims: {', '.join(related)}{sev_clause})"
                if related
                else (f" ({sev_clause.lstrip('; ')})" if severity else "")
            )
            write(f"  - {title}{rel_clause}")
    else:
        write("  (none identified)")
    write("")


def _wrap_indented(text: str, *, indent: str, width: int) -> list[str]:
    """Wrap prose to a width with a leading indent.

    Standalone rather than textwrap so the transcript stays stable across
    minor Python formatting differences.
    """
    out: list[str] = []
    current = indent
    for word in text.split():
        if len(current) + len(word) + 1 > width and current.strip():
            out.append(current.rstrip())
            current = indent + word
        else:
            current = (current + " " + word) if current.strip() else (indent + word)
    if current.strip():
        out.append(current.rstrip())
    return out
