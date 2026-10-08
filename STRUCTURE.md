# Tsukuyomi-chan COEIROINK 日语语音插件 · 结构说明（PLUGIN_STRUCTURE）

> 本文档由代码实地梳理生成，只描述当前源码中**真实存在**的内容。
> 版本：对应 `metadata.yaml` 的 `1.10.0`（v1.2.0 起插件通用化：不再内置任何机器相关路径；
> v1.2.1 起显示名改为 Tsukuyomi-chan COEIROINK 日语语音，并附音源/软件致谢；
> v1.2.2 起补充 Logo 作者声明；v1.2.3 起补充渠道支持说明；
> v1.3.0 起内置 Web UI 图形化管理面板（可开关，开启后原配置面板仅保留开关）；
> v1.3.1 起在 README 与插件描述中加入本地 TTS 宣传介绍；
> v1.4.0 起新增 Web UI 安装向导页（环境自检/下载指引/目录校验/后台启动/合成验证）；
> v1.4.1 起安装向导磁盘自检按文件系统去重，避免同一磁盘重复显示；
> v1.5.0 起因 AstrBot 页面 iframe 沙箱不支持跨页跳转，管理面板与安装向导合并为单页双标签，
> 启动引擎就绪后自动切回管理面板标签；并通过 `.astrbot-plugin/i18n` 提供中文页面标题；
> 1.5.1 起为插件市场上架补齐元数据（repo/short_desc/support_platforms/astrbot_version/tags/category），
> 版本号改用纯语义化格式（去 `v` 前缀）；
> 1.5.2 起以 CC BY-NC 4.0 许可协议发布（LICENSE，署名-非商业性使用，第三方资源按各自条款）；
> 1.5.3 起作者署名更正为 Aurora & deepseek；
> 1.5.4 起新增 CHANGELOG.md 更新日志，版本号保持标准三段式（1.5.3.1 为 AstrBot 不支持的写法，已撤销）；
> 1.6.0 起按 ROADMAP.md 完成 P0/P1/P2 改进：面板基础引导项、并发合成限制、临时文件清理、
> 版本约束放宽与 web 容错导入、长文本分段合成、概率夹取、macOS 内存检查、回环校验、
> pytest/CI、ruff、logo 压缩；
> 1.6.1 起「管理面板」标签页移除重复的测试合成区块，合成验证仅保留在「安装向导」第 5 步；
> 1.6.2 起新增 pyproject.toml（ruff 配置 + 满足 CI pip 缓存条件），修复 1.6.0 的 CI 初始化失败；
> 1.7.0 起补强运行风险与可观测性：合成排队上限、风格切换内存保护、Record.fromFileSystem、
> Web UI 引擎日志与重启引擎、分段段数可配置、行为层测试；
> 1.7.1 起「管理面板」标签页内新增二级菜单（监控 / 设置），功能分组不再一屏堆叠；
> 1.8.0 起性能优化：合成结果 LRU 缓存、is_japanese/clean_text 快路径、内存读数与地址校验缓存；
> 1.9.0 起新增句级缓存（按句/小句复用音频 + ffmpeg 拼接为一条语音，键可忽略结尾标点）；
> 1.10.0 起新增长期保留池：命中统计 + 定期 LLM 复审晋级，池内音频不受临时清理影响（索引落盘可跨重启）。
> 无法从代码/环境中确认的点，统一用「**待确认**」标注，不做臆测。

> ### 🔊 音源与软件致谢
> 本插件所使用的音源是由**夢前黎**制作的**月读酱**（Tsukuyomi-chan），这是一个免费使用的音源，
> 请大家多多支持作者夢前黎。官网：https://tyc.rei-yumesaki.net/
> 本插件所使用的软件为 **COEIROINK**，由**シロワニさん**制作，这是一个免费的 TTS 软件，
> 请大家多多支持シロワニさん。官网以及下载：https://coeiroink.com/
> 本插件所使用的 Logo 由**ノザラシ**制作。获取链接：https://seiga.nicovideo.jp/seiga/im11798588

---

## 一、插件目录树与文件职责

插件根目录（绝对路径）：

```
/root/data/plugins/astrbot_plugin_coeiroink_tts/
├── README.md              # 面向用户/开发者的使用说明（四种触发模式、三种风格、内存提示）
├── CHANGELOG.md           # 更新日志（按版本记录变更）
├── STRUCTURE.md           # 本文件：插件结构说明
├── LICENSE                # CC BY-NC 4.0（署名-非商业性使用 4.0 国际）许可全文
├── metadata.yaml          # AstrBot 插件元信息（名称、显示名、版本、作者、市场字段）
├── main.py                # 插件全部逻辑（1721 行，单文件实现）
├── _conf_schema.json      # AstrBot 插件配置面板 schema（24 个配置项；仅 5 个基础项可见，其余由 Web UI 管理）
├── _selftest_synth.py     # 独立自测脚本：绕开 AstrBot 直接调用核心合成函数
├── tests/                 # pytest 单元测试（加载方式同自测脚本）
│   ├── test_plugin_core.py      # 纯函数（风格/文本/解析链/分段/回环校验）
│   └── test_plugin_behavior.py  # 行为层（队列上限/风格保护/分段配置/临时清理/风格记录）
├── pyproject.toml         # 开发工具配置（ruff 规则集）与 CI 依赖缓存标记；插件运行不依赖它
├── .github/workflows/ci.yml  # GitHub Actions：ruff + py_compile + pytest
├── pages/                 # 内置 Web UI（AstrBot 插件 Pages）
│   └── settings/          # 双层导航页面（管理面板〔监控/设置〕+ 安装向导）：index.html + assets/logo.png
├── .astrbot-plugin/       # 插件国际化（页面标题）
│   └── i18n/              # zh-CN.json / en-US.json：pages.settings.title=管理面板/Dashboard
└── __pycache__/           # Python 字节码缓存（main.cpython-312.pyc），运行期自动生成
```

| 文件 | 职责 |
| --- | --- |
| `metadata.yaml` | 插件标识 `name: astrbot_plugin_coeiroink_tts`、`display_name: Tsukuyomi-chan COEIROINK 日语语音`、`version: 1.10.0`、`author: Aurora & deepseek`；市场字段：`repo`（GitHub 地址）、`short_desc`、`support_platforms: [aiocqhttp]`（NapCat/OneBot v11，已实测）、`astrbot_version: ">=4.24.5,<5"`（插件 Pages 最早可用版本）、`category: utilities`、`tags` |
| `main.py` | 全部实现：常量、默认配置、风格归一化、文本清洗、引擎探测/拉起、合成链路、翻译、TTS Provider 适配器、插件主体（自动触发 + 命令 + LLM 工具） |
| `_conf_schema.json` | 配置项定义，AstrBot 据此渲染配置面板；键名与 `main.py` 中 `DEFAULT_CONFIG` 一一对应 |
| `README.md` | 用户文档：风格对照、配置方法、单次切换用法、非法值行为、内存提示 |
| `_selftest_synth.py` | 排障用；从文件动态加载 `main.py`，直接调 `check_engine_alive / ensure_engine_running / synthesize_to_file`；**通用化**：配置路径/输出目录/ffprobe 均可参数或环境变量覆盖，风格沿用运行配置（`utf-8-sig` 兼容 BOM），避免自测时意外给引擎加载第二种常驻风格 |
| `__pycache__/` | 非源码，可安全删除；重新加载会再生成 |

> 注：本插件**没有** `requirements.txt`（见第五节）。

---

## 二、核心类与关键函数

`main.py` 自上而下分为若干区块，命名均为源码实际函数名。

### 2.1 常量与默认配置
- `DEFAULT_ENGINE_DIR / DEFAULT_ENGINE_BIN / DEFAULT_ENGINE_LOG / DEFAULT_FFMPEG` = 空字符串：**通用化设计，不写死任何机器路径**（见 2.4 的 resolve_* 解析链与第七节）
- `DEFAULT_API_BASE` = `http://127.0.0.1:50032`
- `DEFAULT_SPEAKER_UUID` = `3c37646f-3881-5374-2a83-149267990abc`（つくよみちゃん示例值）
- `DEFAULT_STYLE_ID` = `0`
- `DEFAULT_TRANSLATE_PROMPT`：翻译系统提示词（见第四节）
- `VALID_MODES` = `("always_translate", "on_demand", "probabilistic", "japanese_only")`
- `_LLM_TOOL_NAME` = `coeiroink_speak`
- 环境变量名常量：`ENV_ENGINE_DIR`（`COEIROINK_ENGINE_DIR`）、`ENV_ENGINE_BIN`（`COEIROINK_ENGINE_BIN`）、`ENV_ENGINE_LOG`（`COEIROINK_ENGINE_LOG`）、`ENV_FFMPEG`（`COEIROINK_FFMPEG`）
- 模块级 `_ENGINE_START_LOCK`（`asyncio.Lock`）、`_ENGINE_PROCESS`（`subprocess.Popen`）：防止并发重复拉起引擎
- `_SYNTH_SEMAPHORES` + `get_synth_semaphore(limit)`：**合成并发控制**（P0-2），按并发上限复用进程内 `asyncio.Semaphore`（默认 1，串行）
- 临时音频清理常量（P0-3）：`_TEMP_CLEAN_FIRST_DELAY=60s` / `_TEMP_CLEAN_INTERVAL=3600s` / `_TEMP_RETENTION=2h`
- `_MAX_SYNTH_SEGMENTS = 6`：长文本分段合成的段数上限（P1-2）
- `_SYNTH_INFLIGHT`：进程内在途（含等待）合成计数，配合 `max_synth_queue` 实现排队上限
- `_LOG_TAIL_BYTES = 64 * 1024`：Web UI 引擎日志尾读上限
- `_MEM_CACHE_TTL = 2.0` / `_API_LOCAL_CACHE`：可用内存读数短 TTL 缓存、回环判定备忘（1.8.0 性能）
- `_KANA_SET` / `_KANA_DELETE_TABLE`：假名码点集合与 translate 删除表（`is_japanese` 快路径）
- `_CLAUSE_ENDERS_RE` / `_PHRASE_TAIL_PUNCT_RE`：小句边界正则、句级缓存键的结尾标点清理（1.9.0）
- `_POOL_*` 常量与 `_POOL_REVIEW_PROMPT`：长期池目录名/索引名/复审延迟/候选上限/统计上限与 LLM 复审提示词（1.10.0）
- `resolve_pool_dir()` / `synth_key_to_json()` / `synth_key_from_json()` / `parse_pool_review_response()`：池目录解析、缓存键序列化、复审响应解析（1.10.0）
- `DEFAULT_CONFIG`：插件默认配置字典（键与 `_conf_schema.json` 对应，共 28 项；含 `enable_webui`、`max_concurrent_synth`、`max_synth_queue`、`max_synth_segments`、`allow_style_override`、`style_switch_min_free_mb`、`allow_remote_engine`）
- 通用解析函数（环境相关路径统一走「配置项 → 环境变量 → 自动推导」）：
  - `resolve_engine_dir(cfg) -> str`：引擎根目录（配置 > `COEIROINK_ENGINE_DIR`）
  - `resolve_engine_bin(engine_dir, cfg) -> str | None`：引擎可执行文件（配置/环境变量 > `<engine_dir>/engine/engine`，Windows 为 `engine.exe`；相对路径按相对引擎目录解释）
  - `resolve_engine_log(engine_dir, cfg) -> str | None`：引擎日志（配置/环境变量 > `<engine_dir>/engine.log` > AstrBot 临时目录兜底；返回 `None` 表示用 `os.devnull`）
  - `resolve_ffmpeg(cfg) -> str`：ffmpeg（配置/环境变量 > `shutil.which("ffmpeg")`）

### 2.2 风格归一化
- `STYLE_TABLE: dict[int, tuple[str, str]]`：`{styleId: (中文名, 日文名)}`
  - `0 → ("平静", "れいせい")`
  - `5 → ("温柔", "おしとやか")`
  - `6 → ("充满活力", "げんき")`
- `_STYLE_ALIASES: dict[str, int]`：可识别的别名表，含中文名、日文名、罗马音、英文（如 `平静/冷静/安静/れいせい/reisei/calm`、`温柔/溫柔/温和/柔和/おしとやか/oshitoyaka/gentle`、`充满活力/活力/元气/元気/活泼/げんき/genki/lively/energetic`）
- `_style_key(value)`：去所有空白后转小写，作为查表键
- `normalize_style(value) -> int | None`：把任意风格输入归一化为 styleId；无法识别返回 `None`；**布尔值一律视为非法**（避免 `True/False` 被当成 `1/0`）；支持 `+5`/`5 ` 之类数字字符串
- `style_label(style_id) -> str`：返回形如 `5/温柔（おしとやか）`
- `describe_styles() -> str`：返回 `0=平静（れいせい）、5=温柔（おしとやか）、6=充满活力（げんき）`
- `_STYLE_PREFIX_RE` / `extract_style_prefix(text) -> (style|None, text)`：解析命令中的可选风格前缀，支持 `[温柔]…`、`【温柔】…`、`#温柔 …` 三种写法

### 2.3 文本工具
- `clean_text(text) -> str`：去代码块（```…```）、URL、Markdown 标记、emoji，换行转空格并压缩空白
- `is_japanese(text) -> bool`：含假名，且假名占非空白字符比例 ≥ `0.1` 时判为日语（用于 `japanese_only` 模式）
- `split_text_segments(text, max_len) -> list[str]`：**长文本分段**（P1-2）。按句末标点（`。！？!?…`）切句后贪心合并，尽量每段不超过 `max_len`；单句超长时按 `max_len` 硬切不丢内容
- `api_base_is_local(api_base) -> bool`：**回环地址校验**（P2-5）。`127.x` / `localhost` / `::1` 视为本机；解析失败保守返回 `False`

### 2.4 引擎探测 / 拉起
- 模块级共享资源（性能/内存优化）：
  - `_HTTP_CLIENT` + `_get_http_client()`：进程内共享的 `httpx.AsyncClient`（`max_connections=2`、`max_keepalive_connections=2`），复用连接池，省去每次探测/合成的建连开销；`close_http_client()` 在插件 `terminate()` 时释放。
  - `_ALIVE_CACHE` + `_alive_cached/_alive_cache_set/invalidate_engine_alive_cache`：探活结果缓存（TTL 15s）。**只缓存「存活」结果**，失败不缓存；合成失败时由 `synthesize_with_recovery` 主动失效，避免把坏引擎误判为可用。
- `check_engine_alive(api_base, timeout=3.0) -> bool`：GET `/v1/speakers`，**要求返回非空说话人列表**才视为「可合成」。原因见注释：COEIROINK 启动时 HTTP 先就绪，但模型仍在加载，此时打 `/v1/predict` 会被服务端断开（表现为 httpx `Server disconnected`）。**内存优化**：响应约 1.2MB（含 3 张 base64 图标），只读原始字节判断是否为非空 JSON 数组（`[` 开头且不是 `[]`），不再解析成 Python 对象；命中缓存时直接返回，不产生网络请求。任何异常都返回 `False`，不抛出。
- `_launch_engine(engine_dir, engine_bin, engine_log)`：用 `subprocess.Popen` 后台拉起引擎（POSIX 用 `start_new_session=True`，Windows 用 `CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS`）；stdout/stderr 重定向到 `engine.log`（日志路径为空时用 `os.devnull`）；启动前设置线程/内存池环境变量（Linux 优化项，其他平台无害空操作，见第六节）
- `ensure_engine_running(api_base, engine_dir, engine_bin, engine_log, auto_start, start_timeout=90.0, probe_interval=2.0) -> bool`：先探测，不可用且允许自动拉起时，**经 resolve_* 解析引擎路径**；解析不出可执行文件则记录带指引的日志并返回 `False`（不抛异常）；在锁内双重检查后启动并轮询等待就绪

### 2.5 合成链路
- `stream_wav_to_path(wav_path, text, ..., style_id, speed_scale, output_sampling_rate, timeout=60, retries=1, retry_wait=2.0) -> None`：POST `/v1/predict`，**按块流式落盘**到 `wav_path`，不把整段 wav 缓冲进内存（长文本 wav 可达数 MB）；对 `httpx.TransportError`（引擎热加载时偶发断连）做一次温和重试；合成成功后刷新探活缓存，重试耗尽时使缓存失效
- `wav_to_mp3(wav_path, mp3_path, ffmpeg_path) -> str`：用 ffmpeg 的 `libmp3lame -qscale:a 2` 转 mp3，失败抛 `RuntimeError`（在 `synthesize_to_file` 中经 `asyncio.to_thread` 调用，不阻塞事件循环）；`ffmpeg_path` 为空时走 `resolve_ffmpeg` 自动查找，找不到时报带指引的错误
- `synthesize_to_file(text, output_dir, ..., enable_mp3=True, keep_temp_files=False, ...) -> str`：核心函数，文本 → wav（流式）→（可选）mp3，返回最终音频绝对路径；输出目录默认 `get_astrbot_temp_path()`（本机为 `/root/data/temp`），文件名 `coeiroink_<12位hex>.wav/.mp3`
- `available_memory_mb() -> float | None`：跨平台可用内存（MB）。Linux 读 `/proc/meminfo` 的 `MemAvailable`；Windows 用 `GlobalMemoryStatusEx`（ctypes）；macOS 用 `vm_stat`（free+inactive 页 × `hw.pagesize`）（P1-4）；其他平台/失败返回 `None`（跳过内存门槛检查，不阻断合成）
- `synthesize_with_recovery(text, *, ensure_kwargs, min_available_mb=0.0, **synth_kwargs) -> str`：合成前检查可用内存（低于 `min_available_mb` 直接抛错跳过）；合成失败（如引擎被 OOM 杀掉）时，**先使探活缓存失效**，再按 `ensure_kwargs` 重启引擎并重试一次
- `translate_to_japanese(context, text, system_prompt, umo) -> str | None`：用当前会话的对话模型把文本翻译成日语；无可用模型或调用失败返回 `None`

### 2.6 TTS Provider 适配器
- `class CoeiroinkTTSProvider(TTSProvider)`：AstrBot TTS Provider 适配器
  - `support_stream() -> False`（不支持流式）
  - `get_audio(text) -> str`：先 `ensure_engine_running`，再 `synthesize_with_recovery` 返回音频路径
  - `__init__` 读取 `api_base / speaker_uuid / style_id / speed_scale / output_sampling_rate / enable_mp3 / auto_start_engine / engine_dir / engine_bin / engine_log / timeout`
  - 非法 `style_id` 不再静默回退：打印告警并使用默认风格 `0`
- `_PROVIDER_TYPE_NAME` = `coeiroink_tts`
- `_TTS_PROVIDER_DEFAULT_CONFIG`：Provider 默认配置模板
- `_register_tts_provider_adapter()`：**幂等**注册适配器。因 AstrBot 热重载只清 `sys.modules`/handlers/平台适配器，不清 provider 注册表，故不用装饰器，改为显式调 `register_provider_adapter(...)(CoeiroinkTTSProvider)`；已注册时只刷新类引用。模块导入时立即执行。

### 2.7 插件主体 `class CoeiroinkTTSPlugin(Star)`
- `__init__(context, config)`：合并 `DEFAULT_CONFIG` 与传入 config（值为 `None` 的项不覆盖）；保留 `AstrBotConfig` 引用到 `self._astrbot_config`（含 `save_config_async()`，供 Web UI 写回配置）
- `initialize()`：若开启 `auto_start_engine` 则创建预热任务 `_warm_up_engine`；启动**临时音频清理任务** `_temp_cleanup_loop`（P0-3）；注册 LLM 工具与 **Web UI 后端接口**
- `terminate()`：取消预热/清理任务，并 `close_http_client()` 释放共享 HTTP 连接池
- `_warm_up_engine()`：`try/except` 包裹 `_ensure_engine`，失败仅告警，不影响正常回复
- `_cleanup_temp_audio()/_temp_cleanup_loop()`：定期（每小时）删除 AstrBot 临时目录中超过 2 小时的 `coeiroink_*.mp3/.wav`（P0-3）
- `_register_llm_tools_if_needed()`：幂等注册 LLM 工具 `coeiroink_speak`（仅当 `enable_llm_tool` 为真）
- `_ensure_engine()`：按配置调用 `ensure_engine_running`
- `_resolve_style(override) -> (style_id, error)`：决定本次合成 styleId。`override` 非空视为显式指定，非法则返回错误提示（**不回退**）；`override` 为空用配置默认值，配置非法则告警并回退 `DEFAULT_STYLE_ID`
- `_style_override_guard(style_id) -> str | None`：**风格切换内存保护**（1.7.0）。`allow_style_override=false` 直接拒绝单次切换；目标风格未加载且可用内存 < `style_switch_min_free_mb` 时拒绝并提示；放行时记日志说明该风格将常驻内存
- `_api_base_allowed() -> bool`：**回环校验**（P2-5）。`allow_remote_engine` 开启或 `api_base` 为回环地址时放行；否则告警并阻止合成（防止回复文本外发）
- `_synth_one(text, style_id) -> str | None`：单段合成。**排队上限判定**（在途数 > 并发上限 + `max_synth_queue` 时直接跳过）→ 校验回环 → 确保引擎 → **`get_synth_semaphore` 限流** → `synthesize_with_recovery`（带 `min_available_mb` 门槛）；成功后把该风格记入 `self._loaded_styles`；不可用返回 `None`
- `_segments(text) -> list[str]`：按 `max_text_length` 决定整段/跳过/分段（P1-2）。`skip_if_too_long` 开启时超长跳过；关闭时 `split_text_segments` 切分，超过 `max_synth_segments`（默认 6）段截断并记日志
- `_synthesize_all(text, style_id, *, already_cleaned=False) -> list[str]`：先查整段精确缓存；`phrase_cache_enabled` 时按句/小句查句级缓存并只合成未命中句，再用 `_concat_audio` 拼接为**一条**语音（失败回退逐段）；关闭句级缓存时沿用原「整段 / 合并分段」逻辑（1.9.0）
- `_phrase_cache_get/_phrase_cache_put/_phrase_cache_lookup_key`：句级缓存读写与键归一化（LRU，`phrase_cache_size`）
- `_concat_audio(paths) -> str | None`：ffmpeg concat demuxer 拼接多段音频（按扩展名选择 mp3/wav 编码），失败返回 None
- `_record_use(key, path)`：缓存/池命中统计（供复频率复审）
- `_pool_lookup(key)` / `_pool_candidates()` / `_pool_promote()` / `_pool_drop()`：长期池查找、候选筛选、晋升（复制进插件数据目录）、删除
- `_pool_enforce_limits()` / `_pool_expire()`：超限淘汰（命中数 + 最近使用）、按保留天数回收
- `_pool_save_index()` / `_pool_load_index()`：`index.json` 落盘与启动恢复
- `_llm_review_pool()` / `_pool_review_once()` / `_pool_review_loop()`：LLM 复审（失败回退规则）、单次复审、后台周期循环
- `_synthesize(text, style_id) -> str | None`：单段合成（保持原语义：超长按配置跳过或截断），供测试合成等短文本场景使用
- `_to_japanese(text, umo)`：调用 `translate_to_japanese`
- **Web UI（插件 Pages）**：
  - `_webui_enabled()`：读取 `enable_webui` 配置
  - `_register_web_apis()`：`context.register_web_api` 注册 9 条路由（前缀 `/{_PLUGIN_NAME}`，`_PLUGIN_NAME` 与 metadata 的 name 一致）：`GET/POST /config`、`GET /status`、`POST /test`、`GET /logs`（引擎日志尾部）、`POST /engine_restart`（重启引擎）、`POST /pool_review`（立即复审长期池）、`GET /install_info`、`POST /install_check_path`、`POST /install_launch_engine`（共 8 条路由）；框架对同路由同方法幂等（自动替换），热重载安全；**老版本 AstrBot 缺少 `astrbot.api.web` 或 `register_web_api` 时自动跳过（降级为无 Web UI）**（P1-1）
  - `_save_config() -> bool`：把 `self.config` 经 `AstrBotConfig.save_config_async()` 写回配置文件（不阻塞事件循环）
  - `_webui_get_config()`：返回当前配置 + 风格/模式选项表
  - `_webui_save_config()`：只接受已知键，按 int/float/bool/str 类型收敛；`mode` 必须在 `VALID_MODES` 内、`style_id` 必须可归一化；非法值返回 400 错误响应（`astrbot.api.web` 的 `request / json_response / error_response`）
  - `_webui_status()`：引擎探活、可用内存、当前风格、引擎进程 PID、解析后的 engine_bin/ffmpeg 等
  - `_webui_test_synth()`：`enable_webui` 关闭时返回 403；否则按当前配置合成一句测试语音（已是日语则跳过翻译）
  - `_webui_logs()`：`GET /logs` 尾读引擎日志（最多 `_LOG_TAIL_BYTES` = 64KB），返回路径/是否存在/是否截断/内容
  - `_stop_engine_process(timeout=15s)`：停止由插件拉起的引擎（SIGTERM → 超时 SIGKILL）；**不触碰外部启动的进程**
  - `_webui_engine_restart()`：`POST /engine_restart`。`enable_webui` 关闭时 403；非插件拉起时 400 并提示手动重启；成功后清探活缓存与 `_loaded_styles`，后台 `ensure_engine_running` 重启
  - **安装向导**：
    - `_webui_install_info()`：环境自检（`platform.platform()`、Python、ffmpeg、可用内存、候选目录磁盘剩余空间——**按文件系统 `st_dev` 去重**，同一磁盘只返回一条并合并用途标签、引擎目录/可执行文件解析与存在性、引擎探活）
    - `_webui_install_check_path()`：校验用户填写的引擎目录（解析 engine_dir/engine_bin 并检查存在性，**不保存配置**）
    - `_webui_install_launch_engine()`：`enable_webui` 关闭时 403；引擎已运行时返回 `already_alive`；可执行文件缺失时 400 并提示先下载解压；否则经 `_spawn_background_task` 后台调用 `ensure_engine_running`，立即返回，由前端轮询 `/status` 就绪状态
    - `_spawn_background_task(coro)`：`asyncio.create_task` + `_bg_tasks` 集合持有引用，完成后自动丢弃

### 2.8 对外入口
1. **自动触发** `@filter.on_decorating_result()` → `on_decorating_result(event)`
   - 仅 `always_translate / probabilistic / japanese_only` 三种模式生效（`on_demand` 直接返回）
   - 取 `event.get_result()` 纯文本，清洗后按模式决定 `speak_text`，再把 `Record.fromFileSystem(path, text=...)`（官方构造：`file:///` URI + `path`）**追加**到即将发送的消息链（不改动原文本，避免重复发送）；**长文本经 `_synthesize_all` 分段后逐段追加语音**（P1-2）
   - 性能优化：`always_translate / probabilistic` 模式下，若清洗后文本已判为日语（`is_japanese`），**跳过 LLM 翻译**直接朗读（翻译提示词对日语本就要求原样输出，行为不变）
   - `probabilistic` 模式的 `probability` **夹取到 0~1**，非法值告警（P1-3）
2. **命令** `@filter.command("voice", alias={"念", "voice"})` → `cmd_voice(event)`
   - 用法 `/voice 文本` 或 `/念 文本`；支持风格前缀 `[温柔]…` / `【温柔】…` / `#温柔 …`
   - 流程：解析风格 → （已是日语则跳过翻译）翻译成日语 → `_synthesize_all` 分段合成 → `yield event.chain_result([Record(...)])`
3. **LLM 工具** `coeiroink_speak` → `_llm_tool_handler(event, text, style=None)`
   - 参数：`text`（必填）、`style`（可选，本次朗读临时风格）
   - （已是日语则跳过翻译）翻译 → `_synthesize_all` 分段合成 → `event.send(MessageChain([Record(...)]))`，返回「已发送语音。」
4. **Web UI Pages**：`pages/settings/index.html`（单文件自包含，双层导航：一级标签「管理面板 / 安装向导」，管理面板内二级菜单「监控 / 设置」——监控含运行状态与引擎日志卡片，设置含全部配置卡片与保存/重新加载；通过 `window.AstrBotPluginPage` bridge 与后端通信，支持暗色主题）；**因 AstrBot 页面 iframe 沙箱（无 allow-top-navigation）与 bridge 均不支持跨页跳转，两个功能合并为单页标签切换**；启动引擎就绪后自动切回管理面板标签；页面标题经 `.astrbot-plugin/i18n/*.json` 的 `pages.settings.title` 本地化（zh-CN：管理面板）

---

## 三、三种风格 / 音色对照

| 中文名 | styleId | 日文名 | 引擎音色 ID | 模型目录 | 磁盘（单风格） | 内存占用 |
| --- | --- | --- | --- | --- | --- | --- |
| 平静 | `0` | れいせい | `styleId=0` | `speaker_info/tsukuyomichan-2.0.0/model/0/` | 356 MB（`100epoch.pth` 373 MB + `config.yaml`） | 懒加载常驻，单份约 **2G 以上**（见下注） |
| 温柔 | `5` | おしとやか | `styleId=5` | `speaker_info/tsukuyomichan-2.0.0/model/5/` | 356 MB | 同上 |
| 充满活力 | `6` | げんき | `styleId=6` | `speaker_info/tsukuyomichan-2.0.0/model/6/` | 356 MB | 同上 |

- 三者同属说话人 `つくよみちゃん`（`speakerUuid=3c37646f-3881-5374-2a83-149267990abc`），由 `metas.json` 定义三套 `styles`。
- **内存代价说明**：源码注释、`README.md` 与 `_conf_schema.json` 的 `style_id` hint 均写明「引擎对每个风格是**懒加载且常驻内存**，单份风格约需 **2G 以上**，同时使用多种风格会明显叠加占用，内存小的机器建议只用一种」。
  - 实测参考（2026-10-08 同机 A/B，冷启动引擎）：**不带** `MALLOC_TRIM_THRESHOLD_` 时，刚启动 RSS ≈ 1990 MB，合成一次 style 5 后稳定 ≈ 2046 MB；**带** `MALLOC_TRIM_THRESHOLD_=65536` 时，刚启动 ≈ 1566 MB，style 5 合成后稳定 ≈ 1318 MB（省约 730 MB）。当前引擎进程（PID `1603350`，优化参数重启，已预加载风格 5）RSS 约 **1338 MB**。「2G 以上」为文档/配置提示的经验值，实际常驻与峰值随文本长度、线程数波动。**待确认**：三风格全加载时的确切峰值内存未在本机实测。
- 本机 `model/0` 的 `100epoch.pth` 时间戳为 2022-04-05，`model/5`、`model/6` 为 2026-10-07（后期补齐），三者大小一致。

---

## 四、配置项完整清单（来自 `_conf_schema.json`）

| 键名 | 类型 | 默认值 | 作用 |
| --- | --- | --- | --- |
| `enabled` | bool | `true` | 启用插件；关闭后不参与自动触发与命令 |
| `enable_webui` | bool | `false` | 启用内置 Web UI 管理面板（开启后其余配置项由 Web UI 管理，见下方说明） |
| `mode` | string | `on_demand` | 触发模式：`always_translate`（总是翻译朗读）/ `on_demand`（仅命令/工具触发）/ `probabilistic`（概率）/ `japanese_only`（仅日语朗读） |
| `probability` | float | `0.3` | 仅 `probabilistic` 模式生效，0~1 |
| `speedScale` | float | `1.0` | 语速，1.0 原速 |
| `enable_mp3` | bool | `true` | 开：ffmpeg 转 mp3 发送；关：直接发 wav |
| `keep_temp_files` | bool | `false` | 开：保留中间 wav 便于排障；关：转码后删除 |
| `auto_start_engine` | bool | `true` | 引擎未运行时自动后台拉起 |
| `enable_llm_tool` | bool | `false` | 向对话模型注册 `coeiroink_speak` 工具（需重载插件生效） |
| `engine_start_timeout` | int | `90` | 拉起引擎后等待就绪的最长时间（秒），首次冷启动约 40~60s |
| `min_available_memory_mb` | int | `500` | **内存门槛**：合成前检查 `MemAvailable`，低于该值则跳过本次语音；0 表示不检查 |
| `max_text_length` | int | `200` | 单次合成最大字数 |
| `skip_if_too_long` | bool | `true` | 开：超长整段跳过；关：按句分段朗读（最多 6 段，段间串行合成）（P1-2） |
| `max_concurrent_synth` | int | `1` | **并发合成上限**（P0-2）；内存小的机器保持 1 |
| `max_synth_queue` | int | `2` | 排队上限：在途数 > 并发上限 + 本值时跳过本次合成（1.7.0） |
| `max_synth_segments` | int | `6` | 长文本分段朗读的最大段数（1.7.0，原为硬编码） |
| `synth_cache_size` | int | `32` | 合成结果 LRU 缓存条数（1.8.0；0=禁用，相同文本直接复用音频） |
| `phrase_cache_enabled` | bool | `true` | 句级缓存总开关（1.9.0） |
| `phrase_cache_size` | int | `128` | 句级缓存条数（1.9.0；0=禁用） |
| `phrase_split_mode` | string | `sentence` | 句级切分粒度：`sentence` / `clause`（1.9.0） |
| `phrase_key_ignore_punct` | bool | `true` | 句级键忽略结尾标点（1.9.0） |
| `phrase_pool_enabled` | bool | `true` | 长期保留池总开关（1.10.0） |
| `phrase_pool_min_hits` | int | `3` | 晋级门槛（命中次数，1.10.0） |
| `phrase_pool_max_entries` | int | `64` | 池容量上限（1.10.0） |
| `phrase_pool_retention_days` | int | `30` | 池条目保留天数（0=永久，1.10.0） |
| `phrase_pool_llm_review` | bool | `true` | 复审是否使用 LLM（失败回退规则，1.10.0） |
| `phrase_pool_review_hours` | int | `24` | 复审周期（小时，1.10.0） |
| `allow_style_override` | bool | `true` | 是否允许单次风格切换；关闭后只用默认风格（1.7.0） |
| `style_switch_min_free_mb` | int | `800` | 切换到未加载风格前的最低可用内存（1.7.0） |
| `allow_remote_engine` | bool | `false` | 允许向非回环地址的引擎发送文本（P2-5）；默认禁止防外发 |
| `api_base` | string | `http://127.0.0.1:50032` | 引擎 HTTP 地址；默认仅允许回环地址 |
| `speaker_uuid` | string | `3c37646f-3881-5374-2a83-149267990abc` | 说话人 UUID（つくよみちゃん） |
| `style_id` | string | `"0"` | 默认风格；可填 `0/5/6` 或中文名/日文名；可被单次请求覆盖 |
| `output_sampling_rate` | int | `0` | 0 表示用引擎默认 |
| `engine_dir` | string | `""` | 引擎根目录；留空读环境变量 `COEIROINK_ENGINE_DIR`（解析链见 2.1） |
| `engine_bin` | string | `""` | 引擎可执行文件；留空自动取 `<engine_dir>/engine/engine`（Windows 为 `engine.exe`） |
| `engine_log` | string | `""` | 自动拉起引擎时的 stdout/stderr 重定向目标；留空自动取 `<engine_dir>/engine.log` |
| `ffmpeg_path` | string | `""` | wav→mp3 用的 ffmpeg；留空自动在 PATH 中查找 |
| `translate_system_prompt` | string | 见下 | 翻译成日语时使用的系统提示词 |

> **Web UI 开关**：`enable_webui`（bool，默认 `false`）。开启后请用插件 Pages 管理全部配置；
> schema 中除 5 个**基础引导项**（`enabled`、`enable_webui`、`mode`、`style_id`、`engine_dir`）
> 外的所有配置项都标记了 `"invisible": true`——不开 Web UI 也能完成最小配置闭环（P0-1），
> 其余项在面板上隐藏（值仍保存在配置文件中，由 Web UI 读写）。

`translate_system_prompt` 默认值：
> 你是一个专业的翻译引擎。请把用户给出的文本翻译成自然、口语化的日语。只输出日语译文本身，不要输出任何解释、罗马音、拼音、引号、标注或额外文字。如果原文已经是日语，则原样输出。

> 与「语音触发方式」相关的键：`mode`（触发方式）、`enable_llm_tool`（模型主动调用）、`probability`（概率）；
> 与「翻译成日语再合成」相关的键：`translate_system_prompt`、`mode`（`japanese_only` 时不做翻译）。
>
> **注意 `style_id` 类型**：schema 中声明为 `string`（下拉 `0/5/6`），`main.py` 的 `normalize_style` 同时接受字符串与整数。

---

## 五、外部依赖

### 5.1 引擎安装与启动
> 本节为**本机部署实录**；插件本身不内置这些路径（通用解析链见 2.1/第七节），
> 这些值来自本机配置文件 `/root/data/config/astrbot_plugin_coeiroink_tts_config.json`。
- **安装路径**：`/root/COEIROINK_LINUX_CPU_v.2.13.0`（CPU 版 v2.13.0，约 2.7 GB）
  - 该目录为 Electron 打包产物：`coeiroink-v2`（GUI 主程序）、`COEIROINKv2`（启动脚本，内容为 `./coeiroink-v2`）、`resources/app.asar` 等。
  - **插件不使用 GUI**，而是直接启动引擎二进制：`<engine_dir>/engine/engine`（Windows 为 `engine.exe`）。
  - 另有下载缓存副本：`/root/downloads/coeiroink/v2.13.0/COEIROINK_LINUX_CPU_v.2.13.0/`（同一版本）。
- **启动命令**：`cd /root/COEIROINK_LINUX_CPU_v.2.13.0 && ./engine/engine`（插件由 `_launch_engine` 自动执行）
- **端口**：`127.0.0.1:50032`（仅本机监听，当前由进程 `engine` PID `1603350` 占用）
- **设备**：`engine/engine_settings.json` 为 `{"device": "cpu"}`，纯 CPU 推理
- **接口**：`GET /v1/speakers`（探活 / 说话人列表）、`POST /v1/predict`（合成）

### 5.2 模型目录布局
```
/root/COEIROINK_LINUX_CPU_v.2.13.0/speaker_info/          # 约 1.1 GB
└── tsukuyomichan-2.0.0/
    ├── metas.json                 # speakerName / speakerUuid / styles(0,5,6) / version
    ├── model/
    │   ├── 0/{100epoch.pth, config.yaml}   # 平静 れいせい
    │   ├── 5/{100epoch.pth, config.yaml}   # 温柔 おしとやか
    │   └── 6/{100epoch.pth, config.yaml}   # 充满活力 げんき
    ├── icons/{0,5,6}.png
    ├── voice_samples/*.wav
    ├── portrait.png / policy.md / LICENSE.txt
```
- 每个 `model/<styleId>/` 内为 `100epoch.pth`（约 373 MB）+ `config.yaml`（约 7.7 KB）。

### 5.3 如何只加载单风格
- 引擎对每个 `styleId` 是**懒加载**：只有第一次用某 `styleId` 调用 `/v1/predict` 时才会把该风格模型载入内存，之后常驻。
- **因此「只加载单风格」= 全局只用一个 `style_id`**：
  1. 把配置项 `style_id` 固定为一种（如 `"5"`）；
  2. 不要在用命令 `[风格]` / LLM 工具 `style` 参数时切换到别种风格。
- 若确实要省内存，也可在 `speaker_info/tsukuyomichan-2.0.0/model/` 下只保留所需的那一个风格目录（**待确认**：引擎在缺少某风格目录时的具体报错行为未在本机验证；`metas.json` 仍会列出三套 styles）。

### 5.4 其他系统依赖
- **ffmpeg**：`/usr/bin/ffmpeg`、`/usr/bin/ffprobe`（`enable_mp3=true` 或自测脚本需要）
- **Python 依赖**：`httpx`（导入自 `astrbot.api` 之外的第三方库，当前 AstrBot 运行环境已自带 `httpx 0.28.1`）；其余 `asyncio / os / re / shutil / subprocess / time / uuid / pathlib` 均为标准库。**没有 `requirements.txt`**，即插件未声明额外 pip 依赖。

---

## 六、运行时约束与已知坑

1. **内存门槛 500 MB**：`min_available_memory_mb` 默认 `500`。`synthesize_with_recovery` 合成前读 `/proc/meminfo` 的 `MemAvailable`，低于阈值直接抛 `RuntimeError` 跳过本次语音，避免把引擎和服务器一起逼进 OOM。
2. **单风格内存锁（懒加载常驻）**：每个风格首次合成后常驻内存，单份约 2G+（文档值）。多种风格并用会叠加占用，小内存机器建议只用一种。
3. **OOM 风险**：本机 `MemTotal≈3.8G`（`4005688 kB`）、`Swap 2G`。`_launch_engine` 在启动引擎前设置 `OMP_NUM_THREADS / MKL_NUM_THREADS / OPENBLAS_NUM_THREADS / NUMEXPR_NUM_THREADS / VECLIB_MAXIMUM_THREADS = 1` 与 `MALLOC_ARENA_MAX=2`、`MALLOC_TRIM_THRESHOLD_=65536`（钉住 glibc trim 阈值，防止其随运行自动调大，让引擎在合成间隙更积极地把空闲堆内存还给 OS）、`PYTHONUNBUFFERED=1`，以压低常驻与峰值。故意并发多风格合成仍可能触发 OOM。
4. **并发合成**：模块级 `_ENGINE_START_LOCK` + `ensure_engine_running` 内双重检查，避免并发重复拉起引擎；`stream_wav_to_path` 对引擎热加载断连做一次重试；`synthesize_with_recovery` 在失败时**先使探活缓存失效**再重启引擎并重试一次。**合成本身经 `get_synth_semaphore(max_concurrent_synth)` 限流**（默认串行），避免并发请求叠加引擎内存峰值（P0-2）。
5. **冷启动等待**：`engine_start_timeout` 默认 90s。引擎 HTTP 服务先就绪但模型仍在加载，探活必须以 `/v1/speakers` 返回非空列表为准，否则会误判「已就绪」并被 `/v1/predict` 断连。
6. **日志落盘**：
   - 引擎日志：`/root/downloads/coeiroink/engine.log`（`_launch_engine` 以 `ab` 追加写入；当前约 20 KB）
   - AstrBot 应用日志：`cmd_config.json` 中 `log_file_enable=false`、`log_file_path=logs/astrbot.log`，即**当前未落盘**，插件日志经 `astrbot.api.logger` 输出到进程 stdout/stderr（`/dev/null` 与 socket）。排查主要依赖 `engine.log` 与自测脚本。
7. **临时文件**：合成产物写在 `get_astrbot_temp_path()`（本机 `/root/data/temp`），命名 `coeiroink_<hex>.wav/.mp3`；`keep_temp_files=false` 时转码后删除 wav；插件启动 60 秒后每小时清理一次超过 2 小时的 mp3/wav（P0-3）。
8. **热重载**：TTS Provider 适配器用幂等注册逻辑，避免二次导入抛「提供商适配器已注册」。LLM 工具用 `_llm_tools_registered` 幂等标记。
9. **命令 alias 小注**：`@filter.command("voice", alias={"念", "voice"})` 中 `alias` 集合含 `voice` 与主名重复。**待确认**：AstrBot 对重复 alias 的处理（是否告警/忽略），从代码看不影响 `/voice` 与 `/念` 使用。
10. **排队上限与风格保护（1.7.0）**：`max_synth_queue` 控制在途合成数（超出即跳过，防止延迟堆积）；`allow_style_override` / `style_switch_min_free_mb` 控制单次风格切换——引擎对风格懒加载且常驻、无卸载接口，**重启引擎是释放已加载风格内存的唯一途径**（Web UI 提供「重启引擎」，仅对插件拉起的进程可用）。
11. **回环校验（P2-5）**：`api_base` 默认必须是回环地址（`127.x`/`localhost`/`::1`），否则合成被阻止并告警；远程引擎需开启 `allow_remote_engine`。
12. **AstrBot 版本（P1-1）**：`astrbot_version: ">=4.24.5,<5"`（插件 Pages 最早可用版本，PR #5940 于 2026-05-03 合入、v4.24.5 发布）。低于该版本时 `astrbot.api.web` 容错导入自动停用 Web UI，核心链路不受影响。

---

## 七、对外接口 / 事件注册方式

插件由 AstrBot 识别与调用的三类接口：

1. **插件类**：`class CoeiroinkTTSPlugin(Star)`，经 `metadata.yaml` 的 `name: astrbot_plugin_coeiroink_tts` 与 AstrBot 插件目录（`/root/data/plugins/`）被加载；`__init__(context, config)` 由框架注入 `Context` 与配置。
2. **事件钩子**：
   - `@filter.on_decorating_result()` → 在消息结果装饰阶段改链（自动触发朗读）。
   - `@filter.command("voice", alias={"念", "voice"})` → 注册 `/voice`（及 `/念`）命令。
3. **LLM 工具**：`enable_llm_tool` 开启时，`context.add_llm_tools(FunctionTool(name="coeiroink_speak", ...))` 把工具注册给对话模型。
4. **TTS Provider 适配器**：`register_provider_adapter(provider_type_name="coeiroink_tts", provider_type=ProviderType.TEXT_TO_SPEECH, ...)(CoeiroinkTTSProvider)`，供 AstrBot 统一 TTS 链路调用。
   - **注意**：当前 `cmd_config.json` 的 `provider_tts_settings.enable=false`、`provider_id=""`，即 AstrBot 全局 TTS 开关是关闭的；插件目前实际靠 `on_decorating_result` 钩子与命令/工具发语音。Provider 适配器已注册可被选用，但未在 provider 列表实例化。
5. **渠道支持**：本插件可以联动 **NapCat** 使用 QQ 语音；其他渠道暂未测试。如在其他渠道测试过，请提供测试环境及日志，提交报告到 GitHub：https://github.com/Aurora-Lucas/astrbot_plugin_coeiroink_tts/issues
6. **Web UI（插件 Pages）**：`pages/settings/index.html`（双标签：管理面板 + 安装向导）由 Dashboard 以受限 iframe 加载（`sandbox="allow-scripts allow-forms allow-downloads"`，无跨页导航能力，故采用单页标签），页面脚本经 `window.AstrBotPluginPage` bridge 调用后端；bridge 端点（不含插件名）由 Dashboard 转发到 `/api/v1/plugins/extensions/<plugin_name>/…`，再匹配 `context.register_web_api` 注册的路由（见 2.7）。启用开关为配置项 `enable_webui`；页面标题由 `.astrbot-plugin/i18n` 提供。

---

## 八、相关文件与运行环境速查

- 插件目录：`/root/data/plugins/astrbot_plugin_coeiroink_tts/`
- 运行配置：`/root/data/config/astrbot_plugin_coeiroink_tts_config.json`（另有备份 `…json.bak-20261007235854`）
  - 当前值：`mode=always_translate`、`style_id="5"`（温柔）
- AstrBot 主配置：`/root/data/cmd_config.json`
- AstrBot 运行环境：`/root/.local/share/uv/tools/astrbot/bin/python`（Python 3.12.13）
- 引擎：`/root/COEIROINK_LINUX_CPU_v.2.13.0`（`engine/engine`，CPU，`127.0.0.1:50032`）
- 引擎日志：`/root/downloads/coeiroink/engine.log`
- 自测脚本：`/root/data/plugins/astrbot_plugin_coeiroink_tts/_selftest_synth.py`

自测脚本用法（脚本内 `--help` 给出全部参数）：
```bash
/root/.local/share/uv/tools/astrbot/bin/python \
  /root/data/plugins/astrbot_plugin_coeiroink_tts/_selftest_synth.py "日文文本"
```
通用默认值：配置自动读 `<插件目录>/../config/astrbot_plugin_coeiroink_tts_config.json`
（AstrBot 标准布局，可用 `--config` 或环境变量 `ASTRBOT_PLUGIN_CONFIG` 覆盖）；
输出目录默认 `<插件目录>/selftest_out`（可用 `SELFTEST_OUT_DIR` 覆盖）；
ffprobe 自动从 PATH 查找。合成风格沿用运行配置的 `style_id`（当前为 `5`），读不到配置时回退 `0`。
