"""深度研究代理的工具模块。"""

# 可由 LLM 调用的工具
from src.utils.tools import (
    get_research_tools, 
    web_search, 
    extract_webpage_content,
    analyze_research_topic,
    extract_insights_from_text,
    format_citation,
    validate_section_quality,
    all_research_tools
)

# 网络工具（内部使用）
from src.utils.web_utils import WebSearchTool, ContentExtractor, is_valid_url

# 其他工具
from src.utils.cache import ResearchCache
from src.utils.exports import ReportExporter
from src.utils.credibility import CredibilityScorer
from src.utils.citations import CitationFormatter
from src.utils.history import ResearchHistory

__all__ = [
    # LLM 工具
    'research_tools',
    'get_research_tools',
    'web_search',
    'extract_webpage_content',
    # 网络工具
    'WebSearchTool',
    'ContentExtractor',
    'is_valid_url',
    # 其他工具
    'ResearchCache',
    'ReportExporter',
    'CredibilityScorer',
    'CitationFormatter',
    'ResearchHistory',
]
