"""FinBERT-zh 中文金融情感小模型（懒加载，CPU 推理）。

依赖：transformers + torch + 已离线下载好的中文 FinBERT 权重。
实际选用：bardsai/finance-sentiment-zh-base  (≈100MB, RoBERTa-zh)
回退：模型不可用时返回 (None, None)，调用方使用字典法 sentiment_score。

行为：
- 首次调用触发后台线程加载（不阻塞 enrich 主流程）
- 加载失败不永久锁死：冷却后允许重试（如依赖补齐/网络恢复后自动生效）
- 之后每次 score(text) 返回 (label, confidence)
- label ∈ {"bullish", "bearish", "neutral"}
- 全局单例，线程安全
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Tuple

logger = logging.getLogger("news.sentiment")

# 优先离线，不下载就跳过
DEFAULT_MODEL = os.getenv(
    "FINBERT_ZH_MODEL",
    "bardsai/finance-sentiment-zh-base",
)
# 推理设备：-1=CPU。镜像内 torch 换成 CUDA 构建后，设 FINBERT_DEVICE=0 即走 GPU
DEVICE = int(os.getenv("FINBERT_DEVICE", "-1"))
# 仅 GPU 环境默认启用 FinBERT；CPU 环境默认关闭，避免 CPU 推理打满。
# 显式设置 NEWS_USE_FINBERT=true/false 可覆盖默认行为。
_USE_FINBERT_ENV = os.getenv("NEWS_USE_FINBERT", "").strip().lower()
USE_FINBERT = _USE_FINBERT_ENV == "true" if _USE_FINBERT_ENV else DEVICE >= 0
# 加载失败后的重试冷却（秒）：transformers 依赖补齐 / 网络恢复后能自动生效，
# 而不是一次失败永久锁死到下次进程重启
_RETRY_AFTER = float(os.getenv("FINBERT_RETRY_AFTER", "300"))
# 模型加载在后台线程进行，避免阻塞第一个 enrich 任务几十秒
_MODEL_LOAD_THREAD = None

_model_lock = threading.Lock()
_model_ready = False
_model_failed = False
_last_fail_at = 0.0
_pipeline = None  # transformers Pipeline 实例

# label 映射（不同模型 label 命名不一样）
_LABEL_MAP = {
    "positive": "bullish",
    "bullish": "bullish",
    "neutral": "neutral",
    "negative": "bearish",
    "bearish": "bearish",
    "POSITIVE": "bullish",
    "NEUTRAL": "neutral",
    "NEGATIVE": "bearish",
    "LABEL_0": "bearish",   # 大多数 transformer 默认 0=neg
    "LABEL_1": "neutral",
    "LABEL_2": "bullish",
}


def _try_load() -> None:
    """同步加载模型（仅 _load_worker 线程内调用，持有 _model_lock）。"""
    global _pipeline, _model_ready, _model_failed, _last_fail_at
    try:
        from transformers import pipeline  # type: ignore
        logger.info("加载 FinBERT 模型: %s ...", DEFAULT_MODEL)
        _pipeline = pipeline(
            "sentiment-analysis",
            model=DEFAULT_MODEL,
            tokenizer=DEFAULT_MODEL,
            device=DEVICE,
            truncation=True,
            max_length=256,
        )
        _model_ready = True
        _model_failed = False
        logger.info("FinBERT 加载完成")
    except Exception as e:
        _model_ready = False
        _model_failed = True
        _last_fail_at = time.monotonic()
        logger.warning(
            "FinBERT 加载失败（将仅使用字典法情感，%ss 后重试）：%s；"
            "修复方法：python3 backend/scripts/download_finbert.py",
            _RETRY_AFTER, e,
        )


def _ensure_loading() -> None:
    """确保模型在后台线程加载中（幂等，并发安全）。"""
    global _MODEL_LOAD_THREAD, _model_failed
    if _model_ready or not USE_FINBERT:
        return
    with _model_lock:
        if _model_ready or not USE_FINBERT:
            return
        # 失败后冷却期内不重试
        if _model_failed and time.monotonic() - _last_fail_at < _RETRY_AFTER:
            return
        if _MODEL_LOAD_THREAD is not None and _MODEL_LOAD_THREAD.is_alive():
            return  # 已有加载线程在跑
        _model_failed = False  # 重置失败标记，允许重试
        _MODEL_LOAD_THREAD = threading.Thread(target=_load_worker, daemon=True)
        _MODEL_LOAD_THREAD.start()


def _load_worker() -> None:
    with _model_lock:
        _try_load()


def score(text: str) -> Tuple[str | None, float | None]:
    """返回 (label, confidence)。模型未就绪/失败返回 (None, None)。"""
    if not USE_FINBERT:
        return None, None
    if not _model_ready:
        _ensure_loading()  # 后台启动加载，本次先返回 None（用字典法兜底）
        if not _model_ready:
            return None, None
    if not text or not text.strip():
        return None, None
    try:
        # 截断到 256 token 已在 pipeline 处设置
        res = _pipeline(text[:1000])
        if not res:
            return None, None
        item = res[0]
        raw_label = str(item.get("label") or "").strip()
        conf = float(item.get("score") or 0.0)
        label = _LABEL_MAP.get(raw_label, "neutral")
        return label, conf
    except Exception as e:
        logger.warning("FinBERT 推理失败: %s", str(e)[:120])
        return None, None


def score_batch(texts: list[str]) -> list[tuple[str | None, float | None]]:
    """批量打分（全量重建等离线场景用）。

    输入顺序与输出一一对应；空文本及模型未就绪/失败时对应位置返回 (None, None)。
    """
    if not USE_FINBERT or not texts:
        return [(None, None)] * len(texts)
    if not _model_ready:
        _ensure_loading()
        if not _model_ready:
            return [(None, None)] * len(texts)
    clean = [(t or "")[:1000] for t in texts]
    try:
        res = _pipeline(clean)
    except Exception as e:
        logger.warning("FinBERT 批量推理失败: %s", str(e)[:160])
        return [(None, None)] * len(texts)
    out: list[tuple[str | None, float | None]] = []
    for t, item in zip(clean, res, strict=True):
        if not t.strip():
            out.append((None, None))
            continue
        raw_label = str(item.get("label") or "").strip()
        conf = float(item.get("score") or 0.0)
        out.append((_LABEL_MAP.get(raw_label, "neutral"), conf))
    return out


def is_available() -> bool:
    return _model_ready and not _model_failed
