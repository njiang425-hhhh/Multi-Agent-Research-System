"""带依赖注入的研究流程代理节点。"""

import asyncio
from typing import List, Optional, Dict, Any, Protocol
import logging
import time
import json
import re

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser, JsonOutputParser
from langchain_core.language_models import BaseChatModel
from langchain.agents import create_agent

from src.state import ResearchState, ResearchPlan, SearchQuery, ReportSection, SearchResult
from src.llm.factory import get_llm
from src.utils.tools import get_research_tools
from src.config import config
from src.utils.credibility import CredibilityScorer
from src.utils.citations import CitationFormatter
from src.llm_tracker import estimate_tokens
from src.exceptions import PlanningError, SearchError, SynthesisError, ReportGenerationError
from src.search.config import SearchConfig
from src.search.executor import SearchExecutor
from src.prompts import (
    PLANNER_SYSTEM_PROMPT, PLANNER_USER_TEMPLATE,
    SEARCHER_SYSTEM_PROMPT, SEARCHER_USER_TEMPLATE,
    SYNTHESIZER_SYSTEM_PROMPT, SYNTHESIZER_USER_TEMPLATE,
    WRITER_SYSTEM_PROMPT, WRITER_USER_TEMPLATE
)
from src.callbacks import (
    emit_planning_start, emit_planning_complete,
    emit_search_start, emit_search_results, 
    emit_extraction_start, emit_extraction_complete,
    emit_synthesis_start, emit_synthesis_progress, emit_synthesis_complete,
    emit_writing_start, emit_writing_section, emit_writing_complete,
    emit_error
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Searcher stability limits. These are intentionally local to the Searcher so
# the other Agents and the shared Graph behavior remain unchanged.
SEARCHER_AGENT_RECURSION_LIMIT = 12
SEARCHER_AGENT_TIMEOUT_SECONDS = 90


# =============================================================================
# 研究规划代理
# =============================================================================

class ResearchPlanner:
    """负责规划研究策略的自主代理。"""
    
    def __init__(self, llm: Optional[BaseChatModel] = None, max_retries: int = 3):
        self.llm = llm or get_llm(temperature=0.7)
        self.max_retries = max_retries
        
    async def plan(self, state: ResearchState) -> Dict[str, Any]:
        """使用结构化 LLM 输出创建研究计划。
        
        返回将由 LangGraph 合并到状态中的更新字典。
        """
        logger.info(f"正在规划研究：{state.research_topic}")
        
        await emit_planning_start(state.research_topic)
        
        system_prompt = PLANNER_SYSTEM_PROMPT.format(
            max_queries=config.max_search_queries,
            max_sections=config.max_report_sections
        )
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            ("human", PLANNER_USER_TEMPLATE)
        ])
        
        for attempt in range(self.max_retries):
            try:
                start_time = time.time()
                chain = prompt | self.llm | JsonOutputParser()
                
                input_text = f"{state.research_topic} {config.max_search_queries} {config.max_report_sections}"
                input_tokens = estimate_tokens(input_text)
                
                result = await chain.ainvoke({
                    "topic": state.research_topic,
                    "max_queries": config.max_search_queries,
                    "max_sections": config.max_report_sections
                })
                
                duration = time.time() - start_time
                output_tokens = estimate_tokens(str(result))
                
                call_detail = {
                    'agent': 'ResearchPlanner',
                    'operation': 'plan',
                    'model': config.model_name,
                    'input_tokens': input_tokens,
                    'output_tokens': output_tokens,
                    'duration': round(duration, 2),
                    'attempt': attempt + 1
                }
                
                if not all(key in result for key in ["topic", "objectives", "search_queries", "report_outline"]):
                    raise PlanningError("返回的计划结构无效")
                
                if not result["search_queries"]:
                    raise PlanningError("未生成搜索查询")
                
                plan = ResearchPlan(
                    topic=result["topic"],
                    objectives=result["objectives"][:5],
                    search_queries=[
                        SearchQuery(query=sq["query"], purpose=sq["purpose"])
                        for sq in result["search_queries"][:config.max_search_queries]
                    ],
                    report_outline=result["report_outline"][:config.max_report_sections]
                )
                
                logger.info(f"已创建包含 {len(plan.search_queries)} 个查询的计划（上限：{config.max_search_queries}）")
                logger.info(f"报告大纲包含 {len(plan.report_outline)} 个章节（上限：{config.max_report_sections}）")
                
                await emit_planning_complete(len(plan.search_queries), len(plan.report_outline))
                
                return {
                    "plan": plan,
                    "current_stage": "searching",
                    "iterations": state.iterations + 1,
                    "llm_calls": state.llm_calls + 1,
                    "total_input_tokens": state.total_input_tokens + input_tokens,
                    "total_output_tokens": state.total_output_tokens + output_tokens,
                    "llm_call_details": state.llm_call_details + [call_detail]
                }
                
            except Exception as e:
                logger.warning(f"第 {attempt + 1} 次规划尝试失败：{str(e)}")
                if attempt == self.max_retries - 1:
                    logger.error(f"经过 {self.max_retries} 次尝试后规划仍失败")
                    await emit_error(f"规划失败：{str(e)}")
                    return {
                        "error": f"Planning failed: {str(e)}",
                        "iterations": state.iterations + 1
                    }
                else:
                    await asyncio.sleep(2 ** attempt)
        
        return {
            "error": "规划失败：已超过最大重试次数",
            "iterations": state.iterations + 1
        }


# =============================================================================
# 研究搜索代理
# =============================================================================

class ResearchSearcher:
    """负责执行研究搜索的自主代理。"""
    
    def __init__(
        self, 
        llm: Optional[BaseChatModel] = None,
        credibility_scorer: Optional[CredibilityScorer] = None,
        max_retries: int = 1,
        search_config: Optional[SearchConfig] = None,
    ):
        self.llm = llm or get_llm(temperature=0.3)
        self.tools = get_research_tools(agent_type="search")
        self.credibility_scorer = credibility_scorer or CredibilityScorer()
        self.max_retries = max_retries
        self.search_config = search_config or SearchConfig.from_project_config(config)
        self.search_executor = SearchExecutor(search_config=self.search_config)
        
    async def search(self, state: ResearchState) -> Dict[str, Any]:
        """使用工具自主执行研究搜索。
        
        返回将由 LangGraph 合并到状态中的搜索结果字典。
        """
        if not state.plan:
            await emit_error("没有可用的研究计划")
            return {"error": "没有可用的研究计划"}

        if self.search_config.mode == "deterministic_v2":
            return await self._search_with_executor(state)
        
        logger.info(f"自主代理开始研究：已规划 {len(state.plan.search_queries)} 个查询")
        
        total_queries = len(state.plan.search_queries)
        for i, query in enumerate(state.plan.search_queries, 1):
            await emit_search_start(query.query, i, total_queries)
        
        max_searches = min(config.max_search_queries, 3)
        max_results_per_search = min(config.max_search_results_per_query, 3)
        expected_total_results = max_searches * max_results_per_search
        max_extractions = min(max_searches + 1, 4)
        target_sources = min(expected_total_results, max_extractions)
        
        system_prompt = SEARCHER_SYSTEM_PROMPT.format(
            max_searches=max_searches,
            max_results_per_search=max_results_per_search,
            max_extractions=max_extractions,
            expected_total_results=target_sources
        )
        
        agent_graph = create_agent(
            self.llm,
            self.tools,
            system_prompt=system_prompt
        )
        
        for attempt in range(self.max_retries):
            try:
                start_time = time.time()
                
                objectives_text = "\n".join(f"- {obj}" for obj in state.plan.objectives)
                queries_text = "\n".join(
                    f"- {q.query} (Purpose: {q.purpose})" 
                    for q in state.plan.search_queries
                )
                
                input_message = SEARCHER_USER_TEMPLATE.format(
                    topic=state.research_topic,
                    objectives=objectives_text,
                    queries=queries_text,
                    min_sources=target_sources,
                    max_searches=max_searches,
                    max_extractions=max_extractions
                )
                
                input_tokens = estimate_tokens(input_message)
                
                try:
                    result = await asyncio.wait_for(
                        agent_graph.ainvoke(
                            {
                                "messages": [{"role": "user", "content": input_message}]
                            },
                            config={"recursion_limit": SEARCHER_AGENT_RECURSION_LIMIT},
                        ),
                        timeout=SEARCHER_AGENT_TIMEOUT_SECONDS,
                    )
                except asyncio.TimeoutError as exc:
                    raise SearchError(
                        f"搜索代理执行超时（{SEARCHER_AGENT_TIMEOUT_SECONDS} 秒）",
                        details=(
                            "Searcher Agent 超过单次执行时间限制；"
                            f"recursion_limit={SEARCHER_AGENT_RECURSION_LIMIT}"
                        ),
                    ) from exc
                except Exception as exc:
                    if isinstance(exc, SearchError):
                        raise
                    raise SearchError(
                        "搜索代理执行失败",
                        details=str(exc),
                    ) from exc
                
                duration = time.time() - start_time
                
                messages = result.get('messages', [])
                output_text = ""
                if messages:
                    output_text = str(messages[-1].content if hasattr(messages[-1], 'content') else str(messages[-1]))
                
                output_tokens = estimate_tokens(output_text)
                
                search_results = self._extract_results_from_messages(messages)
                
                logger.info(f"自主代理收集了 {len(search_results)} 个结果")
                
                total_extracted_chars = sum(
                    len(r.content) if r.content else 0 
                    for r in search_results
                )
                extracted_count = sum(1 for r in search_results if r.content)
                
                await emit_extraction_complete(extracted_count, total_extracted_chars)
                
                if not search_results:
                    await emit_error("代理没有收集到任何搜索结果")
                    raise SearchError("代理没有收集到任何搜索结果")
            
                scored_results = self.credibility_scorer.score_search_results(search_results)
                
                filtered_scored = [
                    item for item in scored_results
                    if item['credibility']['score'] >= config.min_credibility_score
                ]
                
                credibility_scores = [item['credibility'] for item in filtered_scored]
                sorted_results = [item['result'] for item in filtered_scored]
                
                logger.info(f"已过滤 {len(search_results)} -> {len(sorted_results)} 个结果（最低可信度={config.min_credibility_score}）")
                
                for q in state.plan.search_queries:
                    q.completed = True
                
                call_detail = {
                    'agent': 'ResearchSearcher',
                    'operation': 'autonomous_search',
                    'model': config.model_name,
                    'input_tokens': input_tokens,
                    'output_tokens': output_tokens,
                    'duration': round(duration, 2),
                    'results_count': len(sorted_results),
                    'original_results_count': len(search_results),
                    'min_credibility_score': config.min_credibility_score,
                    'attempt': attempt + 1
                }
                
                return {
                    "search_results": sorted_results,
                    "credibility_scores": credibility_scores,
                    "current_stage": "synthesizing",
                    "iterations": state.iterations + 1,
                    "llm_calls": state.llm_calls + 1,
                    "total_input_tokens": state.total_input_tokens + input_tokens,
                    "total_output_tokens": state.total_output_tokens + output_tokens,
                    "llm_call_details": state.llm_call_details + [call_detail]
                }
                
            except SearchError as e:
                logger.warning(f"第 {attempt + 1} 次搜索尝试失败：{e}")
                if attempt == self.max_retries - 1:
                    logger.error(f"经过 {self.max_retries} 次尝试后搜索仍失败")
                    await emit_error(f"搜索失败：{e}")
                    return {
                        "error": f"搜索失败：{e}",
                        "iterations": state.iterations + 1
                    }
                else:
                    await asyncio.sleep(2 ** attempt)
            except Exception as e:
                search_error = SearchError("搜索阶段执行失败", details=str(e))
                logger.warning(f"第 {attempt + 1} 次搜索尝试失败：{search_error}")
                if attempt == self.max_retries - 1:
                    logger.error(f"经过 {self.max_retries} 次尝试后搜索仍失败")
                    await emit_error(f"搜索失败：{search_error}")
                    return {
                        "error": f"搜索失败：{search_error}",
                        "iterations": state.iterations + 1
                    }
                else:
                    await asyncio.sleep(2 ** attempt)
        
        return {
            "error": "搜索失败：已超过最大重试次数",
            "iterations": state.iterations + 1
        }

    async def _search_with_executor(self, state: ResearchState) -> Dict[str, Any]:
        """Run deterministic_v2 while preserving the legacy Agent contract."""
        logger.info(
            "Deterministic Search Executor 开始研究：已规划 "
            f"{len(state.plan.search_queries)} 个查询"
        )

        total_queries = len(state.plan.search_queries)
        for i, query in enumerate(state.plan.search_queries, 1):
            await emit_search_start(query.query, i, total_queries)

        execution = await self.search_executor.execute(
            state.plan.search_queries,
            max_results_per_search=self.search_config.max_results_per_search,
        )

        search_results = execution.search_results
        total_extracted_chars = sum(
            len(result.content) if result.content else 0
            for result in search_results
        )
        extracted_count = sum(1 for result in search_results if result.content)
        await emit_extraction_complete(extracted_count, total_extracted_chars)

        if not search_results:
            error = execution.error or "Deterministic Search Executor 没有返回搜索结果"
            await emit_error(f"搜索失败：{error}")
            return {
                "search_results": [],
                "credibility_scores": [],
                "error": f"搜索失败：{error}",
                "iterations": state.iterations + 1,
            }

        scored_results = self.credibility_scorer.score_search_results(search_results)
        filtered_scored = [
            item
            for item in scored_results
            if item["credibility"]["score"] >= config.min_credibility_score
        ]
        credibility_scores = [item["credibility"] for item in filtered_scored]
        sorted_results = [item["result"] for item in filtered_scored]

        for query in state.plan.search_queries:
            query.completed = True

        call_detail = {
            "agent": "ResearchSearcher",
            "operation": "deterministic_search",
            "model": config.model_name,
            "input_tokens": 0,
            "output_tokens": 0,
            "duration": execution.stats.elapsed_seconds,
            "results_count": len(sorted_results),
            "original_results_count": len(search_results),
            "search_calls": execution.stats.search_calls,
            "extract_calls": execution.stats.extract_calls,
            "partial": execution.partial,
        }

        logger.info(
            "Deterministic Search Executor 收集了 "
            f"{len(search_results)} 个结果，过滤后剩余 {len(sorted_results)} 个"
        )

        return {
            "search_results": sorted_results,
            "credibility_scores": credibility_scores,
            "error": None,
            "current_stage": "synthesizing",
            "iterations": state.iterations + 1,
            "llm_calls": state.llm_calls,
            "total_input_tokens": state.total_input_tokens,
            "total_output_tokens": state.total_output_tokens,
            "llm_call_details": state.llm_call_details + [call_detail],
        }
    
    def _extract_results_from_messages(self, messages: list) -> List[SearchResult]:
        """从代理消息中提取搜索结果。"""
        search_results = []
        
        for msg in messages:
            if hasattr(msg, 'name') and msg.name == 'web_search':
                try:
                    content = msg.content
                    if isinstance(content, str):
                        tool_results = json.loads(content)
                    else:
                        tool_results = content
                    
                    if isinstance(tool_results, list):
                        for item in tool_results:
                            if isinstance(item, dict):
                                search_results.append(SearchResult(
                                    query=item.get('query', ''),
                                    title=item.get('title', ''),
                                    url=item.get('url', ''),
                                    snippet=item.get('snippet', ''),
                                    content=None
                                ))
                except Exception as e:
                    logger.warning(f"解析工具结果出错：{e}")
            
            if hasattr(msg, 'name') and msg.name == 'extract_webpage_content':
                try:
                    content = msg.content
                    if search_results and content:
                        for sr in reversed(search_results):
                            if not sr.content:
                                sr.content = content
                                break
                except Exception as e:
                    logger.warning(f"更新内容出错：{e}")
        
        return search_results


# =============================================================================
# 研究综合代理
# =============================================================================

class ResearchSynthesizer:
    """负责综合研究发现的自主代理。"""
    
    def __init__(self, llm: Optional[BaseChatModel] = None, max_retries: int = 3):
        self.llm = llm or get_llm(temperature=0.3, model_override=config.summarization_model)
        self.tools = get_research_tools(agent_type="synthesis")
        self.max_retries = max_retries
        
    async def synthesize(self, state: ResearchState) -> Dict[str, Any]:
        """使用工具和推理自主综合关键发现。
        
        返回将由 LangGraph 合并到状态中的关键发现字典。
        """
        logger.info(f"正在从 {len(state.search_results)} 个结果中综合研究发现")
        
        if not state.search_results:
            await emit_error("没有可供综合的搜索结果")
            return {"error": "没有可供综合的搜索结果"}
        
        await emit_synthesis_start(len(state.search_results))
        
        agent_graph = create_agent(
            self.llm,
            self.tools,
            system_prompt=SYNTHESIZER_SYSTEM_PROMPT
        )
        
        max_results = 20
        
        for attempt in range(self.max_retries):
            try:
                start_time = time.time()
                
                current_max = max(5, max_results - (attempt * 5))
                
                results_to_use = state.search_results[:current_max]
                credibility_scores_to_use = state.credibility_scores[:current_max] if state.credibility_scores else []
                
                results_text = self._format_results_text(results_to_use, credibility_scores_to_use)
                
                input_message = SYNTHESIZER_USER_TEMPLATE.format(
                    topic=state.research_topic,
                    results=results_text
                )
                
                input_tokens = estimate_tokens(input_message)
                
                result = await agent_graph.ainvoke({
                    "messages": [{"role": "user", "content": input_message}]
                })
                
                duration = time.time() - start_time
                
                messages = result.get('messages', [])
                output_text = ""
                if messages:
                    last_msg = messages[-1]
                    output_text = str(last_msg.content if hasattr(last_msg, 'content') else str(last_msg))
                
                output_tokens = estimate_tokens(output_text)
                
                call_detail = {
                    'agent': 'ResearchSynthesizer',
                    'operation': 'autonomous_synthesis',
                    'model': config.summarization_model,
                    'input_tokens': input_tokens,
                    'output_tokens': output_tokens,
                    'duration': round(duration, 2),
                    'attempt': attempt + 1
                }
                
                key_findings = self._extract_findings(output_text, state.search_results)
                
                logger.info(f"已提取 {len(key_findings)} 条关键发现")
                
                await emit_synthesis_complete(len(key_findings))
                
                return {
                    "key_findings": key_findings,
                    "current_stage": "reporting",
                    "iterations": state.iterations + 1,
                    "llm_calls": state.llm_calls + 1,
                    "total_input_tokens": state.total_input_tokens + input_tokens,
                    "total_output_tokens": state.total_output_tokens + output_tokens,
                    "llm_call_details": state.llm_call_details + [call_detail]
                }
                
            except Exception as e:
                logger.warning(f"第 {attempt + 1} 次综合尝试失败：{str(e)}")
                if attempt == self.max_retries - 1:
                    logger.error(f"经过 {self.max_retries} 次尝试后综合仍失败")
                    await emit_error(f"综合失败：{str(e)}")
                    return {
                        "error": f"综合失败：{str(e)}",
                        "iterations": state.iterations + 1
                    }
                else:
                    await asyncio.sleep(2 ** attempt)
        
        return {
            "error": "综合失败：已超过最大重试次数",
            "iterations": state.iterations + 1
        }
    
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

class ReportWriter:
    """负责撰写研究报告的自主代理。"""
    
    def __init__(
        self, 
        llm: Optional[BaseChatModel] = None,
        citation_formatter: Optional[CitationFormatter] = None,
        citation_style: str = 'apa',
        max_retries: int = 3
    ):
        self.llm = llm or get_llm(temperature=0.7)
        self.tools = get_research_tools(agent_type="writing")
        self.max_retries = max_retries
        self.citation_style = citation_style
        self.citation_formatter = citation_formatter or CitationFormatter()
        
    async def write_report(self, state: ResearchState) -> Dict[str, Any]:
        """通过验证和重试撰写最终研究报告。
        
        返回将由 LangGraph 合并到状态中的报告数据字典。
        """
        logger.info("正在撰写最终报告")
        
        if not state.plan or not state.key_findings:
            await emit_error("报告生成所需的数据不足")
            return {"error": "报告生成所需的数据不足"}
        
        await emit_writing_start(len(state.plan.report_outline))
        
        report_llm_calls = 0
        report_input_tokens = 0
        report_output_tokens = 0
        report_call_details = []
        
        for attempt in range(self.max_retries):
            try:
                report_sections = []
                total_sections = len(state.plan.report_outline)
                
                for section_idx, section_title in enumerate(state.plan.report_outline, 1):
                    await emit_writing_section(section_title, section_idx, total_sections)
                    
                    section, section_tokens = await self._write_section(
                        state.research_topic,
                        section_title,
                        state.key_findings,
                        state.search_results
                    )
                    if section:
                        report_sections.append(section)
                        if section_tokens:
                            report_llm_calls += 1
                            report_input_tokens += section_tokens['input_tokens']
                            report_output_tokens += section_tokens['output_tokens']
                            report_call_details.append(section_tokens)
                
                if not report_sections:
                    raise ReportGenerationError("未生成报告章节")
                
                temp_state = ResearchState(
                    research_topic=state.research_topic,
                    plan=state.plan,
                    report_sections=report_sections,
                    search_results=state.search_results
                )
                
                final_report = self._compile_report(temp_state)
                
                if state.search_results:
                    final_report = self.citation_formatter.update_report_citations(
                        final_report,
                        style=self.citation_style,
                        search_results=state.search_results
                    )
                
                if state.credibility_scores:
                    high_cred_sources = [
                        i+1 for i, score in enumerate(state.credibility_scores)
                        if score.get('level') == 'high'
                    ]
                    if high_cred_sources:
                        final_report += f"\n\n---\n\n**注：** 本次研究优先采用了 {len(high_cred_sources)} 个高可信度来源。"
                
                if len(final_report) < 500:
                    raise ReportGenerationError("报告过短，内容不足")
                
                logger.info(f"报告生成完成：{len(final_report)} 个字符")
                
                await emit_writing_complete(len(final_report))
                
                return {
                    "report_sections": report_sections,
                    "final_report": final_report,
                    "current_stage": "complete",
                    "iterations": state.iterations + 1,
                    "llm_calls": state.llm_calls + report_llm_calls,
                    "total_input_tokens": state.total_input_tokens + report_input_tokens,
                    "total_output_tokens": state.total_output_tokens + report_output_tokens,
                    "llm_call_details": state.llm_call_details + report_call_details
                }
                
            except Exception as e:
                logger.warning(f"第 {attempt + 1} 次报告尝试失败：{str(e)}")
                if attempt == self.max_retries - 1:
                    logger.error(f"经过 {self.max_retries} 次尝试后报告生成仍失败")
                    await emit_error(f"报告生成失败：{str(e)}")
                    return {
                        "error": f"报告撰写失败：{str(e)}",
                        "iterations": state.iterations + 1
                    }
                else:
                    await asyncio.sleep(2 ** attempt)
        
        return {
            "error": "报告生成失败：已超过最大重试次数",
            "iterations": state.iterations + 1
        }
    
    async def _write_section(
        self,
        topic: str,
        section_title: str,
        findings: List[str],
        search_results: List
    ) -> tuple:
        """撰写单个报告章节。"""
        logger.info(f"正在撰写章节：{section_title}")
        
        system_prompt = WRITER_SYSTEM_PROMPT.format(min_words=config.min_section_words)
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            ("human", "{input}")
        ])
        
        try:
            start_time = time.time()
            
            sources_context = ""
            if search_results:
                sources_context = "\n可用于引用的来源：\n" + "\n".join(
                    f"[{i+1}] {r.title} ({r.url})"
                    for i, r in enumerate(search_results[:15])
                )
            
            input_message = WRITER_USER_TEMPLATE.format(
                topic=topic,
                section_title=section_title,
                min_words=config.min_section_words,
                findings=chr(10).join(f"- {f}" for f in findings),
                sources_context=sources_context
            )
            
            input_tokens = estimate_tokens(input_message)
            
            chain = prompt | self.llm | StrOutputParser()
            content = await chain.ainvoke({"input": input_message})
            
            if not isinstance(content, str):
                content = str(content)
            
            duration = time.time() - start_time
            output_tokens = estimate_tokens(content)
            
            call_detail = {
                'agent': 'ReportWriter',
                'operation': f'write_section_{section_title[:30]}',
                'model': config.model_name,
                'input_tokens': input_tokens,
                'output_tokens': output_tokens,
                'duration': round(duration, 2)
            }
            
            if not content or len(content.strip()) < 50:
                logger.warning(f"章节“{section_title}”生成的内容不足：{len(content)} 个字符")
                if findings:
                    logger.info(f"正在为章节“{section_title}”创建备用内容")
                    content = f"\n\n{chr(10).join(findings[:3])}\n\n"
                else:
                    logger.error(f"无法创建章节“{section_title}”：没有内容和研究发现")
                    return None, None
            
            citations = re.findall(r'\[(\d+)\]', content)
            source_urls = []
            for cite_num in set(citations):
                idx = int(cite_num) - 1
                if 0 <= idx < len(search_results):
                    source_urls.append(search_results[idx].url)
            
            section = ReportSection(
                title=section_title,
                content=content,
                sources=source_urls
            )
            
            logger.info(f"章节“{section_title}”撰写成功：{len(content)} 个字符")
            return section, call_detail
            
        except Exception as e:
            logger.error(f"撰写章节“{section_title}”出错：{str(e)}")
            return None, None
    
    def _compile_report(self, state: ResearchState) -> str:
        """将所有章节汇编为最终报告。"""
        search_results = getattr(state, 'search_results', []) or []
        report_sections = getattr(state, 'report_sections', []) or []
        
        unique_sources = set()
        for result in search_results:
            if hasattr(result, 'url') and result.url:
                unique_sources.add(result.url)
        
        for section in report_sections:
            if hasattr(section, 'sources'):
                unique_sources.update(section.sources)
        
        source_count = len(unique_sources) if unique_sources else len(search_results)
        
        report_parts = [
            f"# {state.research_topic}\n",
            f"**深度研究报告**\n",
            f"\n## 执行摘要\n",
            f"本报告对 {state.research_topic} 进行了全面分析。",
            f"本次研究覆盖 **{source_count} 个来源**，",
            f"并综合为 **{len(report_sections)} 个主要章节**。\n",
            f"\n## 研究目标\n"
        ]
        
        if state.plan and hasattr(state.plan, 'objectives'):
            for i, obj in enumerate(state.plan.objectives, 1):
                report_parts.append(f"{i}. {obj}\n")
        
        report_parts.append("\n---\n")
        
        has_references_section = False
        for section in report_sections:
            content = section.content.strip()
            
            if "## References" in content or section.title.lower() in {"references", "参考文献"}:
                has_references_section = True
            
            if content.startswith(f"## {section.title}"):
                report_parts.append(f"\n{content}\n\n")
            else:
                report_parts.append(f"\n## {section.title}\n\n")
                report_parts.append(content)
                report_parts.append("\n")
        
        if not has_references_section:
            report_parts.append("\n---\n\n## 参考文献\n\n")
        
        source_info = []
        seen_urls = set()
        
        for result in search_results:
            if hasattr(result, 'url') and result.url and result.url not in seen_urls:
                seen_urls.add(result.url)
                title = getattr(result, 'title', '')
                source_info.append((result.url, title))
        
        for section in report_sections:
            if hasattr(section, 'sources'):
                for url in section.sources:
                    if url not in seen_urls:
                        seen_urls.add(url)
                        source_info.append((url, ''))
        
        if not has_references_section:
            if source_info:
                for i, (url, title) in enumerate(source_info[:30], 1):
                    citation = self.citation_formatter.format_apa(url, title)
                    report_parts.append(f"{i}. {citation}\n")
            else:
                report_parts.append("*本次研究没有可用来源。*\n")
        
        return "".join(report_parts)
