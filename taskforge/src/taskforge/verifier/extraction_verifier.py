"""Concrete verifier for invoice-field extraction submissions.

Implements a three-stage fail-fast scoring pipeline:

1. **Schema check** (mandatory) — verifies all required fields exist with the
   correct types.  Fail → score 0.0, ``passed=False``, stop.
2. **Ground-truth check (70% weight)** — compares scalar fields
   (``vendor_name``, ``invoice_number``, ``invoice_date``, ``total_amount``,
   ``currency``) against the authoritative planted answers.
3. **LLM-judge on line_items (30% weight)** — string matching is too brittle
   for line-item descriptions.  The LLM compares the extracted line items
   against the ground truth and returns a score in ``[0, 1]``.

Only the :class:`ExtractionVerifier` ships for the hackathon; additional task
types can be added post-hackathon by subclassing
:class:`~taskforge.verifier.base.Verifier`.
"""
from __future__ import annotations

import json
import math
import os
import time

from dotenv import load_dotenv
from groq import Groq
from groq import RateLimitError as _GroqRateLimitError

from taskforge.models import Submission, VerdictLog
from taskforge.verifier.base import Verifier

_MODEL = "llama-3.3-70b-versatile"

# Required fields and their expected Python types
_REQUIRED_SCALAR_FIELDS: dict[str, type] = {
    "vendor_name": str,
    "invoice_number": str,
    "invoice_date": str,
    "total_amount": (int, float),  # type: ignore[assignment]
    "currency": str,
}

_LLM_JUDGE_PROMPT = """\
You are a strict invoice-data accuracy judge.

Ground-truth line items (authoritative):
{ground_truth_items}

Extracted line items (to evaluate):
{extracted_items}

Score the extracted line items on a scale from 0.0 to 1.0:
- 1.0 = every item matches in description, quantity, and unit price.
- Deduct 0.25 per item that is missing, extra, or has a wrong quantity or price.
- Description match is fuzzy — minor capitalisation/punctuation differences are OK.
- Respond with ONLY a decimal number between 0.0 and 1.0. No other text.
"""


class ExtractionVerifier(Verifier):
    """Invoice-field extraction verifier (the only concrete verifier).

    Attributes:
        pass_threshold: Minimum ground-truth score to pass.  Submissions below
            this threshold receive ``passed=False`` regardless of the LLM judge.
    """

    def __init__(self, pass_threshold: float = 0.5) -> None:
        """Create an :class:`ExtractionVerifier`.

        Args:
            pass_threshold: Ground-truth score at or above which the submission
                is considered passing.  Defaults to ``0.5``.
        """
        self.pass_threshold = pass_threshold

    # ── Schema check ────────────────────────────────────────────────────────

    def _schema_check(self, payload: dict) -> str | None:
        """Return an error string if the payload fails the schema check, else None.

        Args:
            payload: Extracted output from the submission.

        Returns:
            A human-readable failure reason, or ``None`` if the schema is valid.
        """
        for field, expected_type in _REQUIRED_SCALAR_FIELDS.items():
            if field not in payload:
                return f"Missing required field: '{field}'"
            if not isinstance(payload[field], expected_type):
                return (
                    f"Field '{field}' has wrong type: "
                    f"expected {expected_type}, got {type(payload[field]).__name__}"
                )
        if "line_items" not in payload or not isinstance(payload["line_items"], list):
            return "Missing or invalid 'line_items' field (must be a list)"
        for i, item in enumerate(payload["line_items"]):
            for sub_field in ("description", "quantity", "unit_price"):
                if sub_field not in item:
                    return f"line_items[{i}] missing '{sub_field}'"
        if payload.get("invoice_date"):
            # Basic date format check (YYYY-MM-DD)
            parts = str(payload["invoice_date"]).split("-")
            if len(parts) != 3 or not all(p.isdigit() for p in parts):
                return f"invoice_date '{payload['invoice_date']}' is not in YYYY-MM-DD format"
        return None

    # ── Ground-truth check (70%) ─────────────────────────────────────────────

    def _ground_truth_score(
        self,
        payload: dict,
        ground_truth: dict,
    ) -> tuple[float, str]:
        """Compare scalar fields against planted answers.

        Args:
            payload: Extracted submission output.
            ground_truth: Authoritative answer dict.

        Returns:
            A tuple of ``(score, reason)`` where ``score`` is in ``[0.0, 1.0]``
            and ``reason`` describes any mismatches found.
        """
        fields = ["vendor_name", "invoice_number", "invoice_date", "total_amount", "currency"]
        mismatches: list[str] = []

        for field in fields:
            expected = ground_truth.get(field)
            actual = payload.get(field)
            if isinstance(expected, float) and isinstance(actual, (int, float)):
                # Numeric comparison — allow 1% tolerance for rounding
                if not math.isclose(float(actual), float(expected), rel_tol=0.01):
                    mismatches.append(f"{field}: expected {expected!r}, got {actual!r}")
            elif isinstance(expected, str) and isinstance(actual, str):
                if expected.strip().lower() != actual.strip().lower():
                    mismatches.append(f"{field}: expected {expected!r}, got {actual!r}")
            elif expected != actual:
                mismatches.append(f"{field}: expected {expected!r}, got {actual!r}")

        score = max(0.0, (len(fields) - len(mismatches)) / len(fields))
        reason = (
            "Ground-truth fields all correct."
            if not mismatches
            else "Mismatches: " + "; ".join(mismatches)
        )
        return score, reason

    # ── LLM judge (30%) ──────────────────────────────────────────────────────

    def _llm_judge_score(
        self,
        payload: dict,
        ground_truth: dict,
    ) -> tuple[float, str | None]:
        """Ask the LLM to score the extracted line items.

        Args:
            payload: Extracted submission output (needs ``line_items``).
            ground_truth: Authoritative answer dict (needs ``line_items``).

        Returns:
            Tuple of ``(score, error_msg)`` where ``score`` is in ``[0.0, 1.0]``
            and ``error_msg`` is ``None`` on success or a description of the
            failure.  Score is ``0.0`` on any error.
        """
        _JUDGE_MAX_RETRIES = 3
        _JUDGE_RETRY_DELAY = 6.0

        load_dotenv()
        client = Groq(api_key=os.environ["GROQ_API_KEY"])

        gt_items = json.dumps(ground_truth.get("line_items", []), indent=2)
        ex_items = json.dumps(payload.get("line_items", []), indent=2)

        prompt = _LLM_JUDGE_PROMPT.format(
            ground_truth_items=gt_items,
            extracted_items=ex_items,
        )
        last_exc: Exception | None = None
        for attempt in range(1, _JUDGE_MAX_RETRIES + 1):
            try:
                response = client.chat.completions.create(
                    model=_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                )
                raw = (response.choices[0].message.content or "0.0").strip()
                score = float(raw)
                return max(0.0, min(1.0, score)), None
            except _GroqRateLimitError as exc:
                last_exc = exc
                print(
                    f"  \033[33m⚠ LLM judge rate-limited "
                    f"(attempt {attempt}/{_JUDGE_MAX_RETRIES}) — retrying in "
                    f"{int(_JUDGE_RETRY_DELAY * attempt)}s\033[0m"
                )
                if attempt < _JUDGE_MAX_RETRIES:
                    time.sleep(_JUDGE_RETRY_DELAY * attempt)
            except Exception as exc:  # noqa: BLE001
                print(f"  \033[31m✗ LLM judge error: {exc}\033[0m")
                return 0.0, f"LLM judge error: {exc}"
        print(f"  \033[31m✗ LLM judge rate-limited after {_JUDGE_MAX_RETRIES} attempts\033[0m")
        return 0.0, f"LLM judge rate-limited after {_JUDGE_MAX_RETRIES} attempts: {last_exc}"

    # ── Public verify method ─────────────────────────────────────────────────

    def verify(self, task_spec: dict, submission: Submission) -> VerdictLog:
        """Score the submission through the fail-fast three-stage pipeline.

        Args:
            task_spec: Must contain ``"ground_truth"`` (the authoritative answer
                dict) and optionally ``"invoice_text"`` (not used directly here).
            submission: Worker's :class:`~taskforge.models.Submission`.

        Returns:
            :class:`~taskforge.models.VerdictLog` with score and pass/fail.
        """
        payload = dict(submission.output_payload)
        # Strip the anti-spoofing account field before scoring
        payload.pop("_worker_account_id", None)
        ground_truth: dict = task_spec["ground_truth"]

        # Stage 1 — schema check
        schema_error = self._schema_check(payload)
        if schema_error:
            return VerdictLog(
                job_id=submission.job_id,
                agent_id=submission.agent_id,
                score=0.0,
                passed=False,
                reason=f"Schema check failed: {schema_error}",
                ts=time.time(),
            )

        # Stage 2 — ground-truth check (70% weight)
        gt_score, gt_reason = self._ground_truth_score(payload, ground_truth)

        # Stage 3 — LLM judge on line_items (30% weight)
        llm_score, llm_error = self._llm_judge_score(payload, ground_truth)

        total_score = 0.7 * gt_score + 0.3 * llm_score
        passed = gt_score >= self.pass_threshold

        llm_note = f" ⚠ LLM judge failed ({llm_error}), scored 0.0" if llm_error else ""
        reason = (
            f"{gt_reason}  "
            f"[GT={gt_score:.2f} LLM={llm_score:.2f} total={total_score:.2f}]"
            f"{llm_note}"
        )
        return VerdictLog(
            job_id=submission.job_id,
            agent_id=submission.agent_id,
            score=total_score,
            passed=passed,
            reason=reason,
            ts=time.time(),
        )
