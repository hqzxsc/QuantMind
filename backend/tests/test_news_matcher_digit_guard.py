"""NewsMatcher 纯数字别名边界守卫单元测试。

只测 _boundary_ok 静态方法，不构建自动机、不依赖 DB / ahocorasick 运行态。
背景：裸 4 位 HK 码曾与年份海量误命中（「2026年」→2026.HK）。
"""

from __future__ import annotations

from backend.services.api.news.matcher import NewsMatcher


def _probe(text: str, term: str) -> bool:
    start = text.index(term)
    return NewsMatcher._boundary_ok(text, start, start + len(term), term)


# ---------- 拒识：数值语境 ----------

def test_rejects_year_followed_by_cn_nian():
    # 「展望2026年市场」不应识别出 2026.HK
    assert _probe("展望2026年市场走势", "2026") is False


def test_rejects_amount_quantifier_yi_yuan():
    # 「2007亿元」不应识别出碧桂园 2007.HK
    assert _probe("当年销售额突破2007亿元大关", "2007") is False


def test_rejects_percent_suffix():
    assert _probe("该业务同比下滑300750%", "300750") is False


def test_rejects_wan_suffix_on_ashare_code():
    # 「300750万元」不应命中宁德时代 300750.SZ 的裸码别名
    assert _probe("项目总投资300750万元", "300750") is False


def test_rejects_people_counter():
    assert _probe("累计服务用户600519人", "600519") is False


# ---------- 放行：真实代码语境 ----------

def test_accepts_code_in_full_width_parens():
    assert _probe("贵州茅台（600519）当日上涨", "600519") is True


def test_accepts_dot_market_suffix():
    assert _probe("贵州茅台(600519.SH)发布年报", "600519.SH") is True


def test_accepts_punctuation_delimited_bare_code():
    assert _probe("目标价对应代码为600519，逢低布局", "600519") is True


def test_accepts_hk_full_form_glued_to_cjk():
    # 完整形态 "2026.HK" 不是纯数字，粘 CJK 也应放行：「2026.HK收涨」
    assert _probe("小马智行(2026.HK)今日收涨", "2026.HK") is True


# ---------- 回归：原有边界语义不变 ----------

def test_ascii_letters_still_blocked_when_adjacent():
    assert _probe("假词xAAPLy混排", "AAPL") is False


def test_ascii_ticker_with_spaces_passes():
    assert _probe("关注 苹果 AAPL 后市表现", "AAPL") is True


def test_long_cjk_name_still_free_of_boundary_check():
    assert _probe("比亚迪月销创新高", "比亚迪") is True
