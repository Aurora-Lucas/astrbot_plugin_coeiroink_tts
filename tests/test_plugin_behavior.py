"""行为层单元测试：插件实例方法（队列上限、风格保护、分段配置、临时清理、风格记录）。

不依赖引擎与网络：通过 `object.__new__` 构造插件实例，按需替换实例方法/模块函数。
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import time
from collections import OrderedDict
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent.parent
MAIN = PLUGIN_DIR / "main.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("coeiroink_main_behavior", MAIN)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


m = _load_module()


def _make_plugin(**overrides):
    """构造最小可用的插件实例（绕过 Star 初始化）。"""
    plugin = object.__new__(m.CoeiroinkTTSPlugin)
    plugin.config = dict(m.DEFAULT_CONFIG)
    plugin.config.update(overrides)
    plugin._astrbot_config = None
    plugin._loaded_styles = set()
    plugin._synth_cache = OrderedDict()  # 真实类在 __init__ 中初始化
    plugin._phrase_cache = OrderedDict()
    plugin._bg_tasks = set()
    plugin._warmup_task = None
    plugin._cleanup_task = None
    return plugin


# ---------------------------------------------------------------------------
# 合成排队上限
# ---------------------------------------------------------------------------


def test_synth_queue_cap_skips_when_saturated(monkeypatch):
    plugin = _make_plugin(
        max_concurrent_synth=1,
        max_synth_queue=1,
        api_base="http://127.0.0.1:50032",
    )
    # 在途数已达上限（1 + 1 = 2），再来一条应直接跳过
    monkeypatch.setattr(m, "_SYNTH_INFLIGHT", 2)
    assert asyncio.run(plugin._synth_one("テスト", 0)) is None
    # finally 分支必须把计数还原
    assert m._SYNTH_INFLIGHT == 2


def test_synth_queue_cap_zero_means_no_waiting(monkeypatch):
    plugin = _make_plugin(
        max_concurrent_synth=1,
        max_synth_queue=0,
        api_base="http://127.0.0.1:50032",
    )
    monkeypatch.setattr(m, "_SYNTH_INFLIGHT", 1)
    assert asyncio.run(plugin._synth_one("テスト", 0)) is None
    assert m._SYNTH_INFLIGHT == 1


def test_synth_one_marks_loaded_style(monkeypatch):
    plugin = _make_plugin(
        max_concurrent_synth=1,
        max_synth_queue=2,
        api_base="http://127.0.0.1:50032",
    )

    async def fake_ensure_engine():
        return True

    async def fake_synthesize(text, **kwargs):
        return "/tmp/fake.mp3"

    plugin._ensure_engine = fake_ensure_engine
    monkeypatch.setattr(m, "synthesize_with_recovery", fake_synthesize)

    assert asyncio.run(plugin._synth_one("テスト", 5)) == "/tmp/fake.mp3"
    assert plugin._loaded_styles == {5}


def test_synth_blocked_when_api_base_not_local():
    plugin = _make_plugin(api_base="http://192.168.1.10:50032", allow_remote_engine=False)
    assert plugin._api_base_allowed() is False
    assert asyncio.run(plugin._synth_one("テスト", 0)) is None


# ---------------------------------------------------------------------------
# 风格切换内存保护
# ---------------------------------------------------------------------------


def test_style_override_disabled_by_config():
    plugin = _make_plugin(allow_style_override=False)
    err = plugin._style_override_guard(5)
    assert err and "allow_style_override" in err


def test_style_override_allowed_for_loaded_style():
    plugin = _make_plugin(allow_style_override=True)
    plugin._loaded_styles.add(5)
    assert plugin._style_override_guard(5) is None


def test_style_override_rejected_when_memory_low(monkeypatch):
    plugin = _make_plugin(allow_style_override=True, style_switch_min_free_mb=800)
    monkeypatch.setattr(m, "available_memory_mb", lambda: 100.0)
    err = plugin._style_override_guard(6)
    assert err and "可用内存不足" in err


def test_style_override_ok_when_memory_sufficient(monkeypatch):
    plugin = _make_plugin(allow_style_override=True, style_switch_min_free_mb=800)
    monkeypatch.setattr(m, "available_memory_mb", lambda: 5000.0)
    assert plugin._style_override_guard(6) is None


def test_resolve_style_applies_guard():
    plugin = _make_plugin(allow_style_override=False)
    style_id, err = plugin._resolve_style("温柔")
    assert style_id is None and err


# ---------------------------------------------------------------------------
# 分段段数配置
# ---------------------------------------------------------------------------


def test_segments_respects_max_synth_segments():
    plugin = _make_plugin(max_text_length=6, skip_if_too_long=False, max_synth_segments=2)
    segments = plugin._segments("あああ。いいい。ううう。")
    assert segments == ["あああ。", "いいい。"]


def test_segments_skip_when_skip_flag_on():
    plugin = _make_plugin(max_text_length=6, skip_if_too_long=True)
    assert plugin._segments("あああ。いいい。ううう。") == []


# ---------------------------------------------------------------------------
# 临时文件清理
# ---------------------------------------------------------------------------


def test_cleanup_temp_audio_removes_only_expired(tmp_path, monkeypatch):
    old = tmp_path / "coeiroink_old.mp3"
    new = tmp_path / "coeiroink_new.mp3"
    old.write_bytes(b"x")
    new.write_bytes(b"x")
    stale = time.time() - 3 * 3600
    os.utime(old, (stale, stale))

    monkeypatch.setattr(m, "get_astrbot_temp_path", lambda: str(tmp_path))
    plugin = _make_plugin()
    asyncio.run(plugin._cleanup_temp_audio())

    assert not old.exists()
    assert new.exists()


# ---------------------------------------------------------------------------
# 合成结果 LRU 缓存（1.8.0）
# ---------------------------------------------------------------------------


def _stub_synth_plugin(tmp_path, monkeypatch, **overrides):
    """构造插件实例 + 假合成函数，返回 (plugin, 调用计数)。"""
    plugin = _make_plugin(
        max_concurrent_synth=1,
        max_synth_queue=2,
        api_base="http://127.0.0.1:50032",
        **overrides,
    )
    calls = {"n": 0}
    audio = tmp_path / "cached.mp3"
    audio.write_bytes(b"fake-mp3")

    async def fake_ensure_engine():
        return True

    async def fake_synthesize(text, **kwargs):
        calls["n"] += 1
        return str(audio)

    plugin._ensure_engine = fake_ensure_engine
    monkeypatch.setattr(m, "synthesize_with_recovery", fake_synthesize)
    return plugin, calls, audio


def test_synth_cache_hit_avoids_engine_call(tmp_path, monkeypatch):
    plugin, calls, audio = _stub_synth_plugin(tmp_path, monkeypatch, synth_cache_size=8)
    first = asyncio.run(plugin._synth_one("こんにちは", 5))
    second = asyncio.run(plugin._synth_one("こんにちは", 5))
    assert first == second == str(audio)
    assert calls["n"] == 1  # 第二次命中缓存，未再次调用引擎


def test_synth_cache_disabled_by_zero_size(tmp_path, monkeypatch):
    plugin, calls, _ = _stub_synth_plugin(tmp_path, monkeypatch, synth_cache_size=0)
    asyncio.run(plugin._synth_one("こんにちは", 5))
    asyncio.run(plugin._synth_one("こんにちは", 5))
    assert calls["n"] == 2  # 缓存禁用 => 每次都真实合成


def test_synth_cache_key_includes_style(tmp_path, monkeypatch):
    plugin, calls, _ = _stub_synth_plugin(tmp_path, monkeypatch, synth_cache_size=8)
    asyncio.run(plugin._synth_one("こんにちは", 5))
    asyncio.run(plugin._synth_one("こんにちは", 6))
    assert calls["n"] == 2  # 不同风格不共用缓存


def test_synth_cache_drops_entry_when_file_missing(tmp_path, monkeypatch):
    plugin, calls, audio = _stub_synth_plugin(tmp_path, monkeypatch, synth_cache_size=8)
    asyncio.run(plugin._synth_one("こんにちは", 5))
    audio.unlink()  # 模拟临时目录定时清理
    asyncio.run(plugin._synth_one("こんにちは", 5))
    assert calls["n"] == 2  # 文件已不在 => 重新合成
    assert len(plugin._synth_cache) == 1


def test_synth_cache_lru_eviction(tmp_path, monkeypatch):
    plugin, calls, _ = _stub_synth_plugin(tmp_path, monkeypatch, synth_cache_size=2)
    for text in ("あ", "い", "う"):
        asyncio.run(plugin._synth_one(text, 5))
    assert len(plugin._synth_cache) == 2  # 超过上限淘汰最旧
    asyncio.run(plugin._synth_one("あ", 5))
    assert calls["n"] == 4  # "あ" 已被淘汰 => 重新合成


# ---------------------------------------------------------------------------
# 句级缓存 + 拼接（1.9.0）
# ---------------------------------------------------------------------------


def _stub_phrase_plugin(tmp_path, monkeypatch, **overrides):
    """构造启用句级缓存的插件实例；合成与拼接均为假实现（可统计调用）。"""
    cfg = dict(
        api_base="http://127.0.0.1:50032",
        max_concurrent_synth=1,
        max_synth_queue=4,
        max_text_length=200,
        skip_if_too_long=True,
        synth_cache_size=0,  # 关掉整段缓存，单独观察句级复用
        phrase_cache_enabled=True,
        phrase_cache_size=32,
        phrase_split_mode="sentence",
    )
    cfg.update(overrides)
    plugin = _make_plugin(**cfg)
    calls = {"texts": []}
    merged = tmp_path / "merged.mp3"
    merged.write_bytes(b"merged")

    async def fake_ensure_engine():
        return True

    async def fake_synthesize(text, **kwargs):
        calls["texts"].append(text)
        path = tmp_path / f"seg{len(calls['texts'])}.mp3"
        path.write_bytes(b"x")
        return str(path)

    async def fake_concat(paths):
        calls["concat"] = calls.get("concat", 0) + 1
        return str(merged)

    plugin._ensure_engine = fake_ensure_engine
    plugin._concat_audio = fake_concat
    monkeypatch.setattr(m, "synthesize_with_recovery", fake_synthesize)
    return plugin, calls, merged


def test_split_sentences_modes():
    assert m.split_sentences("你好。世界。", 200) == ["你好。", "世界。"]
    assert m.split_sentences("你好，世界", 200) == ["你好，世界"]
    assert m.split_sentences("你好，世界", 200, clause=True) == ["你好，", "世界"]
    assert m.split_sentences("", 200) == []
    # 单句超长硬切
    assert m.split_sentences("あ" * 10 + "。", 4) == ["ああああ", "ああああ", "ああ。"]


def test_phrase_cache_reuses_shared_sentence(tmp_path, monkeypatch):
    plugin, calls, merged = _stub_phrase_plugin(tmp_path, monkeypatch)

    first = asyncio.run(plugin._synthesize_all("おはよう。今日はいい天気ですね。", 5))
    assert calls["texts"] == ["おはよう。", "今日はいい天気ですね。"]
    assert first == [str(merged)]  # 拼接成一条语音

    calls["texts"].clear()
    second = asyncio.run(plugin._synthesize_all("おはよう。明日も晴れるそうです。", 5))
    assert calls["texts"] == ["明日も晴れるそうです。"]  # 第一句命中，只合成新句
    assert second == [str(merged)]


def test_phrase_cache_single_sentence_unchanged(tmp_path, monkeypatch):
    plugin, calls, _ = _stub_phrase_plugin(tmp_path, monkeypatch)
    paths = asyncio.run(plugin._synthesize_all("おはようございます。", 5))
    assert len(paths) == 1
    assert calls.get("concat") is None  # 单句不触发拼接
    assert calls["texts"] == ["おはようございます。"]


def test_phrase_cache_disabled_falls_back(tmp_path, monkeypatch):
    plugin, calls, _ = _stub_phrase_plugin(
        tmp_path,
        monkeypatch,
        phrase_cache_enabled=False,
        max_text_length=5,
        skip_if_too_long=False,  # 关闭跳过 => 走旧的分段合成路径
    )
    paths = asyncio.run(plugin._synthesize_all("おはよう。いい天気。", 5))
    assert calls.get("concat") is None  # 不走拼接
    assert len(paths) == 2  # 退回逐段多条的旧行为


def test_phrase_cache_concat_failure_falls_back_to_segments(tmp_path, monkeypatch):
    plugin, calls, _ = _stub_phrase_plugin(tmp_path, monkeypatch)

    async def failing_concat(paths):
        return None

    plugin._concat_audio = failing_concat
    paths = asyncio.run(plugin._synthesize_all("おはよう。いい天気。", 5))
    assert len(paths) == 2  # 拼接失败 => 逐段发送，不丢语音


def test_phrase_cache_lru_eviction(tmp_path, monkeypatch):
    plugin, calls, _ = _stub_phrase_plugin(tmp_path, monkeypatch, phrase_cache_size=1)
    asyncio.run(plugin._synthesize_all("ああ。いい。", 5))
    assert len(plugin._phrase_cache) == 1  # 上限 1，只保留最后一句
    calls["texts"].clear()
    asyncio.run(plugin._synthesize_all("ああ。うう。", 5))
    assert "ああ。" in calls["texts"]  # 已被淘汰 => 重新合成


def test_phrase_cache_clause_mode_reuses_clauses(tmp_path, monkeypatch):
    plugin, calls, _ = _stub_phrase_plugin(
        tmp_path, monkeypatch, phrase_split_mode="clause", phrase_cache_size=32
    )
    asyncio.run(plugin._synthesize_all("こんにちは、いい天気ですね、散歩でも。", 5))
    assert calls["texts"] == ["こんにちは、", "いい天気ですね、", "散歩でも。"]
    calls["texts"].clear()
    asyncio.run(plugin._synthesize_all("こんにちは、また明日。", 5))
    assert calls["texts"] == ["また明日。"]  # 小句「こんにちは、」命中


def test_phrase_key_ignores_trailing_punct():
    assert m.phrase_key_text("いい天気ですね。") == "いい天気ですね"
    assert m.phrase_key_text("いい天気ですね、") == "いい天気ですね"
    assert m.phrase_key_text("テスト") == "テスト"


def test_phrase_cache_reuses_across_different_trailing_punct(tmp_path, monkeypatch):
    """「…ですね。」与「…ですね、」应复用同一段音频（键忽略结尾标点）。"""
    plugin, calls, _ = _stub_phrase_plugin(
        tmp_path, monkeypatch, phrase_split_mode="clause", phrase_cache_size=32
    )
    asyncio.run(plugin._synthesize_all("おはようございます、いい天気ですね。", 5))
    assert calls["texts"] == ["おはようございます、", "いい天気ですね。"]
    calls["texts"].clear()
    asyncio.run(plugin._synthesize_all("いい天気ですね、散歩に行きましょう。", 5))
    assert calls["texts"] == ["散歩に行きましょう。"]  # 「いい天気ですね、」命中


def test_phrase_cache_exact_punct_mode_when_disabled(tmp_path, monkeypatch):
    plugin, calls, _ = _stub_phrase_plugin(
        tmp_path,
        monkeypatch,
        phrase_split_mode="clause",
        phrase_key_ignore_punct=False,
        phrase_cache_size=32,
    )
    asyncio.run(plugin._synthesize_all("おはようございます、いい天気ですね。", 5))
    calls["texts"].clear()
    asyncio.run(plugin._synthesize_all("いい天気ですね、散歩に行きましょう。", 5))
    assert calls["texts"] == ["いい天気ですね、", "散歩に行きましょう。"]  # 标点不同 => 不复用
