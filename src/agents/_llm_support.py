"""Small shared LLM accounting helpers for the four Agent modules."""

from __future__ import annotations

from typing import Any, Dict, List

from src.runtime_lifecycle import failed_lifecycle_patch
from src.state import ResearchState, UsageMetrics
from src.state_compat import canonical_iteration, canonical_usage


LLM_OPERATION_TIMEOUT_SECONDS = 90


def _legacy_attempt_limit_to_retries(max_attempts: int) -> int:
    return max(0, max_attempts - 1)


def _llm_patch_totals(
    _state: ResearchState,
    call_details: List[Dict[str, Any]],
) -> tuple[int, int, int]:
    calls = len(call_details)
    input_tokens = sum(int(item.get("input_tokens") or 0) for item in call_details)
    output_tokens = sum(int(item.get("output_tokens") or 0) for item in call_details)
    return calls, input_tokens, output_tokens


def _usage_from_totals(
    state: ResearchState,
    *,
    llm_calls: int,
    input_tokens: int,
    output_tokens: int,
) -> UsageMetrics:
    usage = canonical_usage(state)
    return UsageMetrics(
        llm_calls=llm_calls,
        tool_calls=usage.tool_calls,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        latency_seconds=usage.latency_seconds,
        estimated_cost=usage.estimated_cost,
    )


def _llm_failure_patch(
    state: ResearchState,
    error: Exception,
    message: str,
    *,
    include_iteration: bool = True,
) -> Dict[str, Any]:
    details = list(getattr(error, "llm_call_details", ()) or ())
    calls, input_tokens, output_tokens = _llm_patch_totals(state, details)
    iteration = canonical_iteration(state)
    patch: Dict[str, Any] = {
        "error": message,
        **({"iteration": iteration + 1} if include_iteration else {}),
        **failed_lifecycle_patch(),
    }
    if details:
        prior_usage = canonical_usage(state)
        patch.update(
            {
                "llm_call_details": state.llm_call_details + details,
                "usage": _usage_from_totals(
                    state,
                    llm_calls=prior_usage.llm_calls + calls,
                    input_tokens=prior_usage.input_tokens + input_tokens,
                    output_tokens=prior_usage.output_tokens + output_tokens,
                ),
            }
        )
    execution_context = getattr(error, "context", None) or getattr(error, "execution_context", None)
    if execution_context is not None:
        patch["execution_context"] = execution_context
    return patch
