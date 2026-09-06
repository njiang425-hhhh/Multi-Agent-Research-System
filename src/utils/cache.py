"""Research-result cache with schema-validated canonical state payloads."""

import json
import hashlib
from pathlib import Path
from collections.abc import Mapping
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


CACHE_SCHEMA_VERSION = 2


class ResearchCache:
    """基于文件的研究结果缓存。"""
    
    def __init__(self, cache_dir: Path = Path(".cache/research")):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_file = self.cache_dir / "cache.json"
        self.cache_ttl_days = 7  # 缓存 7 天后过期
        
        # 加载已有缓存
        self._cache: Dict[str, Dict[str, Any]] = self._load_cache()
    
    def _load_cache(self) -> Dict[str, Dict[str, Any]]:
        """从磁盘加载缓存。"""
        if self.cache_file.exists():
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    cache = json.load(f)
                    # 过滤过期条目
                    now = datetime.now()
                    valid_cache = {}
                    for key, value in cache.items():
                        cached_time = datetime.fromisoformat(value.get('timestamp', '2000-01-01'))
                        if (now - cached_time).days < self.cache_ttl_days:
                            valid_cache[key] = value
                    return valid_cache
            except Exception as e:
                logger.warning(f"加载缓存失败：{e}")
                return {}
        return {}
    
    def _save_cache(self):
        """将缓存保存到磁盘。"""
        try:
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self._cache, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"保存缓存失败：{e}")
    
    def _get_key(self, topic: str) -> str:
        """根据主题生成缓存键。"""
        # 规范化主题（转小写并去除首尾空格）
        normalized = topic.lower().strip()
        return hashlib.md5(normalized.encode()).hexdigest()
    
    @staticmethod
    def _serialize_state(data: Mapping[str, Any] | Any) -> Dict[str, Any]:
        """Validate and serialize one canonical state without string fallback.

        ``ResearchState`` already owns the domain schema, so the cache stores
        its JSON-mode dump instead of maintaining a second snapshot contract.
        """

        from src.state import ResearchState
        from src.state_compat import hydrate_canonical_state

        state = data if isinstance(data, ResearchState) else ResearchState.model_validate(data)
        canonical_state = hydrate_canonical_state(state)
        return {
            "cache_schema_version": CACHE_SCHEMA_VERSION,
            "state": canonical_state.model_dump(mode="json"),
        }

    @staticmethod
    def _deserialize_state(payload: Any) -> Optional[Dict[str, Any]]:
        """Return a validated canonical cache state, rejecting stale payloads.

        Historical cache entries were written with ``default=str`` and cannot
        reliably reconstruct Pydantic domain objects.  They are explicit cache
        misses rather than silently replayed partial state.
        """

        if not isinstance(payload, Mapping):
            logger.warning("缓存条目格式无效，忽略该条目")
            return None
        if payload.get("cache_schema_version") != CACHE_SCHEMA_VERSION:
            logger.info(
                "缓存条目 schema 版本不受支持（%r），将作为缓存未命中处理",
                payload.get("cache_schema_version"),
            )
            return None
        raw_state = payload.get("state")
        if not isinstance(raw_state, Mapping):
            logger.warning("缓存条目缺少可验证的 state，忽略该条目")
            return None

        try:
            from src.state import ResearchState
            from src.state_compat import hydrate_canonical_state

            state = hydrate_canonical_state(ResearchState.model_validate(raw_state))
            return state.model_dump(mode="json")
        except Exception as error:
            logger.warning("缓存条目未通过 ResearchState 校验，忽略该条目：%s", error)
            return None

    def get(self, topic: str) -> Optional[Dict[str, Any]]:
        """获取主题对应的缓存研究结果。"""
        key = self._get_key(topic)
        if key in self._cache:
            data = self._deserialize_state(self._cache[key].get('data'))
            if data is not None:
                logger.info(f"缓存命中，主题：{topic}")
                return data
            logger.info(f"缓存条目不可重放，主题：{topic}")
        logger.info(f"缓存未命中，主题：{topic}")
        return None
    
    def set(self, topic: str, data: Dict[str, Any]):
        """缓存主题的研究结果。"""
        key = self._get_key(topic)
        self._cache[key] = {
            'topic': topic,
            'data': self._serialize_state(data),
            'timestamp': datetime.now().isoformat()
        }
        self._save_cache()
        logger.info(f"已缓存主题的研究结果：{topic}")
    
    def clear(self):
        """清除所有缓存条目。"""
        self._cache = {}
        self._save_cache()
        logger.info("缓存已清除")
    
    def get_stats(self) -> Dict[str, Any]:
        """获取缓存统计信息。"""
        return {
            'total_entries': len(self._cache),
            'cache_dir': str(self.cache_dir),
            'cache_file': str(self.cache_file)
        }
