"""TrainingRunLogStream.fetch_snapshot 连续重复行去重。

背景：容器日志经多路消费（docker 轮询 + 内部回调）写入 Redis Stream，同一条
日志可能被重复 append 多次（实测约 5 倍）。此前 fetch_snapshot 不去重，
220 行窗口内只有 ~44 条唯一日志，因子筛选漏斗日志被挤出窗口，用户看到
"空洞"日志。本测试守卫连续去重逻辑：只去重连续重复，非连续相同行保留。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 本地/单测环境可能没有 redis 包：monkeypatch 模块级 redis_lib 为真值，
# 让 _get_client 走已注入的 _client 假客户端（不发起真实连接）。
import backend.services.engine.training.training_log_stream as tls  # noqa: E402
from backend.services.engine.training.training_log_stream import TrainingRunLogStream  # noqa: E402


class _FakeRedis:
    """最小 xrevrange/get 假客户端：构造时传入"最新在前"的记录（与 redis 语义一致）。"""

    def __init__(self, newest_first: list[list], state: dict | None = None) -> None:
        self._records = newest_first
        self._state = state

    def get(self, key: str) -> bytes | None:
        if self._state is None:
            return None
        return json.dumps(self._state, ensure_ascii=False).encode()

    def xrevrange(self, key: str, count: int = 0) -> list[list]:
        return self._records[-count:] if count > 0 else self._records


def _entry(line: str, status: str | None = None) -> list:
    payload: dict = {b"line": line.encode()}
    if status:
        payload[b"status"] = status.encode()
    return [b"1-0", payload]


def _make_stream(fake: _FakeRedis) -> TrainingRunLogStream:
    stream = TrainingRunLogStream()
    stream.enabled = True
    stream._client = fake
    # 无 redis 包时让 _get_client 跳过 redis_lib is None 早退
    tls.redis_lib = object()  # type: ignore[attr-defined]
    return stream


def test_fetch_snapshot_dedupes_consecutive_duplicate_lines() -> None:
    # 最新在前：[Selected, Selected, Screening, Screening, Screening]
    stream = _make_stream(
        _FakeRedis(
            [
                _entry("Selected 34 features from training segment only"),
                _entry("Selected 34 features from training segment only"),
                _entry("=== Factor Selection: IC/ICIR screening ==="),
                _entry("=== Factor Selection: IC/ICIR screening ==="),
                _entry("=== Factor Selection: IC/ICIR screening ==="),
            ]
        )
    )

    snap = stream.fetch_snapshot("run_dedupe_1", line_limit=100)
    assert snap is not None
    assert snap["logs"].split("\n") == [
        "=== Factor Selection: IC/ICIR screening ===",
        "Selected 34 features from training segment only",
    ]


def test_fetch_snapshot_keeps_non_consecutive_identical_lines() -> None:
    # A B A：B 出现打断连续重复，两个 A 都必须保留（不是全局去重）
    stream = _make_stream(_FakeRedis([_entry("A"), _entry("B"), _entry("A")]))

    snap = stream.fetch_snapshot("run_dedupe_2", line_limit=100)
    assert snap is not None
    assert snap["logs"].split("\n") == ["A", "B", "A"]


def test_fetch_snapshot_dedupe_respects_line_limit() -> None:
    # 12 条连续重复 → 去重后 1 行；line_limit 作用于去重前的窗口
    stream = _make_stream(_FakeRedis([_entry("dupe line") for _ in range(12)]))

    snap = stream.fetch_snapshot("run_dedupe_3", line_limit=10)
    assert snap is not None
    assert snap["logs"].split("\n") == ["dupe line"]
