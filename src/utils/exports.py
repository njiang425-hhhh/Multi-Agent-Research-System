"""将研究报告导出为不同格式。"""

from pathlib import Path
from typing import Optional
import logging
import markdown
from datetime import datetime

logger = logging.getLogger(__name__)


class ReportExporter:
    """将报告导出为不同格式。"""
    
    def __init__(self):
        self.supported_formats = ['markdown', 'html', 'txt']
    
    def export_markdown(self, content: str, output_path: Path) -> Path:
        """导出为 Markdown（内容本身已经是 Markdown）。"""
        output_path.write_text(content, encoding='utf-8')
        logger.info(f"Markdown 已导出至 {output_path}")
        return output_path
    
    def export_html(self, content: str, output_path: Path) -> Path:
        """导出为带样式的 HTML。"""
        # 将 Markdown 转换为 HTML
        html_content = markdown.markdown(
            content,
            extensions=['extra', 'codehilite', 'tables']
        )
        
        # 包装为带样式的 HTML 模板
        html_template = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>研究报告</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            line-height: 1.6;
            max-width: 900px;
            margin: 0 auto;
            padding: 20px;
            color: #333;
            background-color: #fff;
        }}
        h1 {{
            color: #2c3e50;
            border-bottom: 3px solid #3498db;
            padding-bottom: 10px;
        }}
        h2 {{
            color: #34495e;
            margin-top: 30px;
            border-bottom: 2px solid #ecf0f1;
            padding-bottom: 5px;
        }}
        h3 {{
            color: #555;
            margin-top: 25px;
        }}
        code {{
            background-color: #f4f4f4;
            padding: 2px 6px;
            border-radius: 3px;
            font-family: 'Courier New', monospace;
        }}
        pre {{
            background-color: #f4f4f4;
            padding: 15px;
            border-radius: 5px;
            overflow-x: auto;
        }}
        blockquote {{
            border-left: 4px solid #3498db;
            margin: 0;
            padding-left: 20px;
            color: #666;
        }}
        table {{
            border-collapse: collapse;
            width: 100%;
            margin: 20px 0;
        }}
        th, td {{
            border: 1px solid #ddd;
            padding: 12px;
            text-align: left;
        }}
        th {{
            background-color: #3498db;
            color: white;
        }}
        a {{
            color: #3498db;
            text-decoration: none;
        }}
        a:hover {{
            text-decoration: underline;
        }}
        .footer {{
            margin-top: 40px;
            padding-top: 20px;
            border-top: 1px solid #ecf0f1;
            color: #7f8c8d;
            font-size: 0.9em;
        }}
    </style>
</head>
<body>
    {html_content}
    <div class="footer">
        <p>生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
    </div>
</body>
</html>"""
        
        output_path.write_text(html_template, encoding='utf-8')
        logger.info(f"HTML 已导出至 {output_path}")
        return output_path
    
    def export_txt(self, content: str, output_path: Path) -> Path:
        """导出为纯文本（去除 Markdown 格式）。"""
        import re
        # 移除 Markdown 格式
        text = content
        # 移除标题
        text = re.sub(r'^#+\s+', '', text, flags=re.MULTILINE)
        # 移除粗体/斜体
        text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
        text = re.sub(r'\*([^*]+)\*', r'\1', text)
        # 移除链接但保留文本
        text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
        # 移除代码块
        text = re.sub(r'```[^`]+```', '', text, flags=re.DOTALL)
        text = re.sub(r'`([^`]+)`', r'\1', text)
        
        output_path.write_text(text, encoding='utf-8')
        logger.info(f"纯文本已导出至 {output_path}")
        return output_path
    
    def export(self, content: str, output_path: Path, format: str = 'markdown') -> Path:
        """将内容导出为指定格式。
        
        Args:
            content: 报告内容（Markdown）
            output_path: 输出文件路径
            format: 导出格式（'markdown'、'html'、'txt'）
        
        Returns:
            已导出文件的路径
        """
        format = format.lower()
        
        if format not in self.supported_formats:
            raise ValueError(f"不支持的格式：{format}。支持的格式：{self.supported_formats}")
        
        # 必要时调整文件扩展名
        if format == 'html' and not output_path.suffix == '.html':
            output_path = output_path.with_suffix('.html')
        elif format == 'txt' and not output_path.suffix == '.txt':
            output_path = output_path.with_suffix('.txt')
        elif format == 'markdown' and not output_path.suffix in ['.md', '.markdown']:
            output_path = output_path.with_suffix('.md')
        
        if format == 'markdown':
            return self.export_markdown(content, output_path)
        elif format == 'html':
            return self.export_html(content, output_path)
        elif format == 'txt':
            return self.export_txt(content, output_path)
        else:
            raise ValueError(f"尚未实现格式为 {format} 的导出")
