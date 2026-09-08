"""深度研究代理的配置管理。"""

import os
from typing import Optional
from pathlib import Path
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# 从 .env 文件加载环境变量
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=env_path)


class ResearchConfig(BaseModel):
    """研究代理配置。"""

    # 模型提供商配置
    model_provider: str = Field(
        default=os.getenv("MODEL_PROVIDER", "gemini"),
        description="模型提供商：'gemini'、'ollama'、'openai'、'deepseek' 或 'llamacpp'"
    )
    
    # API 密钥
    google_api_key: str = Field(
        default_factory=lambda: os.getenv("GEMINI_API_KEY", ""),
        description="Google/Gemini API 密钥（使用 Gemini 时必需）"
    )
    
    openai_api_key: str = Field(
        default_factory=lambda: os.getenv("OPENAI_API_KEY", ""),
        description="OpenAI API 密钥（使用 OpenAI 时必需）"
    )

    openai_base_url: str = Field(
        default_factory=lambda: os.getenv("OPENAI_BASE_URL", "https://api.openai.com"),
        description="OpenAI API 基础 URL（使用 OpenAI 时可选）"
    )

    # DeepSeek 配置（OpenAI 兼容 API）
    deepseek_api_key: str = Field(
        default_factory=lambda: os.getenv("DEEPSEEK_API_KEY", ""),
        description="DeepSeek API 密钥（使用 DeepSeek 时必需）"
    )

    deepseek_base_url: str = Field(
        default_factory=lambda: os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        description="DeepSeek API 基础 URL"
    )
    # Ollama 配置
    ollama_base_url: str = Field(
        default=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
        description="Ollama 服务器 URL"
    )
    
    # llama.cpp 服务器配置
    llamacpp_base_url: str = Field(
        default=os.getenv("LLAMACPP_BASE_URL", "http://localhost:8080"),
        description="llama.cpp 服务器 URL（兼容 OpenAI API）"
    )
    
    # 模型配置
    model_name: str = Field(
        default=os.getenv("MODEL_NAME", "gemini-2.5-flash"),
        description="用于研究和生成的模型"
    )
    
    summarization_model: str = Field(
        default=os.getenv("SUMMARIZATION_MODEL", "gemini-2.5-flash"),
        description="用于总结搜索结果的模型（更快/更便宜）"
    )

    llm_operation_timeout_seconds: float = Field(
        default_factory=lambda: float(os.getenv("LLM_OPERATION_TIMEOUT_SECONDS", "180")),
        gt=0,
        description="单次 LLM operation 的本地 deadline（秒）"
    )
    
    # 搜索提供商配置
    search_provider: str = Field(
        default=os.getenv("SEARCH_PROVIDER", "duckduckgo"),
        description="搜索提供商：'duckduckgo' 或 'tavily'"
    )

    tavily_api_key: str = Field(
        default_factory=lambda: os.getenv("TAVILY_API_KEY", ""),
        description="Tavily API 密钥（SEARCH_PROVIDER=tavily 时必需）"
    )

    # 搜索配置
    max_search_queries: int = Field(
        default=int(os.getenv("MAX_SEARCH_QUERIES", "3")),
        description="最多生成的搜索查询数量"
    )
    
    max_search_results_per_query: int = Field(
        default=int(os.getenv("MAX_SEARCH_RESULTS_PER_QUERY", "3")),
        description="每个搜索查询最多获取的结果数"
    )
    
    max_parallel_searches: int = Field(
        default=int(os.getenv("MAX_PARALLEL_SEARCHES", "3")),
        description="并行搜索操作的最大数量"
    )
    
    # 可信度配置
    min_credibility_score: int = Field(
        default=int(os.getenv("MIN_CREDIBILITY_SCORE", "40")),
        description="过滤低质量来源的最低可信度分数（0-100）"
    )
    
    # 报告配置
    max_report_sections: int = Field(
        default=int(os.getenv("MAX_REPORT_SECTIONS", "8")),
        description="最终报告的最大章节数"
    )
    
    min_section_words: int = Field(
        default=200,
        description="每个章节的最少字数"
    )

    research_memory_enabled: bool = Field(
        default=os.getenv("RESEARCH_MEMORY_ENABLED", "false").lower()
        in {"1", "true", "yes", "on"},
        description="启用本地 Research Memory V1 baseline"
    )

    research_memory_store_path: str = Field(
        default=os.getenv("RESEARCH_MEMORY_STORE_PATH", ".cache/research_memory/memory.db"),
        description="本地 Research Memory SQLite 路径"
    )

    research_memory_ttl_days: int = Field(
        default=int(os.getenv("RESEARCH_MEMORY_TTL_DAYS", "30")),
        ge=1,
        description="Research Memory 默认保留天数"
    )

    research_memory_max_records: int = Field(
        default=int(os.getenv("RESEARCH_MEMORY_MAX_RECORDS", "500")),
        ge=1,
        description="Research Memory 本地 store 最大记录数"
    )

    research_memory_retrieval_limit: int = Field(
        default=int(os.getenv("RESEARCH_MEMORY_RETRIEVAL_LIMIT", "3")),
        ge=0,
        description="Planner/Searcher 每次最多检索的 memory 数"
    )

    research_memory_search_hint_limit: int = Field(
        default=int(os.getenv("RESEARCH_MEMORY_SEARCH_HINT_LIMIT", "2")),
        ge=0,
        description="Searcher 从 memory provenance 派生的最多 source hint 查询数"
    )

    searcher_adaptive_enabled: bool = Field(
        default=os.getenv("SEARCHER_ADAPTIVE_ENABLED", "true").lower()
        in {"1", "true", "yes", "on"},
        description="启用 Searcher 内单次 bounded supplementary search"
    )

    searcher_adaptive_max_rounds: int = Field(
        default=int(os.getenv("SEARCHER_ADAPTIVE_MAX_ROUNDS", "1")),
        ge=0,
        le=1,
        description="Searcher supplementary search 轮数上限（仅支持 0 或 1）"
    )
    
    # 引用配置
    citation_style: str = Field(
        default=os.getenv("CITATION_STYLE", "apa"),
        description="引用格式（apa、mla、chicago、ieee）"
    )
    
    # LangSmith 配置
    langsmith_tracing: bool = Field(
        default=os.getenv("LANGCHAIN_TRACING_V2", "false").lower() == "true",
        description="启用 LangSmith 跟踪"
    )
    
    langsmith_project: str = Field(
        default=os.getenv("LANGCHAIN_PROJECT", "deep-research-agent"),
        description="LangSmith 项目名称"
    )
    
    def validate_config(self) -> bool:
        """验证必需配置是否存在。"""
        if self.model_provider == "gemini":
            if not self.google_api_key:
                raise ValueError(
                    "使用 Gemini 时必须设置 GEMINI_API_KEY。可从 https://makersuite.google.com/app/apikey 获取"
                )
        elif self.model_provider == "ollama":
            # 验证 Ollama 是否可访问
            try:
                import requests
                response = requests.get(f"{self.ollama_base_url}/api/tags", timeout=5)
                if response.status_code != 200:
                    raise ValueError(f"无法访问 Ollama 服务器：{self.ollama_base_url}")
            except requests.exceptions.RequestException as e:
                raise ValueError(f"无法连接 Ollama 服务器 {self.ollama_base_url}：{e}")
        elif self.model_provider == "openai":
            if not self.openai_api_key:
                raise ValueError(
                    "使用 OpenAI 时必须设置 OPENAI_API_KEY。可从 https://platform.openai.com/api-keys 获取"
                )
        elif self.model_provider == "deepseek":
            if not self.deepseek_api_key:
                raise ValueError(
                    "使用 DeepSeek 时必须设置 DEEPSEEK_API_KEY。"
                )
        elif self.model_provider == "llamacpp":
            # 验证 llama.cpp 服务器是否可访问
            try:
                import requests
                response = requests.get(f"{self.llamacpp_base_url}/health", timeout=5)
                if response.status_code not in [200, 404]:  # 404 可以接受，表示服务器运行但没有健康检查端点
                    raise ValueError(f"无法访问 llama.cpp 服务器：{self.llamacpp_base_url}")
            except requests.exceptions.RequestException as e:
                raise ValueError(f"无法连接 llama.cpp 服务器 {self.llamacpp_base_url}：{e}")
        else:
            raise ValueError(
                f"无效的 MODEL_PROVIDER：{self.model_provider}。"
                "必须是 'gemini'、'ollama'、'openai'、'deepseek' 或 'llamacpp'"
            )
        
        return True


# 全局配置实例
config = ResearchConfig()

# 记录配置，便于调试
import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logger.info(f"配置已加载 - MAX_SEARCH_QUERIES：{config.max_search_queries}，"
           f"MAX_SEARCH_RESULTS_PER_QUERY：{config.max_search_results_per_query}，"
           f"MAX_REPORT_SECTIONS：{config.max_report_sections}，"
           f"LLM_OPERATION_TIMEOUT_SECONDS：{config.llm_operation_timeout_seconds}")
