"""核心纯函数单元测试（不依赖引擎与网络）。

加载方式与 _selftest_synth.py 一致：直接从文件加载 main.py，
因此同时覆盖了「插件可被 AstrBot 导入」这一前提。
"""

from __future__ import annotations

import importlib.util
import os
import time
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent
MAIN = PLUGIN_DIR / "main.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("coeiroink_main_test", MAIN)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


m = _load_module()


# ---------------------------------------------------------------------------
# 风格归一化
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, 0),
        (5, 5),
        (6, 6),
        ("0", 0),
        ("5", 5),
        ("6", 6),
        ("+5", 5),
        ("5 ", 5),
        ("平静", 0),
        ("冷静", 0),
        ("れいせい", 0),
        ("reisei", 0),
        ("calm", 0),
        ("温柔", 5),
        ("溫柔", 5),
        ("おしとやか", 5),
        ("OSHITOYAKA", 5),
        ("gentle", 5),
        ("充满活力", 6),
        ("元气", 6),
        ("げんき", 6),
        ("genki", 6),
        ("lively", 6),
        ("1", None),
        ("3", None),
        ("bogus", None),
        ("", None),
        (None, None),
        (True, None),  # 布尔值一律视为非法
        (False, None),
    ],
)
def test_normalize_style(value, expected):
    assert m.normalize_style(value) == expected


# ---------------------------------------------------------------------------
# 风格前缀解析
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "style", "rest"),
    [
        ("[温柔] こんにちは", "温柔", "こんにちは"),
        ("【充满活力】テスト", "充满活力", "テスト"),
        ("#平静 こんにちは", "平静", "こんにちは"),
        ("普通のテキスト", None, "普通のテキスト"),
    ],
)
def test_extract_style_prefix(text, style, rest):
    assert m.extract_style_prefix(text) == (style, rest)


# ---------------------------------------------------------------------------
# 文本工具
# ---------------------------------------------------------------------------


def test_clean_text():
    out = m.clean_text("こんにちは！**太字** [リンク](https://a.b/c) 😀 `code` ```py\nx```")
    assert out == "こんにちは！ 太字 リンク code"
    assert m.clean_text("") == ""


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("こんにちは、今日はいい天気ですね。", True),
        ("日本語のテスト", True),
        ("hello world", False),
        ("你好世界", False),
        ("", False),
    ],
)
def test_is_japanese(text, expected):
    assert m.is_japanese(text) is expected


def test_split_text_segments_short():
    assert m.split_text_segments("こんにちは。元気ですか？", 100) == ["こんにちは。元気ですか？"]
    assert m.split_text_segments("", 10) == []


def test_split_text_segments_by_sentence():
    # 每句 4 字，max_len=6：两句合并会超长，因此逐句成段
    assert m.split_text_segments("あああ。いいい。ううう。", 6) == [
        "あああ。",
        "いいい。",
        "ううう。",
    ]
    # max_len=9：前两句可合并（4+4=8 ≤ 9）
    assert m.split_text_segments("あああ。いいい。ううう。", 9) == [
        "あああ。いいい。",
        "ううう。",
    ]


def test_split_text_segments_hard_cut():
    # 单句超长且无句读：按 max_len 硬切，不丢内容
    assert m.split_text_segments("あ" * 10 + "。", 4) == [
        "ああああ",
        "ああああ",
        "ああ。",
    ]


# ---------------------------------------------------------------------------
# api_base 回环校验（P2-5）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://127.0.0.1:50032", True),
        ("http://localhost:50032", True),
        ("http://[::1]:50032", True),
        ("http://127.1.2.3:50032", True),
        ("http://192.168.1.10:50032", False),
        ("https://example.com:50032", False),
        ("", False),
    ],
)
def test_api_base_is_local(url, expected):
    assert m.api_base_is_local(url) is expected


# ---------------------------------------------------------------------------
# 环境路径解析链（配置 → 环境变量 → 自动推导）
# ---------------------------------------------------------------------------


def test_resolve_engine_dir(monkeypatch):
    monkeypatch.delenv("COEIROINK_ENGINE_DIR", raising=False)
    assert m.resolve_engine_dir("") == ""
    monkeypatch.setenv("COEIROINK_ENGINE_DIR", "/env/dir")
    assert m.resolve_engine_dir("") == "/env/dir"
    assert m.resolve_engine_dir("/cfg/dir") == "/cfg/dir"  # 配置优先


def test_resolve_engine_bin(monkeypatch):
    monkeypatch.delenv("COEIROINK_ENGINE_BIN", raising=False)
    assert m.resolve_engine_bin("") is None
    assert m.resolve_engine_bin("/x") == os.path.join("/x", "engine", "engine")
    # 相对路径按相对引擎目录解释
    assert m.resolve_engine_bin("/x", "engine/engine") == os.path.join("/x", "engine", "engine")
    # 绝对路径原样返回
    assert m.resolve_engine_bin("/x", "/abs/engine") == "/abs/engine"


def test_resolve_engine_log(monkeypatch):
    monkeypatch.delenv("COEIROINK_ENGINE_LOG", raising=False)
    assert m.resolve_engine_log("/x") == os.path.join("/x", "engine.log")
    assert m.resolve_engine_log("/x", "/custom.log") == "/custom.log"


def test_resolve_ffmpeg(monkeypatch):
    monkeypatch.delenv("COEIROINK_FFMPEG", raising=False)
    assert m.resolve_ffmpeg("/my/ffmpeg") == "/my/ffmpeg"
    monkeypatch.setenv("COEIROINK_FFMPEG", "/env/ffmpeg")
    assert m.resolve_ffmpeg("") == "/env/ffmpeg"
    monkeypatch.delenv("COEIROINK_FFMPEG", raising=False)
    assert isinstance(m.resolve_ffmpeg(""), str)  # PATH 查找，找不到返回空串


# ---------------------------------------------------------------------------
# 合成并发信号量（P0-2）
# ---------------------------------------------------------------------------


def test_synth_semaphore_reuse():
    assert m.get_synth_semaphore(1) is m.get_synth_semaphore(1)
    assert m.get_synth_semaphore(2) is not m.get_synth_semaphore(1)
    # 非法值（<1）夹取到 1
    assert m.get_synth_semaphore(0) is m.get_synth_semaphore(1)


# ---------------------------------------------------------------------------
# 性能相关：短 TTL 缓存与备忘（1.8.0）
# ---------------------------------------------------------------------------


def test_available_memory_mb_cached_within_ttl(monkeypatch):
    calls = {"n": 0}

    def fake_read():
        calls["n"] += 1
        return 1234.0

    monkeypatch.setattr(m, "_read_available_memory_mb", fake_read)
    monkeypatch.setattr(m, "_MEM_CACHE", None)
    assert m.available_memory_mb() == 1234.0
    assert m.available_memory_mb() == 1234.0
    assert calls["n"] == 1  # TTL 内复用上一次读数

    # 缓存过期后重新读取
    monkeypatch.setattr(m, "_MEM_CACHE", (time.monotonic() - 10, 1.0))
    assert m.available_memory_mb() == 1234.0
    assert calls["n"] == 2

    # use_cache=False 强制读取
    assert m.available_memory_mb(use_cache=False) == 1234.0
    assert calls["n"] == 3


def test_api_base_is_local_memoized(monkeypatch):
    calls = {"n": 0}
    real_urlparse = m.urlparse

    def counting_urlparse(url):
        calls["n"] += 1
        return real_urlparse(url)

    monkeypatch.setattr(m, "urlparse", counting_urlparse)
    monkeypatch.setattr(m, "_API_LOCAL_CACHE", {})
    assert m.api_base_is_local("http://127.0.0.1:50032") is True
    assert m.api_base_is_local("http://127.0.0.1:50032") is True
    assert calls["n"] == 1  # 同一地址只解析一次
