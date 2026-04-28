# -*- coding: utf-8 -*-
"""
阿里云百炼知识库技能 - OpenClaw 集成
"""

import json
import os
from openai import OpenAI

# 加载配置
def load_config():
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)

CONFIG = load_config()

# 初始化客户端
client = OpenAI(
    api_key=CONFIG["api_key"],
    base_url=CONFIG["base_url"]
)

def query_knowledge_base(question: str) -> str:
    """查询知识库"""
    try:
        response = client.chat.completions.create(
            model=CONFIG["model"],
            messages=[
                {"role": "system", "content": """你是汽车法规标准专家助手，基于 GB17691-2018 和 GB/T17692-2024 标准提供专业解答。

## 回答规范
1. 引用来源：回答时必须标注标准条款号（如"根据 GB17691-2018 表 1"）
2. 数据准确：限值、公式、参数必须精确，不得估算
3. 结构清晰：使用表格、列表、公式等格式，便于阅读
4. 实际应用：在理论解读后，提供实际应用建议
5. 只回答与汽车法规标准相关的问题
"""},
                {"role": "user", "content": question}
            ],
            temperature=CONFIG["temperature"],
            max_tokens=CONFIG["max_tokens"]
        )
        
        return response.choices[0].message.content
        
    except Exception as e:
        return f"❌ 查询失败：{e}"

# OpenClaw 技能入口
def handle_command(command: str, args: str) -> str:
    """处理命令"""
    if command in ["知识库", "kb", "query", "查询"]:
        if not args:
            return "❌ 请输入问题，例如：/知识库 国六阶段ⅥB 的 NOx 限值是多少？"
        
        answer = query_knowledge_base(args)
        return answer
    else:
        return f"❌ 未知命令：{command}"

# 测试
if __name__ == "__main__":
    test_question = "国六阶段ⅥB 的 NOx 限值是多少？"
    print(f"🔍 查询：{test_question}\n")
    answer = query_knowledge_base(test_question)
    print(f"📖 回答：\n{answer}")
