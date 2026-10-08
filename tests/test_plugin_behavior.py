"""行为层单元测试：插件实例方法（队列上限、风格保护、分段配置、临时清理、风格记录）。

不依赖引擎与网络：通过 `object.__new__` 构造插件实例，按需替换实例方法/模块函数。
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import time
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
