"""Abstract verifier interface for TaskForge.

Defines the single method all concrete verifiers must implement.  Only one
concrete verifier ships in the hackathon submission
(:class:`~taskforge.verifier.extraction_verifier.ExtractionVerifier`).

The narrow interface is intentional — the broadcaster and settlement layers
never need to know *how* a submission is scored, only whether it passed and
what score it received.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from taskforge.models import Submission, VerdictLog


class Verifier(ABC):
    """Abstract base class for TaskForge verifiers.

    Subclass this and implement :meth:`verify` to add a new task type.  Only
    one concrete subclass ships (invoice-field extraction); a second was
    explicitly out of scope (PRD §2a).
    """

    @abstractmethod
    def verify(self, task_spec: dict, submission: Submission) -> VerdictLog:
        """Score a worker's submission against the task specification.

        Args:
            task_spec: The task specification dict.  For the extraction verifier
                this must contain ``"ground_truth"`` (the authoritative answers)
                and ``"invoice_text"`` (the source document).
            submission: The worker's :class:`~taskforge.models.Submission`.

        Returns:
            A :class:`~taskforge.models.VerdictLog` capturing the score, pass/
            fail outcome, and a human-readable reason string.
        """
