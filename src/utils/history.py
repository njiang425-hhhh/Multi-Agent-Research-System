"""研究历史跟踪与持久化。"""

import json
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class ResearchHistory:
    """跟踪并管理研究历史。"""
    
    def __init__(self, history_file: Path = Path(".cache/research_history.json")):
        self.history_file = history_file
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        self._history: List[Dict] = self._load_history()
    
    def _load_history(self) -> List[Dict]:
        """从磁盘加载历史记录。"""
        if self.history_file.exists():
            try:
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.warning(f"加载历史记录失败：{e}")
                return []
        return []
    
    def _save_history(self):
        """将历史记录保存到磁盘。"""
        try:
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump(self._history, f, indent=2, default=str)
        except Exception as e:
            logger.warning(f"保存历史记录失败：{e}")
    
    def add_research(
        self,
        topic: str,
        output_file: Optional[Path] = None,
        quality_score: Optional[Dict] = None,
        metadata: Optional[Dict] = None
    ):
        """将一条研究记录添加到历史。"""
        entry = {
            'topic': topic,
            'timestamp': datetime.now().isoformat(),
            'output_file': str(output_file) if output_file else None,
            'quality_score': quality_score,
            'metadata': metadata or {}
        }
        
        # 添加到列表开头（最新记录优先）
        self._history.insert(0, entry)
        
        # 只保留最近 100 条记录
        if len(self._history) > 100:
            self._history = self._history[:100]
        
        self._save_history()
        logger.info(f"已将研究添加到历史记录：{topic}")
    
    def get_recent(self, limit: int = 10) -> List[Dict]:
        """获取最近的研究记录。"""
        return self._history[:limit]
    
    def search_history(self, query: str) -> List[Dict]:
        """按主题搜索历史记录。"""
        query_lower = query.lower()
        return [
            entry for entry in self._history
            if query_lower in entry.get('topic', '').lower()
        ]
    
    def get_by_topic(self, topic: str) -> Optional[Dict]:
        """获取某主题最近的一次研究记录。"""
        for entry in self._history:
            if entry.get('topic', '').lower() == topic.lower():
                return entry
        return None
    
    def clear_history(self):
        """清除全部历史记录。"""
        self._history = []
        self._save_history()
        logger.info("历史记录已清除")
    
    def get_stats(self) -> Dict:
        """获取历史记录统计信息。"""
        if not self._history:
            return {
                'total_researches': 0,
                'oldest': None,
                'newest': None
            }
        
        timestamps = [datetime.fromisoformat(e['timestamp']) for e in self._history if 'timestamp' in e]
        
        return {
            'total_researches': len(self._history),
            'oldest': min(timestamps).isoformat() if timestamps else None,
            'newest': max(timestamps).isoformat() if timestamps else None
        }
