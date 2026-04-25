#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
汽车法规标准知识库 - API 查询示例
使用方法：python3 query-api.py "你的问题"
"""

import sys
import json

try:
    from dashscope import Application
except ImportError:
    print("❌ 请先安装 dashscope: pip install dashscope")
    exit(1)


class AutoRegsKB:
    """汽车法规标准知识库客户端"""
    
    def __init__(self, api_key, app_id):
        """
        初始化知识库客户端
        
        Args:
            api_key: 百炼 API Key
            app_id: 应用 ID
        """
        Application.api_key = api_key
        self.app_id = app_id
    
    def query(self, question, session_id=None):
        """
        查询知识库
        
        Args:
            question: 问题文本
            session_id: 会话 ID（可选，用于多轮对话）
        
        Returns:
            dict: 包含回答、引用来源等信息
        """
        try:
            response = Application.call(
                app_id=self.app_id,
                prompt=question,
                session_id=session_id
            )
            
            return {
                "success": True,
                "answer": response.output.text,
                "references": response.output.get("references", []),
                "session_id": response.session_id
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }
    
    def batch_query(self, questions):
        """
        批量查询
        
        Args:
            questions: 问题列表
        
        Returns:
            list: 回答列表
        """
        results = []
        for q in questions:
            result = self.query(q)
            results.append(result)
        return results


def main():
    """主函数"""
    # 配置（请替换为您的实际值）
    API_KEY = "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"  # 替换为您的 API Key
    APP_ID = "your-app-id"  # 替换为您的应用 ID
    
    # 创建客户端
    kb = AutoRegsKB(API_KEY, APP_ID)
    
    # 测试问题
    test_questions = [
        "国六阶段ⅥB 的 NOx 限值是多少？",
        "WHTC+WHSC 测试循环怎么理解？",
        "净功率怎么计算？附件功率如何修正？",
    ]
    
    # 如果提供了命令行参数，使用命令行参数
    if len(sys.argv) > 1:
        question = " ".join(sys.argv[1:])
        print(f"🔍 查询：{question}")
        result = kb.query(question)
        
        if result["success"]:
            print(f"\n💡 回答：\n{result['answer']}")
            if result.get("references"):
                print(f"\n📚 引用来源：")
                for ref in result["references"]:
                    print(f"  - {ref.get('title', '未知')} (相似度：{ref.get('score', 0):.2f})")
        else:
            print(f"\n❌ 错误：{result['error']}")
    else:
        # 运行测试问题
        print("🧪 运行测试问题...")
        for i, q in enumerate(test_questions, 1):
            print(f"\n{'='*50}")
            print(f"测试 {i}/{len(test_questions)}")
            print(f"🔍 查询：{q}")
            result = kb.query(q)
            
            if result["success"]:
                print(f"\n💡 回答：\n{result['answer'][:500]}...")
            else:
                print(f"\n❌ 错误：{result['error']}")


if __name__ == "__main__":
    main()
