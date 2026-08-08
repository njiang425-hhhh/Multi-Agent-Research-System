"""研究结果缓存层，用于避免重复搜索。"""

import json
import hashlib
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


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
                json.dump(self._cache, f, indent=2, default=str)
        except Exception as e:
            logger.warning(f"保存缓存失败：{e}")
    
    def _get_key(self, topic: str) -> str:
        """根据主题生成缓存键。"""
        # 规范化主题（转小写并去除首尾空格）
        normalized = topic.lower().strip()
        return hashlib.md5(normalized.encode()).hexdigest()
    
    def get(self, topic: str) -> Optional[Dict[str, Any]]:
        """获取主题对应的缓存研究结果。"""
        key = self._get_key(topic)
        if key in self._cache:
            logger.info(f"缓存命中，主题：{topic}")
            return self._cache[key].get('data')
        logger.info(f"缓存未命中，主题：{topic}")
        return None
    
    def set(self, topic: str, data: Dict[str, Any]):
        """缓存主题的研究结果。"""
        key = self._get_key(topic)
        self._cache[key] = {
            'topic': topic,
            'data': data,
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
