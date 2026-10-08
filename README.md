# Tsukuyomi-chan COEIROINK 日语语音（AstrBot 插件）

<p align="center"><img src="logo.png" alt="Tsukuyomi-chan" width="128"></p>

> 🎙️ **你是否苦恼使用远程 TTS 泄露信息？你是否苦恼使用远程 TTS 需要额外付费？**
> 快来试试本插件进行**本地 TTS 部署**吧！本插件拥有较为良好的音频质量以及便捷的图形化设置，
> 同时，由于是本地 TTS 服务所以**完全免费**！TTS 引擎本身的安装还可以交给你的 agent 来完成。

使用本地 **COEIROINK** CPU 引擎，把 AstrBot 的回复文本（必要时先由大模型翻译成日语）
合成为语音，并以语音消息发出。

支持四种触发模式：总是翻译朗读 / 按需触发 / 概率触发 / 仅日语朗读；
并支持 **三种情绪风格自由选择**（平静 / 温柔 / 充满活力）。

---

> ### 🔊 音源与软件致谢
> 本插件所使用的音源是由**夢前黎**制作的**月读酱**（Tsukuyomi-chan），这是一个免费使用的音源，
> 请大家多多支持作者夢前黎。官网：https://tyc.rei-yumesaki.net/
>
> 本插件所使用的软件为 **COEIROINK**，由**シロワニさん**制作，这是一个免费的 TTS 软件，
> 请大家多多支持シロワニさん。官网以及下载：https://coeiroink.com/
>
> 本插件所使用的 Logo 由**ノザラシ**制作。获取链接：https://seiga.nicovideo.jp/seiga/im11798588

---

> ### 📢 渠道支持
> 本插件可以联动 **NapCat** 使用 QQ 语音；其他渠道暂未测试。
> 如您在其他渠道测试过，请提供测试环境及日志，提交报告到 GitHub：
> https://github.com/Aurora-Lucas/astrbot_plugin_coeiroink_tts/issues

---

## 一、三种风格对照表

| 中文名 | styleId | 日文名 | 说明 |
| --- | --- | --- | --- |
| 平静 | `0` | れいせい | 沉稳平缓，中性语气 |
| 温柔 | `5` | おしとやか | 柔和温婉，轻声细语 |
| 充满活力 | `6` | げんき | 元气活泼，情绪饱满 |

> 归一化规则：配置或调用时既可以填 `styleId` 数字（`0` / `5` / `6`），
> 也可以填中文名（`平静` / `温柔` / `充满活力`）或日文名（`れいせい` / `おしとやか` / `げんき`）；
> 此外还兼容罗马音与英文别名（`reisei` / `oshitoyaka` / `genki`、`calm` / `gentle` / `lively` 等）。
> 匹配时会自动忽略空格与大小写。

---

## 二、如何配置默认风格

在 Web UI 管理面板（见[第七节](#七web-ui-图形化管理)）的「默认风格」项，或在插件配置页的「默认风格」项，
或配置文件 `data/config/astrbot_plugin_coeiroink_tts_config.json` 的 `style_id` 字段中选择/填写：

- 下拉可选：`平静（れいせい）` / `温柔（おしとやか）` / `充满活力（げんき）`
- 也可直接填中文名或日文名，例如 `"style_id": "温柔"`

该值是**默认风格**，会在没有单次指定时使用，并且**可以被单次请求临时覆盖**。

---

## 三、如何按单次请求切换风格

默认风格之上，调用方可以针对某一次朗读临时指定情绪，覆盖默认值（不改变配置）：

### 1. 用户指令 `/voice`

在文本前用 `[风格]`、`【风格】` 或 `#风格 ` 指定即可，其余用法与原来完全一致：

```
/voice おはようございます                        # 使用默认风格
/voice [温柔] おはようございます                  # 本次用「温柔」
/voice 【充满活力】今日も頑張ろう！               # 本次用「充满活力」
/voice #平静 落ち着いて話します                   # 本次用「平静」
```

### 2. 对话模型函数工具 `coeiroink_speak`

当插件开启「启用 LLM 工具」后，模型可调用 `coeiroink_speak`，并可选地传入 `style` 参数：

```json
{
  "text": "おはようございます",
  "style": "充满活力"
}
```

`style` 可省略；省略时使用配置的默认风格。

---

## 四、非法值的行为（可预期）

- **配置层**：若默认风格填了无法识别的值，插件会打印明确告警，并回退到默认风格 `0`（平静），
  不会静默丢弃。
- **单次请求**：若命令或工具显式指定了无法识别的风格，会直接返回友好提示并列出可用风格，
  不会悄悄改用别的风格。

告警与提示中都会列出全部可用风格，例如：
`0=平静（れいせい）、5=温柔（おしとやか）、6=充满活力（げんき）`。

---

## 五、内存提示（重要）

> ⚠️ 引擎对**每个风格是懒加载且常驻内存**的，单份风格约需 **2G 以上**内存。
> 同时使用多种风格会显著叠加内存占用。
> **内存较小的机器建议只使用一种风格**（配置好默认风格，少用或不用单次覆盖）。

COEIROINK 的推理默认会把线程数与内存池开得很大，插件在拉起引擎时会限制
`OMP/MKL/OPENBLAS` 等线程数、设置 `MALLOC_ARENA_MAX`，并钉住
`MALLOC_TRIM_THRESHOLD_`，让引擎在合成间隙更积极地把空闲堆内存还给系统，
以压低常驻与峰值内存；合成前也会检查 `MemAvailable`，低于阈值则跳过本次语音，
避免触发系统 OOM。

此外，插件在合成时会把引擎返回的音频**流式落盘**（不把整段 wav 缓冲进内存），
探活请求只做轻量判断并带 15 秒缓存（不再反复解析引擎返回的 1.2MB 图标 JSON），
并复用 HTTP 连接池；当文本本身已是日语时，会自动跳过 LLM 翻译环节。
合成默认**串行执行**（`max_concurrent_synth`，防止并发内存峰值叠加），
超出排队上限（`max_synth_queue`）的请求会直接跳过而不是无限等待；
插件会**定时清理临时目录中超过 2 小时的音频文件**，避免磁盘无限增长；
单次风格切换（`/voice [风格]`、LLM 工具 `style`）也会先检查可用内存
（`style_switch_min_free_mb`），内存不足时拒绝切换，避免多加载一份常驻风格导致 OOM。

---

## 六、主要配置项

| 配置项 | 说明 |
| --- | --- |
| `enabled` | 启用插件 |
| `enable_webui` | 是否启用 Web UI 图形化管理（开启后其余配置项由 Web UI 管理） |
| `mode` | 触发模式：`always_translate` / `on_demand` / `probabilistic` / `japanese_only` |
| `style_id` | 默认风格（平静/温柔/充满活力，或 0/5/6，或日文名） |
| `speedScale` | 语速，1.0 为原速 |
| `speaker_uuid` | 说话人 UUID |
| `enable_llm_tool` | 是否向对话模型注册 `coeiroink_speak` 工具（需重载插件生效） |
| `min_available_memory_mb` | 合成前最低可用内存阈值，低于则跳过 |
| `max_text_length` | 单次合成最大字数 |
| `skip_if_too_long` | 超长文本处理：开启则整段跳过；关闭则按句分段朗读（最多 `max_synth_segments` 段） |
| `max_concurrent_synth` | 最大并发合成数（默认 1；内存小的机器建议保持 1） |
| `max_synth_queue` | 合成排队上限（默认 2；在途数超过「并发上限 + 该值」的请求直接跳过） |
| `max_synth_segments` | 长文本分段朗读的最大段数（默认 6） |
| `allow_style_override` | 是否允许单次风格切换（关闭后只用默认风格，最省内存） |
| `style_switch_min_free_mb` | 切换到未加载风格前的最低可用内存（默认 800MB，不足则拒绝） |
| `allow_remote_engine` | 允许向非回环地址的引擎发送文本（默认禁止，防止回复内容外发） |
| `auto_start_engine` | 引擎未运行时自动后台拉起 |
| `engine_dir` | 引擎根目录（留空则读环境变量 `COEIROINK_ENGINE_DIR`） |
| `engine_bin` | 引擎可执行文件（留空自动取 `<engine_dir>/engine/engine`，Windows 为 `engine.exe`） |
| `engine_log` | 引擎日志路径（留空自动取 `<engine_dir>/engine.log`） |
| `ffmpeg_path` | ffmpeg 路径（留空自动在系统 PATH 中查找） |

---

## 七、Web UI 图形化管理

插件内置 **Web UI 管理面板**（AstrBot 插件 Pages），提供图形化配置、运行状态监控与测试合成。

### 开启方式

1. 在 AstrBot 插件管理页找到本插件，打开配置面板，把「启用 Web UI 管理」打开（或直接编辑
   `data/config/astrbot_plugin_coeiroink_tts_config.json` 设 `"enable_webui": true`）；
2. **重载插件**；
3. 进入插件详情页 → 打开「Pages」中的管理面板。

> 开启 Web UI 后，其余配置项会从原配置面板隐藏，统一由 Web UI 管理。
> 原配置面板保留「启用插件」「启用 Web UI 管理」「触发模式」「默认风格」「引擎目录」
> 五个基础项，保证不开 Web UI 也能完成最小配置。

### 面板功能

插件 Pages 提供**一个双标签页面「管理面板」**（页面内可随时互切，无需跨页跳转）：

- **管理面板（默认标签）**：内含二级菜单「**监控** / **设置**」
  - **监控**：**运行状态**（插件开关、引擎探活、可用内存含阈值预警、当前风格、**已加载风格（常驻内存）**、
    ffmpeg 与引擎进程 PID）与**引擎日志**（面板内尾读引擎日志，最多 64KB）；并提供「刷新状态」「重启引擎」
    （释放已加载风格内存的唯一途径；外部启动的引擎会提示无法自动重启）「打开安装向导」按钮
  - **设置**：全部配置项分组管理（基本设置 / 引擎连接 / 内存与文本限制 / 安全 / 音频与翻译 / Web UI 开关），
    底部为「保存配置」「重新加载」按钮
- **安装向导（标签页）**：五步完成本地引擎部署——① 环境自检（系统/ffmpeg/内存/磁盘/引擎状态）
  ② 官方下载指引（Windows/Linux 链接与注意事项）③ 引擎目录校验与保存 ④ 一键后台启动引擎
  （自动轮询就绪状态，**就绪后自动返回管理面板标签**）⑤ **合成验证**（一键合成测试语音，可临时指定风格，
  位于本标签页；管理面板标签不再重复提供）。首次安装请从这里开始。

Web UI 后端接口（`context.register_web_api`，路由前缀为插件标识名）：
`GET/POST /config`、`GET /status`、`POST /test`、`GET /logs`（引擎日志尾部）、
`POST /engine_restart`（重启引擎）、`GET /install_info`（环境自检）、
`POST /install_check_path`（校验引擎目录）、`POST /install_launch_engine`（后台启动引擎）。
关闭 Web UI 开关后，页面仍可打开，
但会提示未启用，且测试合成、启动/重启引擎等写操作接口返回 403。

---

## 八、通用部署说明（不绑定具体机器）

插件**不内置任何机器相关路径**，所有环境相关项都按
「**配置项 → 环境变量 → 自动推导**」的顺序解析：

| 项目 | 配置项 | 环境变量 | 自动推导 |
| --- | --- | --- | --- |
| 引擎根目录 | `engine_dir` | `COEIROINK_ENGINE_DIR` | — |
| 引擎可执行文件 | `engine_bin` | `COEIROINK_ENGINE_BIN` | `<engine_dir>/engine/engine`（Windows：`engine.exe`） |
| 引擎日志 | `engine_log` | `COEIROINK_ENGINE_LOG` | `<engine_dir>/engine.log`（兜底 AstrBot 临时目录） |
| ffmpeg | `ffmpeg_path` | `COEIROINK_FFMPEG` | 系统 `PATH` 中的 `ffmpeg` |

- **未配置 `engine_dir` 时**：插件仍可调用「已经在运行」的引擎（通过 `api_base`），
  只是无法自动拉起；会在日志中给出明确的配置指引。
- **`engine_bin` 支持相对路径**（相对 `engine_dir`），例如填 `engine/engine`。
- **Windows**：引擎路径自动按 `engine.exe` 推导；拉起进程用 `DETACHED_PROCESS`
  脱离控制台；内存门槛检查改用 `GlobalMemoryStatusEx`（Linux 用 `/proc/meminfo`，
  macOS 用 `vm_stat`，其他平台跳过检查）。引擎启动时设置的 `OMP/MALLOC_*` 等
  环境变量是 Linux 内存优化项，在其他平台是无害空操作。
- **引擎 HTTP 地址**：默认 `http://127.0.0.1:50032`（COEIROINK 引擎默认端口）。
  **默认只允许回环地址**（防止回复文本外发）；如需远程引擎，请把 `api_base` 指向
  远程地址并开启 `allow_remote_engine`（注意：远程引擎无法自动拉起）。
- **AstrBot 版本**：要求 `>=4.24.5,<5`（插件 Pages 最早可用版本）。低于该版本时
  插件仍可加载（Web UI 自动停用，合成链路不受影响），安装时忽略版本警告即可。

---

## 九、开发者文档

插件目录树、核心类与函数、风格对照、完整配置清单、引擎依赖、运行时约束与对外接口等，
详见 **[STRUCTURE.md](./STRUCTURE.md)**（插件结构说明）；版本变更见 **[CHANGELOG.md](./CHANGELOG.md)**（更新日志）；
待实施的改进项见 **[ROADMAP.md](./ROADMAP.md)**（改进路线图）。

---

## 十、许可证

本插件代码以 **[CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/deed.zh-hans)**
（知识共享 署名-非商业性使用 4.0 国际）许可协议发布，全文见 [LICENSE](./LICENSE)。
使用时请保留作者署名（Aurora & deepseek），且**不得用于商业目的**。

插件引用的第三方资源按其原有条款使用：
音源「月读酱」（Tsukuyomi-chan）© 夢前黎、软件 COEIROINK © シロワニさん、
Logo © ノザラシ，相关许可与使用规则请以各自官网说明为准。
