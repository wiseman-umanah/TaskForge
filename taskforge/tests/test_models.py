"""Unit tests for taskforge.models — to_json serialisation."""
from __future__ import annotations

import json

import pytest

from taskforge.models import (
    Job,
    PaymentRecord,
    Submission,
    VerdictLog,
    to_json,
)


def _sample_job() -> Job:
    return Job(
        job_id="j1",
        description="Extract invoice fields",
        output_schema={"vendor_name": "string"},
        bounty_amount=0.1,
        currency="HBAR",
        deadline_ts=1_700_000_000.0,
    )


def _sample_submission() -> Submission:
    return Submission(
        job_id="j1",
        agent_id="agent_a",
        output_payload={"vendor_name": "Acme Ltd."},
        submitted_ts=1_700_000_001.0,
    )


def _sample_verdict() -> VerdictLog:
    return VerdictLog(
        job_id="j1",
        agent_id="agent_a",
        score=0.85,
        passed=True,
        reason="All fields correct.",
        ts=1_700_000_002.0,
    )


def _sample_payment() -> PaymentRecord:
    return PaymentRecord(
        job_id="j1",
        winner_agent_id="agent_a",
        tx_hash="0.0.1234@1700000000.123",
        amount=0.1,
        hcs_message_id="0.0.1234@1700000001.456",
    )


# ── to_json ──────────────────────────────────────────────────────────────────

class TestToJson:
    def test_job_round_trips(self) -> None:
        job = _sample_job()
        parsed = json.loads(to_json(job))
        assert parsed["job_id"] == "j1"
        assert parsed["bounty_amount"] == 0.1
        assert parsed["currency"] == "HBAR"

    def test_injects_type_key(self) -> None:
        for obj, expected in [
            (_sample_job(), "Job"),
            (_sample_submission(), "Submission"),
            (_sample_verdict(), "VerdictLog"),
            (_sample_payment(), "PaymentRecord"),
        ]:
            parsed = json.loads(to_json(obj))
            assert parsed["_type"] == expected, f"Expected _type={expected!r}"

    def test_submission_payload_preserved(self) -> None:
        sub = _sample_submission()
        parsed = json.loads(to_json(sub))
        assert parsed["output_payload"] == {"vendor_name": "Acme Ltd."}

    def test_verdict_score_preserved(self) -> None:
        v = _sample_verdict()
        parsed = json.loads(to_json(v))
        assert parsed["score"] == pytest.approx(0.85)
        assert parsed["passed"] is True

    def test_output_is_valid_json_string(self) -> None:
        # to_json must never raise for any of the four dataclass types
        for obj in [
            _sample_job(),
            _sample_submission(),
            _sample_verdict(),
            _sample_payment(),
        ]:
            result = to_json(obj)
            assert isinstance(result, str)
            json.loads(result)  # must not raise

    def test_nested_dict_in_output_schema(self) -> None:
        job = Job(
            job_id="j2",
            description="...",
            output_schema={"line_items": [{"description": "string"}]},
            bounty_amount=0.1,
            currency="HBAR",
            deadline_ts=0.0,
        )
        parsed = json.loads(to_json(job))
        assert parsed["output_schema"]["line_items"][0]["description"] == "string"
