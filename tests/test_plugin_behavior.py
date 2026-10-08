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
    plugin._usage = {}
    plugin._pool = {}
    plugin._pool_task = None
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


# ---------------------------------------------------------------------------
# 长期保留池（分层治理 + LLM 复审，1.10.0）
# ---------------------------------------------------------------------------


def _pool_plugin(tmp_path, monkeypatch, **overrides):
    """启用长期池的插件实例；池目录指向 tmp_path。"""
    cfg = dict(
        api_base="http://127.0.0.1:50032",
        max_concurrent_synth=1,
        max_synth_queue=4,
        synth_cache_size=8,
        phrase_cache_enabled=True,
        phrase_cache_size=8,
        phrase_pool_enabled=True,
        phrase_pool_min_hits=2,
        phrase_pool_max_entries=4,
        phrase_pool_retention_days=30,
        phrase_pool_llm_review=False,
        phrase_rank_mode="hits",
        phrase_ultra_top_rank=8,
        phrase_ultra_min_hits=10,
        phrase_ultra_max_entries=32,
        phrase_ultra_retention_days=365,
    )
    cfg.update(overrides)
    plugin = _make_plugin(**cfg)
    pool_dir = tmp_path / "pool"
    monkeypatch.setattr(m, "resolve_pool_dir", lambda: pool_dir)
    return plugin, pool_dir


def _seed_usage(plugin, text, hits, path, style_id=5):
    key = plugin._synth_cache_key(text, style_id)
    plugin._usage[key] = {"hits": hits, "ts": time.time(), "text": text, "path": str(path)}
    return key


def test_key_json_roundtrip():
    key = ("你好", 5, 1.0, "uuid-x", 0, True)
    assert m.synth_key_from_json(m.synth_key_to_json(key)) == key
    assert m.synth_key_from_json([1, 2]) is None  # 结构不合法


def test_parse_pool_review_response_variants():
    assert m.parse_pool_review_response('{"keep": [0, 1], "evict": [2], "reason": "ok"}') == {
        "keep": [0, 1],
        "ultra": [],
        "evict": [2],
        "reason": "ok",
    }
    parsed_ultra = m.parse_pool_review_response('{"ultra": [3], "keep": [], "reason": "r"}')
    assert parsed_ultra and parsed_ultra["ultra"] == [3]
    fenced = m.parse_pool_review_response('```json\n{"keep": [1], "reason": "x"}\n```')
    assert fenced and fenced["keep"] == [1] and fenced["evict"] == []
    noisy = m.parse_pool_review_response('结果为：{"keep": ["2"], "evict": []} 完毕')
    assert noisy and noisy["keep"] == [2]
    assert m.parse_pool_review_response("不是 JSON") is None


def test_record_use_counts_hits(tmp_path, monkeypatch):
    plugin, _ = _pool_plugin(tmp_path, monkeypatch)
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    key = _seed_usage(plugin, "こんにちは", 1, audio)
    plugin._record_use(key, str(audio))
    assert plugin._usage[key]["hits"] == 2


def test_pool_lookup_prefers_pool_and_counts(tmp_path, monkeypatch):
    plugin, pool_dir = _pool_plugin(tmp_path, monkeypatch)
    pool_dir.mkdir(parents=True, exist_ok=True)
    pooled = pool_dir / "p.mp3"
    pooled.write_bytes(b"x")
    key = plugin._synth_cache_key("おはよう", 5)
    plugin._pool[key] = {
        "path": str(pooled),
        "text": "おはよう",
        "hits": 0,
        "last_used": 0.0,
        "promoted_at": 0.0,
    }
    assert plugin._synth_cache_get(key) == str(pooled)
    assert plugin._pool[key]["hits"] == 1
    assert plugin._usage[key]["hits"] == 1


def test_pool_candidates_respect_threshold(tmp_path, monkeypatch):
    plugin, _ = _pool_plugin(tmp_path, monkeypatch)
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    _seed_usage(plugin, "高い", 5, audio)
    _seed_usage(plugin, "低い", 1, audio)  # 未达门槛 2
    candidates = plugin._pool_candidates()
    assert [c["text"] for c in candidates] == ["高い"]


def test_pool_promote_copies_and_clears_temp_cache(tmp_path, monkeypatch):
    plugin, pool_dir = _pool_plugin(tmp_path, monkeypatch)
    audio = tmp_path / "src.mp3"
    audio.write_bytes(b"audio-bytes")
    key = plugin._synth_cache_key("ありがとう", 5)
    plugin._synth_cache[key] = str(audio)
    plugin._phrase_cache[key] = str(audio)
    _seed_usage(plugin, "ありがとう", 3, audio)

    assert plugin._pool_promote(plugin._pool_candidates()[0]) is True
    entry = plugin._pool[key]
    assert os.path.isfile(entry["path"]) and entry["path"].startswith(str(pool_dir))
    assert key not in plugin._synth_cache and key not in plugin._phrase_cache


def test_pool_review_rule_based_promotes(tmp_path, monkeypatch):
    plugin, _ = _pool_plugin(tmp_path, monkeypatch)
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    _seed_usage(plugin, "おはよう", 4, audio)
    result = asyncio.run(plugin._pool_review_once())
    assert result["llm_used"] is False
    assert result["promoted"] == 1 and result["pool_entries"] == 1


def test_pool_review_uses_llm_keep_list(tmp_path, monkeypatch):
    plugin, _ = _pool_plugin(tmp_path, monkeypatch, phrase_pool_llm_review=True)
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    _seed_usage(plugin, "汎用の挨拶", 5, audio)
    _seed_usage(plugin, "一度きりの長文", 5, audio)

    class FakeProvider:
        async def text_chat(self, prompt=None, system_prompt=None, **kw):
            return type(
                "R", (), {"completion_text": '{"keep": [0], "evict": [], "reason": "保留问候"}'}
            )()

    class FakeContext:
        async def get_using_provider_async(self, umo=None):
            return FakeProvider()

    plugin.context = FakeContext()
    result = asyncio.run(plugin._pool_review_once())
    assert result["llm_used"] is True and result["promoted"] == 1
    assert result["reason"] == "保留问候"


def test_pool_review_falls_back_when_llm_returns_garbage(tmp_path, monkeypatch):
    plugin, _ = _pool_plugin(tmp_path, monkeypatch, phrase_pool_llm_review=True)
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    _seed_usage(plugin, "おはよう", 3, audio)

    class FakeProvider:
        async def text_chat(self, prompt=None, system_prompt=None, **kw):
            return type("R", (), {"completion_text": "我觉得都很好"})()  # 非 JSON

    class FakeContext:
        async def get_using_provider_async(self, umo=None):
            return FakeProvider()

    plugin.context = FakeContext()
    result = asyncio.run(plugin._pool_review_once())
    assert result["llm_used"] is False  # 解析失败 => 回退规则
    assert result["promoted"] == 1


def test_pool_enforce_limits_evicts_lowest_hits(tmp_path, monkeypatch):
    plugin, pool_dir = _pool_plugin(tmp_path, monkeypatch, phrase_pool_max_entries=2)
    pool_dir.mkdir(parents=True, exist_ok=True)
    for i, hits in enumerate((1, 9, 5)):
        f = pool_dir / f"p{i}.mp3"
        f.write_bytes(b"x")
        key = plugin._synth_cache_key(f"text{i}", 5)
        plugin._pool[key] = {
            "path": str(f),
            "text": f"text{i}",
            "hits": hits,
            "last_used": time.time(),
            "promoted_at": 0.0,
        }
    removed = plugin._pool_enforce_limits()
    assert removed == 1 and len(plugin._pool) == 2
    assert all(v["hits"] > 1 for v in plugin._pool.values())  # 命中最低的被淘汰


def test_pool_expire_removes_stale_entries(tmp_path, monkeypatch):
    plugin, pool_dir = _pool_plugin(tmp_path, monkeypatch, phrase_pool_retention_days=1)
    pool_dir.mkdir(parents=True, exist_ok=True)
    old_file = pool_dir / "old.mp3"
    old_file.write_bytes(b"x")
    key = plugin._synth_cache_key("古い", 5)
    plugin._pool[key] = {
        "path": str(old_file),
        "text": "古い",
        "hits": 9,
        "last_used": time.time() - 3 * 86400,
        "promoted_at": 0.0,
    }
    assert plugin._pool_expire() == 1
    assert key not in plugin._pool and not old_file.exists()


def test_pool_index_roundtrip(tmp_path, monkeypatch):
    plugin, pool_dir = _pool_plugin(tmp_path, monkeypatch)
    pool_dir.mkdir(parents=True, exist_ok=True)
    audio = pool_dir / "keep.mp3"
    audio.write_bytes(b"x")
    key = plugin._synth_cache_key("保存される", 5)
    plugin._pool[key] = {
        "path": str(audio),
        "text": "保存される",
        "hits": 7,
        "last_used": 123.0,
        "promoted_at": 100.0,
    }
    assert plugin._pool_save_index() is True

    other, _ = _pool_plugin(tmp_path, monkeypatch)
    assert other._pool_load_index() == 1
    assert other._pool[key]["hits"] == 7 and other._pool[key]["path"] == str(audio)


# ---------------------------------------------------------------------------
# 排序权重 + 超长期池（1.11.0）
# ---------------------------------------------------------------------------


def test_entry_weight_modes(tmp_path, monkeypatch):
    plugin, _ = _pool_plugin(tmp_path, monkeypatch)
    info = {"hits": 4, "text": "こんにちは"}  # 6 字
    assert plugin._entry_weight(info) == 4.0  # hits 模式：纯调用次数
    plugin.config["phrase_rank_mode"] = "weighted"
    assert plugin._entry_weight(info) == 4.0 * len(info["text"])  # weighted：次数 × 长度


def test_rank_entries_orders_by_weight_then_recency(tmp_path, monkeypatch):
    plugin, _ = _pool_plugin(tmp_path, monkeypatch)
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    _seed_usage(plugin, "少", 1, audio)
    _seed_usage(plugin, "多", 9, audio)
    _seed_usage(plugin, "中", 5, audio)
    ranked = plugin._rank_entries()
    assert [info["text"] for _, info, _ in ranked] == ["多", "中", "少"]
    assert [rank for _, _, rank in ranked] == [1, 2, 3]


def test_ultra_qualified_or_logic(tmp_path, monkeypatch):
    plugin, _ = _pool_plugin(
        tmp_path, monkeypatch, phrase_ultra_top_rank=3, phrase_ultra_min_hits=10
    )
    # 条件①：排名前 3（即使命中次数低）
    assert plugin._ultra_qualified({"hits": 1, "text": "x"}, 2) is True
    # 条件②：命中次数 ≥ 10（即使排名靠后）
    assert plugin._ultra_qualified({"hits": 10, "text": "x"}, 99) is True
    # 两个条件都不满足
    assert plugin._ultra_qualified({"hits": 2, "text": "x"}, 50) is False


def test_ultra_qualified_respects_disabled_conditions(tmp_path, monkeypatch):
    plugin, _ = _pool_plugin(
        tmp_path, monkeypatch, phrase_ultra_top_rank=0, phrase_ultra_min_hits=0
    )
    assert plugin._ultra_qualified({"hits": 100, "text": "x"}, 1) is False  # 都关闭 => 不晋级


def test_review_promotes_ultra_by_rank_rule(tmp_path, monkeypatch):
    """LLM 关闭时：排名前 X 的候选直接进超长期池（纯排序算法决策）。"""
    plugin, _ = _pool_plugin(
        tmp_path,
        monkeypatch,
        phrase_pool_llm_review=False,
        phrase_ultra_top_rank=1,
        phrase_ultra_min_hits=0,
        phrase_pool_min_hits=1,
    )
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    _seed_usage(plugin, "一番よく使う", 5, audio)
    _seed_usage(plugin, "たまに使う", 2, audio)
    result = asyncio.run(plugin._pool_review_once())
    assert result["llm_used"] is False  # 纯算法
    assert result["promoted"] == 2 and result["promoted_ultra"] == 1
    tiers = sorted(v.get("tier") for v in plugin._pool.values())
    assert tiers == ["pool", "ultra"]


def test_review_promotes_ultra_by_hits_rule(tmp_path, monkeypatch):
    """条件②：调用次数达标即进超长期池（与排名无关）。"""
    plugin, _ = _pool_plugin(
        tmp_path,
        monkeypatch,
        phrase_pool_llm_review=False,
        phrase_ultra_top_rank=0,  # 关闭条件①
        phrase_ultra_min_hits=3,
        phrase_pool_min_hits=1,
    )
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    _seed_usage(plugin, "よく使う", 4, audio)
    _seed_usage(plugin, "あまり使わない", 1, audio)
    result = asyncio.run(plugin._pool_review_once())
    assert result["promoted_ultra"] == 1
    ultra_texts = [v["text"] for v in plugin._pool.values() if v.get("tier") == "ultra"]
    assert ultra_texts == ["よく使う"]


def test_review_llm_can_upgrade_and_evict(tmp_path, monkeypatch):
    plugin, _ = _pool_plugin(
        tmp_path,
        monkeypatch,
        phrase_pool_llm_review=True,
        phrase_ultra_top_rank=0,
        phrase_ultra_min_hits=0,  # 规则不晋级 ultra，交给 LLM
    )
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    _seed_usage(plugin, "汎用の挨拶", 3, audio)

    class FakeProvider:
        async def text_chat(self, prompt=None, system_prompt=None, **kw):
            return type(
                "R",
                (),
                {
                    "completion_text": '{"ultra": [0], "keep": [], "evict": [], "reason": "通用问候"}'
                },
            )()

    class FakeContext:
        async def get_using_provider_async(self, umo=None):
            return FakeProvider()

    plugin.context = FakeContext()
    result = asyncio.run(plugin._pool_review_once())
    assert result["llm_used"] is True
    assert result["promoted_ultra"] + result["upgraded_ultra"] == 1
    assert result["ultra_entries"] == 1
    assert list(plugin._pool.values())[0]["tier"] == "ultra"


def test_pool_expire_is_tier_aware(tmp_path, monkeypatch):
    """长期池保留 1 天、超长期池保留 365 天：只有长期池条目被回收。"""
    plugin, pool_dir = _pool_plugin(
        tmp_path, monkeypatch, phrase_pool_retention_days=1, phrase_ultra_retention_days=365
    )
    pool_dir.mkdir(parents=True, exist_ok=True)
    stale = time.time() - 3 * 86400
    keys = {}
    for name, tier in (("pool", "pool"), ("ultra", "ultra")):
        f = pool_dir / f"{name}.mp3"
        f.write_bytes(b"x")
        key = plugin._synth_cache_key(name, 5)
        plugin._pool[key] = {
            "path": str(f),
            "text": name,
            "hits": 5,
            "last_used": stale,
            "promoted_at": 0.0,
            "tier": tier,
        }
        keys[name] = key
    assert plugin._pool_expire() == 1
    assert keys["pool"] not in plugin._pool  # 长期池过期回收
    assert keys["ultra"] in plugin._pool  # 超长期池仍在保留期内


def test_pool_enforce_limits_per_tier(tmp_path, monkeypatch):
    plugin, pool_dir = _pool_plugin(
        tmp_path,
        monkeypatch,
        phrase_pool_max_entries=1,
        phrase_ultra_max_entries=1,
    )
    pool_dir.mkdir(parents=True, exist_ok=True)
    for name, tier, hits in (
        ("p1", "pool", 2),
        ("p2", "pool", 8),
        ("u1", "ultra", 3),
        ("u2", "ultra", 9),
    ):
        f = pool_dir / f"{name}.mp3"
        f.write_bytes(b"x")
        key = plugin._synth_cache_key(name, 5)
        plugin._pool[key] = {
            "path": str(f),
            "text": name,
            "hits": hits,
            "last_used": time.time(),
            "promoted_at": 0.0,
            "tier": tier,
        }
    removed = plugin._pool_enforce_limits()
    assert removed == 2  # 每个 tier 各淘汰 1 条（权重最低）
    texts = sorted(v["text"] for v in plugin._pool.values())
    assert texts == ["p2", "u2"]


def test_pool_retier_upgrades_existing_entry(tmp_path, monkeypatch):
    plugin, pool_dir = _pool_plugin(tmp_path, monkeypatch)
    pool_dir.mkdir(parents=True, exist_ok=True)
    f = pool_dir / "x.mp3"
    f.write_bytes(b"x")
    key = plugin._synth_cache_key("昇格対象", 5)
    plugin._pool[key] = {
        "path": str(f),
        "text": "昇格対象",
        "hits": 3,
        "last_used": time.time(),
        "promoted_at": 0.0,
        "tier": "pool",
    }
    assert plugin._pool_retier(key, "ultra") is True
    assert plugin._pool[key]["tier"] == "ultra"
    assert plugin._pool_retier(key, "ultra") is False  # 已经是该层级
