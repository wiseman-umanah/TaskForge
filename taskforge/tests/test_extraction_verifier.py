"""Unit tests for taskforge.verifier.extraction_verifier.

All LLM calls are mocked out — tests are fast and offline.
"""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from taskforge.broadcaster.broadcast_job import GROUND_TRUTH
from taskforge.models import Submission
from taskforge.verifier.extraction_verifier import ExtractionVerifier

_GT = GROUND_TRUTH  # shorthand


def _make_submission(payload: dict, agent_id: str = "agent_b") -> Submission:
    return Submission(
        job_id="j1",
        agent_id=agent_id,
        output_payload=payload,
        submitted_ts=time.time(),
    )


def _task_spec(gt: dict | None = None) -> dict:
    return {"ground_truth": gt or _GT}


def _verifier(threshold: float = 0.5) -> ExtractionVerifier:
    return ExtractionVerifier(pass_threshold=threshold)


# ── Schema check ──────────────────────────────────────────────────────────────

class TestSchemaCheck:
    def test_empty_payload_fails(self) -> None:
        v = _verifier()
        result = v.verify(_task_spec(), _make_submission({}))
        assert result.score == 0.0
        assert result.passed is False
        assert "Schema check failed" in result.reason

    def test_missing_one_field_fails(self) -> None:
        payload = {
            "vendor_name": "Acme",
            # invoice_number missing
            "invoice_date": "2024-01-01",
            "total_amount": 100.0,
            "currency": "USD",
            "line_items": [],
        }
        result = _verifier().verify(_task_spec(), _make_submission(payload))
        assert result.score == 0.0
        assert "invoice_number" in result.reason

    def test_wrong_type_total_amount_fails(self) -> None:
        payload = {
            "vendor_name": "Acme",
            "invoice_number": "INV-001",
            "invoice_date": "2024-01-01",
            "total_amount": "100.00",   # string, not number
            "currency": "USD",
            "line_items": [],
        }
        result = _verifier().verify(_task_spec(), _make_submission(payload))
        assert result.score == 0.0
        assert "total_amount" in result.reason

    def test_bad_date_format_fails(self) -> None:
        payload = {
            "vendor_name": "Acme",
            "invoice_number": "INV-001",
            "invoice_date": "15/11/2024",   # wrong format
            "total_amount": 100.0,
            "currency": "USD",
            "line_items": [],
        }
        result = _verifier().verify(_task_spec(), _make_submission(payload))
        assert result.score == 0.0
        assert "YYYY-MM-DD" in result.reason

    def test_missing_line_item_subfield_fails(self) -> None:
        payload = {
            "vendor_name": "Acme",
            "invoice_number": "INV-001",
            "invoice_date": "2024-01-01",
            "total_amount": 100.0,
            "currency": "USD",
            "line_items": [{"description": "Widget"}],  # no quantity or unit_price
        }
        result = _verifier().verify(_task_spec(), _make_submission(payload))
        assert result.score == 0.0


# ── Ground-truth scoring ──────────────────────────────────────────────────────

class TestGroundTruthScore:
    """Ground-truth scoring tested in isolation by monkeypatching the LLM judge."""

    def _verify_no_llm(
        self,
        payload: dict,
        llm_score: float = 1.0,
    ) -> float:
        """Run verify with a fixed LLM score so we isolate GT behaviour."""
        v = _verifier()
        with patch.object(v, "_llm_judge_score", return_value=(llm_score, None)):
            return v.verify(_task_spec(), _make_submission(payload)).score

    def _perfect_payload(self) -> dict:
        return {
            "vendor_name": "Meridian Cloud Solutions Ltd.",
            "invoice_number": "MCL-2024-0391",
            "invoice_date": "2024-11-15",
            "total_amount": 1866.0,
            "currency": "GBP",
            "line_items": [
                {"description": "Cloud Compute (t3.xlarge, 30 days)", "quantity": 1, "unit_price": 840.0},
                {"description": "Managed PostgreSQL (db.r5.large)", "quantity": 1, "unit_price": 320.0},
                {"description": "Egress Bandwidth (2.4 TB @ £0.05/GB)", "quantity": 2400, "unit_price": 0.05},
                {"description": "Support Package — Enterprise Tier", "quantity": 1, "unit_price": 275.0},
            ],
        }

    def test_perfect_score_is_1(self) -> None:
        score = self._verify_no_llm(self._perfect_payload(), llm_score=1.0)
        assert score == pytest.approx(1.0)

    def test_one_mismatch_reduces_score(self) -> None:
        payload = self._perfect_payload()
        payload["vendor_name"] = "Wrong Name"
        score = self._verify_no_llm(payload, llm_score=1.0)
        # 4/5 GT fields correct → GT=0.8 → 0.7*0.8 + 0.3*1.0 = 0.86
        assert score == pytest.approx(0.86)

    def test_total_uses_1pct_tolerance(self) -> None:
        payload = self._perfect_payload()
        payload["total_amount"] = 1866.01   # within 1% of 1866.00
        score = self._verify_no_llm(payload, llm_score=1.0)
        assert score == pytest.approx(1.0)

    def test_total_outside_tolerance_penalised(self) -> None:
        payload = self._perfect_payload()
        payload["total_amount"] = 1555.0   # subtotal, not total
        score = self._verify_no_llm(payload, llm_score=1.0)
        assert score < 1.0

    def test_case_insensitive_vendor_match(self) -> None:
        payload = self._perfect_payload()
        payload["vendor_name"] = "meridian cloud solutions ltd."
        score = self._verify_no_llm(payload, llm_score=1.0)
        assert score == pytest.approx(1.0)

    def test_all_fields_wrong_gives_0_gt(self) -> None:
        payload = self._perfect_payload()
        payload["vendor_name"] = "X"
        payload["invoice_number"] = "X"
        payload["invoice_date"] = "2000-01-01"
        payload["total_amount"] = 1.0
        payload["currency"] = "USD"
        score = self._verify_no_llm(payload, llm_score=0.0)
        assert score == pytest.approx(0.0)

    def test_pass_threshold_gates_passed_field(self) -> None:
        payload = self._perfect_payload()
        # GT score = 4/5 = 0.8 (one field wrong)
        payload["vendor_name"] = "Wrong"

        v_strict = ExtractionVerifier(pass_threshold=0.9)
        v_lenient = ExtractionVerifier(pass_threshold=0.5)

        with patch.object(v_strict, "_llm_judge_score", return_value=(1.0, None)):
            result_strict = v_strict.verify(_task_spec(), _make_submission(payload))
        with patch.object(v_lenient, "_llm_judge_score", return_value=(1.0, None)):
            result_lenient = v_lenient.verify(_task_spec(), _make_submission(payload))

        assert result_strict.passed is False   # GT=0.8 < threshold 0.9
        assert result_lenient.passed is True   # GT=0.8 >= threshold 0.5

    def test_antispoofing_field_stripped_before_scoring(self) -> None:
        """_worker_account_id in payload must not affect score."""
        payload = self._perfect_payload()
        payload["_worker_account_id"] = "0.0.9999"
        score = self._verify_no_llm(payload, llm_score=1.0)
        assert score == pytest.approx(1.0)


# ── Weighting ─────────────────────────────────────────────────────────────────

class TestWeighting:
    def test_70_30_split(self) -> None:
        payload = {
            "vendor_name": "Meridian Cloud Solutions Ltd.",
            "invoice_number": "MCL-2024-0391",
            "invoice_date": "2024-11-15",
            "total_amount": 1866.0,
            "currency": "GBP",
            "line_items": [{"description": "x", "quantity": 1, "unit_price": 1.0}],
        }
        v = _verifier()
        with patch.object(v, "_llm_judge_score", return_value=(0.0, None)):
            result = v.verify(_task_spec(), _make_submission(payload))
        # GT=1.0, LLM=0.0 → 0.7*1.0 + 0.3*0.0 = 0.7
        assert result.score == pytest.approx(0.7)
