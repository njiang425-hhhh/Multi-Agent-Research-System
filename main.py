"""深度研究代理的命令行入口。"""

import asyncio
import sys
from pathlib import Path
import logging

from src.config import config
from src.graph import run_research

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def main():
    """运行研究代理的主函数。"""
    
    # 验证配置
    try:
        config.validate_config()
    except ValueError as e:
        logger.error(f"配置错误：{e}")
        sys.exit(1)
    
    # 获取研究主题
    if len(sys.argv) > 1:
        topic = " ".join(sys.argv[1:])
    else:
        print("\n深度研究代理")
        print("=" * 50)
        topic = input("\n请输入研究主题：").strip()
    
    if not topic:
        logger.error("未提供研究主题")
        sys.exit(1)
    
    print(f"\n[信息] 开始对以下主题进行深度研究：{topic}\n")
    print("这可能需要几分钟，请稍候……\n")
    
    try:
        # 运行研究流程
        final_state = await run_research(topic, verbose=True)
        
        # LangGraph 返回包含状态的字典，直接访问其中的字段
        # 检查错误
        if final_state.get("error"):
            logger.error(f"研究失败：{final_state.get('error')}")
            sys.exit(1)
        
        # 显示结果
        print("\n" + "=" * 80)
        print("研究完成")
        print("=" * 80)
        
        if final_state.get("plan"):
            plan = final_state["plan"]
            print("\n研究计划摘要：")
            print(f"  - 目标：{len(plan.objectives)}")
            print(f"  - 搜索查询：{len(plan.search_queries)}")
            print(f"  - 报告章节：{len(plan.report_outline)}")
        
        print("\n研究数据摘要：")
        print(f"  - 搜索结果：{len(final_state.get('search_results', []))}")
        print(f"  - 关键发现：{len(final_state.get('key_findings', []))}")
        print(f"  - 报告章节：{len(final_state.get('report_sections', []))}")
        print(f"  - 迭代次数：{final_state.get('iterations', 0)}")
        
        # 保存报告
        if final_state.get("final_report"):
            output_dir = Path("outputs")
            output_dir.mkdir(exist_ok=True)
            
            # 创建安全文件名
            safe_topic = "".join(c if c.isalnum() or c in (' ', '-', '_') else '_' for c in topic)
            safe_topic = safe_topic[:50].strip()
            
            output_file = output_dir / f"{safe_topic}.md"
            final_report = final_state["final_report"]
            output_file.write_text(final_report, encoding='utf-8')
            
            print(f"\n[成功] 报告已保存至：{output_file}")
            print(f"      报告长度：{len(final_report)} 个字符")
            
            # 显示预览
            print("\n" + "=" * 80)
            print("报告预览")
            print("=" * 80)
            print(final_report[:1500])
            if len(final_report) > 1500:
                print(f"\n……（显示前 1500 个字符，共 {len(final_report)} 个字符）")
            print("\n" + "=" * 80)
            
        else:
            logger.warning("未生成报告")
        
    except KeyboardInterrupt:
        print("\n\n[警告] 用户中断了研究")
        sys.exit(0)
    except Exception as e:
        logger.error(f"[错误] 未预期的错误：{e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
