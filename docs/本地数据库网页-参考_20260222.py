# -*- coding: utf-8 -*-
#
# RAG 融合版 v6 (生成日期 2026-01-27)
# - 融合 app_updated (1).py 的核心链路（解析->向量化->索引->问答）
# - 保留/强化 rag_kb_qa_merged_v5.py 的生产化能力：UI主题、PDF体检+可选OCR、Chroma自检、docstore校验、
#   安全增量更新（kb_manifest.json + 稳定doc_id/file_hash）、引擎失效控制与自愈、调试面板与回答注释
#
"""
工业标准知识库问答系统（Streamlit + LlamaIndex + Chroma + Ollama）
- PDF 解析 -> 向量化 -> 建索引 -> 对话检索回答
- 兼容“仅向量恢复 / 完整恢复（docstore/BM25）”两种启动路径
- 增强诊断：PDF 文本层体检、Chroma 写入校验、docstore 持久化校验、索引诊断面板
- 可选离线 OCR 前置（ocrmypdf），用于扫描版/图片 PDF
"""

import os
import re
import sys
import time
import uuid
import shutil
import random
import warnings
import traceback
import logging
import subprocess
import json
import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple, List, Dict, Any

import streamlit as st

# ===============================
# UI 主题/风格（可扩展：后续可做导入/导出、每用户保存）
# ===============================
DEFAULT_THEMES: Dict[str, Dict[str, Any]] = {
    "Aurora Glass": {
        "bg1": "#0b1020",
        "bg2": "#2b1d4a",
        "accent": "#7c5cff",
        "card_alpha": 0.10,
        "radius": 18,
        "font": "ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, 'PingFang SC', 'Microsoft YaHei', sans-serif",
    },
    "Mono Ink": {
        "bg1": "#0a0a0a",
        "bg2": "#1a1a1a",
        "accent": "#eaeaea",
        "card_alpha": 0.08,
        "radius": 16,
        "font": "ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, 'PingFang SC', 'Microsoft YaHei', sans-serif",
    },
    "Sunset Paper": {
        "bg1": "#1a0b12",
        "bg2": "#2a1c10",
        "accent": "#ffb454",
        "card_alpha": 0.10,
        "radius": 18,
        "font": "ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, 'PingFang SC', 'Microsoft YaHei', sans-serif",
    },
}


def get_ui_theme() -> Dict[str, Any]:
    style = st.session_state.get("ui_style", {}) or {}
    theme_name = style.get("theme") or "Aurora Glass"
    base = (DEFAULT_THEMES.get(theme_name) or DEFAULT_THEMES["Aurora Glass"]).copy()
    # 允许用户覆盖主题参数
    for k in ["bg1", "bg2", "accent", "card_alpha", "radius", "font"]:
        if k in style and style[k] not in (None, ""):
            base[k] = style[k]
    return base


def apply_ui_css(theme: Dict[str, Any]) -> None:
    bg1 = theme.get("bg1", "#0b1020")
    bg2 = theme.get("bg2", "#2b1d4a")
    accent = theme.get("accent", "#7c5cff")
    card_alpha = float(theme.get("card_alpha", 0.10))
    radius = int(theme.get("radius", 18))
    font = theme.get("font", "ui-sans-serif, system-ui, sans-serif")

    # 玻璃态卡片背景：根据深色背景用白色透明层
    card_bg = f"rgba(255, 255, 255, {card_alpha:.3f})"
    border = "rgba(255, 255, 255, 0.10)"

    css = f"""
    <style>
      /* App 背景 */
      .stApp {{
        background: radial-gradient(1200px 800px at 20% 10%, rgba(124,92,255,0.22), transparent 55%),
                    radial-gradient(900px 600px at 80% 0%, rgba(255,180,84,0.14), transparent 55%),
                    linear-gradient(135deg, {bg1}, {bg2});
        font-family: {font};
      }}

      /* 顶部留白 */
      .block-container {{
        padding-top: 1.1rem;
        padding-bottom: 2.2rem;
      }}

      /* 统一卡片（expander/sidebars 的块） */
      section[data-testid="stSidebar"] > div {{
        background: {card_bg};
        border-right: 1px solid {border};
        backdrop-filter: blur(12px);
      }}

      div[data-testid="stExpander"] > details {{
        background: {card_bg};
        border: 1px solid {border};
        border-radius: {radius}px;
        padding: 0.35rem 0.6rem;
      }}

      /* Chat 消息气泡微调 */
      div[data-testid="stChatMessage"] {{
        border-radius: {radius}px;
      }}

      /* 主按钮 */
      div.stButton > button {{
        border-radius: {radius}px !important;
        border: 1px solid {border} !important;
        background: linear-gradient(135deg, rgba(124,92,255,0.35), rgba(255,180,84,0.18)) !important;
        color: white !important;
      }}
      div.stButton > button:hover {{
        border-color: rgba(255,255,255,0.22) !important;
        transform: translateY(-1px);
      }}

      /* 输入框 */
      input, textarea {{
        border-radius: {radius}px !important;
      }}

      /* 强调色（部分控件） */
      a, code {{
        color: {accent} !important;
      }}
    </style>
    """
    st.markdown(css, unsafe_allow_html=True)


from tenacity import retry, stop_after_attempt, wait_fixed

# ===============================
# 1) 系统环境与路径
# ===============================
os.environ.pop("SSL_CERT_FILE", None)
os.environ.pop("SSL_CERT_DIR", None)
os.environ.setdefault("HF_HUB_OFFLINE", "1")  # 避免离线环境误联网
warnings.filterwarnings("ignore", category=UserWarning)

try:
    import importlib.metadata as md

    import chromadb
    import torch
    import requests

    from llama_index.core import (
        VectorStoreIndex,
        StorageContext,
        Settings,
        SimpleDirectoryReader,
        load_index_from_storage,
    )
    from llama_index.vector_stores.chroma import ChromaVectorStore
    from llama_index.llms.ollama import Ollama
    from llama_index.embeddings.huggingface import HuggingFaceEmbedding
    from llama_index.core.node_parser import SentenceWindowNodeParser
    from llama_index.core.retrievers import QueryFusionRetriever
    from llama_index.retrievers.bm25 import BM25Retriever
    from llama_index.core.postprocessor.metadata_replacement import MetadataReplacementPostProcessor
    from llama_index.postprocessor.sbert_rerank import SentenceTransformerRerank
    from llama_index.core.chat_engine import CondensePlusContextChatEngine
    from llama_index.core.memory import ChatMemoryBuffer
except ImportError as e:
    st.error(
        "❌ 核心组件缺失：{}\n\n"
        "建议（示例）：\n"
        "pip install -U streamlit chromadb torch requests tenacity\n"
        "pip install -U llama-index-core llama-index-llms-ollama llama-index-embeddings-huggingface\n"
        "pip install -U llama-index-vector-stores-chroma llama-index-retrievers-bm25 llama-index-postprocessor-sbert-rerank sentence-transformers\n".format(
            e
        )
    )
    st.stop()

# 可选：PyMuPDF 用于“PDF 文本层体检”
try:
    import fitz  # PyMuPDF
except Exception:
    fitz = None

BASE_DIR = os.environ.get("KB_BASE_DIR", r"E:\personal-kb")
RAW_DIR = os.path.join(BASE_DIR, "data/raw")
OCR_DIR = os.path.join(BASE_DIR, "data/ocr")  # 可选：OCR 后 PDF 放这里
UPLOAD_DIR = os.path.join(BASE_DIR, "data/uploads")  # 手动上传参考文件默认目录
CHROMA_DIR = os.path.join(BASE_DIR, "chroma")
INDEX_DIR = os.path.join(BASE_DIR, "index_store")  # docstore/index_store 持久化目录
MANIFEST_PATH = os.path.join(INDEX_DIR, "kb_manifest.json")  # 文件级增量同步清单
EMBED_PATH = os.path.join(BASE_DIR, "models/bge-m3")
RERANK_PATH = os.path.join(BASE_DIR, "models/bge-reranker-base")

for p in [RAW_DIR, OCR_DIR, UPLOAD_DIR, CHROMA_DIR, INDEX_DIR]:
    os.makedirs(p, exist_ok=True)

# 支持的参考文件格式（可按需扩展）
SUPPORTED_EXTS = [".pdf", ".docx", ".pptx", ".ppt", ".txt", ".md", ".html", ".htm"]
SUPPORTED_UPLOAD_TYPES = [e.lstrip(".") for e in SUPPORTED_EXTS]


def list_source_files(directory: str) -> List[str]:
    """列出目录下支持的文件（仅文件名）"""
    if not os.path.exists(directory):
        return []
    files = []
    for f in os.listdir(directory):
        fp = os.path.join(directory, f)
        if not os.path.isfile(fp):
            continue
        ext = os.path.splitext(f)[1].lower()
        if ext in SUPPORTED_EXTS:
            files.append(f)
    files.sort(key=lambda x: x.lower())
    return files


def _unique_filename(target_dir: str, filename: str) -> str:
    """若目标已存在同名文件，追加时间戳/短uuid避免覆盖"""
    base, ext = os.path.splitext(filename)
    safe_base = re.sub(r'[\\/:*?"<>|]', "_", base).strip() or "file"
    safe_ext = ext if ext else ""
    out = f"{safe_base}{safe_ext}"
    fp = os.path.join(target_dir, out)
    if not os.path.exists(fp):
        return out
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    short = uuid.uuid4().hex[:6]
    out = f"{safe_base}_{ts}_{short}{safe_ext}"
    return out


def save_uploaded_files(uploaded_files, target_dir: str) -> Tuple[List[str], List[str]]:
    """保存 st.file_uploader 上传的文件，返回 (保存后的完整路径列表, 说明信息列表)"""
    os.makedirs(target_dir, exist_ok=True)
    saved_paths = []
    msgs = []
    for uf in (uploaded_files or []):
        try:
            name = uf.name
            ext = os.path.splitext(name)[1].lower()
            if ext not in SUPPORTED_EXTS:
                msgs.append(f"跳过不支持格式: {name}")
                continue
            final_name = _unique_filename(target_dir, name)
            out_path = os.path.join(target_dir, final_name)
            with open(out_path, "wb") as f:
                f.write(uf.getbuffer())
            saved_paths.append(out_path)
            msgs.append(f"已保存: {final_name}")
        except Exception as e:
            msgs.append(f"保存失败: {getattr(uf, 'name', '(unknown)')} | {type(e).__name__}: {e}")
    return saved_paths, msgs


def preflight_file_dependencies(paths: List[str]) -> None:
    """根据待解析文件类型做依赖预检，给出更明确的错误提示"""
    exts = {os.path.splitext(p)[1].lower() for p in paths}
    if ".docx" in exts:
        try:
            import docx2txt
        except Exception:
            raise RuntimeError("解析 .docx 需要依赖 docx2txt：pip install docx2txt")
    if ".pptx" in exts or ".ppt" in exts:
        try:
            import pptx  # python-pptx
        except Exception:
            raise RuntimeError("解析 .pptx/.ppt 需要依赖 python-pptx：pip install python-pptx（建议优先使用 .pptx）")


# ===============================
# 2) 工具函数
# ===============================
def clean_think_tags(text: str) -> str:
    """清理模型的思考标签，避免 UI 露出 <thinking>/<think> ..."""
    if not text:
        return ""
    # 常见：<thinking>...</thinking> / <think>...</think>（也可能只吐出 </think>）
    pattern = r"<(thinking|reasoning|pondering|think)>.*?</\1>"
    out = re.sub(pattern, "", text, flags=re.DOTALL | re.IGNORECASE)
    out = re.sub(r"</think>", "", out, flags=re.IGNORECASE)
    out = re.sub(r"<think>", "", out, flags=re.IGNORECASE)
    return out.strip()


def pkg_version(name: str) -> str:
    try:
        return md.version(name)
    except Exception:
        return "unknown"


def show_versions_ui():
    """在侧边栏展示关键运行时版本（避免跑错环境）"""
    st.caption(f"python: {sys.version.split()[0]}")
    st.caption(f"chromadb: {pkg_version('chromadb')}")
    st.caption(f"llama-index-core: {pkg_version('llama-index-core')}")
    st.caption(f"llama-index-vector-stores-chroma: {pkg_version('llama-index-vector-stores-chroma')}")
    st.caption(f"llama-index-llms-ollama: {pkg_version('llama-index-llms-ollama')}")
    st.caption(f"llama-index-embeddings-huggingface: {pkg_version('llama-index-embeddings-huggingface')}")
    st.caption(f"sentence-transformers: {pkg_version('sentence-transformers')}")
    st.caption(f"torch: {pkg_version('torch')}")
    st.caption(f"pymupdf: {pkg_version('pymupdf') if fitz else '(not installed)'}")
    st.caption(f"BASE_DIR: {BASE_DIR}")


def has_docstore_persist(index_dir: str) -> bool:
    """docstore.json 存在且非空，才算可用于完整恢复"""
    p = os.path.join(index_dir, "docstore.json")
    return os.path.exists(p) and os.path.getsize(p) > 10


def get_file_hash_md5(file_path: str, chunk_size: int = 1024 * 1024) -> str:
    """稳定的文件哈希（用于增量同步）"""
    md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        while True:
            data = f.read(chunk_size)
            if not data:
                break
            md5.update(data)
    return md5.hexdigest()


def load_kb_manifest(index_dir: str) -> Dict[str, Any]:
    """加载文件级增量清单；不存在则返回空清单"""
    p = os.path.join(index_dir, "kb_manifest.json")
    if not os.path.exists(p):
        return {"version": 1, "source_dir": None, "files": {}}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"version": 1, "source_dir": None, "files": {}}
        data.setdefault("version", 1)
        data.setdefault("source_dir", None)
        data.setdefault("files", {})
        if not isinstance(data["files"], dict):
            data["files"] = {}
        return data
    except Exception:
        # manifest 损坏 -> 视为不存在；增量时将触发清空重建以确保一致性
        return {"version": 1, "source_dir": None, "files": {}, "_corrupted": True}


def save_kb_manifest(index_dir: str, manifest: Dict[str, Any]) -> None:
    """原子写入 manifest，避免写一半导致损坏"""
    os.makedirs(index_dir, exist_ok=True)
    p = os.path.join(index_dir, "kb_manifest.json")
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def build_selection_maps(source_dir: str, selected_files: List[str]) -> Tuple[
    Dict[str, str], Dict[str, str], Dict[str, Dict[str, Any]]]:
    """把 UI 选择转换为稳定 doc_id（相对路径）-> 绝对路径，并计算 hash/mtime/size"""
    doc_id_to_path: Dict[str, str] = {}
    doc_id_to_hash: Dict[str, str] = {}
    doc_id_to_stat: Dict[str, Dict[str, Any]] = {}
    for f in selected_files:
        p = os.path.join(source_dir, f)
        doc_id = os.path.relpath(p, source_dir)  # 稳定：以 source_dir 为基准的相对路径
        doc_id_to_path[doc_id] = p
        try:
            doc_id_to_hash[doc_id] = get_file_hash_md5(p)
        except Exception:
            doc_id_to_hash[doc_id] = ""
        try:
            doc_id_to_stat[doc_id] = {
                "mtime": os.path.getmtime(p),
                "size": os.path.getsize(p),
            }
        except Exception:
            doc_id_to_stat[doc_id] = {"mtime": None, "size": None}
    return doc_id_to_path, doc_id_to_hash, doc_id_to_stat


def assign_doc_ids_and_hash(
        documents: List[Any],
        parse_path_to_doc_id: Dict[str, str],
        doc_id_to_hash: Dict[str, str],
        doc_id_to_path: Dict[str, str],
        source_dir: str,
) -> None:
    """统一设置稳定 doc_id + file_hash 等元数据，确保 delete_ref_doc 可用"""
    for d in documents:
        fp = None
        try:
            fp = d.metadata.get("file_path") or d.metadata.get("filename")
        except Exception:
            fp = None

        doc_id = parse_path_to_doc_id.get(fp) if fp else None
        if not doc_id:
            doc_id = os.path.basename(fp) if fp else str(uuid.uuid4())

        # 兼容：有的叫 doc_id，有的叫 id_
        try:
            d.doc_id = doc_id
        except Exception:
            pass
        try:
            d.id_ = doc_id
        except Exception:
            pass

        try:
            d.metadata["doc_id"] = doc_id
            d.metadata["source_dir"] = source_dir
            d.metadata["source_path"] = doc_id_to_path.get(doc_id, "")
            d.metadata["file_hash"] = doc_id_to_hash.get(doc_id, "")
        except Exception:
            pass


def index_store_files(index_dir: str) -> Dict[str, int]:
    """列出 index_store 关键文件及大小（字节）"""
    out = {}
    for name in ["docstore.json", "index_store.json", "vector_store.json"]:
        fp = os.path.join(index_dir, name)
        if os.path.exists(fp):
            out[name] = os.path.getsize(fp)
    return out


def safe_docstore_nodes(index: Optional[VectorStoreIndex]) -> List[Any]:
    """安全获取 docstore nodes（兼容 from_vector_store 恢复为空）"""
    if index is None:
        return []
    ds = getattr(index, "docstore", None)
    docs = getattr(ds, "docs", None) if ds else None
    if not docs:
        return []
    try:
        return list(docs.values())
    except Exception:
        return []


@dataclass
class PdfTextStats:
    pages: int
    nonempty_pages: int
    total_chars: int


def pdf_text_stats_pymupdf(pdf_path: str, max_pages: int = 500) -> Optional[PdfTextStats]:
    """
    快速检测 PDF 是否有可提取文本层。
    - 仅用于“扫描版/图片 PDF”与“后续链路问题”的区分
    """
    if fitz is None:
        return None
    doc = fitz.open(pdf_path)
    pages = min(doc.page_count, max_pages)
    nonempty = 0
    total_chars = 0
    for i in range(pages):
        txt = doc.load_page(i).get_text("text") or ""
        t = txt.strip()
        if t:
            nonempty += 1
            total_chars += len(t)
    doc.close()
    return PdfTextStats(pages=pages, nonempty_pages=nonempty, total_chars=total_chars)


def is_probably_scanned(stats: Optional[PdfTextStats]) -> bool:
    """经验阈值：非空页==0 或 总字符极小，基本就是扫描版/解析不到文本层"""
    if stats is None:
        return False
    return stats.nonempty_pages == 0 or stats.total_chars < 50


def run_ocrmypdf(input_pdf: str, output_pdf: str, lang: str = "chi_sim+eng") -> Tuple[bool, str]:
    """
    可选：调用 ocrmypdf 做离线 OCR，生成“带文本层 PDF”
    需要用户本机已安装：ocrmypdf + tesseract（含语言包）
    """
    exe = shutil.which("ocrmypdf")
    if not exe:
        return False, "未找到 ocrmypdf 可执行文件。请先安装：pip install ocrmypdf，并确保命令可用。"

    cmd = [
        exe,
        "--skip-text",
        "--deskew",
        "--clean",
        "--optimize",
        "1",
        "-l",
        lang,
        input_pdf,
        output_pdf,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            stdout = (proc.stdout or "").strip()
            msg = stderr if stderr else stdout if stdout else f"returncode={proc.returncode}"
            return False, f"OCR 失败：{msg}"
        return True, f"OCR 成功：{os.path.basename(output_pdf)}"
    except Exception as e:
        return False, f"OCR 调用异常：{type(e).__name__}: {e}"


@st.cache_resource(show_spinner=False)
def init_global_settings(embed_path: str, rerank_path: str):
    """
    全局 Settings：embedding + node parser
    注意：embedding batch 在低资源机器上很敏感，可通过环境变量覆盖：
    - KB_EMBED_BATCH_GPU（默认 8）
    - KB_EMBED_BATCH_CPU（默认 4）
    """
    gpu_on = torch.cuda.is_available()

    if not os.path.exists(embed_path):
        st.error(f"❌ 嵌入模型路径不存在: {embed_path}\n请先把 BAAI/bge-m3 下载到该目录。")
        st.stop()

    rerank_ok = os.path.exists(rerank_path)

    try:
        gpu_bs = int(os.environ.get("KB_EMBED_BATCH_GPU", "8"))
        cpu_bs = int(os.environ.get("KB_EMBED_BATCH_CPU", "4"))
    except Exception:
        gpu_bs, cpu_bs = 8, 4

    Settings.embed_model = HuggingFaceEmbedding(
        model_name=embed_path,
        device="cuda" if gpu_on else "cpu",
        embed_batch_size=gpu_bs if gpu_on else cpu_bs,
        trust_remote_code=True,
    )
    Settings.node_parser = SentenceWindowNodeParser.from_defaults(
        window_size=3,
        window_metadata_key="window",
        original_text_metadata_key="original_text",
    )
    return gpu_on, rerank_ok


@st.cache_resource(show_spinner=False)
def get_vector_store(chroma_dir: str, collection_name: str, reset_token: int = 0):
    """
    reset_token 用于“清空 collection 后强制重建对象”，避免 cache 复用旧句柄
    """
    client = chromadb.PersistentClient(path=chroma_dir)
    col = client.get_or_create_collection(collection_name)
    vs = ChromaVectorStore(col)
    return client, vs, col


def load_index(vs) -> Tuple[Optional[VectorStoreIndex], str]:
    """
    有 docstore.json -> load_index_from_storage（BM25可用）
    否则 -> from_vector_store（向量检索可用，BM25可能不可用）
    """
    try:
        if has_docstore_persist(INDEX_DIR):
            storage_ctx = StorageContext.from_defaults(vector_store=vs, persist_dir=INDEX_DIR)
            index = load_index_from_storage(storage_ctx)
            n = len(safe_docstore_nodes(index))
            return index, f"✅ 从 index_store 恢复成功（docstore 节点 {n}）"
        else:
            index = VectorStoreIndex.from_vector_store(vs)
            return index, "🟡 仅从 Chroma 恢复向量索引（未发现 docstore.json；BM25 可能不可用）"
    except Exception as e:
        # 尝试降级，至少让应用可用
        try:
            index = VectorStoreIndex.from_vector_store(vs)
            return index, f"🟠 index_store 加载失败，已降级为仅向量索引：{type(e).__name__}: {e}"
        except Exception as e2:
            return None, f"❌ 加载失败（含降级失败）: {type(e2).__name__}: {e2}"


def chroma_healthcheck(chroma_client, chroma_dir: str) -> Tuple[bool, str]:
    """
    写入/读取/删除自检。注意集合名必须合法（小写字母/数字开头和结尾）。
    """
    try:
        name = f"healthcheck_{uuid.uuid4().hex}"  # ✅ 合法：小写字母开头，数字结尾
        col = chroma_client.get_or_create_collection(name)
        col.add(ids=["1"], documents=["healthcheck"], metadatas=[{"ok": True}])
        c = col.count()
        chroma_client.delete_collection(name)
        return True, f"✅ 写入/读取/删除正常（写入后 count={c}） | DB={chroma_dir}"
    except Exception as e:
        return False, f"❌ Healthcheck 失败：{type(e).__name__}: {e}"


def try_chroma_peek(chroma_col, n: int = 3) -> Optional[Dict[str, Any]]:
    """少量 peek，避免一次性拉太多"""
    try:
        return chroma_col.peek(n)
    except Exception:
        return None


def validate_documents_text(documents) -> Tuple[int, int]:
    """
    返回：(non_empty_docs, total_chars)
    注意：PDF loader 可能 1页=1document，因此这里的“有效页”就是 non_empty_docs
    """
    texts = [(getattr(d, "text", "") or "") for d in documents]
    total_chars = sum(len(t.strip()) for t in texts)
    non_empty = sum(1 for t in texts if len(t.strip()) > 0)
    return non_empty, total_chars


# ===============================
# 2.6) P0：对话持久化 + 构建互斥锁
# ===============================
CONV_PERSIST_PATH = os.path.join(INDEX_DIR, "conversations.json")
BUILD_LOCK_PATH = os.path.join(INDEX_DIR, ".kb_build.lock")


def _json_safe(obj):
    """尽量把对象转成可 JSON 序列化（created_at 等）。"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj


def persist_conversations_to_disk() -> None:
    """把 st.session_state.conversations 落盘（跨刷新/重启保留）。"""
    try:
        convs = st.session_state.get("conversations", {}) or {}
        out = {}
        for cid, c in convs.items():
            out[cid] = {
                "messages": c.get("messages", []) or [],
                "engine_ver": int(c.get("engine_ver", st.session_state.get("kb_revision", 0) or 0)),
                "engine_status": c.get("engine_status", "未初始化"),
                "model": c.get("model", "qwen2:7b"),
                "temp": float(c.get("temp", 0.1)),
                "limit": int(c.get("limit", 2048)),
                "enable_rerank": bool(c.get("enable_rerank", True)),
                "vec_top_k": int(c.get("vec_top_k", 6)),
                "bm25_top_k": int(c.get("bm25_top_k", 6)),
                "fusion_top_k": int(c.get("fusion_top_k", 6)),
                "rerank_top_n": int(c.get("rerank_top_n", 4)),
                "created_at": _json_safe(c.get("created_at", datetime.now())),
            }
        payload = {
            "version": 1,
            "saved_at": datetime.now().isoformat(),
            "current_id": st.session_state.get("current_id"),
            "conversations": out,
        }
        os.makedirs(INDEX_DIR, exist_ok=True)
        tmp = CONV_PERSIST_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=_json_safe)
        os.replace(tmp, CONV_PERSIST_PATH)
    except Exception as e:
        dbg(f"对话落盘失败：{type(e).__name__}: {e}", "WARNING")


def load_conversations_from_disk() -> None:
    """启动时从磁盘恢复对话（若存在）。"""
    try:
        if not os.path.exists(CONV_PERSIST_PATH):
            return
        with open(CONV_PERSIST_PATH, "r", encoding="utf-8") as f:
            payload = json.load(f) or {}
        convs = payload.get("conversations") or {}
        if not isinstance(convs, dict) or not convs:
            return

        st.session_state.conversations = {}
        for cid, c in convs.items():
            if not isinstance(c, dict):
                continue
            created_at = c.get("created_at")
            try:
                created_at = datetime.fromisoformat(created_at) if isinstance(created_at, str) else datetime.now()
            except Exception:
                created_at = datetime.now()

            st.session_state.conversations[cid] = {
                "messages": c.get("messages", []) or [],
                "engine": None,  # 引擎不落盘，恢复后按需重建
                "engine_ver": int(c.get("engine_ver", st.session_state.get("kb_revision", 0) or 0)),
                "engine_status": c.get("engine_status", "未初始化（已从磁盘恢复对话）"),
                "model": c.get("model", "qwen2:7b"),
                "temp": float(c.get("temp", 0.1)),
                "limit": int(c.get("limit", 2048)),
                "enable_rerank": bool(c.get("enable_rerank", True)),
                "vec_top_k": int(c.get("vec_top_k", 6)),
                "bm25_top_k": int(c.get("bm25_top_k", 6)),
                "fusion_top_k": int(c.get("fusion_top_k", 6)),
                "rerank_top_n": int(c.get("rerank_top_n", 4)),
                "created_at": created_at,
            }

        cur = payload.get("current_id")
        if cur in st.session_state.conversations:
            st.session_state.current_id = cur
        else:
            latest = sorted(
                st.session_state.conversations.keys(),
                key=lambda k: st.session_state.conversations[k]["created_at"],
                reverse=True,
            )[0]
            st.session_state.current_id = latest

        dbg(f"对话已从磁盘恢复：{len(st.session_state.conversations)} 个", "INFO")
    except Exception as e:
        dbg(f"对话恢复失败：{type(e).__name__}: {e}", "WARNING")


def try_acquire_build_lock() -> Tuple[bool, str]:
    """
    简单跨进程互斥锁（不依赖第三方库）。
    成功返回 (True, msg)，失败返回 (False, msg)。
    """
    try:
        os.makedirs(INDEX_DIR, exist_ok=True)
        fd = os.open(BUILD_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps({"pid": os.getpid(), "ts": datetime.now().isoformat()}, ensure_ascii=False))
        return True, "✅ 已获取构建锁"
    except FileExistsError:
        try:
            with open(BUILD_LOCK_PATH, "r", encoding="utf-8") as f:
                info = f.read().strip()
        except Exception:
            info = "(无法读取锁信息)"
        return False, f"⚠️ 构建锁已被占用：{info}"
    except Exception as e:
        return False, f"⚠️ 获取构建锁失败：{type(e).__name__}: {e}"


def release_build_lock() -> None:
    try:
        if os.path.exists(BUILD_LOCK_PATH):
            os.remove(BUILD_LOCK_PATH)
    except Exception:
        pass


# ===============================
# 2.5) 调试日志 & 回答注释（精确度/来源，保留近两次）
# ===============================
def dbg(msg: str, level: str = "INFO") -> None:
    """追加一条调试日志到 session_state（用于右侧调试面板）"""
    try:
        if "debug_logs" not in st.session_state:
            st.session_state.debug_logs = []
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        st.session_state.debug_logs.append(f"{ts} [{level}] {msg}")
        # 防止无限增长
        st.session_state.debug_logs = st.session_state.debug_logs[-800:]
    except Exception:
        pass


def set_last_exception(e: Exception, where: str = "") -> None:
    """记录最近一次异常堆栈，展示到右侧面板"""
    try:
        st.session_state.last_exception_where = where
        st.session_state.last_exception_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        st.session_state.last_exception = f"{type(e).__name__}: {e}"
        st.session_state.last_traceback = traceback.format_exc()
        dbg(f"异常@{where}: {type(e).__name__}: {e}", "ERROR")
    except Exception:
        pass


# ===============================
# KB 版本/引擎失效控制（稳定性关键）
# ===============================
def invalidate_all_engines(reason: str) -> None:
    """让所有会话的 RAG 引擎失效，避免复用旧 retriever / 旧 Chroma collection 句柄。"""
    try:
        convs = st.session_state.get("conversations", {}) or {}
        for _cid, _c in convs.items():
            _c["engine"] = None
            _c["engine_ver"] = -1
            _c["engine_status"] = f"已失效：{reason}（请重建引擎）"
    except Exception:
        pass


def bump_kb_revision(reason: str, *, bump_chroma_token: bool = False) -> int:
    """
    任何会影响检索结果/向量库句柄的动作（清库/重建/切目录/自愈刷新）都应该调用它。

    - kb_revision：用于判定对话引擎是否需要重建
    - chroma_reset_token：仅用于强制 st.cache_resource 重新创建 Chroma client/collection 句柄
    """
    try:
        if bump_chroma_token:
            st.session_state.chroma_reset_token = int(st.session_state.get("chroma_reset_token", 0)) + 1

        st.session_state.kb_revision = int(st.session_state.get("kb_revision", 0)) + 1
        st.session_state.kb_revision_reason = reason
        st.session_state.kb_revision_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        invalidate_all_engines(reason)
        dbg(
            f"KB_REV bumped -> {st.session_state.kb_revision} | reason={reason} | bump_chroma_token={bump_chroma_token}",
            "INFO",
        )
        return st.session_state.kb_revision
    except Exception:
        return int(st.session_state.get("kb_revision", 0))


def recover_chroma_collection_missing(e: Exception, where: str = "") -> None:
    """Chroma collection 句柄失效/被删除时的自愈：刷新 client/collection，并强制用户重建索引/引擎。"""
    try:
        set_last_exception(e, where=where or "chroma_collection_missing")
        bump_kb_revision("检测到 Chroma collection 缺失：已自动刷新连接", bump_chroma_token=True)

        # 旧 index/engine 都可能引用旧 collection，直接作废并要求重建更安全
        st.session_state.global_index = None
        st.session_state.index_status = "🟠 检测到 Chroma collection 缺失（已自动刷新）。请重新构建索引。"
        st.session_state.kb_pending_reset = True
    except Exception:
        pass


def _guess_source_name(metadata: Dict[str, Any]) -> str:
    src = (metadata.get("file_name") or metadata.get("filename") or metadata.get("source") or metadata.get(
        "file_path") or metadata.get("path") or "")
    src = str(src) if src is not None else ""
    return os.path.basename(src) if src else "(unknown)"


def build_answer_note(question: str, answer: str, response: Any, elapsed_s: float) -> Dict[str, Any]:
    """构造回答注释：精确度 + 参考来源（启发式），供 UI 展示；只保留近两次。"""
    source_nodes = getattr(response, "source_nodes", None) or []
    rows: List[Dict[str, Any]] = []
    scores: List[float] = []

    for i, sn in enumerate(source_nodes, start=1):
        node = getattr(sn, "node", None)
        md = getattr(node, "metadata", {}) or {}
        score = getattr(sn, "score", None)
        if isinstance(score, (int, float)):
            scores.append(float(score))

        src = _guess_source_name(md)
        loc = md.get("page_label") or md.get("page") or md.get("page_number") or md.get("slide") or md.get(
            "section") or ""
        text = (getattr(node, "text", "") or "").strip()
        snippet = re.sub(r"\s+", " ", text)[:180]

        rows.append({
            "rank": i,
            "source": src,
            "loc": loc,
            "score": None if score is None else float(score),
            "snippet": snippet,
        })

    # 精确度：启发式
    if not rows:
        level = "低"
        detail = "未检索到可引用来源（source_nodes=0）。可能：知识库未构建/索引为空/检索未命中。"
    else:
        if scores:
            top = max(scores)
            avg = sum(scores) / max(len(scores), 1)
            if top >= 0.75 and avg >= 0.55:
                level = "高"
            elif top >= 0.55:
                level = "中"
            else:
                level = "较低"
            detail = f"启发式评估：top_score={top:.3f}, avg_score={avg:.3f}, 引用数={len(rows)}；耗时={elapsed_s:.2f}s"
        else:
            level = "中"
            detail = f"有引用来源（{len(rows)} 条），但当前返回未提供 score 字段；耗时={elapsed_s:.2f}s"

    return {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "question": question,
        "answer": answer,
        "precision": level,
        "detail": detail,
        "sources": rows[:10],  # UI 只展示前 10 条
    }


def push_answer_note(note: Dict[str, Any]) -> None:
    try:
        if "answer_notes" not in st.session_state:
            st.session_state.answer_notes = []
        st.session_state.answer_notes = (st.session_state.answer_notes + [note])[-2:]
    except Exception:
        pass


def render_answer_note(note: Dict[str, Any]) -> None:
    title = f"📌 回答注释：精确度 {note.get('precision', '?')} | 引用 {len(note.get('sources', []))} 条"
    with st.expander(title, expanded=False):
        st.caption(note.get("detail", ""))
        sources = note.get("sources") or []
        if sources:
            st.dataframe(sources, use_container_width=True)
        else:
            st.caption("（本次无可展示引用）")


def render_recent_notes() -> None:
    notes = st.session_state.get("answer_notes") or []
    with st.expander("🕘 最近两次回答注释（仅保留两条）", expanded=False):
        if not notes:
            st.caption("（暂无）")
            return
        for n in reversed(notes):
            st.markdown(f"**时间**：{n.get('ts', '')}")
            st.markdown(f"**Q**：{n.get('question', '')}")
            st.markdown(f"**精确度**：{n.get('precision', '')}  —  {n.get('detail', '')}")
            srcs = n.get("sources") or []
            if srcs:
                st.markdown("**来源（前5）**：")
                for r in srcs[:5]:
                    loc = r.get("loc")
                    sc = r.get("score")
                    st.markdown(
                        f"- {r.get('source')} {(' | ' + str(loc)) if loc else ''} {(' | score=' + str(sc)) if sc is not None else ''}")
            st.divider()


def render_debug_panel() -> None:
    """右侧调试面板：运行日志/错误/关键状态。"""
    st.subheader("🧪 调试面板")

    show = st.session_state.get("show_debug_panel", True)
    if not show:
        st.caption("调试面板已隐藏（可在左侧打开）。")
        return

    # 关键状态
    st.caption("关键状态")
    _conv = st.session_state.conversations.get(st.session_state.current_id, {}) or {}
    st.write({
        "index_status": st.session_state.get("index_status", ""),
        "engine_status": _conv.get("engine_status", ""),
        "current_model": _conv.get("model", ""),
        "last_source_dir": st.session_state.get("kb_last_source_dir"),
        "pending_reset": st.session_state.get("kb_pending_reset", False),
        "last_built_files": (st.session_state.get("last_built_files", []) or [])[:8],
    })

    # 最近异常
    last_tb = st.session_state.get("last_traceback", "")
    if last_tb:
        with st.expander("❌ 最近一次异常堆栈", expanded=False):
            st.caption(
                f"{st.session_state.get('last_exception_ts', '')} | "
                f"{st.session_state.get('last_exception_where', '')} | "
                f"{st.session_state.get('last_exception', '')}"
            )
            st.code(last_tb)

    # 日志
    logs = st.session_state.get("debug_logs") or []
    with st.expander(f"📜 运行日志（最近 {min(len(logs), 200)} 条）", expanded=True):
        c1, c2 = st.columns(2)
        with c1:
            if st.button("清空日志", use_container_width=True, key="dbg_clear_logs"):
                st.session_state.debug_logs = []
                st.success("已清空")
        with c2:
            st.caption("提示：后期可通过左侧开关隐藏此面板")
        tail = "\n".join(logs[-200:])
        st.text_area("logs", tail, height=520, key="dbg_logs_area")

    st.divider()
    render_recent_notes()


# ===============================
# 3) 对话引擎：docstore 缺失自动降级（仅向量检索）
# ===============================
def rebuild_chat_engine(
        index,
        model_name: str,
        temp: float = 0.1,
        context_limit: int = 2048,
        status=None,
        enable_rerank: bool = True,
        vec_top_k: int = 6,
        bm25_top_k: int = 6,
        fusion_top_k: int = 6,
        rerank_top_n: int = 4,
        history_messages: Optional[List[Dict[str, Any]]] = None,
):
    if index is None:
        return None, "索引不存在"

    try:
        if status:
            status.write("引擎构建: 1/6 - 检查 Ollama...")

        @retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
        def check_ollama():
            requests.get("http://localhost:11434/api/tags", timeout=5)

        try:
            check_ollama()
        except Exception:
            return None, "Ollama 服务器未启动。请运行 'ollama serve' 并拉取模型。"

        if status:
            status.write("引擎构建: 2/6 - 初始化 LLM...")

        timeout_s = int(os.environ.get("KB_LLM_TIMEOUT", "600"))
        llm = Ollama(
            model=model_name,
            base_url="http://localhost:11434",
            temperature=temp,
            request_timeout=timeout_s,
        )
        dbg(f"LLM 初始化: model={model_name} timeout={timeout_s}s temp={temp}", "INFO")

        if status:
            status.write("引擎构建: 3/6 - 构建向量检索器...")
        vector_retriever = index.as_retriever(similarity_top_k=int(vec_top_k))

        nodes = safe_docstore_nodes(index)
        bm25_ready = len(nodes) > 0

        if status:
            status.write(
                f"引擎构建: 4/6 - docstore 节点 {len(nodes)}（{'启用BM25' if bm25_ready else '仅向量检索'}）"
            )

        if bm25_ready:
            # BM25 太大时（尤其 CPU），先采样避免构建过慢
            if len(nodes) > 1500:
                nodes = random.sample(nodes, 500)

            bm25_retriever = BM25Retriever.from_defaults(nodes=nodes, similarity_top_k=int(bm25_top_k))

            retriever = QueryFusionRetriever(
                retrievers=[vector_retriever, bm25_retriever],
                similarity_top_k=int(fusion_top_k),
                num_queries=1,
                mode="reciprocal_rerank",
            )
        else:
            retriever = vector_retriever

        if status:
            status.write("引擎构建: 5/6 - 组装后处理器...")

        post = [MetadataReplacementPostProcessor(target_metadata_key="window")]
        if enable_rerank and os.path.exists(RERANK_PATH):
            post.append(
                SentenceTransformerRerank(
                    model=RERANK_PATH,
                    top_n=int(rerank_top_n),
                    device="cuda" if torch.cuda.is_available() else "cpu",
                )
            )

        if status:
            status.write("引擎构建: 6/6 - 组装 ChatEngine...")

        engine = CondensePlusContextChatEngine.from_defaults(
            retriever=retriever,
            llm=llm,
            node_postprocessors=post,
            memory=ChatMemoryBuffer.from_defaults(token_limit=int(context_limit)),
            system_prompt="你是一个工业标准专家。请基于参考资料提供准确的中文回答。",
            verbose=False,
        )

        if torch.cuda.is_available():
            torch.cuda.empty_cache()

        return engine, "构建成功"

    except Exception as e:
        set_last_exception(e, where="rebuild_chat_engine")
        dbg("ChatEngine 构建失败（已记录异常）", "ERROR")
        error_msg = f"构建失败: {type(e).__name__}: {e}"
        st.error(f"🚨 {error_msg}")
        with st.expander("查看完整错误堆栈"):
            st.code(traceback.format_exc())
        return None, error_msg


# ===============================
# 4) UI 初始化
# ===============================
st.set_page_config(page_title="工业标准 AI 专家", layout="wide")

# ——UI 风格初始化（可扩展为“用户自定义风格”）——
if "ui_style" not in st.session_state:
    st.session_state.ui_style = {
        "theme": "Aurora Glass",
        "accent": None,
        "bg1": None,
        "bg2": None,
        "card_alpha": None,
        "radius": None,
        "font": None,
    }
apply_ui_css(get_ui_theme())

gpu_on, rerank_ok = init_global_settings(EMBED_PATH, RERANK_PATH)

# 用于“清空 collection 后强制刷新”
if "chroma_reset_token" not in st.session_state:
    st.session_state.chroma_reset_token = 0

# ——新增：调试面板/回答注释/文件来源切换控制——
if "debug_logs" not in st.session_state:
    st.session_state.debug_logs = []
if "last_traceback" not in st.session_state:
    st.session_state.last_traceback = ""
if "last_exception" not in st.session_state:
    st.session_state.last_exception = ""
if "last_exception_where" not in st.session_state:
    st.session_state.last_exception_where = ""
if "last_exception_ts" not in st.session_state:
    st.session_state.last_exception_ts = ""
if "answer_notes" not in st.session_state:
    st.session_state.answer_notes = []
if "kb_last_source_dir" not in st.session_state:
    st.session_state.kb_last_source_dir = None
if "kb_pending_reset" not in st.session_state:
    st.session_state.kb_pending_reset = False

if "kb_revision" not in st.session_state:
    st.session_state.kb_revision = 0
if "kb_revision_reason" not in st.session_state:
    st.session_state.kb_revision_reason = ""
if "kb_revision_ts" not in st.session_state:
    st.session_state.kb_revision_ts = ""

if "show_debug_panel" not in st.session_state:
    st.session_state.show_debug_panel = True
if "last_built_files" not in st.session_state:
    st.session_state.last_built_files = []
if "log_handler_setup" not in st.session_state:
    st.session_state.log_handler_setup = False

dbg("应用启动：初始化 session_state 完成", "INFO")

# 尝试把 Python logging 汇入右侧调试面板（避免只在控制台可见）
if not st.session_state.get("log_handler_setup", False):
    class _SessionStateLogHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            try:
                msg = f"{record.name} - {record.getMessage()}"
                dbg(msg, level=record.levelname)
            except Exception:
                pass


    _h = _SessionStateLogHandler()
    _h.setLevel(logging.INFO)
    _h.setFormatter(logging.Formatter("%(name)s - %(levelname)s - %(message)s"))
    root = logging.getLogger()
    root.addHandler(_h)
    root.setLevel(logging.INFO)
    st.session_state.log_handler_setup = True
    dbg("已启用日志转发：logging -> 调试面板", "INFO")

COLLECTION_NAME = os.environ.get("KB_CHROMA_COLLECTION", "standard_v13")
chroma_client, vs, chroma_col = get_vector_store(CHROMA_DIR, COLLECTION_NAME, st.session_state.chroma_reset_token)

# ——P0：启动时尝试从磁盘恢复对话（只做一次）——
if "conversations_loaded" not in st.session_state:
    st.session_state.conversations_loaded = True
    load_conversations_from_disk()
if "global_index" not in st.session_state:
    idx, msg = load_index(vs)
    st.session_state.global_index = idx
    st.session_state.index_status = msg

if "conversations" not in st.session_state:
    st.session_state.conversations = {}

if "current_id" not in st.session_state:
    cid = str(uuid.uuid4())
    st.session_state.conversations[cid] = {
        "messages": [],
        "engine": None,
        "engine_ver": st.session_state.get("kb_revision", 0),
        "engine_status": "未初始化",
        "model": "qwen2:7b",
        "temp": 0.1,
        "limit": 2048,
        "enable_rerank": True,
        # 检索参数（低资源优先稳）
        "vec_top_k": 6,
        "bm25_top_k": 6,
        "fusion_top_k": 6,
        "rerank_top_n": 4,
        "created_at": datetime.now(),
    }
    st.session_state.current_id = cid
    persist_conversations_to_disk()

# ===============================
# 5) 侧边栏
# ===============================
with st.sidebar:
    st.title("⚙️ 专家系统配置")

    # 当前会话摘要（始终显示，保持侧边栏简洁）
    _conv = st.session_state.conversations[st.session_state.current_id]
    st.caption(f"会话: {st.session_state.current_id[:8]} | 模型: {_conv['model']}")
    if st.session_state.global_index is None:
        st.warning("Index: 未加载（请先解析/构建）")
    else:
        st.caption(f"Index: 已加载 | docstore 节点: {len(safe_docstore_nodes(st.session_state.global_index))}")

    st.session_state.show_debug_panel = st.toggle(
        "🧪 显示右侧调试面板（调试完成后可关闭）",
        value=bool(st.session_state.get("show_debug_panel", True)),
    )

# 🎨 UI 外观 / 主题（后续可扩展：导入/导出/每用户保存）
with st.expander("🎨 外观 / 主题", expanded=False):
    style = st.session_state.get("ui_style", {}) or {}
    theme_name = st.selectbox("主题", options=list(DEFAULT_THEMES.keys()),
                              index=list(DEFAULT_THEMES.keys()).index(style.get("theme") or "Aurora Glass"))
    accent = st.color_picker("强调色", value=(style.get("accent") or DEFAULT_THEMES[theme_name]["accent"]))
    bg1 = st.color_picker("背景色 1", value=(style.get("bg1") or DEFAULT_THEMES[theme_name]["bg1"]))
    bg2 = st.color_picker("背景色 2", value=(style.get("bg2") or DEFAULT_THEMES[theme_name]["bg2"]))
    card_alpha = st.slider("卡片透明度", 0.04, 0.22,
                           float(style.get("card_alpha") or DEFAULT_THEMES[theme_name]["card_alpha"]), step=0.01)
    radius = st.slider("圆角", 10, 26, int(style.get("radius") or DEFAULT_THEMES[theme_name]["radius"]), step=1)

    c_a, c_b = st.columns(2)
    with c_a:
        if st.button("应用风格", use_container_width=True):
            st.session_state.ui_style.update({
                "theme": theme_name,
                "accent": accent,
                "bg1": bg1,
                "bg2": bg2,
                "card_alpha": card_alpha,
                "radius": radius,
            })
            # 立即应用
            apply_ui_css(get_ui_theme())
            st.success("已应用")
    with c_b:
        if st.button("恢复默认", use_container_width=True):
            st.session_state.ui_style = {
                "theme": "Aurora Glass",
                "accent": None,
                "bg1": None,
                "bg2": None,
                "card_alpha": None,
                "radius": None,
                "font": None,
            }
            apply_ui_css(get_ui_theme())
            st.success("已恢复默认")

    st.caption("风格 JSON（后续可做导入/导出、用户保存）")
    st.text_area("style_json", json.dumps(st.session_state.ui_style, ensure_ascii=False, indent=2), height=160)

    # 1) 版本信息
    with st.expander("📦 版本信息", expanded=False):
        show_versions_ui()

    # 2) 系统状态
    with st.expander("📊 系统状态", expanded=False):
        if st.session_state.global_index is not None:
            docstore_nodes = len(safe_docstore_nodes(st.session_state.global_index))
            st.success(f"docstore 节点: {docstore_nodes}")
            if docstore_nodes == 0:
                try:
                    cc = chroma_col.count()
                    if cc > 0:
                        st.warning("docstore 为空但 Chroma 有数据：当前属于“仅向量恢复”模式（BM25 会自动降级）。")
                except Exception:
                    pass
        else:
            st.warning(f"Index: 未加载 | {st.session_state.get('index_status', '')}")

        try:
            st.info(f"Chroma collection: {COLLECTION_NAME} | count: {chroma_col.count()}")
        except Exception as e:
            st.warning(f"Chroma count 获取失败: {type(e).__name__}: {e}")

        st.caption("index_store 文件：")
        fs = index_store_files(INDEX_DIR)
        if fs:
            for k, v in fs.items():
                st.caption(f"- {k}: {v / 1024:.1f} KB")
        else:
            st.caption("- (空)")

        if gpu_on:
            gpu_name = torch.cuda.get_device_name(0)
            gpu_mem = torch.cuda.memory_allocated(0) / 1024 ** 3
            gpu_total = torch.cuda.get_device_properties(0).total_memory / 1024 ** 3
            st.info(f"🖥️ GPU: {gpu_name}")
            st.progress(min(gpu_mem / max(gpu_total, 1e-6), 1.0), text=f"{gpu_mem:.2f}/{gpu_total:.1f}GB")
        else:
            st.info("⚠️ CPU 模式")

    # 3) Chroma 健康检查
    with st.expander("🧪 Chroma 写入自检", expanded=False):
        if st.button("运行 healthcheck", use_container_width=True):
            ok, msg = chroma_healthcheck(chroma_client, CHROMA_DIR)
            (st.success if ok else st.error)(msg)

    # 4) 索引诊断
    with st.expander("🔍 索引诊断（docstore/写入/恢复）", expanded=False):
        st.caption("docstore.json 存在且非空：" + ("✅" if has_docstore_persist(INDEX_DIR) else "❌"))
        if st.session_state.global_index is not None:
            nodes = safe_docstore_nodes(st.session_state.global_index)
            st.caption(f"docstore 节点数：{len(nodes)}")
            if nodes:
                sample = random.choice(nodes)
                txt = (getattr(sample, "text", "") or "")[:300]
                st.text_area("随机节点预览（前 300 字）", txt, height=160)

        pk = try_chroma_peek(chroma_col, n=3)
        if pk:
            st.caption("Chroma peek(3)：")
            st.json(pk, expanded=False)
        else:
            st.caption("Chroma peek 不可用或失败（可忽略）")

    # 5) 文件管理：上传/删除（支持 pdf/docx/pptx/txt/md/html）
    with st.expander("📁 文件管理（上传/删除）", expanded=False):
        st.caption("支持格式: " + ", ".join(SUPPORTED_EXTS))
        target_dir = st.selectbox(
            "上传保存到",
            options=[UPLOAD_DIR, RAW_DIR, OCR_DIR],
            index=0,
            format_func=lambda p: p,
        )
        uploaded = st.file_uploader(
            "选择文件（可多选）",
            type=SUPPORTED_UPLOAD_TYPES,
            accept_multiple_files=True,
        )
        if st.button("保存上传文件", use_container_width=True, disabled=not uploaded):
            saved, msgs = save_uploaded_files(uploaded, target_dir)
            for m in msgs:
                (st.success if m.startswith("已保存") else st.warning)(m)
            if saved:
                st.info("保存完成后，可在「📚 文档解析中心」选择目录并构建索引。")

        st.divider()
        st.caption("目录文件列表（支持格式）")
        cur_files = list_source_files(target_dir)
        st.caption(f"共 {len(cur_files)} 个")
        if cur_files:
            del_sel = st.multiselect("选择要删除的文件", options=cur_files)
            if st.button("删除所选文件", use_container_width=True, disabled=not del_sel):
                errs = 0
                for f in del_sel:
                    try:
                        os.remove(os.path.join(target_dir, f))
                    except Exception as e:
                        errs += 1
                        st.error(f"删除失败: {f} | {type(e).__name__}: {e}")
                if errs == 0:
                    st.success("删除完成")
                    time.sleep(0.2)
                    st.rerun()
        else:
            st.caption("（空）")

    # 6) 对话管理
    with st.expander("💬 对话管理", expanded=False):
        if st.button("➕ 新建对话任务", use_container_width=True):
            new_id = str(uuid.uuid4())
            st.session_state.conversations[new_id] = {
                "messages": [],
                "engine": None,
                "engine_ver": st.session_state.get("kb_revision", 0),
                "engine_status": "未初始化",
                "model": "qwen2:7b",
                "temp": 0.1,
                "limit": 2048,
                "enable_rerank": True,
                "vec_top_k": 6,
                "bm25_top_k": 6,
                "fusion_top_k": 6,
                "rerank_top_n": 4,
                "created_at": datetime.now(),
            }
            st.session_state.current_id = new_id
            persist_conversations_to_disk()
            st.rerun()

        if st.button("🧹 清空当前对话", use_container_width=True):
            conv = st.session_state.conversations[st.session_state.current_id]
            if conv.get("engine"):
                try:
                    conv["engine"].reset()
                except Exception:
                    pass
            conv["messages"] = []
            st.success("已清空")
            persist_conversations_to_disk()
            st.rerun()

        st.divider()
        st.caption("历史对话:")
        for cid in sorted(
                st.session_state.conversations.keys(),
                key=lambda k: st.session_state.conversations[k]["created_at"],
                reverse=True,
        ):
            conv_item = st.session_state.conversations[cid]
            is_current = cid == st.session_state.current_id
            created_str = conv_item["created_at"].strftime("%Y-%m-%d %H:%M")
            if st.button(
                    f"{'🔵' if is_current else '⚪'} {cid[:8]} ({created_str})",
                    key=f"btn_{cid}",
                    use_container_width=True,
            ):
                st.session_state.current_id = cid
                persist_conversations_to_disk()
                st.rerun()

    # 7) 模型参数
    with st.expander("🤖 模型参数", expanded=False):
        conv = st.session_state.conversations[st.session_state.current_id]

        if conv.get("engine"):
            st.success("✅ 引擎: 已就绪")
        else:
            st.warning(f"⚠️ 引擎: {conv.get('engine_status', '未初始化')}")


        @retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
        def get_models():
            return requests.get("http://localhost:11434/api/tags", timeout=5).json()["models"]


        try:
            m_list = [m["name"] for m in get_models()]
        except Exception:
            m_list = ["qwen2:7b", "qwen2:1.5b", "deepseek-coder:7b"]
            st.warning("⚠️ Ollama API 不可达，使用默认模型列表")

        selected_model = st.selectbox("核心 LLM", options=m_list,
                                      index=max(0, m_list.index(conv["model"])) if conv["model"] in m_list else 0)
        if selected_model != conv["model"]:
            conv["model"] = selected_model
            conv["engine"] = None
            conv["engine_status"] = "需要重建"

        conv["temp"] = st.slider("随机性 (Temp)", 0.0, 1.0, float(conv["temp"]))
        conv["limit"] = st.select_slider("记忆窗口", [1024, 2048, 4096], value=int(conv["limit"]))
        conv["enable_rerank"] = st.toggle("启用重排 (rerank)", value=bool(conv.get("enable_rerank", True)))

        st.subheader("🔎 检索参数（低资源建议：K=6，关 rerank）")
        conv["vec_top_k"] = st.slider("向量 top_k", 3, 20, int(conv.get("vec_top_k", 6)))
        conv["bm25_top_k"] = st.slider("BM25 top_k", 3, 20, int(conv.get("bm25_top_k", 6)))
        conv["fusion_top_k"] = st.slider("融合输出 top_k", 3, 20, int(conv.get("fusion_top_k", 6)))
        conv["rerank_top_n"] = st.slider("重排 top_n", 1, 10, int(conv.get("rerank_top_n", 4)))

        if st.button("🔥 预热模型", use_container_width=True):
            with st.spinner("预热中..."):
                try:
                    Ollama(model=conv["model"], base_url="http://localhost:11434").complete("test")
                    st.success("✅ 模型预热完成")
                except Exception as e:
                    st.error(f"❌ 预热失败: {type(e).__name__}: {e}")

        if st.button("🔄 重建引擎", use_container_width=True):
            if st.session_state.global_index is None:
                st.error("❌ 请先解析文档或加载索引")
            else:
                with st.spinner("重建中..."):
                    engine, msg = rebuild_chat_engine(
                        st.session_state.global_index,
                        conv["model"],
                        conv["temp"],
                        conv["limit"],
                        enable_rerank=conv["enable_rerank"],
                        vec_top_k=conv["vec_top_k"],
                        bm25_top_k=conv["bm25_top_k"],
                        fusion_top_k=conv["fusion_top_k"],
                        rerank_top_n=conv["rerank_top_n"],
                        history_messages=conv.get("messages") or [],

                    )
                    conv["engine"] = engine
                    conv["engine_ver"] = st.session_state.get("kb_revision", 0)
                    conv["engine_status"] = msg
                    persist_conversations_to_disk()
                    (st.success if engine else st.error)(msg)
                    time.sleep(0.2)
                    st.rerun()

    # 8) 文档解析中心（支持多格式）
    with st.expander("📚 文档解析中心（构建/更新索引）", expanded=False):
        st.caption("支持格式: " + ", ".join(SUPPORTED_EXTS))
        st.caption("扫描版 PDF 建议先 OCR 到 data/ocr，再解析。")

        source_dir = st.selectbox(
            "文档来源目录",
            options=[UPLOAD_DIR, RAW_DIR, OCR_DIR],
            index=0,
            key="kb_source_dir",
            format_func=lambda p: f"{p}  (文件: {len(list_source_files(p))})",
        )

        # ✅ 新规则：切换文件来源（目录）后，不保留上一次解析结果（需要清空并重建）
        if st.session_state.kb_last_source_dir is None:
            st.session_state.kb_last_source_dir = source_dir
        elif source_dir != st.session_state.kb_last_source_dir:
            st.session_state.kb_last_source_dir = source_dir
            st.session_state.kb_pending_reset = True

            # 立即让旧索引失效，避免“切目录后仍用旧索引问答”
            st.session_state.global_index = None
            st.session_state.index_status = "已切换文件来源：旧解析结果已清空，请重新构建"

            # 让所有会话引擎失效（避免复用旧 retriever）
            bump_kb_revision("切换文件来源目录（旧索引/引擎作废）")

            # 清空“待解析文件”选择（强制用户重新选择）
            st.session_state["kb_selected_files"] = []
            dbg(f"切换文档来源目录 -> {source_dir}，清空旧解析结果并标记待重建", "INFO")
            st.info("已切换文件来源：不会保留上一次解析结果。请重新选择文件并构建索引。")

        files = list_source_files(source_dir)
        if files:
            st.success(f"发现 {len(files)} 个文件")
        else:
            st.caption(f"把参考文件放到目录：{source_dir}")

        strict_mode = st.toggle("严格解析模式（errors='raise'）", value=False)
        skip_scanned = st.toggle("检测到无文本层PDF时：阻止解析并提示 OCR", value=True)
        auto_ocr = st.toggle("（可选）自动调用 ocrmypdf 给扫描PDF做 OCR 并解析", value=False)
        ocr_lang = st.text_input("OCR 语言（ocrmypdf -l）", value="chi_sim+eng", disabled=not auto_ocr)

        rebuild_mode = st.selectbox("构建模式", options=["增量写入（默认）", "清空后重建（推荐用于排查）"], index=0)
        default_sel = files[:10] if len(files) > 10 else files

        selected = st.multiselect(
            "待解析文件（切换目录后需重新选择）",
            options=files,
            default=default_sel,
            key="kb_selected_files",
        )

        if st.button("🚀 开始解析并构建", use_container_width=True, type="primary"):
            if not selected:
                st.warning("请先勾选文件！")
            else:
                ok_lock, lock_msg = try_acquire_build_lock()
                if not ok_lock:
                    st.error(lock_msg)
                    st.stop()
                else:
                    dbg(lock_msg, "INFO")

                with st.status("🛠️ 正在执行知识库构建...", expanded=True) as status:
                    try:
                        # 0) 清空后重建（切换文件来源后将强制清空，以确保只保留本次文件解析结果）
                        need_reset = bool(st.session_state.get("kb_pending_reset", False)) or (
                                    rebuild_mode == "清空后重建（推荐用于排查）")
                        if need_reset:
                            status.write("步骤 0/6: 清空 index_store + Chroma collection...")
                            dbg("开始清空 index_store + Chroma collection（重建）", "INFO")
                            shutil.rmtree(INDEX_DIR, ignore_errors=True)
                            os.makedirs(INDEX_DIR, exist_ok=True)
                            try:
                                chroma_client.delete_collection(COLLECTION_NAME)
                            except Exception as e:
                                dbg(f"delete_collection 失败：{type(e).__name__}: {e}", "WARNING")

                            bump_kb_revision("清空并重建 Chroma collection", bump_chroma_token=True)

                            chroma_client2, vs2, chroma_col2 = get_vector_store(
                                CHROMA_DIR, COLLECTION_NAME, st.session_state.chroma_reset_token
                            )
                            chroma_client, vs, chroma_col = chroma_client2, vs2, chroma_col2
                            st.session_state.kb_pending_reset = False

                        # 1) 文件路径准备 + 依赖预检 + 增量差异计算
                        status.write("步骤 1/6: 准备文件列表与增量差异...")
                        paths = [os.path.join(source_dir, f) for f in selected]
                        st.session_state.last_built_files = list(selected)
                        dbg(f"构建索引：source_dir={source_dir} 选中文件={len(selected)} rebuild_mode={rebuild_mode}", "INFO")
                        preflight_file_dependencies(paths)

                        doc_id_to_path, doc_id_to_hash, doc_id_to_stat = build_selection_maps(source_dir, selected)
                        manifest = load_kb_manifest(INDEX_DIR)

                        # manifest 额外一致性检查
                        if manifest.get("_corrupted"):
                            status.write("⚠️ 检测到 kb_manifest.json 损坏：为确保一致性，将自动清空后重建。")
                            need_reset = True
                        if manifest.get("source_dir") not in (None, source_dir):
                            status.write("⚠️ 检测到 manifest 的 source_dir 与当前目录不一致：将自动清空后重建以避免混用。")
                            need_reset = True

                        use_incremental = (not need_reset) and rebuild_mode.startswith("增量")

                        # 增量更新的硬前提：必须有可用 docstore.json，否则会出现“向量库累积/重复但 docstore 覆盖”的不一致
                        if use_incremental and (not has_docstore_persist(INDEX_DIR)):
                            status.write("⚠️ 未发现有效 docstore.json，无法执行安全增量更新（会导致 BM25/向量不一致）。将自动切换为清空后重建。")
                            need_reset = True
                            use_incremental = False

                        # 若增量模式但 manifest 不存在且已有向量数据，无法安全对齐旧数据：建议首次用“清空后重建”
                        if use_incremental and (not os.path.exists(MANIFEST_PATH)):
                            try:
                                existing_cnt = chroma_col.count()
                            except Exception:
                                existing_cnt = None
                            if existing_cnt and existing_cnt > 0:
                                status.write("⚠️ 当前索引缺少 kb_manifest.json（可能是旧版本构建）。为避免重复/不一致，将自动清空后重建一次生成清单。")
                                need_reset = True
                                use_incremental = False

                        # 如果在此处决定 need_reset，则立即执行清空重建（与上方逻辑一致）
                        if need_reset:
                            status.write("步骤 0/6: 清空 index_store + Chroma collection...")
                            dbg("开始清空 index_store + Chroma collection（重建）", "INFO")
                            shutil.rmtree(INDEX_DIR, ignore_errors=True)
                            os.makedirs(INDEX_DIR, exist_ok=True)
                            try:
                                chroma_client.delete_collection(COLLECTION_NAME)
                            except Exception as e:
                                dbg(f"delete_collection 失败：{type(e).__name__}: {e}", "WARNING")

                            bump_kb_revision("清空并重建 Chroma collection", bump_chroma_token=True)

                            chroma_client2, vs2, chroma_col2 = get_vector_store(
                                CHROMA_DIR, COLLECTION_NAME, st.session_state.chroma_reset_token
                            )
                            chroma_client, vs, chroma_col = chroma_client2, vs2, chroma_col2
                            st.session_state.kb_pending_reset = False
                            use_incremental = False
                            manifest = {"version": 1, "source_dir": source_dir, "files": {}}

                        # 2) 计算增量计划（基于 manifest，对齐“当前选择文件集”）
                        status.write("步骤 2/6: 计算增量计划（新增/更新/删除）...")
                        prev_files = manifest.get("files", {}) if isinstance(manifest, dict) else {}
                        target_ids = set(doc_id_to_path.keys())
                        prev_ids = set(prev_files.keys()) if isinstance(prev_files, dict) else set()

                        to_delete_ids = set(prev_ids - target_ids)  # 取消选择/文件消失
                        to_upsert_ids = []
                        for doc_id in sorted(target_ids):
                            old = prev_files.get(doc_id)
                            new_hash = doc_id_to_hash.get(doc_id, "")
                            if (old is None) or (old.get("hash") != new_hash):
                                to_upsert_ids.append(doc_id)
                                if old is not None:
                                    to_delete_ids.add(doc_id)  # 更新：先删后插，避免重复

                        status.write(
                            f"增量计划：新增/更新 {len(to_upsert_ids)} | 删除 {len(to_delete_ids)} | 目标文件数 {len(target_ids)}")
                        dbg(f"增量计划：to_upsert={len(to_upsert_ids)} to_delete={len(to_delete_ids)} target={len(target_ids)}",
                            "INFO")

                        # 3) 若选择增量且存在已有索引 -> 加载索引；否则走完整重建
                        if use_incremental and st.session_state.global_index is None:
                            status.write("加载已有索引（用于增量更新）...")
                            idx, msg = load_index(vs)
                            st.session_state.global_index = idx
                            status.write(msg)
                            if st.session_state.global_index is None:
                                status.write("⚠️ 既有索引加载失败，自动切换为清空后重建。")
                                use_incremental = False

                        # delete_ref_doc 兼容性检查（不同版本 LlamaIndex 可能不支持）
                        if use_incremental and not hasattr(st.session_state.global_index, "delete_ref_doc"):
                            status.write("⚠️ 当前 LlamaIndex 版本不支持 delete_ref_doc，无法做安全增量更新。将自动切换为清空后重建。")
                            use_incremental = False

                        # 4) 执行更新：增量 or 全量
                        if use_incremental:
                            status.write("步骤 3/6: 执行增量更新（delete + upsert）...")

                            # 记录 count 变化用于诊断
                            before_count = None
                            try:
                                before_count = chroma_col.count()
                            except Exception:
                                pass

                            # 4.1 删除（先删）
                            if to_delete_ids:
                                for doc_id in sorted(to_delete_ids):
                                    try:
                                        st.session_state.global_index.delete_ref_doc(doc_id,
                                                                                     delete_from_vector_store=True)
                                    except Exception as e:
                                        dbg(f"delete_ref_doc 失败 doc_id={doc_id}: {type(e).__name__}: {e}", "WARNING")
                                status.write(f"已删除 ref_doc：{len(to_delete_ids)}")

                            # 4.2 新增/更新（再插）
                            if to_upsert_ids:
                                upsert_paths = [doc_id_to_path[i] for i in to_upsert_ids]

                                status.write("步骤 4/6: PDF 文本层体检/可选 OCR（仅更新集合）...")
                                pdf_paths = [p for p in upsert_paths if p.lower().endswith(".pdf")]
                                scanned: List[str] = []
                                stats_map: Dict[str, Optional[PdfTextStats]] = {}

                                # doc_id -> parse_path（OCR 后会变化；doc_id 不变）
                                doc_id_to_parse_path = {i: doc_id_to_path[i] for i in to_upsert_ids}
                                for p in pdf_paths:
                                    s = pdf_text_stats_pymupdf(p) if fitz else None
                                    stats_map[p] = s
                                    if is_probably_scanned(s):
                                        scanned.append(p)

                                if scanned:
                                    status.write(f"检测到疑似扫描/无文本层 PDF：{len(scanned)} 个（仅更新集合）")
                                    for p in scanned[:5]:
                                        status.write(f"- {os.path.basename(p)}")
                                    if auto_ocr:
                                        status.write("执行 OCR（ocrmypdf）...")
                                        for in_pdf in scanned:
                                            base = os.path.splitext(os.path.basename(in_pdf))[0]
                                            out_pdf = os.path.join(OCR_DIR, f"{base}_ocr.pdf")
                                            ok, msg = run_ocrmypdf(in_pdf, out_pdf,
                                                                   lang=ocr_lang.strip() or "chi_sim+eng")
                                            if ok:
                                                status.write("✅ " + msg)
                                                # 将该文件的 parse_path 切换为 OCR 输出（doc_id 不变）
                                                for did, op in doc_id_to_path.items():
                                                    if op == in_pdf and did in doc_id_to_parse_path:
                                                        doc_id_to_parse_path[did] = out_pdf
                                            else:
                                                status.write("❌ " + msg)

                                        if not any(os.path.exists(p) for p in doc_id_to_parse_path.values()):
                                            raise RuntimeError("OCR 后仍没有可解析的文件。请检查 OCR 是否成功。")
                                    else:
                                        if skip_scanned:
                                            raise RuntimeError("检测到扫描版/无文本层 PDF。请先 OCR 后再解析，或关闭“阻止解析并提示 OCR”。")
                                        status.write("⚠️ 已允许继续解析无文本层 PDF（可能得到空文本/0 节点）")

                                parse_paths = list(dict.fromkeys(doc_id_to_parse_path.values()))
                                parse_path_to_doc_id = {v: k for k, v in doc_id_to_parse_path.items()}

                                status.write("步骤 5/6: 读取文档并写入（仅更新集合）...")
                                documents = SimpleDirectoryReader(
                                    input_files=parse_paths,
                                    errors="raise" if strict_mode else "ignore",
                                ).load_data()

                                if not documents:
                                    raise RuntimeError("文档加载失败（documents=0）。")

                                assign_doc_ids_and_hash(documents, parse_path_to_doc_id, doc_id_to_hash, doc_id_to_path,
                                                        source_dir)

                                non_empty, total_chars = validate_documents_text(documents)
                                status.write(f"文本统计（更新集合）：有效文档 {non_empty}/{len(documents)}，总字符 {total_chars}")
                                if total_chars == 0:
                                    raise RuntimeError("更新集合提取到的文本为空（total_chars=0）。请检查 PDF 文本层/是否需要 OCR/解析依赖。")

                                for d in documents:
                                    st.session_state.global_index.insert(d)

                            # 4.3 持久化（无论是否有 upsert，只要删了就要 persist）
                            st.session_state.global_index.storage_context.persist(persist_dir=INDEX_DIR)

                            if not has_docstore_persist(INDEX_DIR):
                                raise RuntimeError("docstore.json 未生成或为空（persist 失败）。")

                            # 4.4 更新 manifest（对齐当前选择文件集）
                            new_manifest_files = {}
                            for doc_id in sorted(target_ids):
                                new_manifest_files[doc_id] = {
                                    "hash": doc_id_to_hash.get(doc_id, ""),
                                    "mtime": doc_id_to_stat.get(doc_id, {}).get("mtime"),
                                    "size": doc_id_to_stat.get(doc_id, {}).get("size"),
                                }
                            manifest = {"version": 1, "source_dir": source_dir, "files": new_manifest_files,
                                        "updated_at": datetime.now().isoformat()}
                            save_kb_manifest(INDEX_DIR, manifest)

                            nodes_count = len(safe_docstore_nodes(st.session_state.global_index))
                            after_count = None
                            try:
                                after_count = chroma_col.count()
                            except Exception:
                                pass

                            if after_count is not None:
                                if after_count == 0:
                                    raise RuntimeError("Chroma count=0：向量写入未生效（检查 CHROMA_DIR/权限/锁/healthcheck）。")
                                if before_count is not None and after_count == before_count and (
                                        to_upsert_ids or to_delete_ids):
                                    status.write("⚠️ Chroma count 未变化：可能是重复导入覆盖（或写入失败）。建议跑 healthcheck 并尝试清空后重建。")

                            status.write(
                                f"✅ 增量更新完成：docstore 节点 {nodes_count} | Chroma count {after_count if after_count is not None else '(unknown)'}")

                        else:
                            # ===== 全量重建（保留原流程，但补齐稳定 doc_id + manifest）=====
                            status.write("步骤 3/6: PDF 文本层体检（仅PDF）...")
                            pdf_paths = [p for p in paths if p.lower().endswith(".pdf")]
                            scanned: List[str] = []
                            stats_map: Dict[str, Optional[PdfTextStats]] = {}

                            # doc_id -> parse_path（OCR 后会变化；doc_id 不变）
                            doc_id_to_parse_path = {doc_id: doc_id_to_path[doc_id] for doc_id in target_ids}
                            for p in pdf_paths:
                                s = pdf_text_stats_pymupdf(p) if fitz else None
                                stats_map[p] = s
                                if is_probably_scanned(s):
                                    scanned.append(p)

                            if scanned:
                                status.write(f"检测到疑似扫描/无文本层 PDF：{len(scanned)} 个")
                                for p in scanned[:5]:
                                    status.write(f"- {os.path.basename(p)}")

                                if auto_ocr:
                                    status.write("步骤 3/6: 自动 OCR（ocrmypdf）...")
                                    for in_pdf in scanned:
                                        base = os.path.splitext(os.path.basename(in_pdf))[0]
                                        out_pdf = os.path.join(OCR_DIR, f"{base}_ocr.pdf")
                                        ok, msg = run_ocrmypdf(in_pdf, out_pdf, lang=ocr_lang.strip() or "chi_sim+eng")
                                        if ok:
                                            status.write("✅ " + msg)
                                            # 将该文件的 parse_path 切换为 OCR 输出（doc_id 不变）
                                            for did, op in doc_id_to_path.items():
                                                if op == in_pdf:
                                                    doc_id_to_parse_path[did] = out_pdf
                                        else:
                                            status.write("❌ " + msg)

                                    if not any(os.path.exists(p) for p in doc_id_to_parse_path.values()):
                                        raise RuntimeError("OCR 后仍没有可解析的文件。请检查 OCR 是否成功。")
                                else:
                                    if skip_scanned:
                                        raise RuntimeError("检测到扫描版/无文本层 PDF。请先 OCR 后再解析，或关闭“阻止解析并提示 OCR”。")
                                    status.write("⚠️ 已允许继续解析无文本层 PDF（可能得到空文本/0 节点）")

                            parse_paths = list(dict.fromkeys(doc_id_to_parse_path.values()))
                            parse_path_to_doc_id = {v: k for k, v in doc_id_to_parse_path.items()}

                            # 4) 读取原始文档
                            status.write("步骤 4/6: 读取原始文档（SimpleDirectoryReader）...")
                            documents = SimpleDirectoryReader(
                                input_files=parse_paths,
                                errors="raise" if strict_mode else "ignore",
                            ).load_data()

                            if not documents:
                                raise RuntimeError("文档加载失败（documents=0）。可能路径/权限/解析器失败或格式未被支持。")

                            assign_doc_ids_and_hash(documents, parse_path_to_doc_id, doc_id_to_hash, doc_id_to_path,
                                                    source_dir)

                            # 5) 文本有效性检查
                            non_empty, total_chars = validate_documents_text(documents)
                            status.write(f"步骤 5/6: 文本统计：有效文档 {non_empty}/{len(documents)}，总字符 {total_chars}")

                            if total_chars == 0:
                                if pdf_paths:
                                    sum_pdf_text = sum(
                                        (stats_map.get(p).total_chars for p in pdf_paths if stats_map.get(p)), 0)
                                    if sum_pdf_text > 0:
                                        raise RuntimeError(
                                            "PDF 预检显示存在文本层，但解析器提取到的文本为 0。\n"
                                            "建议：开启严格模式查看具体异常；或升级/更换 PDF 解析依赖（pymupdf/pypdf）。"
                                        )
                                    raise RuntimeError(
                                        "PDF 提取到的文本为空：很可能是扫描版/图片PDF 或解析器读不到。\n"
                                        "解决：换可复制文字的 PDF，或先对 PDF 做 OCR（推荐 ocrmypdf）。"
                                    )
                                raise RuntimeError(
                                    "所有文件提取到的文本为空。\n"
                                    "建议：开启严格模式（errors='raise'）查看报错；或将文件转为 .pdf/.txt 后再试。"
                                )

                            # 6) 构建索引（写入 Chroma + persist docstore）
                            status.write("步骤 6/6: 构建索引（Chroma 向量库 + index_store）...")
                            before_count = None
                            try:
                                before_count = chroma_col.count()
                            except Exception:
                                pass

                            storage_ctx = StorageContext.from_defaults(vector_store=vs)
                            st.session_state.global_index = VectorStoreIndex.from_documents(
                                documents,
                                storage_context=storage_ctx,
                                show_progress=True,
                            )
                            st.session_state.global_index.storage_context.persist(persist_dir=INDEX_DIR)

                            if not has_docstore_persist(INDEX_DIR):
                                raise RuntimeError(
                                    "docstore.json 未生成或为空（persist 失败）。\n"
                                    "请检查 INDEX_DIR 是否可写、磁盘空间、以及是否被杀软/权限阻挡。"
                                )

                            # 写 manifest（对齐当前选择文件集）
                            new_manifest_files = {}
                            for doc_id in sorted(target_ids):
                                new_manifest_files[doc_id] = {
                                    "hash": doc_id_to_hash.get(doc_id, ""),
                                    "mtime": doc_id_to_stat.get(doc_id, {}).get("mtime"),
                                    "size": doc_id_to_stat.get(doc_id, {}).get("size"),
                                }
                            manifest = {"version": 1, "source_dir": source_dir, "files": new_manifest_files,
                                        "updated_at": datetime.now().isoformat()}
                            save_kb_manifest(INDEX_DIR, manifest)

                            nodes_count = len(safe_docstore_nodes(st.session_state.global_index))
                            after_count = None
                            try:
                                after_count = chroma_col.count()
                            except Exception:
                                pass

                            if after_count is not None:
                                if after_count == 0:
                                    raise RuntimeError("Chroma count=0：向量写入未生效（检查 CHROMA_DIR/权限/锁/healthcheck）。")
                                if before_count is not None and after_count == before_count:
                                    status.write("⚠️ Chroma count 未变化：可能是重复导入覆盖（或写入失败）。建议跑 healthcheck 并尝试清空后重建。")

                            status.write(
                                f"✅ 构建完成：docstore 节点 {nodes_count} | Chroma count {after_count if after_count is not None else '(unknown)'}")

                        # ===== 构建完成：统一刷新 KB 版本 + 重建引擎 =====
                        bump_kb_revision("知识库构建完成：索引已更新")

                        conv = st.session_state.conversations[st.session_state.current_id]
                        status.write("引擎同步: 重建中...")
                        engine, msg = rebuild_chat_engine(
                            st.session_state.global_index,
                            conv["model"],
                            conv["temp"],
                            conv["limit"],
                            status=status,
                            enable_rerank=conv["enable_rerank"],
                            vec_top_k=conv["vec_top_k"],
                            bm25_top_k=conv["bm25_top_k"],
                            fusion_top_k=conv["fusion_top_k"],
                            rerank_top_n=conv["rerank_top_n"],
                            history_messages=conv.get("messages") or [],
                        )
                        conv["engine"] = engine
                        conv["engine_ver"] = st.session_state.get("kb_revision", 0)
                        conv["engine_status"] = msg
                        persist_conversations_to_disk()

                        st.session_state.index_status = f"✅ 已构建（docstore 节点 {len(safe_docstore_nodes(st.session_state.global_index))}）"
                        if engine:
                            status.update(label="✅ 解析成功！知识库已更新。", state="complete")
                            st.balloons()
                        else:
                            status.update(label=f"⚠️ 解析完成，但引擎失败：{msg}", state="error")

                        release_build_lock()
                        dbg("已释放构建锁", "INFO")

                    except Exception as e:
                        set_last_exception(e, where="build_index")
                        dbg("知识库构建失败（已记录异常）", "ERROR")
                        status.update(label=f"❌ 解析失败: {str(e)}", state="error")
                        release_build_lock()
                        dbg("已释放构建锁（异常路径）", "INFO")
                        with st.expander("查看详细错误堆栈"):
                            st.code(traceback.format_exc())

# 9) 危险操作（清库重建）
with st.expander("🧨 危险操作：清理索引/向量库（重建用）", expanded=False):
    st.warning("清理后不可恢复。建议先备份目录。")
    do_clear_index = st.checkbox("删除 index_store（docstore/index_store）", value=False)
    do_clear_chroma = st.checkbox(f"删除 Chroma collection：{COLLECTION_NAME}", value=False)
    if st.button("执行清理", use_container_width=True, disabled=not (do_clear_index or do_clear_chroma)):
        err = None
        try:
            if do_clear_index:
                shutil.rmtree(INDEX_DIR, ignore_errors=True)
                os.makedirs(INDEX_DIR, exist_ok=True)
            reason_parts = []
            if do_clear_chroma:
                try:
                    chroma_client.delete_collection(COLLECTION_NAME)
                    reason_parts.append(f"已删除 Chroma collection：{COLLECTION_NAME}")
                except Exception as e:
                    dbg(f"delete_collection 失败：{type(e).__name__}: {e}", "WARNING")
                    reason_parts.append(f"删除 Chroma collection 失败：{COLLECTION_NAME}")
            if do_clear_index:
                reason_parts.append("已删除 index_store")

            bump_kb_revision("；".join(reason_parts) if reason_parts else "已清理", bump_chroma_token=bool(do_clear_chroma))
            st.session_state.global_index = None
            st.session_state.index_status = "已清理，请重新构建"
            st.session_state.kb_pending_reset = True
        except Exception as e:
            err = e

        if err:
            st.error(f"清理失败：{type(err).__name__}: {err}")
        else:
            st.success("清理完成，页面将刷新。")
            time.sleep(0.3)
            st.rerun()
# ===============================
# ===============================
# 6) 主界面：交互区（中间问答 + 右侧调试区）
# ===============================
conv = st.session_state.conversations[st.session_state.current_id]
st.title("📑 工业标准 AI 专家")
st.caption(
    f"会话: {st.session_state.current_id[:8]} | 模型: {conv['model']} | 状态: {conv.get('engine_status', '未知')}"
)

# 中间：问答区；右侧：调试区（可在左侧开关隐藏）
col_mid, col_right = st.columns([2.7, 1.3], gap="large")

with col_right:
    try:
        render_debug_panel()
    except Exception as e:
        # 调试面板失败不应影响主功能
        set_last_exception(e, where="render_debug_panel")
        st.error(f"调试面板渲染失败: {type(e).__name__}: {e}")

with col_mid:
    with st.expander("📁 上传参考文件（无需手动拷贝到目录）", expanded=False):
        st.caption("支持格式: " + ", ".join(SUPPORTED_EXTS))
        target_dir_main = st.selectbox("保存到目录", options=[UPLOAD_DIR, RAW_DIR, OCR_DIR], index=0, key="main_upload_dir")
        up_main = st.file_uploader("选择文件（可多选）", type=SUPPORTED_UPLOAD_TYPES, accept_multiple_files=True,
                                   key="main_uploader")
        if st.button("保存上传文件", use_container_width=True, disabled=not up_main, key="main_save_uploads"):
            saved, msgs = save_uploaded_files(up_main, target_dir_main)
            for m in msgs:
                (st.success if m.startswith("已保存") else st.warning)(m)
            if saved:
                dbg(f"上传文件完成：{len(saved)} 个 -> {target_dir_main}", "INFO")
                st.info("已保存。请到左侧「📚 文档解析中心」选择目录并构建索引。")

    # 历史对话
    for msg in conv["messages"]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    if prompt := st.chat_input("请提问有关工业标准的内容..."):
        dbg(f"用户提问: {prompt}", "INFO")
        conv["messages"].append({"role": "user", "content": prompt})
        persist_conversations_to_disk()
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            if st.session_state.global_index is None:
                st.error("🚨 错误：知识库未加载。请先在左侧「文档解析中心」完成解析/构建。")
            else:
                if conv["engine"] is None or conv.get("engine_ver") != st.session_state.get("kb_revision", 0):
                    with st.spinner("🚀 正在初始化 RAG 引擎..."):
                        try:
                            engine, msg = rebuild_chat_engine(
                                st.session_state.global_index,
                                conv["model"],
                                conv["temp"],
                                conv["limit"],
                                enable_rerank=conv.get("enable_rerank", True),
                                vec_top_k=conv.get("vec_top_k", 6),
                                bm25_top_k=conv.get("bm25_top_k", 6),
                                fusion_top_k=conv.get("fusion_top_k", 6),
                                rerank_top_n=conv.get("rerank_top_n", 4),
                                history_messages=conv.get("messages") or [],
                            )
                            conv["engine"] = engine
                            conv["engine_ver"] = st.session_state.get("kb_revision", 0)
                            conv["engine_status"] = msg
                            dbg(f"引擎初始化结果: {msg}", "INFO" if engine else "ERROR")
                        except Exception as e:
                            set_last_exception(e, where="init_engine")
                            st.error(f"❌ 初始化失败: {type(e).__name__}: {e}")
                            st.stop()

                        if conv["engine"] is None:
                            st.error(f"❌ 初始化失败: {msg}")
                            st.info("💡 请点击左侧侧边栏「重建引擎」")
                            st.stop()

                try:
                    placeholder = st.empty()
                    placeholder.markdown("🤔 模型思考中...（本地模型可能较慢，请耐心等待）")
                    full_raw = ""
                    start_time = time.time()
                    response = conv["engine"].stream_chat(prompt)

                    for token in response.response_gen:
                        full_raw += token
                        placeholder.markdown(clean_think_tags(full_raw) + "▌")

                    final_txt = clean_think_tags(full_raw)
                    placeholder.markdown(final_txt)
                    conv["messages"].append({"role": "assistant", "content": final_txt})
                    persist_conversations_to_disk()

                    elapsed = time.time() - start_time
                    note = build_answer_note(prompt, final_txt, response, elapsed_s=elapsed)
                    push_answer_note(note)
                    render_answer_note(note)

                    # 兼容：仍保留原来的 “本次回答参考”
                    if getattr(response, "source_nodes", None):
                        num_nodes = len(response.source_nodes)
                        with st.expander(f"📊 本次回答参考 (耗时: {elapsed:.2f}秒)", expanded=False):
                            st.caption(f"检索节点: {num_nodes}")

                    dbg(f"回答完成: elapsed={elapsed:.2f}s precision={note.get('precision')} sources={len(note.get('sources', []))}",
                        "INFO")

                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()

                except Exception as e:
                    # Chroma collection 被删除/句柄失效：自动自愈并提示重建
                    _is_nf = False
                    try:
                        _is_nf = isinstance(e, chromadb.errors.NotFoundError)
                    except Exception:
                        _is_nf = False

                    if _is_nf or ("Collection" in str(e) and "does not exist" in str(e)):
                        recover_chroma_collection_missing(e, where="chat_answer")
                        st.warning(
                            "⚠️ 检测到向量库 collection 不存在/句柄失效（通常由清库或多实例导致）。"
                            "已自动刷新连接，请在左侧重新构建索引后重试。"
                        )
                        st.stop()

                    set_last_exception(e, where="chat_answer")
                    st.error(f"⚠️ 生成回答时发生异常: {type(e).__name__}: {e}")
                    with st.expander("查看错误详情"):
                        st.code(traceback.format_exc())

