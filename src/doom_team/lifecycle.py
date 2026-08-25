"""DEV -> QA -> Reviewer lifecycle implementation."""

from __future__ import annotations

import datetime
from collections.abc import Callable

from doom_team.domain import Candidate, Evidence
from doom_team.evidence import evidence_is_fresh
from doom_team.verdicts import Verdict, VerdictStore


class Lifecycle:
    """Runs the DEV-to-Reviewer validation pipeline for a candidate."""

    def __init__(self, verdict_store: VerdictStore) -> None:
        self.verdict_store = verdict_store

    def run(
        self,
        candidate: Candidate,
        evidence: Evidence,
        qa_checker: Callable[[Candidate, Evidence], bool],
        reviewer_checker: Callable[[Candidate, Evidence], bool],
    ) -> tuple[Verdict, Verdict]:
        """Run QA and Reviewer validation on the candidate.

        The evidence must be fresh (bound to the exact candidate). If not,
        a ValueError is raised. QA and Reviewer are invoked with the candidate
        and evidence; their boolean results are recorded as durable verdicts.
        """
        if not evidence_is_fresh(evidence, candidate):
            raise ValueError("Evidence is not bound to the candidate")

        qa_ok = qa_checker(candidate, evidence)
        qa_verdict = Verdict(
            candidate_id=candidate.id,
            role="QA",
            decision=qa_ok,
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        self.verdict_store.save(qa_verdict)

        reviewer_ok = reviewer_checker(candidate, evidence)
        reviewer_verdict = Verdict(
            candidate_id=candidate.id,
            role="REVIEWER",
            decision=reviewer_ok,
            timestamp=datetime.datetime.now(datetime.timezone.utc),
        )
        self.verdict_store.save(reviewer_verdict)

        return qa_verdict, reviewer_verdict
