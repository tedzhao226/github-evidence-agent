from cloudbees_agent.eval import evaluate_cases
from cloudbees_agent.models import EvalCase, EvidenceItem, EvidenceResult, ToolName


def test_eval_scores_first_tool_fallback_grounding_and_trace_completeness(tmp_path):
    cases = [
        EvalCase(
            name="fallback from empty issues",
            repo="pydantic/pydantic-ai",
            question="How is tracing supported?",
            mocked_outputs={
                ToolName.ISSUES: EvidenceResult(
                    tool=ToolName.ISSUES,
                    query="tracing",
                    summary="",
                    items=[],
                ),
                ToolName.README: EvidenceResult(
                    tool=ToolName.README,
                    query="tracing",
                    summary="Logfire tracing is documented.",
                    items=[
                        EvidenceItem(
                            kind="readme",
                            title="README",
                            url="https://github.com/pydantic/pydantic-ai",
                            excerpt="Logfire tracing is documented.",
                        )
                    ],
                ),
            },
            expected_first_tool=ToolName.ISSUES,
            expected_fallback=ToolName.README,
            required_evidence_fields=["url", "excerpt"],
        )
    ]

    summary = evaluate_cases(cases, trace_dir=tmp_path)

    assert summary.total == 1
    assert summary.first_tool_accuracy == 1.0
    assert summary.fallback_success_rate == 1.0
    assert summary.grounded_answer_rate == 1.0
    assert summary.trace_completeness == 1.0
