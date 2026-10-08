# 改进路线图（ROADMAP）

> 状态：**待实施**。本文件是一次代码评审的产出，全部结论基于源码实测（未动手修改）。
> 版本基线：`1.5.4`。实施时请同步更新 `CHANGELOG.md`、`metadata.yaml` 版本号，以及 `README.md` / `STRUCTURE.md` 的相关描述。
> 实施完成后，请把对应条目从本文件移除或标记为已完成。

## 项目背景（接手须知）

- 插件：`astrbot_plugin_coeiroink_tts`（显示名 Tsukuyomi-chan COEIROINK 日语语音），AstrBot 插件，本地 COEIROINK 引擎合成日语语音。
- 许可：CC BY-NC 4.0（不得商用，保留署名 Aurora & deepseek）。
- 本机环境：Linux，内存约 3.8G；引擎 `127.0.0.1:50032`；AstrBot 4.28.1（Python 3.12，`/root/.local/share/uv/tools/astrbot/bin/python`）。
- 验证手段：
  - `python3 -m py_compile main.py _selftest_synth.py`
  - `_selftest_synth.py`（真实调用引擎合成；输出目录可用 `SELFTEST_OUT_DIR` 覆盖）
  - Web UI 后端可用 `object.__new__(CoeiroinkTTSPlugin)` + stub context 单测（见现成做法）
- 推送：`export GIT_SSH_COMMAND="ssh -o UserKnownHostsFile=/tmp/github_known_hosts -o StrictHostKeyChecking=accept-new"` 后 `git push origin main`。

---

## P0 — 真实缺陷（建议优先）

### P0-1 Web UI 关闭时配置面板被"锁死"（配置自举死锁）

- **现象**：`_conf_schema.json` 共 22 项，其中仅 `enabled`、`enable_webui` 可见，其余 20 项（含 `engine_dir` / `mode` / `style_id`）均 `"invisible": true`。
- **影响**：用户若未开启 Web UI，则**无法从配置面板设置引擎目录**，只能手工编辑 `data/config/astrbot_plugin_coeiroink_tts_config.json`；若其 AstrBot 版本不支持 Pages，则完全无图形化配置入口。
- **方案（二选一）**：
  1. 保留一组"基础引导项"可见：`enabled`、`enable_webui`、`engine_dir`、`mode`、`style_id`，其余隐藏；或
  2. 仅在 Web UI 可用时隐藏（AstrBot schema 不支持按另一项取值动态显隐，故只能用方案 1 或文档强提示）。
- **验收**：全新环境下不开 Web UI 也能从面板完成"填引擎目录 → 合成成功"的最小闭环。

### P0-2 无并发合成限制（内存峰值叠加风险）

- **现象**：`main.py` 中无任何 `asyncio.Semaphore`；`_synthesize` 可被多条消息并发调用。
- **影响**：并发打 `/v1/predict` 时引擎侧缓冲与推理峰值叠加；本机单风格常驻已约 1.3G，容易触发 OOM（README 已警告 OOM 风险），且失败会走"重启引擎"重试，代价大。
- **方案**：加模块级 `asyncio.Semaphore`（默认 1），包裹 `synthesize_with_recovery` 调用；可加配置项 `max_concurrent_synth`（int，默认 1）。
- **验收**：并发发起 3 次合成时，日志/引擎日志显示串行执行，内存峰值不叠加。

### P0-3 合成产物 mp3 从不清理（磁盘无限增长）

- **现象**：wav 在转码后会被 `_safe_unlink` 删除，但最终 `.mp3` 一直留在 AstrBot 临时目录。
- **实测**：`/root/data/temp/coeiroink_*` 已累积 21 个文件、约 2.8MB（会随运行时间持续增长）。
- **方案（任一）**：
  1. 后台定时任务（如每小时）清理 N 小时前的 `coeiroink_*`（注意：发送后平台可能仍需短暂持有文件，建议保留 1~2 小时）；或
  2. 记录本次生成的文件，发送完成后延迟删除。
- **验收**：连续运行后临时目录文件数保持稳定，且不影响语音消息正常送达。

---

## P1 — 兼容性与体验

### P1-1 `astrbot_version: ">=4.28.1"` 过严 + web 导入无容错

- **现象**：约束取"实测版本"，会把大量可用的老版本用户挡在门外；且 `main.py` 顶部 `from astrbot.api.web import error_response, json_response, request` 一旦模块不存在会**整体加载失败**。
- **方案**：
  1. 查明 `context.register_web_api` 与插件 Pages 的最早可用 AstrBot 版本（可查官方 GitHub 历史/发布说明），据此放宽 `astrbot_version`；
  2. 把 web 导入改为 `try/except ImportError`，缺失时 `_register_web_apis()` 记一条提示日志并跳过（核心合成链路不依赖它）。
- **验收**：在缺少 `astrbot.api.web` 的版本上插件可正常加载并合成（仅无 Web UI）；`astrbot_version` 与实际最低可用版本一致。

### P1-2 长文本直接跳过（长回复无语音）

- **现象**：`_synthesize` 中超过 `max_text_length`（默认 200）时，若 `skip_if_too_long=true` 则**整段跳过**。
- **方案**：按日文句末标点（`。！？!?`）切分为多段，逐段合成后按顺序发送多条 `Record`（或合并音频）；保留 `skip_if_too_long` 作为兜底开关。注意与 P0-2 的并发限制配合，避免一次性并发过多请求。
- **验收**：400 字回复能完整朗读，不再静默。

### P1-3 `probability` 未夹取到 0~1

- **现象**：`probabilistic` 模式直接比较 `random.random() >= float(cfg)`；配置 >1 永不触发、<0 必触发。
- **方案**：读取时 `max(0.0, min(1.0, value))`，非法值告警并回退默认。
- **验收**：配置 2 时按 1.0 处理、-1 时按 0 处理，均有日志。

### P1-4 macOS 无内存门槛检查

- **现象**：`available_memory_mb()` 在非 Linux/Windows 平台返回 `None` → `min_available_memory_mb` 保护失效。
- **方案**：macOS 走 `vm_stat`（解析 free+inactive 页 × 页大小）或 `sysctl hw.memsize` 估算；失败仍返回 `None` 且不阻断合成。
- **验收**：macOS 上状态接口能返回可用内存数值。

---

## P2 — 工程质量

### P2-1 无自动化测试与 CI

- **方案**：加 `tests/`（pytest）覆盖纯函数：`normalize_style`、`extract_style_prefix`、`clean_text`、`is_japanese`、`resolve_engine_dir/bin/log/ffmpeg` 解析链；加 `.github/workflows/ci.yml`（ruff + `py_compile` + pytest，Python 3.12）。
- **验收**：CI 在 GitHub Actions 全绿；核心解析逻辑有回归保护。

### P2-2 代码风格未过 ruff

- **方案**：按官方开发原则执行 `ruff format` + `ruff check --fix`，修正后重跑合成自测。
- **验收**：ruff 无报错；行为不变（对比改动前后合成产物与日志）。

### P2-3 市场页面缺截图

- **方案**：为 Web UI 双标签页面（管理面板 / 安装向导）与运行状态卡片截 1~2 张图，放入仓库（如 `docs/screenshots/`）并在 `README.md` 中展示。
- **验收**：README 顶部展示真实界面截图（注意仓库体积，压缩后建议单张 < 300KB）。

### P2-4 logo 体积优化

- **现象**：`logo.png` 为 600×600 / 119KB；官方推荐 256×256。
- **方案**：压缩并缩放为 256×256（保留 1:1），同步替换 `pages/settings/assets/logo.png`。
- **验收**：仓库与插件包体积下降，视觉无可见损失。

### P2-5 安全加固：`api_base` 未校验

- **现象**：用户可把 `api_base` 填成公网地址，回复文本会被外发。
- **方案**：默认只允许回环地址（`127.0.0.1` / `localhost` / `::1`），非回环时告警并要求显式开启新配置项（如 `allow_remote_engine`）；`_conf_schema.json` 的 hint 同步说明。
- **验收**：填公网地址时不静默外发，有明确告警与开关。

---

## 实施顺序建议

1. P0-1 → P0-2 → P0-3（一轮改动，一次提交，逐项验证）
2. P1-1 → P1-2 → P1-3 → P1-4
3. P2-1 / P2-2（可与上面并行）→ P2-3 / P2-4 / P2-5

每批改动完成后：`py_compile` → 真实合成自测 → 更新 `CHANGELOG.md` + 版本号 → 提交推送。
