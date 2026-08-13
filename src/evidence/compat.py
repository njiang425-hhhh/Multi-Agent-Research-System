"""Compatibility adapters for the incremental ResearchState migration."""

from hashlib import sha256
from typing import Sequence

from src.state import Finding


LEGACY_PROJECTION_SUMMARY = (
    "Legacy compatibility projection from key_findings; no source-grounded Evidence is attached."
)


def key_findings_to_findings(key_findings: Sequence[str]) -> list[Finding]:
    """Project legacy synthesis strings into deterministic, unverified Findings.

    The original statement and sequence are retained exactly.  The zero-based
    position is part of each identity so repeated statements remain distinct
    while the same ordered input always receives the same identifiers.
    """
    findings: list[Finding] = []
    for position, statement in enumerate(key_findings):
        identity = f"legacy-key-finding\x1f{position}\x1f{statement}"
        digest = sha256(identity.encode("utf-8")).hexdigest()[:24]
        findings.append(
            Finding(
                finding_id=f"finding:legacy:{digest}",
                statement=statement,
                evidence_refs=[],
                contradictory_evidence_refs=[],
                confidence=None,
                reasoning_summary=LEGACY_PROJECTION_SUMMARY,
                status="unverified",
            )
        )
    return findings
