"""基于域名权威性等因素评估来源可信度。"""

import re
from typing import List, Dict, Any
from urllib.parse import urlparse
import logging

logger = logging.getLogger(__name__)


class CredibilityScorer:
    """根据域名权威性和其他可信度因素为来源评分。"""
    
    # 可信域名
    TRUSTED_DOMAINS = {
        # 学术机构（全球）
        '.edu', '.ac.uk', '.ac.in', '.edu.in', '.edu.au', '.ac.jp',
        
        # 政府机构（全球）
        '.gov', '.gov.uk', '.gov.au', '.gov.ca', '.gov.in', '.europa.eu',
        
        # 国际新闻机构
        'bbc.com', 'bbc.co.uk', 'reuters.com', 'ap.org', 'npr.org',
        'theguardian.com', 'nytimes.com', 'washingtonpost.com', 'wsj.com',
        'ft.com', 'economist.com', 'bloomberg.com', 'cnbc.com',
        'cnn.com', 'aljazeera.com', 'france24.com', 'dw.com',
        
        # 印度新闻机构
        'thehindu.com', 'indianexpress.com', 'timesofindia.com', 'indiatimes.com',
        'economictimes.com', 'financialexpress.com', 'livemint.com',
        'business-standard.com', 'moneycontrol.com', 'businessline.in',
        'businesstoday.in', 'businessinsider.in',
        
        # 学术与研究平台
        'arxiv.org', 'scholar.google.com', 'researchgate.net', 'semanticscholar.org',
        'pubmed.ncbi.nlm.nih.gov', 'ncbi.nlm.nih.gov', 'nih.gov', 'nature.com',
        'sciencedirect.com', 'springer.com', 'wiley.com', 'ieee.org',
        'jstor.org', 'plos.org', 'sciencemag.org', 'cell.com',
        
        # 医疗与健康机构
        'who.int', 'cdc.gov', 'mayoclinic.org', 'nih.gov', 'webmd.com',
        
        # 国际组织
        'un.org', 'worldbank.org', 'imf.org', 'wto.org', 'oecd.org',
        
        # 科技与科学出版物
        'nature.com', 'scientificamerican.com', 'newscientist.com',
        'technologyreview.com', 'spectrum.ieee.org', 'arstechnica.com',
        'wired.com', 'techcrunch.com', 'theverge.com',
        
        # Wikipedia 与教育资源
        'wikipedia.org', 'britannica.com', 'khanacademy.org',
        
        # 法律与政策
        'supremecourt.gov', 'congress.gov', 'loc.gov',
        
        # 统计与数据
        'census.gov', 'bls.gov', 'data.gov', 'worldbank.org',
        'statista.com', 'pewresearch.org', 'gallup.com'
    }
    
    # 可疑模式
    SUSPICIOUS_PATTERNS = [
        r'\.(xyz|tk|ml|ga|cf|gq)$',  # 可疑顶级域名
        r'bit\.ly|tinyurl|t\.co',  # URL 缩短服务
        r'blogspot|wordpress\.com',  # 个人博客（可信度较低）
    ]
    
    def score_url(self, url: str) -> Dict[str, Any]:
        """评估 URL 的可信度。
        
        Returns:
            返回包含 'score'（0-100）、'factors' 和 'level'（low/medium/high）的字典
        """
        if not url:
            return {'score': 0, 'factors': ['无 URL'], 'level': 'low'}
        
        score = 50  # 基础分数
        factors = []
        
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            
            # 检查可信域名
            is_trusted = False
            for trusted in self.TRUSTED_DOMAINS:
                if trusted in domain:
                    score += 30
                    factors.append(f'可信域名：{trusted}')
                    is_trusted = True
                    break
            
            # 检查可疑模式
            is_suspicious = False
            for pattern in self.SUSPICIOUS_PATTERNS:
                if re.search(pattern, domain):
                    score -= 20
                    factors.append(f'可疑模式：{pattern}')
                    is_suspicious = True
                    break
            
            # HTTPS 加分
            if parsed.scheme == 'https':
                score += 5
                factors.append('已启用 HTTPS')
            else:
                score -= 10
                factors.append('未使用 HTTPS')
            
            # 域名年龄指标（根据域名结构进行启发式判断）
            if not is_trusted and not is_suspicious:
                # 较长的域名可能可信度较低（通常是垃圾网站）
                if len(domain.split('.')) > 3:
                    score -= 5
                    factors.append('复杂的域名结构')
            
            # 学术路径
            if '/papers/' in parsed.path or '/research/' in parsed.path or '/publications/' in parsed.path:
                score += 10
                factors.append('学术/研究路径')
            
            # 将分数限制在 0-100
            score = max(0, min(100, score))
            
            # 确定等级
            if score >= 70:
                level = 'high'
            elif score >= 40:
                level = 'medium'
            else:
                level = 'low'
            
            return {
                'score': score,
                'factors': factors if factors else ['普通域名'],
                'level': level,
                'domain': domain
            }
            
        except Exception as e:
            logger.warning(f"URL 评分出错 {url}：{e}")
            return {'score': 30, 'factors': ['评分错误'], 'level': 'low'}
    
    def score_search_results(self, results: List) -> List[Dict]:
        """为搜索结果列表评分。"""
        scored = []
        for result in results:
            if hasattr(result, 'url'):
                url = result.url
            elif isinstance(result, dict):
                url = result.get('url', '')
            else:
                url = str(result)
            
            credibility = self.score_url(url)
            scored.append({
                'result': result,
                'credibility': credibility
            })
        
        # 按可信度分数排序（从高到低）
        scored.sort(key=lambda x: x['credibility']['score'], reverse=True)
        return scored
    
    def filter_by_credibility(self, results: List, min_score: int = 40) -> List:
        """按最低可信度分数过滤结果。"""
        scored = self.score_search_results(results)
        filtered = [
            item['result'] for item in scored
            if item['credibility']['score'] >= min_score
        ]
        logger.info(f"已过滤 {len(results)} -> {len(filtered)} 个结果（最低分数={min_score}）")
        return filtered
