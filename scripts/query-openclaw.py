# -*- coding: utf-8 -*-
"""
汽车法规标准知识库 - OpenClaw 集成
调用阿里云百炼 API
"""

import os
import sys

# 读取 API Key
def get_api_key():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    api_key_file = os.path.join(script_dir, "API_KEY.md")
    
    try:
        with open(api_key_file, "r", encoding="utf-8") as f:
            content = f.read()
            for line in content.split("\n"):
                if line.strip().startswith("sk-"):
                    return line.strip()
    except Exception as e:
        print(f"读取 API Key 失败：{e}")
        return None
    return None

API_KEY = get_api_key()

if not API_KEY:
    print("❌ 未找到 API Key")
    sys.exit(1)

# 调用百炼 API
def query_knowledge_base(question: str) -> str:
    """查询知识库"""
    from openai import OpenAI
    
    client = OpenAI(
        api_key=API_KEY,
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    
    try:
        response = client.chat.completions.create(
            model="qwen-max",
            messages=[
                {"role": "system", "content": """你是汽车法规标准专家助手，基于 GB17691-2018 和 GB/T17692-2024 标准提供专业解答。

## 回答规范
1. 引用来源：回答时必须标注标准条款号
2. 数据准确：限值、公式、参数必须精确
3. 结构清晰：使用表格、列表等格式
4. 只回答汽车法规标准相关问题
"""},
                {"role": "user", "content": question}
            ],
            temperature=0.7,
            max_tokens=2000
        )
        
        return response.choices[0].message.content
        
    except Exception as e:
        return f"❌ 查询失败：{e}"

# 命令行调用
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法：python query-openclaw.py <问题>")
        print("示例：python query-openclaw.py '国六阶段ⅥB 的 NOx 限值是多少？'")
        sys.exit(1)
    
    question = " ".join(sys.argv[1:])
    print(f"🔍 查询：{question}\n")
    
    answer = query_knowledge_base(question)
    print(f"📖 回答：\n{answer}")
