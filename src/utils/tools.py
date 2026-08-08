"""供研究代理调用的 LLM 工具。"""

from typing import List, Optional, Dict
from langchain_core.tools import tool
import logging
import json

from src.utils.web_utils import (
    WebSearchTool as WebSearchImpl,
    ContentExtractor as ContentExtractorImpl,
    DuckDuckGoProvider,
)
from src.search.providers.base import SearchProvider
from src.search.providers.factory import SearchProviderFactory
from src.search.providers.models import ProviderSearchResult
from src.state import SearchResult
from src.utils.citations import CitationFormatter
from src.config import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# 使用配置初始化工具实现
def _build_search_providers():
    """构建尚未迁移到新 Provider Layer 的旧 DuckDuckGo 实现。"""
    return [DuckDuckGoProvider(config.max_search_results_per_query)]


_search_impl = WebSearchImpl(
    max_results=config.max_search_results_per_query,
    providers=_build_search_providers(),
)
_search_provider_factory = SearchProviderFactory()
_search_provider_cache: Dict[str, SearchProvider] = {}
_extractor_impl = ContentExtractorImpl(timeout=10)
_citation_formatter = CitationFormatter()


def _get_tavily_provider() -> SearchProvider:
    """Lazily create the configured Tavily provider through the factory."""
    provider = _search_provider_cache.get("tavily")
    if provider is None:
        provider = _search_provider_factory.create(
            "tavily",
            api_key=config.tavily_api_key or None,
        )
        _search_provider_cache["tavily"] = provider
    return provider


def _to_legacy_search_result(result: ProviderSearchResult) -> SearchResult:
    """Convert a Provider result to the legacy SearchResult contract."""
    return SearchResult(
        query=result.query,
        title=result.title,
        url=result.url,
        snippet=result.snippet,
    )


def _serialize_search_results(results: List[SearchResult]) -> List[dict]:
    """Serialize legacy SearchResult objects for the existing Tool contract."""
    return [
        {
            "query": result.query,
            "title": result.title,
            "url": result.url,
            "snippet": result.snippet,
        }
        for result in results
    ]


@tool
async def web_search(query: str, max_results: int = None) -> List[dict]:
    """使用配置的搜索 Provider 搜索权威网络信息。

    用于收集事实、寻找学术/政府/官方来源、研究近期进展、核验论断和发现专家分析。
    查询应具体，必要时加入“official”“research”、年份或 `site:edu`/`site:gov` 等限定词；
    避免单个词或超过 15 个词的宽泛查询。返回的每个字典包含 query、title、url 和 snippet。

    Args:
        query: 精心设计的搜索查询，最好不超过约 10 个词。
        max_results: 返回的最大结果数，默认使用配置值。
    """
    if max_results is None:
        max_results = config.max_search_results_per_query

    if config.search_provider == "tavily":
        provider = _get_tavily_provider()
        provider_results = await provider.search(query, max_results)
        legacy_results = [
            _to_legacy_search_result(result)
            for result in provider_results
        ]
        return _serialize_search_results(legacy_results)

    if config.search_provider != "duckduckgo":
        raise ValueError(
            f"不支持的 SEARCH_PROVIDER：{config.search_provider}"
        )

    try:
        if _search_impl.max_results != max_results:
            _search_impl.max_results = max_results

        results = await _search_impl.search_async(query)
        return _serialize_search_results(results)
    except Exception as e:
        logger.error(f"网络搜索工具出错：{str(e)}")
        return []


@tool
async def extract_webpage_content(url: str) -> Optional[str]:
    """提取网页的主要文本内容，并移除导航、广告、侧栏、页脚等噪声。

    通常在 web_search 找到有价值的来源后调用，用于获取摘要之外的完整上下文，
    核验论断并提取技术细节、示例或数据表。优先选择公开可访问的官方文档、学术论文、
    政府报告和高质量技术文章；登录页、视频页、社交媒体和纯图片页面通常不适合提取。

    Args:
        url: 有效且公开可访问的 HTTP/HTTPS URL。
    Returns:
        清理后的主要文本（最多 5000 个字符）；提取失败时返回 None。
    """
    try:
        content = await _extractor_impl.extract_content_async(url)
        return content
    except Exception as e:
        logger.error(f"网页内容提取工具出错：{str(e)}")
        return None


@tool
def analyze_research_topic(topic: str) -> Dict[str, List[str]]:
    """将研究主题拆解为结构化维度，帮助全面覆盖主题。

    返回 aspects（研究方面）、perspectives（利益相关者/分析视角）和 questions（待回答问题），
    可用于生成多样化查询、组织报告大纲并检查最终报告是否完整。
    """
    # 这是规划代理使用的结构化思考工具
    # 返回结构化拆解结果以辅助规划
    logger.info(f"正在分析主题：{topic}")
    
    # 基础启发式分析
    aspects = []
    perspectives = []
    questions = []
    
    # 提取关键概念
    words = topic.lower().split()
    if "ai" in words or "artificial" in words or "intelligence" in words:
        aspects.extend(["applications", "technology", "impact"])
        perspectives.extend(["technical", "ethical", "societal"])
    
    if "healthcare" in words or "medical" in words or "health" in words:
        aspects.extend(["patient care", "diagnosis", "treatment"])
        perspectives.extend(["patients", "doctors", "researchers"])
    
    # 默认结构
    if not aspects:
        aspects = ["overview", "current state", "future trends", "implications"]
    if not perspectives:
        perspectives = ["technical", "practical", "societal"]
    
    questions = [
        f"{topic} 的当前状态是什么？",
        "关键收益和挑战是什么？",
        f"{topic} 的未来发展如何？"
    ]
    
    return {
        "aspects": aspects[:5],
        "perspectives": perspectives[:4],
        "questions": questions[:5]
    }


@tool
def extract_insights_from_text(text: str, focus: str = "key findings") -> List[str]:
    """根据指定关注点，从文本中提取具体、针对性的洞见。

    可用于提取关键发现、趋势、挑战、收益、技术细节或统计数据。focus 应尽量具体，
    以便筛选相关句子；返回的每条洞见都是可独立理解的陈述。
    """
    logger.info(f"正在提取洞见，关注点：{focus}")
    
    # 简单提取：按句子拆分并筛选
    insights = []
    sentences = text.split('. ')
    
    focus_keywords = focus.lower().split()
    for sentence in sentences[:20]:  # 仅处理前 20 个句子
        sentence_lower = sentence.lower()
        # 检查句子是否包含关注点关键词
        if any(keyword in sentence_lower for keyword in focus_keywords):
            if len(sentence) > 20 and len(sentence) < 300:
                insights.append(sentence.strip() + '.')
    
    return insights[:10] if insights else ["没有找到与该关注点相关的具体洞见。"]


@tool
def format_citation(url: str, title: str = "", style: str = "apa") -> str:
    """按标准学术格式生成来源引用。

    支持 APA（默认）、MLA、Chicago 和 IEEE。用于生成报告“参考文献”章节中的规范引用。
    """
    logger.info(f"正在按 {style} 格式生成引用")
    
    try:
        # 根据格式选择相应的生成方法
        if style.lower() == "apa":
            return _citation_formatter.format_apa(url, title)
        elif style.lower() == "mla":
            return _citation_formatter.format_mla(url, title)
        elif style.lower() == "chicago":
            return _citation_formatter.format_chicago(url, title)
        elif style.lower() == "ieee":
            return _citation_formatter.format_ieee(url, title)
        else:
            # 默认使用 APA
            return _citation_formatter.format_apa(url, title)
    except Exception as e:
        logger.error(f"引用格式化出错：{e}")
        # 回退到简单格式
        if title:
            return f"{title}。来源：{url}"
        return url


@tool
def validate_section_quality(section_text: str, min_words: int = 150) -> Dict[str, any]:
    """在定稿前根据质量标准验证报告章节。

    检查章节长度、引用、Markdown 结构和可读性。返回 is_valid、word_count、has_citations、
    issues 和 suggestions，章节通过检查后再提交。
    """
    logger.info("正在验证章节质量")
    
    word_count = len(section_text.split())
    has_citations = '[' in section_text and ']' in section_text
    has_headers = '#' in section_text
    
    issues = []
    suggestions = []
    
    if word_count < min_words:
        issues.append(f"章节过短：{word_count} 个词（最低要求：{min_words}）")
        suggestions.append("增加更多细节和支持信息")
    
    if not has_citations:
        issues.append("未找到引用")
        suggestions.append("添加行内引用 [1]、[2] 以支持论断")
    
    if not has_headers and word_count > 300:
        suggestions.append("考虑添加小标题以改善结构")
    
    is_valid = len(issues) == 0
    
    return {
        "is_valid": is_valid,
        "word_count": word_count,
        "has_citations": has_citations,
        "issues": issues,
        "suggestions": suggestions
    }


# 不同代理的工具列表
research_search_tools = [
    web_search,
    extract_webpage_content
]

synthesis_tools = [
    extract_insights_from_text
]

writing_tools = [
    format_citation,
    validate_section_quality
]

planning_tools = [
    analyze_research_topic
]

# 所有工具的集合
all_research_tools = [
    web_search,
    extract_webpage_content,
    analyze_research_topic,
    extract_insights_from_text,
    format_citation,
    validate_section_quality
]


def get_research_tools(agent_type: str = "search") -> List:
    """获取指定代理类型的研究工具。
    
    Args:
        agent_type: 代理类型（"search"、"synthesis"、"writing"、"planning"、"all"）
        
    Returns:
        该代理可用的 LangChain 工具对象列表
    """
    tools_map = {
        "search": research_search_tools,
        "synthesis": synthesis_tools,
        "writing": writing_tools,
        "planning": planning_tools,
        "all": all_research_tools
    }
    return tools_map.get(agent_type, research_search_tools)
