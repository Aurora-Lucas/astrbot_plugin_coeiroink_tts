"""独立验证脚本：绕开 AstrBot 运行时，直接调用插件核心合成函数。

用法：
    python _selftest_synth.py [要合成的文本] [--config <插件配置json路径>]

说明（全部可选，都有通用默认值）：
- 文本：默认 "こんにちは、今日はいい天気ですね。"
- 配置：--config 或环境变量 ASTRBOT_PLUGIN_CONFIG；
  默认取 <插件目录>/../config/astrbot_plugin_coeiroink_tts_config.json
  （AstrBot 标准目录布局）。读不到配置时使用插件默认值/环境变量。
- 输出目录：环境变量 SELFTEST_OUT_DIR，默认 <插件目录>/selftest_out。
- 引擎路径、ffmpeg 等：优先用配置，其次用插件内置的
  配置→环境变量→自动推导 解析链（与运行时行为一致）。
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent
PLUGIN_MAIN = os.path.join(PLUGIN_DIR, "main.py")

# 默认配置路径：<插件目录>/../config/astrbot_plugin_coeiroink_tts_config.json
DEFAULT_CONFIG_PATH = PLUGIN_DIR.parent.parent / "config" / "astrbot_plugin_coeiroink_tts_config.json"


def load_plugin_module():
    spec = importlib.util.spec_from_file_location("coeiroink_main_selftest", PLUGIN_MAIN)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_config(config_path: str | None) -> dict:
    """加载插件运行配置；失败返回空 dict（走插件默认值/环境变量）。"""
    path = (
        config_path
        or os.environ.get("ASTRBOT_PLUGIN_CONFIG")
        or str(DEFAULT_CONFIG_PATH)
    )
    try:
        # 配置文件可能带 UTF-8 BOM，用 utf-8-sig 读取
        with open(path, encoding="utf-8-sig") as f:
            cfg = json.load(f)
        print(f"[config] 使用配置 {path}")
        return cfg if isinstance(cfg, dict) else {}
    except Exception as e:  # noqa: BLE001
        print(f"[config] 未读取到配置（{path}）：{e}；改用插件默认值/环境变量")
        return {}


def resolve_style(m, cfg: dict) -> int:
    """沿用插件配置里的默认风格，避免自测时意外给引擎加载第二种风格。

    引擎对每个风格是懒加载且常驻内存（单份数百 MB），自测若用了与
    运行配置不同的风格，会白白多占一份内存。读不到配置时回退默认风格。
    """
    sid = m.normalize_style(cfg.get("style_id"))
    if sid is None:
        sid = m.DEFAULT_STYLE_ID
    print(f"[style] 使用风格 styleId={sid}")
    return sid


def probe_duration(path: str) -> float | None:
    """用 ffprobe 探测音频时长；找不到 ffprobe 返回 None。"""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        proc = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "json", path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        return float(json.loads(proc.stdout)["format"]["duration"])
    except Exception:  # noqa: BLE001
        return None


async def main() -> int:
    parser = argparse.ArgumentParser(description="COEIROINK 插件自测")
    parser.add_argument("text", nargs="?", default="こんにちは、今日はいい天気ですね。")
    parser.add_argument("--config", default=None, help="插件配置 JSON 路径")
    args = parser.parse_args()

    out_dir = os.environ.get("SELFTEST_OUT_DIR") or str(PLUGIN_DIR / "selftest_out")
    os.makedirs(out_dir, exist_ok=True)

    m = load_plugin_module()
    cfg = load_config(args.config)

    api_base = cfg.get("api_base") or m.DEFAULT_API_BASE
    speaker_uuid = cfg.get("speaker_uuid") or m.DEFAULT_SPEAKER_UUID
    style_id = resolve_style(m, cfg)
    speed_scale = float(cfg.get("speedScale") or 1.0)
    engine_dir = cfg.get("engine_dir") or ""
    engine_bin = cfg.get("engine_bin") or None
    engine_log = cfg.get("engine_log") or ""

    alive = await m.check_engine_alive(api_base)
    print(f"[engine] alive={alive} (api_base={api_base})")
    if not alive:
        ok = await m.ensure_engine_running(
            api_base=api_base,
            engine_dir=engine_dir,
            engine_bin=engine_bin,
            engine_log=engine_log,
            auto_start=True,
            start_timeout=90,
        )
        print(f"[engine] auto_start -> {ok}")

    path = await m.synthesize_to_file(
        args.text,
        output_dir=out_dir,
        api_base=api_base,
        speaker_uuid=speaker_uuid,
        style_id=style_id,
        speed_scale=speed_scale,
        enable_mp3=True,
        keep_temp_files=False,
    )
    size = os.path.getsize(path)
    print(json.dumps({
        "text": args.text,
        "path": path,
        "bytes": size,
        "duration_sec": probe_duration(path),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
