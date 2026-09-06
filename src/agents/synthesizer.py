"""Synthesizer node for source-linked canonical Findings."""

from __future__ import annotations

from hashlib import sha256
import json
import logging
import re
from typing import Any, Callable, Dict, Optional

from langchain_core.language_models import BaseChatModel

from src.agents._llm_support import LLM_OPERATION_TIMEOUT_SECONDS, _legacy_attempt_limit_to_retries, _llm_failure_patch, _llm_patch_totals, _usage_from_totals
from src.callbacks import emit_error, emit_synthesis_complete, emit_synthesis_start
from src.config import config
from src.evidence.config import EvidenceRuntimeConfig
from src.evidence.sidecar import EvidenceSidecarService
from src.llm.factory import get_llm
from src.llm_execution import execute_llm_operation
from src.prompts import SYNTHESIZER_SYSTEM_PROMPT, SYNTHESIZER_USER_TEMPLATE
from src.runtime_lifecycle import failed_lifecycle_patch
from src.state import Document, EvidenceDiagnostics, Finding, ResearchState
from src.state_compat import canonical_documents, canonical_iteration, canonical_plan as _research_plan, canonical_query as _research_query, canonical_usage
from src.utils.tools import get_research_tools


logger = logging.getLogger(__name__)


def _create_agent(*args, **kwargs):
    """Resolve the public injection seam at call time for existing tests."""
    from src import agents
    return agents.create_agent(*args, **kwargs)


class ResearchSynthesizer:
    """Turn canonical Documents into source-linked research Findings."""

    def __init__(self, llm: Optional[BaseChatModel] = None, max_retries: int = 3, evidence_config: Optional[EvidenceRuntimeConfig] = None, sidecar_factory: Optional[Callable[[], EvidenceSidecarService]] = None):
        self.llm = llm or get_llm(temperature=0.3, model_override=config.summarization_model)
        self.tools = get_research_tools(agent_type="synthesis")
        self.max_retries = max_retries
        self.evidence_config = evidence_config or EvidenceRuntimeConfig.from_environment()
        self.sidecar_factory = sidecar_factory or (lambda: EvidenceSidecarService.create_production(config=self.evidence_config))

    async def synthesize(self, state: ResearchState) -> Dict[str, Any]:
        """Generate source-linked Findings from the canonical Document sequence."""
        topic = _research_query(state)
        documents = self._citeable_documents(canonical_documents(state))[:20]
        logger.info("正在从 %s 个 canonical Documents 中综合研究发现", len(documents))
        if not documents:
            await emit_error("没有可供综合的有效文档")
            return {"error": "没有可供综合的有效文档", **failed_lifecycle_patch()}

        await emit_synthesis_start(len(documents))
        agent_graph = _create_agent(self.llm, self.tools, system_prompt=SYNTHESIZER_SYSTEM_PROMPT)
        input_message = SYNTHESIZER_USER_TEMPLATE.format(topic=topic, results=self._format_documents_text(documents)) + (
            "\n\n请仅返回 JSON 数组："
            '[{"claim": "可验证的发现", "source_numbers": [1, 2]}]。'
            "source_numbers 必须引用上方 Documents 的方括号编号；每条事实发现至少一个编号。"
        )

        def output_text(result: dict[str, Any]) -> str:
            messages = result.get("messages", [])
            if not messages:
                return ""
            last_msg = messages[-1]
            return str(last_msg.content if hasattr(last_msg, "content") else last_msg)

        try:
            execution = await execute_llm_operation(
                lambda: agent_graph.ainvoke({"messages": [{"role": "user", "content": input_message}]}),
                agent="ResearchSynthesizer", operation_name="autonomous_synthesis", model=config.summarization_model,
                input_text=input_message, local_timeout_seconds=LLM_OPERATION_TIMEOUT_SECONDS,
                max_retries=_legacy_attempt_limit_to_retries(self.max_retries), context=state.execution_context,
                output_text=output_text,
            )
        except Exception as error:
            logger.error("综合调用失败：%s", error)
            await emit_error(f"综合失败：{error}")
            return _llm_failure_patch(state, error, f"综合失败：{error}")

        findings = self._extract_findings(output_text(execution.value), documents)
        calls, input_tokens, output_tokens = _llm_patch_totals(state, execution.call_details)
        logger.info("已提取 %s 条 source-linked 发现", len(findings))
        await emit_synthesis_complete(len(findings))
        success_patch: Dict[str, Any] = {
            "findings": findings, "current_stage": "reporting",
            "iteration": canonical_iteration(state) + 1,
            "llm_call_details": state.llm_call_details + execution.call_details,
            "usage": _usage_from_totals(state, llm_calls=canonical_usage(state).llm_calls + calls, input_tokens=canonical_usage(state).input_tokens + input_tokens, output_tokens=canonical_usage(state).output_tokens + output_tokens),
        }
        if execution.context is not None:
            success_patch["execution_context"] = execution.context
        return await self._merge_evidence_sidecar(state, success_patch, documents)

    async def _merge_evidence_sidecar(self, state: ResearchState, success_patch: Dict[str, Any], documents: list[Document]) -> Dict[str, Any]:
        """Attach optional enrichment without changing the Writer input set."""
        plan = _research_plan(state)
        if not self.evidence_config.enabled:
            success_patch["evidence_diagnostics"] = EvidenceDiagnostics(status="disabled", source="p2_documents" if documents else "none")
            return success_patch
        if not success_patch["findings"]:
            success_patch["evidence_diagnostics"] = EvidenceDiagnostics(status="not_run", source="p2_documents" if documents else "none")
            return success_patch
        try:
            sidecar = self.sidecar_factory()
            sidecar_args: dict[str, Any] = {"topic": _research_query(state), "documents": documents, "objectives": plan.objectives if plan else ()}
            execution_context = success_patch.get("execution_context") or state.execution_context
            if execution_context is not None:
                sidecar_args["execution_context"] = execution_context
            sidecar_result = await sidecar.run(**sidecar_args)
        except Exception as exc:
            success_patch["evidence_diagnostics"] = EvidenceDiagnostics(status="failed", source="p2_documents", sidecar_errors=[f"Evidence sidecar integration failed: {exc}"])
            return success_patch

        success_patch["document_analyses"] = sidecar_result.document_analyses
        success_patch["evidence"] = sidecar_result.evidence
        success_patch["evidence_diagnostics"] = sidecar_result.diagnostics
        if sidecar_result.execution_context is not None:
            success_patch["execution_context"] = sidecar_result.execution_context
        # Evidence output is optional enrichment and never replaces the
        # source-linked Findings passed to Writer.
        success_patch["llm_call_details"] = success_patch["llm_call_details"] + sidecar_result.llm_call_details
        usage = success_patch["usage"]
        success_patch["usage"] = usage.model_copy(update={
            "llm_calls": usage.llm_calls + sidecar_result.llm_calls_delta,
            "input_tokens": usage.input_tokens + sidecar_result.input_tokens_delta,
            "output_tokens": usage.output_tokens + sidecar_result.output_tokens_delta,
            "total_tokens": usage.total_tokens + sidecar_result.input_tokens_delta + sidecar_result.output_tokens_delta,
        })
        return success_patch

    @staticmethod
    def _citeable_documents(documents: list[Document]) -> list[Document]:
        return [document for document in documents if document.source_type == "web" and document.status != "invalid_source" and document.document_id and document.uri.startswith(("http://", "https://"))]

    @staticmethod
    def _format_documents_text(documents: list[Document]) -> str:
        blocks: list[str] = []
        for index, document in enumerate(documents, 1):
            credibility = document.credibility or {}
            block = f"[{index}] {document.title}\nURL：{document.uri}\n可信度：{credibility.get('level', 'unknown').upper()}（分数：{credibility.get('score', 'N/A')}/100）\n摘要：{document.snippet}\n"
            if document.content:
                block += f"内容：{document.content[:300]}..."
            blocks.append(block)
        return "\n\n".join(blocks)

    @staticmethod
    def _extract_json_array(output_text: str) -> list[Any]:
        try:
            value = json.loads(output_text.strip())
            return value if isinstance(value, list) else []
        except json.JSONDecodeError:
            match = re.search(r"\[\s*\{.*\}\s*\]", output_text, re.DOTALL)
            if not match:
                return []
            try:
                value = json.loads(match.group(0))
                return value if isinstance(value, list) else []
            except json.JSONDecodeError:
                return []

    def _extract_findings(self, output_text: str, documents: list[Document]) -> list[Finding]:
        """Map LLM source numbers to stable Document IDs, dropping bad links."""
        findings: list[Finding] = []
        for item in self._extract_json_array(output_text)[:15]:
            if not isinstance(item, dict):
                continue
            statement = str(item.get("claim") or item.get("statement") or "").strip()
            if not statement:
                continue
            ids: list[str] = []
            for number in item.get("source_numbers", item.get("sources", [])) or []:
                try:
                    index = int(number) - 1
                except (TypeError, ValueError):
                    continue
                if 0 <= index < len(documents) and documents[index].document_id not in ids:
                    ids.append(documents[index].document_id)
            if not ids:
                continue
            identity = f"{statement}\x1f{'|'.join(ids)}"
            findings.append(Finding(finding_id=f"finding:{sha256(identity.encode('utf-8')).hexdigest()[:24]}", statement=statement, source_document_ids=ids, confidence=None, status="unverified"))
        return findings


__all__ = ["ResearchSynthesizer"]
