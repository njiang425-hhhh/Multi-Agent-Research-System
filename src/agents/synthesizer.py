"""Synthesizer node and its optional Evidence sidecar integration."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Dict, List, Optional

from langchain_core.language_models import BaseChatModel

from src.agents._llm_support import (
    LLM_OPERATION_TIMEOUT_SECONDS,
    _legacy_attempt_limit_to_retries,
    _llm_failure_patch,
    _llm_patch_totals,
    _usage_from_legacy_totals,
)
from src.callbacks import emit_error, emit_synthesis_complete, emit_synthesis_start
from src.config import config
from src.evidence.compat import key_findings_to_findings
from src.evidence.config import EvidenceRuntimeConfig
from src.evidence.sidecar import EvidenceSidecarResult, EvidenceSidecarService
from src.llm.factory import get_llm
from src.llm_execution import execute_llm_operation
from src.prompts import SYNTHESIZER_SYSTEM_PROMPT, SYNTHESIZER_USER_TEMPLATE
from src.runtime_lifecycle import failed_lifecycle_patch
from src.state import EvidenceDiagnostics, Finding, ResearchState
from src.state_compat import canonical_iteration, canonical_plan as _research_plan, canonical_query as _research_query, canonical_usage
from src.utils.tools import get_research_tools


logger = logging.getLogger(__name__)


def _create_agent(*args, **kwargs):
    """Resolve the public injection seam at call time for existing tests."""

    from src import agents

    return agents.create_agent(*args, **kwargs)


class ResearchSynthesizer:
    """负责综合研究发现的自主代理。"""

    def __init__(
        self,
        llm: Optional[BaseChatModel] = None,
        max_retries: int = 3,
        evidence_config: Optional[EvidenceRuntimeConfig] = None,
        sidecar_factory: Optional[Callable[[], EvidenceSidecarService]] = None,
    ):
        self.llm = llm or get_llm(temperature=0.3, model_override=config.summarization_model)
        self.tools = get_research_tools(agent_type="synthesis")
        self.max_retries = max_retries
        self.evidence_config = evidence_config or EvidenceRuntimeConfig.from_environment()
        self.sidecar_factory = sidecar_factory or (
            lambda: EvidenceSidecarService.create_production(config=self.evidence_config)
        )

    async def synthesize(self, state: ResearchState) -> Dict[str, Any]:
        """使用工具和推理自主综合关键发现。

        返回将由 LangGraph 合并到状态中的关键发现字典。
        """
        topic = _research_query(state)
        logger.info(f"正在从 {len(state.search_results)} 个结果中综合研究发现")

        if not state.search_results:
            await emit_error("没有可供综合的搜索结果")
            return {"error": "没有可供综合的搜索结果", **failed_lifecycle_patch()}

        await emit_synthesis_start(len(state.search_results))

        agent_graph = _create_agent(
            self.llm,
            self.tools,
            system_prompt=SYNTHESIZER_SYSTEM_PROMPT
        )

        results_to_use = state.search_results[:20]
        credibility_scores_to_use = state.credibility_scores[:20] if state.credibility_scores else []
        results_text = self._format_results_text(results_to_use, credibility_scores_to_use)
        input_message = SYNTHESIZER_USER_TEMPLATE.format(topic=topic, results=results_text)

        def output_text(result: dict[str, Any]) -> str:
            messages = result.get("messages", [])
            if not messages:
                return ""
            last_msg = messages[-1]
            return str(last_msg.content if hasattr(last_msg, "content") else last_msg)

        try:
            execution = await execute_llm_operation(
                lambda: agent_graph.ainvoke({"messages": [{"role": "user", "content": input_message}]}),
                agent="ResearchSynthesizer",
                operation_name="autonomous_synthesis",
                model=config.summarization_model,
                input_text=input_message,
                local_timeout_seconds=LLM_OPERATION_TIMEOUT_SECONDS,
                max_retries=_legacy_attempt_limit_to_retries(self.max_retries),
                context=state.execution_context,
                output_text=output_text,
            )
        except Exception as error:
            logger.error(f"综合调用失败：{error}")
            await emit_error(f"综合失败：{error}")
            return _llm_failure_patch(state, error, f"综合失败：{error}")

        text = output_text(execution.value)
        key_findings = self._extract_findings(text, state.search_results)
        findings = key_findings_to_findings(key_findings)
        calls, input_tokens, output_tokens = _llm_patch_totals(state, execution.call_details)
        logger.info(f"已提取 {len(key_findings)} 条关键发现")
        await emit_synthesis_complete(len(key_findings))
        success_patch: Dict[str, Any] = {
            "key_findings": key_findings,
            "findings": findings,
            "current_stage": "reporting",
            "iterations": state.iterations + 1,
            "iteration": state.iteration + 1,
            "llm_calls": state.llm_calls + calls,
            "total_input_tokens": state.total_input_tokens + input_tokens,
            "total_output_tokens": state.total_output_tokens + output_tokens,
            "llm_call_details": state.llm_call_details + execution.call_details,
            "usage": _usage_from_legacy_totals(
                state,
                llm_calls=state.llm_calls + calls,
                total_input_tokens=state.total_input_tokens + input_tokens,
                total_output_tokens=state.total_output_tokens + output_tokens,
            ),
        }
        if execution.context is not None:
            success_patch["execution_context"] = execution.context
        return await self._merge_evidence_sidecar(state, success_patch)

    async def _merge_evidence_sidecar(
        self,
        state: ResearchState,
        success_patch: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Attach optional Evidence output without affecting legacy synthesis success."""
        plan = _research_plan(state)
        if not self.evidence_config.enabled:
            success_patch["evidence_diagnostics"] = EvidenceDiagnostics(
                status="disabled",
                source=self._evidence_source(state),
            )
            return success_patch

        if not success_patch["key_findings"]:
            success_patch["evidence_diagnostics"] = EvidenceDiagnostics(
                status="not_run",
                source=self._evidence_source(state),
            )
            return success_patch

        try:
            sidecar = self.sidecar_factory()
            sidecar_args = dict(
                topic=_research_query(state),
                documents=state.documents,
                search_results=state.search_results,
                objectives=plan.objectives if plan else (),
            )
            execution_context = success_patch.get("execution_context") or state.execution_context
            if execution_context is not None:
                sidecar_args["execution_context"] = execution_context
            sidecar_result = await sidecar.run(**sidecar_args)
        except Exception as exc:
            success_patch["evidence_diagnostics"] = EvidenceDiagnostics(
                status="failed",
                source=self._evidence_source(state),
                sidecar_errors=[f"Evidence sidecar integration failed: {exc}"],
            )
            return success_patch

        success_patch["document_analyses"] = sidecar_result.document_analyses
        success_patch["evidence"] = sidecar_result.evidence
        success_patch["evidence_diagnostics"] = sidecar_result.diagnostics
        if sidecar_result.execution_context is not None:
            success_patch["execution_context"] = sidecar_result.execution_context
        if sidecar_result.diagnostics.source == "legacy_search_results_backfill":
            success_patch["documents"] = sidecar_result.documents
        if self._can_adopt_evidence_findings(sidecar_result):
            success_patch["findings"] = sidecar_result.findings

        success_patch["llm_calls"] += sidecar_result.llm_calls_delta
        success_patch["total_input_tokens"] += sidecar_result.input_tokens_delta
        success_patch["total_output_tokens"] += sidecar_result.output_tokens_delta
        success_patch["llm_call_details"] = (
            success_patch["llm_call_details"] + sidecar_result.llm_call_details
        )
        success_patch["usage"] = _usage_from_legacy_totals(
            state,
            llm_calls=success_patch["llm_calls"],
            total_input_tokens=success_patch["total_input_tokens"],
            total_output_tokens=success_patch["total_output_tokens"],
        )
        return success_patch

    def _can_adopt_evidence_findings(self, result: EvidenceSidecarResult) -> bool:
        """Accept only fully reference-valid Findings allowed by sidecar diagnostics."""
        diagnostics = result.diagnostics
        partial_allowed = self.evidence_config.analyzer.allow_partial_results
        diagnostics_allow_adoption = (
            diagnostics.aggregation_attempted
            and (diagnostics.aggregation_completed or (diagnostics.aggregation_partial and partial_allowed))
            and (
                diagnostics.analyzer_completed
                or (diagnostics.analyzer_partial and partial_allowed)
            )
        )
        if not diagnostics_allow_adoption or not result.findings:
            return False

        evidence_ids = {item.evidence_id for item in result.evidence}
        for finding in result.findings:
            references = set(finding.evidence_refs) | set(finding.contradictory_evidence_refs)
            if not references or not references.issubset(evidence_ids):
                return False
        return True

    @staticmethod
    def _evidence_source(state: ResearchState) -> str:
        if state.documents:
            return "p2_documents"
        if state.search_results:
            return "legacy_search_results_backfill"
        return "none"

    def _format_results_text(self, results: list, credibility_scores: list) -> str:
        """格式化带可信度信息的搜索结果。"""
        if len(results) != len(credibility_scores):
            return "\n\n".join([
                f"[{i+1}] {r.title}\nURL：{r.url}\n摘要：{r.snippet}\n" +
                (f"内容：{r.content[:300]}..." if r.content else "")
                for i, r in enumerate(results)
            ])

        return "\n\n".join([
            f"[{i+1}] {r.title}\n"
            f"URL：{r.url}\n"
            f"可信度：{cred.get('level', 'unknown').upper()}（分数：{cred.get('score', 'N/A')}/100）- {', '.join(cred.get('factors', []))}\n"
            f"摘要：{r.snippet}\n" +
            (f"内容：{r.content[:300]}..." if r.content else "")
            for i, (r, cred) in enumerate(zip(results, credibility_scores))
        ])

    def _extract_findings(self, output_text: str, search_results: list) -> List[str]:
        """从综合输出中提取关键发现。"""
        json_match = re.search(r'\[(.*?)\]', output_text, re.DOTALL)

        key_findings = []
        if json_match:
            try:
                findings = json.loads(json_match.group(0))
                if isinstance(findings, list):
                    key_findings = [str(f) for f in findings]
                else:
                    key_findings = [str(findings)]
            except json.JSONDecodeError:
                pass

        if not key_findings:
            lines = output_text.split('\n')
            for line in lines:
                line = line.strip().lstrip('-').lstrip('*').lstrip('>').strip()
                line = re.sub(r'^\d+\.\s*', '', line)
                if len(line) > 30 and not line.startswith('[') and not line.startswith(']'):
                    key_findings.append(line)
            key_findings = key_findings[:15]

        if not key_findings and search_results:
            logger.warning("代理没有生成发现，将根据结果创建基础发现")
            key_findings = [
                f"{r.title}: {r.snippet[:100]}..."
                for r in search_results[:10]
                if r.snippet
            ]

        return key_findings


# =============================================================================
# 报告撰写代理
# =============================================================================



__all__ = ["ResearchSynthesizer"]
