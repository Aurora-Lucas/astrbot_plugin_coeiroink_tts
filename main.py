"""Tsukuyomi-chan COEIROINK 日语语音插件（AstrBot）。

功能：把 AstrBot 的回复文本（必要时先翻译成日语）通过本地 COEIROINK CPU 引擎
合成为语音，并以 Record 语音消息发出。

音源：月读酱（Tsukuyomi-chan，夢前黎制作，免费音源 https://tyc.rei-yumesaki.net/）
软件：COEIROINK（シロワニさん制作，免费 TTS https://coeiroink.com/）
Logo：ノザラシ制作（https://seiga.nicovideo.jp/seiga/im11798588）

引擎默认监听 127.0.0.1:50032，仅本机访问，不对外暴露。
"""

from __future__ import annotations

import asyncio
import os
import platform
import random
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Record
from astrbot.api.provider import ProviderType
from astrbot.api.star import Context, Star

# 插件 Pages（Web UI）所需的 web 助手只在较新版本的 AstrBot 中提供。
# 容错导入：老版本 AstrBot 下自动降级为「无 Web UI」，核心合成链路不受影响。
try:
    from astrbot.api.web import error_response, json_response, request
except ImportError:  # pragma: no cover - 仅老版本 AstrBot 触发
    error_response = json_response = request = None  # type: ignore[assignment]
from astrbot.core.agent.tool import FunctionTool
from astrbot.core.provider.provider import TTSProvider
from astrbot.core.provider.register import register_provider_adapter
from astrbot.core.utils.astrbot_path import get_astrbot_temp_path

# ---------------------------------------------------------------------------
# 常量与默认配置
# ---------------------------------------------------------------------------
# 通用默认值：不写死任何机器相关路径。环境相关项留空，运行时按
# 「配置项 → 环境变量 → 自动推导」的顺序解析（见下方 resolve_* 函数）。
DEFAULT_ENGINE_DIR = ""  # 留空 = 未配置；可从环境变量 COEIROINK_ENGINE_DIR 读取
DEFAULT_ENGINE_LOG = ""  # 留空 = 自动取 <engine_dir>/engine.log（兜底 AstrBot 临时目录）
DEFAULT_ENGINE_BIN = ""  # 留空 = 自动取 <engine_dir>/engine/engine（Windows 为 engine.exe）
DEFAULT_API_BASE = "http://127.0.0.1:50032"
DEFAULT_SPEAKER_UUID = "3c37646f-3881-5374-2a83-149267990abc"
DEFAULT_STYLE_ID = 0
DEFAULT_FFMPEG = ""  # 留空 = 自动在 PATH 中查找 ffmpeg

# 与引擎路径相关的可选环境变量（配置项优先）
ENV_ENGINE_DIR = "COEIROINK_ENGINE_DIR"
ENV_ENGINE_BIN = "COEIROINK_ENGINE_BIN"
ENV_ENGINE_LOG = "COEIROINK_ENGINE_LOG"
ENV_FFMPEG = "COEIROINK_FFMPEG"

DEFAULT_TRANSLATE_PROMPT = (
    "你是一个专业的翻译引擎。请把用户给出的文本翻译成自然、口语化的日语。"
    "只输出日语译文本身，不要输出任何解释、罗马音、拼音、引号、标注或额外文字。"
    "如果原文已经是日语，则原样输出。"
)

VALID_MODES = ("always_translate", "on_demand", "probabilistic", "japanese_only")

_LLM_TOOL_NAME = "coeiroink_speak"

# 插件标识（与 metadata.yaml 的 name 一致；Web UI 路由以它作为前缀）
_PLUGIN_NAME = "astrbot_plugin_coeiroink_tts"

# 防止并发重复拉起引擎（模块级锁，进程内唯一）
_ENGINE_START_LOCK = asyncio.Lock()
_ENGINE_PROCESS: "subprocess.Popen[bytes] | None" = None

# 合成并发控制（P0-2）：按并发上限复用信号量，避免并发请求叠加引擎内存峰值
_SYNTH_SEMAPHORES: dict[int, asyncio.Semaphore] = {}

# 临时音频清理（P0-3）：定期删除过期产物，防止磁盘无限增长
_TEMP_CLEAN_FIRST_DELAY = 60.0  # 启动后首次清理的延迟（秒）
_TEMP_CLEAN_INTERVAL = 3600.0  # 清理周期（秒）
_TEMP_RETENTION = 2 * 3600.0  # 保留时长（秒），发送后平台可能仍需短暂持有文件

# 长文本分段合成（P1-2）：默认最多合成段数（可被配置 max_synth_segments 覆盖）
_MAX_SYNTH_SEGMENTS = 6

# 合成排队上限：在途（含等待）合成数超过 并发上限 + max_synth_queue 时直接跳过，
# 避免忙时合成请求无限排队、回复延迟持续增长
_SYNTH_INFLIGHT = 0

# 引擎日志尾读上限（Web UI「引擎日志」用）
_LOG_TAIL_BYTES = 64 * 1024

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "mode": "on_demand",
    "probability": 0.3,
    "speedScale": 1.0,
    "enable_mp3": True,
    "keep_temp_files": False,
    "auto_start_engine": True,
    "enable_llm_tool": False,
    "enable_webui": False,
    "engine_start_timeout": 90,
    "min_available_memory_mb": 500,
    "max_text_length": 200,
    "skip_if_too_long": True,
    "max_concurrent_synth": 1,
    "max_synth_queue": 2,
    "max_synth_segments": _MAX_SYNTH_SEGMENTS,
    "allow_style_override": True,
    "style_switch_min_free_mb": 800,
    "allow_remote_engine": False,
    "api_base": DEFAULT_API_BASE,
    "speaker_uuid": DEFAULT_SPEAKER_UUID,
    "style_id": DEFAULT_STYLE_ID,
    "output_sampling_rate": 0,
    "engine_dir": DEFAULT_ENGINE_DIR,
    "engine_bin": DEFAULT_ENGINE_BIN,
    "engine_log": DEFAULT_ENGINE_LOG,
    "ffmpeg_path": DEFAULT_FFMPEG,
    "translate_system_prompt": DEFAULT_TRANSLATE_PROMPT,
}


def get_synth_semaphore(limit: Any) -> asyncio.Semaphore:
    """按并发上限复用进程内信号量（P0-2）。"""
    n = max(1, int(limit or 1))
    sem = _SYNTH_SEMAPHORES.get(n)
    if sem is None:
        sem = _SYNTH_SEMAPHORES[n] = asyncio.Semaphore(n)
    return sem


# ---------------------------------------------------------------------------
# 环境相关路径/工具解析（配置项 → 环境变量 → 自动推导）
# ---------------------------------------------------------------------------


def _first_nonempty(*values: Any) -> str:
    """返回第一个非空字符串；全部为空时返回空串。"""
    for v in values:
        s = str(v or "").strip()
        if s:
            return s
    return ""


def resolve_engine_dir(cfg_value: Any = "") -> str:
    """解析引擎根目录：配置 > 环境变量 COEIROINK_ENGINE_DIR。"""
    return _first_nonempty(cfg_value, os.environ.get(ENV_ENGINE_DIR))


def resolve_engine_bin(engine_dir: str = "", cfg_value: Any = "") -> str | None:
    """解析引擎可执行文件路径。

    优先级：配置 engine_bin > 环境变量 COEIROINK_ENGINE_BIN >
    自动推导 <engine_dir>/engine/engine（Windows 为 engine.exe）。
    相对路径按相对引擎目录解释（与拉起时的 cwd 一致）。
    """
    v = _first_nonempty(cfg_value, os.environ.get(ENV_ENGINE_BIN))
    if v:
        return v if os.path.isabs(v) else os.path.join(engine_dir, v)
    if not engine_dir:
        return None
    sub = "engine.exe" if os.name == "nt" else "engine"
    return os.path.join(engine_dir, "engine", sub)


def resolve_engine_log(engine_dir: str = "", cfg_value: Any = "") -> str | None:
    """解析引擎日志路径。

    优先级：配置 engine_log > 环境变量 COEIROINK_ENGINE_LOG >
    自动取 <engine_dir>/engine.log；连引擎目录都没有时兜底 AstrBot 临时目录。
    返回 None 表示无处可写，拉起时改用 os.devnull。
    """
    v = _first_nonempty(cfg_value, os.environ.get(ENV_ENGINE_LOG))
    if v:
        return v
    if engine_dir:
        return os.path.join(engine_dir, "engine.log")
    try:
        return str(Path(get_astrbot_temp_path()) / "coeiroink_engine.log")
    except Exception:  # noqa: BLE001
        return None


def resolve_ffmpeg(cfg_value: Any = "") -> str:
    """解析 ffmpeg 可执行文件：配置 > 环境变量 COEIROINK_FFMPEG > PATH 查找。"""
    v = _first_nonempty(cfg_value, os.environ.get(ENV_FFMPEG))
    if v:
        return v
    return shutil.which("ffmpeg") or ""


# ---------------------------------------------------------------------------
# 风格（styleId）映射与归一化
# ---------------------------------------------------------------------------

# 引擎支持的三套风格：styleId -> (中文名, 日文名)
STYLE_TABLE: dict[int, tuple[str, str]] = {
    0: ("平静", "れいせい"),
    5: ("温柔", "おしとやか"),
    6: ("充满活力", "げんき"),
}

# 可识别的风格别名（统一小写、去空白后匹配）：styleId 数字 / 中文名 / 日文名 / 罗马音 / 英文
_STYLE_ALIASES: dict[str, int] = {
    "0": 0,
    "5": 5,
    "6": 6,
    # 平静（れいせい / reisei）
    "平静": 0,
    "冷静": 0,
    "安静": 0,
    "れいせい": 0,
    "reisei": 0,
    "calm": 0,
    # 温柔（おしとやか / oshitoyaka）
    "温柔": 5,
    "溫柔": 5,
    "温和": 5,
    "柔和": 5,
    "おしとやか": 5,
    "oshitoyaka": 5,
    "gentle": 5,
    # 充满活力（げんき / genki）
    "充满活力": 6,
    "活力": 6,
    "元气": 6,
    "元気": 6,
    "活泼": 6,
    "げんき": 6,
    "genki": 6,
    "lively": 6,
    "energetic": 6,
}


def _style_key(value: Any) -> str:
    """把风格输入压成用于查表的键：去所有空白后转小写。"""
    return re.sub(r"\s+", "", str(value)).lower()


def normalize_style(value: Any) -> int | None:
    """把风格输入归一化为 styleId；无法识别时返回 None。

    支持：styleId 整数（0/5/6，含 "+5"/"5 " 之类的数字字符串）、
    中文名（平静/温柔/充满活力）、日文名（れいせい/おしとやか/げんき），
    以及罗马音/英文别名（reisei/oshitoyaka/genki、calm/gentle/lively…）。
    布尔值一律视为非法，避免 True/False 被当成 1/0。
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value in STYLE_TABLE else None
    key = _style_key(value)
    if not key:
        return None
    if key in _STYLE_ALIASES:
        return _STYLE_ALIASES[key]
    if key.lstrip("+-").isdigit():
        n = int(key)
        return n if n in STYLE_TABLE else None
    return None


def style_label(style_id: int) -> str:
    """返回形如 `5/温柔（おしとやか）` 的可读标签。"""
    zh, ja = STYLE_TABLE.get(style_id, ("未知", "不明"))
    return f"{style_id}/{zh}（{ja}）"


def describe_styles() -> str:
    """返回全部可用风格的说明串，用于提示与日志。"""
    return "、".join(f"{sid}={zh}（{ja}）" for sid, (zh, ja) in STYLE_TABLE.items())


_STYLE_PREFIX_RE = re.compile(
    r"^\s*(?:[\[【]\s*(?P<bracket>[^\]】]+?)\s*[\]】]\s*|#\s*(?P<hash>\S+)\s+)"
)


def extract_style_prefix(text: str) -> tuple[str | None, str]:
    """从命令文本中解析可选的风格前缀。

    支持 `[温柔]…`、`【温柔】…`、`#温柔 …` 三种写法；
    未出现风格前缀时返回 (None, 原文本)。
    """
    m = _STYLE_PREFIX_RE.match(text or "")
    if not m:
        return None, text
    style = m.group("bracket") or m.group("hash")
    return style, text[m.end() :].lstrip()


# ---------------------------------------------------------------------------
# 文本工具
# ---------------------------------------------------------------------------

_KANA_RE = re.compile(r"[\u3040-\u309f\u30a0-\u30ff\u30fc]")
_URL_RE = re.compile(r"https?://\S+")
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
# 去掉常见 Markdown 标记与控制字符，但保留中日常用标点
_MD_RE = re.compile(r"[*_`~#>\[\]()]|!\[[^\]]*\]")
_EMOJI_RE = re.compile("[\U0001f000-\U0001faff\u200d\ufe0f\u2600-\u26ff]")


def clean_text(text: str) -> str:
    """清理待合成文本：去代码块、URL、Markdown 标记、emoji，压缩空白。"""
    if not text:
        return ""
    t = _FENCE_RE.sub(" ", text)
    t = _URL_RE.sub(" ", t)
    t = _MD_RE.sub(" ", t)
    t = _EMOJI_RE.sub(" ", t)
    t = t.replace("\r", " ").replace("\n", " ")
    t = re.sub(r"\s+", " ", t).strip()
    return t


def is_japanese(text: str) -> bool:
    """粗略判断文本是否为日语：含假名，且假名占非空白字符比例达标。"""
    if not text:
        return False
    meaningful = [c for c in text if not c.isspace()]
    if not meaningful:
        return False
    kana = sum(1 for c in meaningful if _KANA_RE.match(c))
    if kana == 0:
        return False
    return (kana / len(meaningful)) >= 0.1


_SENT_ENDERS_RE = re.compile(r"(?<=[。！？!?…])")


def split_text_segments(text: str, max_len: int) -> list[str]:
    """把长文本按句末标点（。！？!?…）切分成不超过 max_len 的段落（P1-2）。

    - 先按句末标点切句（标点保留在句尾）；
    - 贪心合并短句，使每段尽量接近但不超 max_len；
    - 单句仍超长时按 max_len 硬切为多段，不丢弃内容。
    """
    if not text:
        return []
    max_len = max(1, int(max_len))
    if len(text) <= max_len:
        return [text]
    sentences = [s for s in _SENT_ENDERS_RE.split(text) if s]
    if not sentences:
        return [text[i : i + max_len] for i in range(0, len(text), max_len)]

    segments: list[str] = []
    buf = ""
    for s in sentences:
        if len(s) > max_len:
            if buf:
                segments.append(buf)
                buf = ""
            segments.extend(s[i : i + max_len] for i in range(0, len(s), max_len))
            continue
        if buf and len(buf) + len(s) > max_len:
            segments.append(buf)
            buf = s
        else:
            buf += s
    if buf:
        segments.append(buf)
    return segments


# 回环主机名（P2-5）：默认只允许向本机引擎发送待朗读文本，防止内容外发
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def api_base_is_local(api_base: str) -> bool:
    """判断引擎地址是否为回环地址。解析失败视为非本地（保守拒绝）。"""
    try:
        host = (urlparse(str(api_base or "")).hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    return host in _LOOPBACK_HOSTS or host.startswith("127.")


# ---------------------------------------------------------------------------
# 引擎探测 / 拉起
# ---------------------------------------------------------------------------

# 共享 httpx 客户端：引擎是本机 HTTP 服务，进程内复用一条连接池可省去
# 每次探测/合成的建连开销；连接数刻意调小，减少常驻 socket 缓冲内存。
_HTTP_CLIENT: "httpx.AsyncClient | None" = None
_HTTP_CLIENT_LOCK = asyncio.Lock()

# 探活缓存：合成或探活成功后，短时间内直接信任引擎可用，跳过又一次
# /v1/speakers 请求（其响应约 1.2MB，含 3 张 base64 图标，省流量也省内存）。
_ALIVE_CACHE_TTL = 15.0
_ALIVE_CACHE: dict[str, tuple[float, bool]] = {}


async def _get_http_client() -> httpx.AsyncClient:
    """惰性获取进程内共享的 httpx 客户端（连接池复用）。"""
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None or _HTTP_CLIENT.is_closed:
        async with _HTTP_CLIENT_LOCK:
            if _HTTP_CLIENT is None or _HTTP_CLIENT.is_closed:
                _HTTP_CLIENT = httpx.AsyncClient(
                    limits=httpx.Limits(
                        max_connections=2,
                        max_keepalive_connections=2,
                    ),
                )
    return _HTTP_CLIENT


async def close_http_client() -> None:
    """关闭共享客户端、释放连接资源（插件停用/重载时调用）。"""
    global _HTTP_CLIENT
    client, _HTTP_CLIENT = _HTTP_CLIENT, None
    if client is not None and not client.is_closed:
        await client.aclose()


def _alive_cache_key(api_base: str) -> str:
    return api_base.rstrip("/")


def _alive_cached(api_base: str) -> bool | None:
    """命中缓存且未过期时返回缓存值，否则返回 None（过期即删除）。"""
    entry = _ALIVE_CACHE.get(_alive_cache_key(api_base))
    if entry is None:
        return None
    ts, alive = entry
    if time.monotonic() - ts > _ALIVE_CACHE_TTL:
        _ALIVE_CACHE.pop(_alive_cache_key(api_base), None)
        return None
    return alive


def _alive_cache_set(api_base: str, alive: bool) -> None:
    """只缓存「存活」结果；失败不缓存，避免把瞬时故障固化。"""
    if alive:
        _ALIVE_CACHE[_alive_cache_key(api_base)] = (time.monotonic(), True)


def invalidate_engine_alive_cache(api_base: str | None = None) -> None:
    """使探活缓存失效（合成失败时调用，强制下次走真实探活/重启逻辑）。"""
    if api_base is None:
        _ALIVE_CACHE.clear()
    else:
        _ALIVE_CACHE.pop(_alive_cache_key(api_base), None)


async def check_engine_alive(
    api_base: str = DEFAULT_API_BASE,
    timeout: float = 3.0,
) -> bool:
    """探测引擎是否真的可以合成语音。任何异常都视为不可用（不抛出）。

    注意：COEIROINK 启动时 HTTP 服务会先就绪（访问 `/` 就已返回 200），
    但说话人模型仍在加载，此时打 /v1/predict 会被服务端直接断开连接，
    表现为 httpx 的 "Server disconnected without sending a response"。
    因此必须以 /v1/speakers 能返回非空说话人列表作为“可合成”的标准。

    性能/内存优化：/v1/speakers 的响应约 1.2MB（含 3 张 base64 图标），
    这里只读取原始字节判断是否为非空 JSON 数组，不再把它完整解析成
    Python 对象；另加 15s 存活缓存，高频调用时几乎不再产生网络请求。
    """
    base = api_base.rstrip("/")
    cached = _alive_cached(base)
    if cached is True:
        return True
    try:
        client = await _get_http_client()
        resp = await client.get(f"{base}/v1/speakers", timeout=timeout)
        if resp.status_code != 200:
            return False
        stripped = resp.content.strip()
        # 非空数组才算就绪：[]、对象、null 等一律视为未就绪
        if not stripped.startswith(b"[") or stripped == b"[]":
            return False
        _alive_cache_set(base, True)
        return True
    except Exception:
        return False


def _launch_engine(engine_dir: str, engine_bin: str, engine_log: str | None) -> None:
    """以独立会话后台拉起引擎进程（跨平台）。

    COEIROINK 的 torch 推理默认会把线程数和内存池开得很大，容易在合成
    瞬间触发系统 OOM。这里限制 BLAS/OpenMP 线程数与 malloc arena 数量，
    并钉住 glibc trim 阈值，明显压低常驻内存和峰值。
    这些环境变量是 Linux 优化项，在 Windows/macOS 上是无害的空操作。
    """
    global _ENGINE_PROCESS
    if not engine_log:
        engine_log = os.devnull
    if engine_log != os.devnull:
        log_dir = os.path.dirname(engine_log)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

    env = os.environ.copy()
    env.setdefault("OMP_NUM_THREADS", "1")
    env.setdefault("MKL_NUM_THREADS", "1")
    env.setdefault("OPENBLAS_NUM_THREADS", "1")
    env.setdefault("NUMEXPR_NUM_THREADS", "1")
    env.setdefault("VECLIB_MAXIMUM_THREADS", "1")
    env.setdefault("MALLOC_ARENA_MAX", "2")
    # glibc 的 trim 阈值默认会随进程运行自动调大，空闲内存迟迟不还给 OS；
    # 显式钉在 64KiB，让引擎在合成间隙更积极收缩堆，压低常驻内存。
    env.setdefault("MALLOC_TRIM_THRESHOLD_", "65536")
    env.setdefault("PYTHONUNBUFFERED", "1")

    log_fh = open(engine_log, "ab")  # noqa: SIM115 - 进程生命周期内保持打开
    popen_kwargs: dict[str, Any] = {
        "cwd": engine_dir or None,
        "stdout": log_fh,
        "stderr": log_fh,
        "env": env,
    }
    if os.name == "nt":
        # Windows：脱离当前控制台/进程组，避免随 AstrBot 退出被带走
        popen_kwargs["creationflags"] = getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        ) | getattr(subprocess, "DETACHED_PROCESS", 0)
    else:
        popen_kwargs["start_new_session"] = True

    _ENGINE_PROCESS = subprocess.Popen([engine_bin], **popen_kwargs)  # noqa: S603
    logger.info(f"[COEIROINK] 已拉起引擎进程 pid={_ENGINE_PROCESS.pid}")


async def ensure_engine_running(
    api_base: str = DEFAULT_API_BASE,
    engine_dir: str = "",
    engine_bin: str | None = None,
    engine_log: str = "",
    auto_start: bool = True,
    start_timeout: float = 90.0,
    probe_interval: float = 2.0,
) -> bool:
    """确保引擎可用；不可用且允许自动拉起时，带锁与超时地启动并轮询等待。

    engine_dir / engine_bin / engine_log 为空时按「配置 → 环境变量 →
    自动推导」解析（见 resolve_* 函数）；解析不出可执行文件则无法自动拉起，
    仅记录带指引的日志。失败时返回 False，绝不抛出，避免阻塞正常回复。
    """
    if await check_engine_alive(api_base):
        return True
    if not auto_start:
        return False

    engine_dir = resolve_engine_dir(engine_dir)
    engine_bin = resolve_engine_bin(engine_dir, engine_bin or None)
    engine_log = resolve_engine_log(engine_dir, engine_log) or ""

    if not engine_bin or not os.path.isfile(engine_bin):
        logger.error(
            "[COEIROINK] 找不到引擎可执行文件，无法自动拉起。请配置 engine_dir "
            "（引擎根目录）或 engine_bin（engine 可执行文件绝对路径），"
            "或设置环境变量 COEIROINK_ENGINE_DIR / COEIROINK_ENGINE_BIN。"
        )
        return False

    async with _ENGINE_START_LOCK:
        # 双重检查，避免并发重复启动
        if await check_engine_alive(api_base):
            return True
        try:
            await asyncio.to_thread(_launch_engine, engine_dir, engine_bin, engine_log)
        except Exception as e:  # noqa: BLE001
            logger.error(f"[COEIROINK] 拉起引擎失败：{e}")
            return False

        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(1.0, float(start_timeout))
        while loop.time() < deadline:
            if await check_engine_alive(api_base):
                logger.info("[COEIROINK] 引擎已就绪")
                return True
            await asyncio.sleep(probe_interval)
        logger.error(f"[COEIROINK] 引擎在 {start_timeout}s 内未就绪")
        return False


# ---------------------------------------------------------------------------
# 合成链路
# ---------------------------------------------------------------------------


async def stream_wav_to_path(
    wav_path: Path,
    text: str,
    api_base: str = DEFAULT_API_BASE,
    speaker_uuid: str = DEFAULT_SPEAKER_UUID,
    style_id: int = DEFAULT_STYLE_ID,
    speed_scale: float = 1.0,
    output_sampling_rate: int | None = None,
    timeout: float = 60.0,
    retries: int = 1,
    retry_wait: float = 2.0,
) -> None:
    """调用 /v1/predict，把返回的 wav 流式写入 wav_path。

    引擎刚起来或模型热加载时，偶发会直接断开连接；这里做一次温和重试，
    避免把这种瞬时故障暴露给用户。

    内存优化：响应按块流式落盘，不再把整段 wav 缓冲进内存（长文本的
    wav 可达数 MB）；合成成功即证明引擎可用，顺带刷新探活缓存。
    """
    payload: dict[str, Any] = {
        "speakerUuid": speaker_uuid,
        "styleId": int(style_id),
        "text": text,
        "speedScale": float(speed_scale),
    }
    if output_sampling_rate:
        payload["outputSamplingRate"] = int(output_sampling_rate)
    url = f"{api_base.rstrip('/')}/v1/predict"
    base = api_base.rstrip("/")

    last_exc: Exception | None = None
    for attempt in range(max(0, retries) + 1):
        try:
            client = await _get_http_client()
            written = 0
            async with client.stream("POST", url, json=payload, timeout=timeout) as resp:
                resp.raise_for_status()
                with open(wav_path, "wb") as fh:
                    async for chunk in resp.aiter_bytes():
                        fh.write(chunk)
                        written += len(chunk)
            if written == 0:
                raise RuntimeError("引擎返回了空音频")
            _alive_cache_set(base, True)
            return
        except httpx.TransportError as e:
            last_exc = e
            if attempt >= retries:
                invalidate_engine_alive_cache(base)
                _safe_unlink(wav_path)
                raise
            logger.warning(f"[COEIROINK] 合成请求被引擎断开（{e}），{retry_wait}s 后重试一次")
            await asyncio.sleep(retry_wait)
        except httpx.HTTPStatusError:
            _safe_unlink(wav_path)
            raise
    assert last_exc is not None
    raise last_exc


def _safe_unlink(path: str | Path) -> None:
    """删除临时文件；文件不存在等 OSError 静默忽略。"""
    try:
        os.remove(path)
    except OSError:
        pass


def wav_to_mp3(
    wav_path: str | Path,
    mp3_path: str | Path,
    ffmpeg_path: str = "",
) -> str:
    """用 ffmpeg 把 wav 转成 mp3，返回 mp3 路径。

    ffmpeg_path 为空时按「配置 → COEIROINK_FFMPEG → PATH」解析；
    仍然找不到时抛出带指引的 RuntimeError。
    """
    ffmpeg = resolve_ffmpeg(ffmpeg_path)
    if not ffmpeg:
        raise RuntimeError(
            "未找到 ffmpeg，无法转码 mp3。请安装 ffmpeg 并加入 PATH，"
            "或在插件配置中设置 ffmpeg_path（或环境变量 COEIROINK_FFMPEG）。"
        )
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(wav_path),
        "-codec:a",
        "libmp3lame",
        "-qscale:a",
        "2",
        str(mp3_path),
    ]
    proc = subprocess.run(  # noqa: S603
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "ignore")[-500:]
        raise RuntimeError(f"ffmpeg 转码失败：{err}")
    return str(mp3_path)


async def synthesize_to_file(
    text: str,
    output_dir: str | Path | None = None,
    api_base: str = DEFAULT_API_BASE,
    speaker_uuid: str = DEFAULT_SPEAKER_UUID,
    style_id: int = DEFAULT_STYLE_ID,
    speed_scale: float = 1.0,
    output_sampling_rate: int | None = None,
    enable_mp3: bool = True,
    keep_temp_files: bool = False,
    ffmpeg_path: str = "",
    timeout: float = 60.0,
) -> str:
    """核心合成函数：文本 -> wav -> (mp3)，返回最终音频文件的绝对路径。"""
    if not text or not text.strip():
        raise ValueError("待合成文本为空")
    out_dir = Path(output_dir) if output_dir else Path(get_astrbot_temp_path())
    out_dir.mkdir(parents=True, exist_ok=True)

    uid = uuid.uuid4().hex[:12]
    wav_path = out_dir / f"coeiroink_{uid}.wav"
    await stream_wav_to_path(
        wav_path,
        text,
        api_base=api_base,
        speaker_uuid=speaker_uuid,
        style_id=style_id,
        speed_scale=speed_scale,
        output_sampling_rate=output_sampling_rate,
        timeout=timeout,
    )

    if not enable_mp3:
        return str(wav_path)

    mp3_path = out_dir / f"coeiroink_{uid}.mp3"
    # ffmpeg 是独立进程，放入线程池执行，避免阻塞事件循环
    await asyncio.to_thread(wav_to_mp3, wav_path, mp3_path, ffmpeg_path)
    if not keep_temp_files:
        _safe_unlink(wav_path)
    return str(mp3_path)


def _available_memory_windows_mb() -> float | None:
    """Windows：GlobalMemoryStatusEx 取可用物理内存（MB）。失败返回 None。"""
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):  # noqa: N801
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        st = MEMORYSTATUSEX()
        st.dwLength = ctypes.sizeof(st)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
            return st.ullAvailPhys / 1048576.0
    except Exception:  # noqa: BLE001
        pass
    return None


def _available_memory_macos_mb() -> float | None:
    """macOS：解析 vm_stat 的 free+inactive 页 × 页大小（MB）。失败返回 None。"""
    try:
        vm = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5).stdout
        free_pages = inactive_pages = 0
        for line in vm.splitlines():
            key, _, val = line.partition(":")
            key = key.strip().lower()
            if key == "pages free":
                free_pages = int(val.strip().rstrip("."))
            elif key == "pages inactive":
                inactive_pages = int(val.strip().rstrip("."))
        page_size = int(
            subprocess.run(
                ["sysctl", "-n", "hw.pagesize"], capture_output=True, text=True, timeout=5
            ).stdout.strip()
        )
        if page_size > 0 and (free_pages or inactive_pages):
            return (free_pages + inactive_pages) * page_size / 1048576.0
    except Exception:  # noqa: BLE001
        pass
    return None


def available_memory_mb() -> float | None:
    """读取当前系统可用内存（单位 MB）。

    Linux 读 /proc/meminfo 的 MemAvailable；Windows 用
    GlobalMemoryStatusEx；macOS 用 vm_stat（free+inactive 页）；
    其他平台或读取失败返回 None（跳过内存门槛检查，不阻断合成）。
    """
    try:
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return float(line.split()[1]) / 1024.0
    except Exception:  # noqa: BLE001
        pass
    if os.name == "nt":
        return _available_memory_windows_mb()
    if sys.platform == "darwin":
        return _available_memory_macos_mb()
    return None


async def synthesize_with_recovery(
    text: str,
    *,
    ensure_kwargs: dict[str, Any] | None = None,
    min_available_mb: float = 0.0,
    **synth_kwargs: Any,
) -> str:
    """合成语音；若引擎中途被系统 OOM 杀掉，会自动重新拉起并重试一次。"""
    avail = available_memory_mb()
    if min_available_mb and avail is not None and avail < min_available_mb:
        raise RuntimeError(
            f"可用内存不足（{avail:.0f}MB < {min_available_mb:.0f}MB），本次跳过合成"
        )
    try:
        return await synthesize_to_file(text, **synth_kwargs)
    except Exception as e:  # noqa: BLE001
        if ensure_kwargs is None:
            raise
        # 合成失败说明探活缓存可能失真（如引擎被 OOM 杀掉），先使其失效，
        # 再走真实探活/重启逻辑，避免缓存把坏掉的引擎误判为可用。
        _api_base = synth_kwargs.get("api_base")
        invalidate_engine_alive_cache(str(_api_base) if _api_base else None)
        logger.warning(f"[COEIROINK] 合成失败（{e}），尝试重启引擎后重试一次")
        if not await ensure_engine_running(**ensure_kwargs):
            raise
        return await synthesize_to_file(text, **synth_kwargs)


async def translate_to_japanese(
    context: Context,
    text: str,
    system_prompt: str = DEFAULT_TRANSLATE_PROMPT,
    umo: str | None = None,
) -> str | None:
    """用当前会话使用的对话模型把文本翻译成日语；失败返回 None。"""
    provider = None
    try:
        provider = await context.get_using_provider_async(umo)
    except Exception as e:  # noqa: BLE001
        logger.error(f"[COEIROINK] 获取对话模型失败：{e}")
        return None
    if provider is None:
        logger.warning("[COEIROINK] 未找到可用的对话模型，无法翻译")
        return None
    try:
        resp = await provider.text_chat(prompt=text, system_prompt=system_prompt)
    except Exception as e:  # noqa: BLE001
        logger.error(f"[COEIROINK] 翻译调用失败：{e}")
        return None
    result = (getattr(resp, "completion_text", "") or "").strip()
    return result or None


# ---------------------------------------------------------------------------
# TTS Provider（供 AstrBot 语音链路调用）
# ---------------------------------------------------------------------------


class CoeiroinkTTSProvider(TTSProvider):
    """COEIROINK TTS Provider 适配器。"""

    def __init__(self, provider_config: dict, provider_settings: dict) -> None:
        super().__init__(provider_config, provider_settings)
        self.api_base = provider_config.get("api_base", DEFAULT_API_BASE)
        self.speaker_uuid = provider_config.get("speaker_uuid", DEFAULT_SPEAKER_UUID)
        # 风格：支持 0/5/6，也接受中文名/日文名（详见 normalize_style）。
        # 非法值不再静默回退，而是给出明确日志并使用默认风格。
        _raw_style = provider_config.get("style_id", DEFAULT_STYLE_ID)
        _resolved_style = normalize_style(_raw_style)
        if _resolved_style is None:
            logger.warning(
                f"[COEIROINK] 无法识别的 style_id={_raw_style!r}，"
                f"已回退到默认 {style_label(DEFAULT_STYLE_ID)}；"
                f"可用风格：{describe_styles()}"
            )
            _resolved_style = DEFAULT_STYLE_ID
        self.style_id = _resolved_style
        self.speed_scale = float(provider_config.get("speed_scale", 1.0) or 1.0)
        self.output_sampling_rate = int(provider_config.get("output_sampling_rate", 0) or 0)
        self.enable_mp3 = bool(provider_config.get("enable_mp3", True))
        self.auto_start_engine = bool(provider_config.get("auto_start_engine", True))
        self.engine_dir = provider_config.get("engine_dir", DEFAULT_ENGINE_DIR)
        self.engine_bin = provider_config.get("engine_bin", DEFAULT_ENGINE_BIN)
        self.engine_log = provider_config.get("engine_log", DEFAULT_ENGINE_LOG)
        self.timeout = float(provider_config.get("timeout", 60) or 60)

    def support_stream(self) -> bool:
        return False

    async def get_audio(self, text: str) -> str:
        await ensure_engine_running(
            api_base=self.api_base,
            engine_dir=self.engine_dir,
            engine_bin=self.engine_bin or None,
            engine_log=self.engine_log,
            auto_start=self.auto_start_engine,
        )
        return await synthesize_with_recovery(
            text,
            ensure_kwargs={
                "api_base": self.api_base,
                "engine_dir": self.engine_dir,
                "engine_bin": self.engine_bin or None,
                "engine_log": self.engine_log,
                "auto_start": self.auto_start_engine,
            },
            api_base=self.api_base,
            speaker_uuid=self.speaker_uuid,
            style_id=self.style_id,
            speed_scale=self.speed_scale,
            output_sampling_rate=self.output_sampling_rate or None,
            enable_mp3=self.enable_mp3,
            timeout=self.timeout,
        )


_PROVIDER_TYPE_NAME = "coeiroink_tts"

_TTS_PROVIDER_DEFAULT_CONFIG: dict[str, Any] = {
    "api_base": DEFAULT_API_BASE,
    "speaker_uuid": DEFAULT_SPEAKER_UUID,
    "style_id": DEFAULT_STYLE_ID,
    "speed_scale": 1.0,
    "output_sampling_rate": 0,
    "enable_mp3": True,
    "auto_start_engine": True,
    "engine_dir": DEFAULT_ENGINE_DIR,
    "engine_bin": DEFAULT_ENGINE_BIN,
    "engine_log": DEFAULT_ENGINE_LOG,
    "timeout": 60,
}


def _register_tts_provider_adapter() -> None:
    """幂等注册 TTS Provider 适配器。

    AstrBot 在热重载插件时只会清理 sys.modules、handlers 和平台适配器，
    并不会清理 provider 注册表。若直接使用 @register_provider_adapter 装饰器，
    模块二次导入时就会抛出「检测到大模型提供商适配器 XXX 已经注册」。

    因此这里改成显式调用，并在已注册时只刷新类引用，保证重载安全。
    """
    try:
        from astrbot.core.provider.register import (  # noqa: PLC0415
            provider_cls_map,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[COEIROINK] 无法导入 provider 注册表，跳过适配器注册：{e}")
        return

    existing = provider_cls_map.get(_PROVIDER_TYPE_NAME)
    if existing is not None:
        # 已注册（通常是插件热重载），刷新为新模块里的类，避免指向旧对象
        try:
            existing.cls_type = CoeiroinkTTSProvider
            if not getattr(existing, "default_config_tmpl", None):
                existing.default_config_tmpl = dict(_TTS_PROVIDER_DEFAULT_CONFIG)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[COEIROINK] 刷新已注册适配器失败（可忽略）：{e}")
        logger.debug("[COEIROINK] TTS Provider 适配器已存在，跳过重复注册")
        return

    register_provider_adapter(
        provider_type_name=_PROVIDER_TYPE_NAME,
        desc="Tsukuyomi-chan COEIROINK 日语语音（本地 CPU 引擎）",
        provider_type=ProviderType.TEXT_TO_SPEECH,
        default_config_tmpl=dict(_TTS_PROVIDER_DEFAULT_CONFIG),
    )(CoeiroinkTTSProvider)


_register_tts_provider_adapter()


# ---------------------------------------------------------------------------
# 插件主体
# ---------------------------------------------------------------------------


class CoeiroinkTTSPlugin(Star):
    """Tsukuyomi-chan COEIROINK 日语语音插件。"""

    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context, config)
        # 保留 AstrBotConfig 引用（含 save_config()），供 Web UI 写回配置
        self._astrbot_config = config if hasattr(config, "save_config") else None
        self.config: dict[str, Any] = dict(DEFAULT_CONFIG)
        if config:
            for k, v in config.items():
                if v is not None:
                    self.config[k] = v
        self._llm_tools_registered = False
        self._warmup_task: asyncio.Task | None = None
        self._cleanup_task: asyncio.Task | None = None
        self._bg_tasks: set[asyncio.Task] = set()
        # 已由引擎加载（常驻内存）的风格集合；用于单次风格切换的内存保护
        self._loaded_styles: set[int] = set()

    # ---------------- 配置 ----------------

    def _cfg(self, key: str) -> Any:
        return self.config.get(key, DEFAULT_CONFIG.get(key))

    # ---------------- 生命周期 ----------------

    async def initialize(self) -> None:
        if self._cfg("auto_start_engine"):
            self._warmup_task = asyncio.create_task(self._warm_up_engine())
        self._cleanup_task = asyncio.create_task(self._temp_cleanup_loop())
        self._register_llm_tools_if_needed()
        self._register_web_apis()

    async def terminate(self) -> None:
        if self._warmup_task and not self._warmup_task.done():
            self._warmup_task.cancel()
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
        # 释放共享 HTTP 客户端的连接池资源（重载后按需重建）
        await close_http_client()

    async def _warm_up_engine(self) -> None:
        try:
            ok = await self._ensure_engine()
            if not ok:
                logger.info(
                    "[COEIROINK] 预热引擎未成功：若引擎未运行且需要自动拉起，"
                    "请确认已配置 engine_dir（或设置环境变量 COEIROINK_ENGINE_DIR）。"
                )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[COEIROINK] 预热引擎失败（不影响正常回复）：{e}")

    async def _cleanup_temp_audio(self) -> None:
        """清理 AstrBot 临时目录中超过保留时长的合成产物（P0-3）。"""
        try:
            temp_dir = Path(get_astrbot_temp_path())
        except Exception:  # noqa: BLE001
            return
        cutoff = time.time() - _TEMP_RETENTION
        removed = 0
        for pattern in ("coeiroink_*.mp3", "coeiroink_*.wav"):
            for f in temp_dir.glob(pattern):
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink()
                        removed += 1
                except OSError:
                    pass
        if removed:
            logger.info(f"[COEIROINK] 已清理 {removed} 个过期临时音频文件")

    async def _temp_cleanup_loop(self) -> None:
        """后台定时清理任务：先短暂延迟清一次，之后每小时清一次。"""
        await asyncio.sleep(_TEMP_CLEAN_FIRST_DELAY)
        while True:
            await self._cleanup_temp_audio()
            await asyncio.sleep(_TEMP_CLEAN_INTERVAL)

    def _register_llm_tools_if_needed(self) -> None:
        """幂等注册 LLM 工具，避免重复注册。"""
        if self._llm_tools_registered:
            return
        if not self._cfg("enable_llm_tool"):
            self._llm_tools_registered = True
            return
        try:
            tool = FunctionTool(
                name=_LLM_TOOL_NAME,
                description=(
                    "将指定文本合成为日语语音并发送给用户。"
                    "当用户明确要求用语音说、朗读或念出某段内容时调用。"
                    f"可选地用 style 参数临时指定情绪风格，可用：{describe_styles()}。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "要朗读的文本内容（可中/日文，中文会被翻译成日语）",
                        },
                        "style": {
                            "type": "string",
                            "description": (
                                "可选。本次朗读使用的情绪风格，如 平静/温柔/充满活力，"
                                "也接受 0/5/6 或 れいせい/おしとやか/げんき。"
                                "不填则使用插件配置的默认风格。"
                            ),
                        },
                    },
                    "required": ["text"],
                },
                handler=self._llm_tool_handler,
            )
            self.context.add_llm_tools(tool)
        except Exception as e:  # noqa: BLE001
            logger.error(f"[COEIROINK] 注册 LLM 工具失败：{e}")
        self._llm_tools_registered = True

    # ---------------- Web UI（插件 Pages） ----------------

    def _webui_enabled(self) -> bool:
        return bool(self._cfg("enable_webui"))

    def _register_web_apis(self) -> None:
        """注册 Web UI 后端接口。

        AstrBot 的 register_web_api 对「相同路由 + 相同方法」幂等（自动替换），
        因此插件热重载后重复调用是安全的。路由前缀必须是插件标识名。
        老版本 AstrBot 缺少 `astrbot.api.web` 或 `register_web_api` 时自动跳过
        （降级为无 Web UI，核心合成链路不受影响）。
        """
        if request is None or not hasattr(self.context, "register_web_api"):
            logger.info(
                "[COEIROINK] 当前 AstrBot 版本不支持插件 Pages，Web UI 已停用；"
                "配置可通过面板与配置文件管理。"
            )
            return
        api = f"/{_PLUGIN_NAME}"
        try:
            self.context.register_web_api(
                f"{api}/config", self._webui_get_config, ["GET"], "读取插件配置"
            )
            self.context.register_web_api(
                f"{api}/config", self._webui_save_config, ["POST"], "保存插件配置"
            )
            self.context.register_web_api(
                f"{api}/status", self._webui_status, ["GET"], "引擎与运行状态"
            )
            self.context.register_web_api(
                f"{api}/test", self._webui_test_synth, ["POST"], "测试合成一句话"
            )
            self.context.register_web_api(
                f"{api}/install_info", self._webui_install_info, ["GET"], "安装向导：环境自检"
            )
            self.context.register_web_api(
                f"{api}/install_check_path",
                self._webui_install_check_path,
                ["POST"],
                "安装向导：校验引擎目录",
            )
            self.context.register_web_api(
                f"{api}/install_launch_engine",
                self._webui_install_launch_engine,
                ["POST"],
                "安装向导：后台启动引擎",
            )
            self.context.register_web_api(
                f"{api}/logs", self._webui_logs, ["GET"], "引擎日志（尾部）"
            )
            self.context.register_web_api(
                f"{api}/engine_restart",
                self._webui_engine_restart,
                ["POST"],
                "重启引擎（仅限插件拉起的进程）",
            )
        except Exception as e:  # noqa: BLE001
            logger.error(f"[COEIROINK] 注册 Web UI 接口失败：{e}")

    async def _save_config(self) -> bool:
        """把当前配置写回 AstrBot 配置文件（Web UI 保存时调用）。"""
        cfg = self._astrbot_config
        if cfg is None:
            return False
        try:
            # save_config_async 会把给定值合并进配置快照后落盘，不阻塞事件循环
            await cfg.save_config_async(dict(self.config))
            return True
        except Exception as e:  # noqa: BLE001
            logger.error(f"[COEIROINK] 保存配置失败：{e}")
            return False

    async def _webui_get_config(self):
        """GET /config：返回当前配置与风格/模式说明。"""
        return json_response(
            {
                "enable_webui": self._webui_enabled(),
                "config": dict(self.config),
                "styles": [
                    {"id": sid, "label": style_label(sid), "zh": zh, "ja": ja}
                    for sid, (zh, ja) in STYLE_TABLE.items()
                ],
                "modes": [
                    {"value": "always_translate", "label": "总是翻译朗读"},
                    {"value": "on_demand", "label": "按需触发"},
                    {"value": "probabilistic", "label": "概率触发"},
                    {"value": "japanese_only", "label": "仅日语朗读"},
                ],
            }
        )

    async def _webui_save_config(self):
        """POST /config：校验并保存配置项（只接受已知键，按类型收敛）。"""
        payload = await request.json(default=None)
        if not isinstance(payload, dict):
            return error_response("请求体必须是 JSON 对象", status_code=400)

        int_keys = {
            "engine_start_timeout",
            "min_available_memory_mb",
            "max_text_length",
            "output_sampling_rate",
            "max_concurrent_synth",
            "max_synth_queue",
            "max_synth_segments",
            "style_switch_min_free_mb",
        }
        float_keys = {"probability", "speedScale"}
        bool_keys = {
            "enabled",
            "enable_mp3",
            "keep_temp_files",
            "auto_start_engine",
            "enable_llm_tool",
            "skip_if_too_long",
            "enable_webui",
            "allow_remote_engine",
            "allow_style_override",
        }
        # 其余键（mode/style_id/api_base/…）统一按字符串处理

        updated: dict[str, Any] = {}
        for key, raw in payload.items():
            if key not in DEFAULT_CONFIG:
                continue
            try:
                if key in bool_keys:
                    if isinstance(raw, bool):
                        value: Any = raw
                    elif str(raw).strip().lower() in ("true", "false"):
                        value = str(raw).strip().lower() == "true"
                    else:
                        return error_response(f"配置项 {key} 必须是布尔值", status_code=400)
                elif key in int_keys:
                    value = int(float(raw))
                elif key in float_keys:
                    value = float(raw)
                else:
                    value = str(raw).strip()
            except (TypeError, ValueError):
                return error_response(f"配置项 {key} 的值非法", status_code=400)

            if key == "mode" and value not in VALID_MODES:
                return error_response(
                    f"触发模式必须是以下之一：{'、'.join(VALID_MODES)}", status_code=400
                )
            if key == "style_id" and normalize_style(value) is None:
                return error_response(
                    f"无法识别的风格「{value}」。可用风格：{describe_styles()}", status_code=400
                )
            if key == "max_concurrent_synth" and value < 1:
                return error_response("max_concurrent_synth 必须 ≥ 1", status_code=400)
            if key == "max_synth_queue" and value < 0:
                return error_response("max_synth_queue 必须 ≥ 0", status_code=400)
            if key == "max_synth_segments" and value < 1:
                return error_response("max_synth_segments 必须 ≥ 1", status_code=400)
            if key == "style_switch_min_free_mb" and value < 0:
                return error_response("style_switch_min_free_mb 必须 ≥ 0", status_code=400)
            updated[key] = value

        if not updated:
            return json_response({"saved": False, "changed": [], "config": dict(self.config)})

        self.config.update(updated)
        if not await self._save_config():
            return error_response("配置已更新到内存，但写回配置文件失败", status_code=500)
        return json_response(
            {
                "saved": True,
                "changed": list(updated.keys()),
                "config": dict(self.config),
            }
        )

    async def _webui_status(self):
        """GET /status：引擎探活、内存、风格等运行状态。"""
        api_base = str(self._cfg("api_base") or "")
        alive = await check_engine_alive(api_base)
        style_id, _ = self._resolve_style(None)
        engine_pid = None
        if _ENGINE_PROCESS is not None and _ENGINE_PROCESS.poll() is None:
            engine_pid = _ENGINE_PROCESS.pid
        engine_dir = resolve_engine_dir(self._cfg("engine_dir"))
        return json_response(
            {
                "plugin_enabled": bool(self._cfg("enabled")),
                "webui_enabled": self._webui_enabled(),
                "mode": self._cfg("mode"),
                "style_id": style_id,
                "style_label": style_label(style_id),
                "engine_alive": alive,
                "engine_pid": engine_pid,
                "api_base": api_base,
                "api_base_local": api_base_is_local(api_base),
                "allow_remote_engine": bool(self._cfg("allow_remote_engine")),
                "loaded_styles": sorted(self._loaded_styles),
                "synth_inflight": _SYNTH_INFLIGHT,
                "engine_dir": engine_dir or None,
                "engine_bin": resolve_engine_bin(engine_dir, self._cfg("engine_bin") or None),
                "engine_log": resolve_engine_log(engine_dir, self._cfg("engine_log")),
                "ffmpeg": resolve_ffmpeg(self._cfg("ffmpeg_path")) or None,
                "mem_available_mb": available_memory_mb(),
                "min_available_memory_mb": float(self._cfg("min_available_memory_mb") or 0),
            }
        )

    async def _webui_test_synth(self):
        """POST /test：用当前配置合成一句测试语音。"""
        if not self._webui_enabled():
            return error_response("Web UI 管理未启用，无法执行测试合成", status_code=403)
        payload = await request.json(default={})
        if not isinstance(payload, dict):
            payload = {}
        text = str(payload.get("text") or "こんにちは、これはテスト音声です。").strip()
        if not text:
            return error_response("测试文本为空", status_code=400)
        style_id, style_err = self._resolve_style(payload.get("style"))
        if style_err:
            return error_response(style_err, status_code=400)
        jp = text if is_japanese(text) else await self._to_japanese(text, None)
        if not jp:
            return error_response("翻译失败：未找到可用的对话模型", status_code=502)
        path = await self._synthesize(jp, style_id)
        if not path:
            return error_response("语音合成失败：引擎不可用或文本为空", status_code=502)
        return json_response(
            {
                "ok": True,
                "text": jp,
                "style_id": style_id,
                "style_label": style_label(style_id),
                "file": path,
            }
        )

    # ---------------- 引擎日志 / 重启（Web UI） ----------------

    async def _webui_logs(self):
        """GET /logs：返回引擎日志尾部内容（最多 _LOG_TAIL_BYTES），便于在 Web UI 排障。"""
        engine_dir = resolve_engine_dir(self._cfg("engine_dir"))
        log_path = resolve_engine_log(engine_dir, self._cfg("engine_log"))
        info: dict[str, Any] = {
            "path": log_path,
            "exists": bool(log_path and os.path.isfile(log_path)),
            "content": "",
            "truncated": False,
            "tail_bytes": _LOG_TAIL_BYTES,
        }
        if info["exists"]:
            try:
                with open(log_path, "rb") as f:  # type: ignore[arg-type]
                    f.seek(0, os.SEEK_END)
                    size = f.tell()
                    read_size = min(size, _LOG_TAIL_BYTES)
                    f.seek(max(0, size - read_size))
                    data = f.read(read_size)
                info["truncated"] = size > read_size
                info["size_bytes"] = size
                info["content"] = data.decode("utf-8", "ignore")
            except OSError as e:
                info["error"] = str(e)
        return json_response(info)

    async def _stop_engine_process(self, timeout: float = 15.0) -> bool:
        """停止由插件拉起的引擎进程（先 SIGTERM，超时后 SIGKILL）。

        仅处理 `_ENGINE_PROCESS` 记录的进程；外部启动的引擎不会被触碰。
        """
        proc = _ENGINE_PROCESS
        if proc is None or proc.poll() is not None:
            return False
        try:
            proc.terminate()
        except OSError as e:
            logger.warning(f"[COEIROINK] 终止引擎进程失败：{e}")
            return False
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(1.0, timeout)
        while loop.time() < deadline:
            if proc.poll() is not None:
                return True
            await asyncio.sleep(0.5)
        try:
            proc.kill()
        except OSError as e:
            logger.warning(f"[COEIROINK] 强杀引擎进程失败：{e}")
        await asyncio.sleep(1.0)
        return proc.poll() is not None

    async def _webui_engine_restart(self):
        """POST /engine_restart：重启由插件拉起的引擎。

        重启是释放「已加载风格所占常驻内存」的唯一途径（引擎无卸载接口）。
        """
        if not self._webui_enabled():
            return error_response("Web UI 管理未启用", status_code=403)
        proc = _ENGINE_PROCESS
        if proc is None or proc.poll() is not None:
            return error_response(
                "当前引擎不是由本插件拉起的，无法自动重启；"
                "请在宿主机上手动重启引擎，或先由本插件（安装向导）启动后再试。",
                status_code=400,
            )
        api_base = str(self._cfg("api_base") or "")
        if not await self._stop_engine_process():
            return error_response("引擎进程停止失败，请手动处理", status_code=500)
        # 旧进程已退出：清掉探活缓存与风格加载记录（新引擎需重新加载风格）
        invalidate_engine_alive_cache(api_base)
        self._loaded_styles.clear()
        self._spawn_background_task(
            ensure_engine_running(
                api_base=self._cfg("api_base"),
                engine_dir=self._cfg("engine_dir"),
                engine_bin=self._cfg("engine_bin") or None,
                engine_log=self._cfg("engine_log"),
                auto_start=True,
                start_timeout=float(self._cfg("engine_start_timeout") or 90),
            )
        )
        return json_response(
            {
                "ok": True,
                "message": "已停止旧引擎并在后台重新启动，请稍候刷新状态（首次冷启动约 40~60 秒）",
            }
        )

    # ---------------- 安装向导（Web UI） ----------------

    def _spawn_background_task(self, coro: Any) -> None:
        """以后台任务运行（持有引用防 GC），完成后自动丢弃。"""
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def _webui_install_info(self):
        """GET /install_info：安装向导环境自检。"""
        cfg_dir = resolve_engine_dir(self._cfg("engine_dir"))
        bin_path = resolve_engine_bin(cfg_dir, self._cfg("engine_bin") or None)

        # 三个候选位置目的不同、可能位于不同磁盘；按文件系统（st_dev）去重，
        # 同一磁盘只返回一条，并在 labels 里保留各位置的用途说明。
        candidates: list[tuple[str, str]] = []
        if cfg_dir:
            candidates.append(("引擎目录", cfg_dir))
        try:
            candidates.append(("AstrBot 临时目录", str(Path(get_astrbot_temp_path()))))
        except Exception:  # noqa: BLE001
            pass
        candidates.append(("用户主目录", str(Path.home())))

        disk: list[dict[str, Any]] = []
        seen_paths: set[str] = set()
        seen_devs: set[int] = set()
        for label, cand in candidates:
            if not cand or cand in seen_paths:
                continue
            seen_paths.add(cand)
            try:
                dev = os.stat(cand).st_dev
            except Exception:  # noqa: BLE001 - 路径不存在/不可读，跳过
                continue
            if dev in seen_devs:
                for item in disk:
                    if item.get("_dev") == dev:
                        item["labels"].append(label)
                        break
                continue
            seen_devs.add(dev)
            try:
                usage = shutil.disk_usage(cand)
            except Exception:  # noqa: BLE001
                continue
            disk.append(
                {
                    "_dev": dev,
                    "path": cand,
                    "labels": [label],
                    "total_gb": round(usage.total / 2**30, 1),
                    "free_gb": round(usage.free / 2**30, 1),
                }
            )
        for item in disk:
            item.pop("_dev", None)

        return json_response(
            {
                "platform": platform.platform(),
                "os_name": os.name,
                "python": sys.version.split()[0],
                "ffmpeg": resolve_ffmpeg(self._cfg("ffmpeg_path")) or None,
                "engine_dir": cfg_dir or None,
                "engine_bin": bin_path,
                "engine_bin_exists": bool(bin_path and os.path.isfile(bin_path)),
                "engine_alive": await check_engine_alive(str(self._cfg("api_base") or "")),
                "api_base": self._cfg("api_base"),
                "mem_available_mb": available_memory_mb(),
                "disk": disk,
                "webui_enabled": self._webui_enabled(),
            }
        )

    async def _webui_install_check_path(self):
        """POST /install_check_path：校验用户填写的引擎目录（不保存配置）。

        入参：{"engine_dir": "...", "engine_bin": "..."(可选)}
        """
        payload = await request.json(default=None)
        if not isinstance(payload, dict):
            return error_response("请求体必须是 JSON 对象", status_code=400)
        engine_dir = str(payload.get("engine_dir") or "").strip()
        engine_bin_cfg = str(payload.get("engine_bin") or "").strip() or None
        if not engine_dir:
            return error_response("请先填写引擎目录", status_code=400)
        resolved_dir = resolve_engine_dir(engine_dir)
        resolved_bin = resolve_engine_bin(resolved_dir, engine_bin_cfg)
        exists = bool(resolved_bin and os.path.isfile(resolved_bin))
        return json_response(
            {
                "engine_dir": resolved_dir,
                "engine_dir_exists": os.path.isdir(resolved_dir),
                "engine_bin": resolved_bin,
                "engine_bin_exists": exists,
                "ok": exists,
            }
        )

    async def _webui_install_launch_engine(self):
        """POST /install_launch_engine：后台拉起引擎（不阻塞请求，前端轮询状态）。"""
        if not self._webui_enabled():
            return error_response("Web UI 管理未启用", status_code=403)
        api_base = str(self._cfg("api_base") or "")
        if await check_engine_alive(api_base):
            return json_response(
                {
                    "ok": True,
                    "already_alive": True,
                    "message": "引擎已在运行，无需启动",
                }
            )
        ensure_kwargs = {
            "api_base": api_base,
            "engine_dir": self._cfg("engine_dir"),
            "engine_bin": self._cfg("engine_bin") or None,
            "engine_log": self._cfg("engine_log"),
            "auto_start": True,
            "start_timeout": float(self._cfg("engine_start_timeout") or 90),
        }
        bin_path = resolve_engine_bin(
            resolve_engine_dir(ensure_kwargs["engine_dir"]), ensure_kwargs["engine_bin"]
        )
        if not bin_path or not os.path.isfile(bin_path):
            return error_response(
                f"找不到引擎可执行文件（{bin_path or '未解析出路径'}）。"
                "请先下载并解压引擎，再把引擎目录填入配置。",
                status_code=400,
            )
        self._spawn_background_task(ensure_engine_running(**ensure_kwargs))
        return json_response(
            {
                "ok": True,
                "already_alive": False,
                "message": "已在后台启动引擎，请稍候并刷新状态（首次冷启动约需 40~60 秒）",
            }
        )

    # ---------------- 引擎 / 合成封装 ----------------

    async def _ensure_engine(self) -> bool:
        return await ensure_engine_running(
            api_base=self._cfg("api_base"),
            engine_dir=self._cfg("engine_dir"),
            engine_bin=self._cfg("engine_bin") or None,
            engine_log=self._cfg("engine_log"),
            auto_start=bool(self._cfg("auto_start_engine")),
            start_timeout=float(self._cfg("engine_start_timeout") or 90),
        )

    def _resolve_style(self, override: Any) -> tuple[int | None, str | None]:
        """决定本次合成使用的 styleId。

        - override 非空：视为调用方显式指定的风格；非法时返回错误提示（不回退），
          以便命令/工具把问题明确反馈给用户。
        - override 为空：使用配置里的默认风格；配置值非法时告警并回退到
          DEFAULT_STYLE_ID，保证行为可预期。

        返回 (style_id, error)：error 非空时 style_id 为 None。
        """
        if override is not None and str(override).strip() != "":
            resolved = normalize_style(override)
            if resolved is None:
                return None, (
                    f"无法识别的风格「{override}」。可用风格：{describe_styles()}；"
                    "也接受中文名与日文名。"
                )
            guard_err = self._style_override_guard(resolved)
            if guard_err:
                return None, guard_err
            return resolved, None

        cfg_value = self._cfg("style_id")
        resolved = normalize_style(cfg_value)
        if resolved is None:
            logger.warning(
                f"[COEIROINK] 配置的 style_id={cfg_value!r} 无法识别，"
                f"已回退到默认 {style_label(DEFAULT_STYLE_ID)}；"
                f"可用风格：{describe_styles()}"
            )
            return DEFAULT_STYLE_ID, None
        return resolved, None

    def _style_override_guard(self, style_id: int) -> str | None:
        """单次风格切换的内存保护。

        引擎对每个风格懒加载且**常驻内存**（无卸载接口），一次风格覆盖会永久
        多占一份驻留内存。此处按配置拦截：
        - `allow_style_override=false`：直接拒绝单次切换（只允许配置里的默认风格）；
        - 目标风格尚未加载且可用内存低于 `style_switch_min_free_mb`：拒绝并提示。

        返回错误提示；放行时返回 None。
        """
        if not self._cfg("allow_style_override"):
            return (
                "已禁用单次风格切换（allow_style_override=false）；"
                f"如需其它风格请在配置中修改默认风格。可用风格：{describe_styles()}"
            )
        if style_id in self._loaded_styles:
            return None
        min_free = float(self._cfg("style_switch_min_free_mb") or 0)
        avail = available_memory_mb()
        if min_free and avail is not None and avail < min_free:
            logger.warning(
                f"[COEIROINK] 风格 {style_label(style_id)} 尚未加载，"
                f"可用内存 {avail:.0f}MB < {min_free:.0f}MB，已拒绝本次风格切换"
            )
            return (
                f"可用内存不足（{avail:.0f}MB < {min_free:.0f}MB），"
                "已拒绝临时切换到未加载的风格；请释放内存，或调整 "
                "style_switch_min_free_mb / allow_style_override。"
            )
        logger.info(
            f"[COEIROINK] 本次将加载并常驻风格 {style_label(style_id)}"
            "（引擎无卸载接口，内存紧张时建议只用一种风格）"
        )
        return None

    def _api_base_allowed(self) -> bool:
        """校验 api_base：默认只允许回环地址，防止回复文本外发（P2-5）。"""
        if self._cfg("allow_remote_engine"):
            return True
        base = str(self._cfg("api_base") or "")
        if api_base_is_local(base):
            return True
        logger.warning(
            f"[COEIROINK] api_base={base} 不是回环地址，已阻止语音合成（防止文本外发）。"
            "如确需使用远程引擎，请开启配置项 allow_remote_engine。"
        )
        return False

    async def _synth_one(self, text: str, style_id: int) -> str | None:
        """合成单段文本，返回音频路径；不可用时返回 None。

        - 并发限流：`max_concurrent_synth`（默认 1，串行）；
        - 排队上限：在途（含等待）数超过 并发上限 + `max_synth_queue` 时直接跳过，
          避免忙时请求无限堆积、语音延迟持续增长。
        """
        global _SYNTH_INFLIGHT
        limit = max(1, int(self._cfg("max_concurrent_synth") or 1))
        max_queue = max(0, int(self._cfg("max_synth_queue") or 0))
        _SYNTH_INFLIGHT += 1
        try:
            if _SYNTH_INFLIGHT > limit + max_queue:
                logger.info(
                    f"[COEIROINK] 合成队列已满（在途 {_SYNTH_INFLIGHT - 1} 条 > "
                    f"上限 {limit + max_queue}），本次跳过语音合成"
                )
                return None
            try:
                if not self._api_base_allowed():
                    return None
                if not await self._ensure_engine():
                    logger.warning("[COEIROINK] 引擎不可用，跳过语音合成")
                    return None
                sem = get_synth_semaphore(limit)
                async with sem:
                    path = await synthesize_with_recovery(
                        text,
                        ensure_kwargs={
                            "api_base": self._cfg("api_base"),
                            "engine_dir": self._cfg("engine_dir"),
                            "engine_bin": self._cfg("engine_bin") or None,
                            "engine_log": self._cfg("engine_log"),
                            "auto_start": bool(self._cfg("auto_start_engine")),
                            "start_timeout": float(self._cfg("engine_start_timeout") or 90),
                        },
                        min_available_mb=float(self._cfg("min_available_memory_mb") or 0),
                        api_base=self._cfg("api_base"),
                        speaker_uuid=self._cfg("speaker_uuid"),
                        style_id=int(style_id),
                        speed_scale=float(self._cfg("speedScale") or 1.0),
                        output_sampling_rate=int(self._cfg("output_sampling_rate") or 0) or None,
                        enable_mp3=bool(self._cfg("enable_mp3")),
                        keep_temp_files=bool(self._cfg("keep_temp_files")),
                        ffmpeg_path=self._cfg("ffmpeg_path"),
                    )
                # 合成成功 => 该风格已被引擎加载并常驻内存
                self._loaded_styles.add(int(style_id))
                return path
            except Exception as e:  # noqa: BLE001
                logger.error(f"[COEIROINK] 合成失败（不影响正常回复）：{e}")
                return None
        finally:
            _SYNTH_INFLIGHT -= 1

    def _segments(self, text: str) -> list[str]:
        """把待合成文本切成若干段（P1-2），返回清洗后的段列表。

        - 未超 max_text_length：整段返回；
        - 超长且 skip_if_too_long：返回空（保持原「跳过」语义）；
        - 超长且允许：按句切分，段数超过 `max_synth_segments` 时截断并记日志。
        """
        cleaned = clean_text(text)
        if not cleaned:
            return []
        max_len = int(self._cfg("max_text_length") or 200)
        if len(cleaned) <= max_len:
            return [cleaned]
        if self._cfg("skip_if_too_long"):
            logger.info(f"[COEIROINK] 文本超过 {max_len} 字，且 skip_if_too_long 开启，跳过合成")
            return []
        max_segments = max(1, int(self._cfg("max_synth_segments") or _MAX_SYNTH_SEGMENTS))
        segments = split_text_segments(cleaned, max_len)
        if len(segments) > max_segments:
            logger.info(
                f"[COEIROINK] 文本过长（{len(cleaned)} 字，切出 {len(segments)} 段），"
                f"仅朗读前 {max_segments} 段"
            )
            segments = segments[:max_segments]
        return segments

    async def _synthesize_all(self, text: str, style_id: int) -> list[str]:
        """按段合成全部文本，返回音频路径列表（可能为空）。"""
        segments = self._segments(text)
        if not segments:
            return []
        paths: list[str] = []
        for seg in segments:
            path = await self._synth_one(seg, style_id)
            if path:
                paths.append(path)
        return paths

    async def _synthesize(self, text: str, style_id: int) -> str | None:
        """单段合成（保留原有语义：超长按配置跳过或截断），供测试合成等短文本场景使用。"""
        cleaned = clean_text(text)
        if not cleaned:
            return None
        max_len = int(self._cfg("max_text_length") or 200)
        if len(cleaned) > max_len:
            if self._cfg("skip_if_too_long"):
                logger.info(f"[COEIROINK] 文本超过 {max_len} 字，跳过合成")
                return None
            cleaned = cleaned[:max_len]
        return await self._synth_one(cleaned, style_id)

    async def _to_japanese(self, text: str, umo: str | None) -> str | None:
        return await translate_to_japanese(
            self.context,
            text,
            self._cfg("translate_system_prompt"),
            umo,
        )

    # ---------------- 自动触发（on_decorating_result） ----------------

    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent) -> None:
        if not self._cfg("enabled"):
            return
        mode = self._cfg("mode")
        if mode not in ("always_translate", "probabilistic", "japanese_only"):
            return

        result = event.get_result()
        if result is None or not getattr(result, "chain", None):
            return
        plain = result.get_plain_text()
        if not plain or not plain.strip():
            return

        cleaned = clean_text(plain)
        if not cleaned:
            return

        umo = event.unified_msg_origin
        if mode == "japanese_only":
            if not is_japanese(cleaned):
                return
            speak_text: str | None = cleaned
        elif mode == "probabilistic":
            # P1-3：probability 夹取到 0~1，非法值告警
            try:
                probability = float(self._cfg("probability") or 0)
            except (TypeError, ValueError):
                probability = DEFAULT_CONFIG["probability"]
            if not 0.0 <= probability <= 1.0:
                logger.warning(f"[COEIROINK] probability={probability} 超出 0~1，已按边界夹取")
                probability = max(0.0, min(1.0, probability))
            if random.random() >= probability:
                return
            # 已经是日语就直接朗读，省掉一次 LLM 翻译往返（翻译提示词对
            # 日语文本本就是“原样输出”，行为不变）
            speak_text = cleaned if is_japanese(cleaned) else await self._to_japanese(cleaned, umo)
        else:  # always_translate
            speak_text = cleaned if is_japanese(cleaned) else await self._to_japanese(cleaned, umo)

        if not speak_text:
            return

        style_id, _ = self._resolve_style(None)
        # P1-2：长文本按句分段合成，逐段追加语音（保持原文本不变，避免重复发送）
        paths = await self._synthesize_all(speak_text, style_id)
        if not paths:
            return
        for path in paths:
            result.chain.append(Record.fromFileSystem(path, text=speak_text))

    # ---------------- 按需触发（命令） ----------------

    @filter.command("voice", alias={"念", "voice"})
    async def cmd_voice(self, event: AstrMessageEvent):
        """把文本合成为日语语音发出。用法：/voice 要朗读的内容。"""
        if not self._cfg("enabled"):
            yield event.plain_result("COEIROINK 语音插件已禁用。")
            return

        raw = (event.message_str or "").strip()
        text = re.sub(r"^/?\s*(voice|念)\s*", "", raw, flags=re.IGNORECASE).strip()
        if not text:
            yield event.plain_result(
                "请在命令后附上要朗读的文本（例如：/voice おはようございます）。"
                "可在文本前用 [风格] 临时指定情绪，例如：/voice [温柔] おはよう。"
                f"可用风格：{describe_styles()}",
            )
            return

        # 可选风格前缀（[温柔]… / 【温柔】… / #温柔 …），不影响原有纯文本用法
        style_raw, text = extract_style_prefix(text)
        style_id, style_err = self._resolve_style(style_raw)
        if style_err:
            yield event.plain_result(style_err)
            return
        if not text:
            yield event.plain_result("请在风格之后附上要朗读的文本。")
            return

        # 用户直接输入日语时跳过 LLM 翻译，省一次往返与 token
        jp = text if is_japanese(text) else await self._to_japanese(text, event.unified_msg_origin)
        if not jp:
            yield event.plain_result("翻译失败：未找到可用的对话模型。")
            return
        # P1-2：长文本按句分段合成，逐条语音发出
        paths = await self._synthesize_all(jp, style_id)
        if not paths:
            yield event.plain_result("语音合成失败：引擎不可用或文本为空。")
            return
        yield event.chain_result([Record.fromFileSystem(p, text=jp) for p in paths])

    # ---------------- 按需触发（LLM 工具） ----------------

    async def _llm_tool_handler(
        self,
        event: AstrMessageEvent,
        text: str,
        style: str | None = None,
    ):
        if not self._cfg("enabled"):
            return "COEIROINK 语音插件已禁用。"
        if not text or not text.strip():
            return "未提供要朗读的文本。"
        style_id, style_err = self._resolve_style(style)
        if style_err:
            return style_err
        # 已是日语则直接朗读，跳过 LLM 翻译往返
        jp = text if is_japanese(text) else await self._to_japanese(text, event.unified_msg_origin)
        if not jp:
            return "翻译失败：未找到可用的对话模型。"
        # P1-2：长文本按句分段合成，逐条语音发出
        paths = await self._synthesize_all(jp, style_id)
        if not paths:
            return "语音合成失败：引擎不可用或文本为空。"
        await event.send(MessageChain([Record.fromFileSystem(p, text=jp) for p in paths]))
        return "已发送语音。"
