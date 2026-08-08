"""LLM 调用跟踪和 Token 用量监控。"""

from typing import Dict, Optional, Any
import logging
from functools import wraps
import time

logger = logging.getLogger(__name__)


class LLMCallTracker:
    """跟踪 LLM 调用和 Token 用量。"""
    
    def __init__(self):
        self.calls = []
        self.total_input_tokens = 0
        self.total_output_tokens = 0
    
    def track_call(
        self,
        agent_name: str,
        operation: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        duration: float = 0.0,
        model: str = "",
        success: bool = True,
        error: Optional[str] = None
    ) -> Dict[str, Any]:
        """跟踪一次 LLM 调用。"""
        call_info = {
            'agent': agent_name,
            'operation': operation,
            'model': model,
            'input_tokens': input_tokens,
            'output_tokens': output_tokens,
            'total_tokens': input_tokens + output_tokens,
            'duration': round(duration, 2),
            'success': success,
            'error': error,
            'timestamp': time.time()
        }
        
        self.calls.append(call_info)
        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens
        
        logger.info(
            f"LLM 调用 [{agent_name}/{operation}]："
            f"输入 {input_tokens} + 输出 {output_tokens} = {input_tokens + output_tokens} Token "
            f"（{duration:.2f} 秒）"
        )
        
        return call_info
    
    def get_summary(self) -> Dict[str, Any]:
        """获取所有 LLM 调用的摘要。"""
        total_tokens = self.total_input_tokens + self.total_output_tokens
        total_duration = sum(call['duration'] for call in self.calls)
        
        # 按代理分组
        by_agent = {}
        for call in self.calls:
            agent = call['agent']
            if agent not in by_agent:
                by_agent[agent] = {
                    'calls': 0,
                    'input_tokens': 0,
                    'output_tokens': 0,
                    'total_tokens': 0,
                    'duration': 0.0
                }
            by_agent[agent]['calls'] += 1
            by_agent[agent]['input_tokens'] += call['input_tokens']
            by_agent[agent]['output_tokens'] += call['output_tokens']
            by_agent[agent]['total_tokens'] += call['total_tokens']
            by_agent[agent]['duration'] += call['duration']
        
        return {
            'total_calls': len(self.calls),
            'total_input_tokens': self.total_input_tokens,
            'total_output_tokens': self.total_output_tokens,
            'total_tokens': total_tokens,
            'total_duration': round(total_duration, 2),
            'by_agent': by_agent,
            'successful_calls': sum(1 for c in self.calls if c['success']),
            'failed_calls': sum(1 for c in self.calls if not c['success'])
        }
    
    def get_calls(self) -> list:
        """获取所有已跟踪的调用。"""
        return self.calls


def estimate_tokens(text: str) -> int:
    """估算文本 Token 数量（粗略估计：1 Token 约等于 4 个字符）。"""
    return max(1, len(text) // 4)


def track_llm_call(agent_name: str, operation: str, model: str = ""):
    """跟踪 LLM 调用的装饰器。"""
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = await func(*args, **kwargs)
                duration = time.time() - start_time
                
                # 如果结果中有 Token 信息则尝试提取
                input_tokens = kwargs.get('_input_tokens', 0)
                output_tokens = kwargs.get('_output_tokens', 0)
                
                # 如果未提供，则根据结果估算
                if output_tokens == 0 and isinstance(result, str):
                    output_tokens = estimate_tokens(result)
                
                return result, {
                    'agent': agent_name,
                    'operation': operation,
                    'model': model,
                    'input_tokens': input_tokens,
                    'output_tokens': output_tokens,
                    'duration': duration,
                    'success': True
                }
            except Exception as e:
                duration = time.time() - start_time
                logger.error(f"LLM 调用失败：{e}")
                raise
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                duration = time.time() - start_time
                return result
            except Exception as e:
                duration = time.time() - start_time
                logger.error(f"LLM 调用失败：{e}")
                raise
        
        # 根据函数类型返回相应包装器
        import inspect
        if inspect.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator
