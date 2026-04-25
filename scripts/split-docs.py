#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
汽车法规标准知识库 - 文档分块脚本
使用方法：python3 split-docs.py
"""

import os
import re
import json
from pathlib import Path

try:
    import fitz  # PyMuPDF
except ImportError:
    print("❌ 请先安装 PyMuPDF: pip install PyMuPDF")
    exit(1)


def extract_text_from_pdf(pdf_path):
    """从 PDF 中提取文本"""
    doc = fitz.open(pdf_path)
    full_text = ""
    
    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text()
        full_text += f"\n\n--- 第 {page_num + 1} 页 ---\n\n{text}"
    
    doc.close()
    return full_text


def clean_text(text):
    """清洗文本"""
    # 去除页眉页脚（简单规则）
    text = re.sub(r'第\s*\d+\s*页\s*', '', text)
    text = re.sub(r'—\s*第\s*\d+\s*页\s*—\s*', '', text)
    
    # 去除多余空白
    text = re.sub(r'\n\s*\n', '\n\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    
    return text.strip()


def split_by_chapters(text, max_chunk_size=800):
    """按章节分块"""
    chunks = []
    
    # 按章节标题分割（"第 X 章"、"第 X 条"等）
    chapter_pattern = r'(第\s*\d+\s*[章节条])'
    parts = re.split(chapter_pattern, text)
    
    current_chunk = ""
    for i, part in enumerate(parts):
        if re.match(chapter_pattern, part):
            # 新章节开始，保存当前块
            if current_chunk.strip():
                chunks.append(current_chunk.strip())
            current_chunk = part
        else:
            current_chunk += part
            
            # 如果块太大，继续分割
            if len(current_chunk) > max_chunk_size:
                chunks.append(current_chunk[:max_chunk_size].strip())
                current_chunk = current_chunk[max_chunk_size:]
    
    # 保存最后一个块
    if current_chunk.strip():
        chunks.append(current_chunk.strip())
    
    return chunks


def process_pdf(pdf_path, output_dir):
    """处理单个 PDF 文件"""
    print(f"📖 正在处理：{pdf_path}")
    
    # 提取文本
    text = extract_text_from_pdf(pdf_path)
    print(f"   提取文本：{len(text)} 字符")
    
    # 清洗文本
    text = clean_text(text)
    print(f"   清洗后：{len(text)} 字符")
    
    # 分块
    chunks = split_by_chapters(text)
    print(f"   分块数量：{len(chunks)} 块")
    
    # 保存分块
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    
    for i, chunk in enumerate(chunks):
        chunk_file = output_path / f"chunk_{i+1:03d}.md"
        with open(chunk_file, 'w', encoding='utf-8') as f:
            f.write(f"# 分块 {i+1}\n\n{chunk}")
    
    print(f"   ✅ 已保存到：{output_path}")
    return len(chunks)


def main():
    """主函数"""
    base_dir = Path(__file__).parent.parent
    standards_dir = base_dir / "docs" / "standards"
    kb_dir = base_dir / "docs" / "knowledge-base"
    
    # 查找所有 PDF 文件
    pdf_files = list(standards_dir.glob("*.pdf"))
    
    if not pdf_files:
        print("❌ 未找到 PDF 文件")
        return
    
    print(f"📚 找到 {len(pdf_files)} 个 PDF 文件")
    
    total_chunks = 0
    for pdf_file in pdf_files:
        # 创建输出目录
        output_dir = kb_dir / pdf_file.stem
        
        # 处理 PDF
        chunks = process_pdf(str(pdf_file), str(output_dir))
        total_chunks += chunks
    
    print(f"\n✅ 全部完成！共处理 {len(pdf_files)} 个文件，{total_chunks} 个分块")


if __name__ == "__main__":
    main()
