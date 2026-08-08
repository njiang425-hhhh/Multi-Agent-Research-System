"""不同引用格式的引用生成工具。"""

from typing import List, Dict
from datetime import datetime
import re
import logging

logger = logging.getLogger(__name__)


class CitationFormatter:
    """按不同学术格式生成引用。"""
    
    def __init__(self):
        self.styles = ['apa', 'mla', 'chicago', 'ieee']
    
    def format_apa(self, url: str, title: str = "", author: str = "", date: str = "") -> str:
        """按 APA 格式生成引用。"""
        if author and date:
            return f"{author}（{date}）。{title}。来源：{url}"
        elif title:
            return f"{title}。（日期不详）。来源：{url}"
        else:
            return f"来源：{url}"
    
    def format_mla(self, url: str, title: str = "", author: str = "", date: str = "") -> str:
        """按 MLA 格式生成引用。"""
        parts = []
        if author:
            parts.append(author)
        if title:
            parts.append(f'"{title}"')
        if date:
            parts.append(date)
        parts.append(f"网页。访问日期：{datetime.now().strftime('%Y-%m-%d')}")
        parts.append(f"<{url}>")
        return ". ".join(parts)
    
    def format_chicago(self, url: str, title: str = "", author: str = "", date: str = "") -> str:
        """按 Chicago 格式生成引用。"""
        if author:
            return f"{author}。《{title}》。访问日期：{datetime.now().strftime('%Y-%m-%d')}。{url}。"
        else:
            return f"《{title}》。访问日期：{datetime.now().strftime('%Y-%m-%d')}。{url}。"
    
    def format_ieee(self, url: str, title: str = "", author: str = "", date: str = "") -> str:
        """按 IEEE 格式生成引用。"""
        if author:
            return f"{author}，《{title}》，{url}，访问日期：{datetime.now().strftime('%Y-%m-%d')}。"
        else:
            return f"《{title}》，{url}，访问日期：{datetime.now().strftime('%Y-%m-%d')}。"
    
    def format_references_section(
        self,
        urls: List[str],
        style: str = 'apa',
        search_results: List = None
    ) -> str:
        """按指定格式生成参考文献章节。
        
        Args:
            urls: 要引用的 URL 列表
            style: 引用格式（'apa'、'mla'、'chicago'、'ieee'）
            search_results: 可选的搜索结果，用于提取元数据
        
        Returns:
            格式化后的参考文献章节
        """
        style = style.lower()
        if style not in self.styles:
            style = 'apa'
            logger.warning(f"未知引用格式 {style}，将使用 APA")
        
        # 创建 URL 到元数据的映射
        url_metadata = {}
        if search_results:
            for result in search_results:
                if hasattr(result, 'url') and result.url:
                    url_metadata[result.url] = {
                        'title': getattr(result, 'title', ''),
                        'snippet': getattr(result, 'snippet', '')
                    }
        
        references = []
        for i, url in enumerate(urls, 1):
            metadata = url_metadata.get(url, {})
            title = metadata.get('title', '')
            
            if style == 'apa':
                citation = self.format_apa(url, title)
            elif style == 'mla':
                citation = self.format_mla(url, title)
            elif style == 'chicago':
                citation = self.format_chicago(url, title)
            elif style == 'ieee':
                citation = self.format_ieee(url, title)
            else:
                citation = url
            
            references.append(f"{i}. {citation}")
        
        return "\n".join(references)
    
    def update_report_citations(
        self,
        report_content: str,
        style: str = 'apa',
        search_results: List = None
    ) -> str:
        """将报告中的引用更新为指定格式。
        
        只更新参考文献章节，保留 [1]、[2] 等行内引用。
        """
        # 从参考文献章节提取 URL
        references_match = re.search(
            r'## (?:References|参考文献)\n\n(.*?)(?=\n##|\Z)',
            report_content,
            re.DOTALL
        )
        
        if not references_match:
            return report_content
        
        # 从已有参考文献中提取 URL
        url_pattern = r'https?://[^\s\)]+'
        existing_refs = references_match.group(1)
        urls = re.findall(url_pattern, existing_refs)
        
        if not urls:
            return report_content
        
        # 生成新的参考文献章节
        new_references = f"## 参考文献\n\n{self.format_references_section(urls, style, search_results)}"
        
        # 替换参考文献章节
        updated_report = re.sub(
            r'## (?:References|参考文献)\n\n.*?(?=\n##|\Z)',
            new_references,
            report_content,
            flags=re.DOTALL
        )
        
        return updated_report
