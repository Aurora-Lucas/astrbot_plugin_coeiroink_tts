# 更新日志（Changelog）

## 1.7.1

- Web UI「管理面板」新增**二级菜单**：**监控**（运行状态 + 引擎日志）与**设置**（其余全部配置项与保存/重新加载按钮），功能增多后不再一屏堆叠
- 切入「监控」自动刷新状态与日志；页面首次加载即拉取日志

## 1.7.0

第二轮评审改进（按用户选定范围实施）：

**运行风险**
- 新增 `max_synth_queue`（默认 2）：在途合成数超过「并发上限 + 该值」时直接跳过本次语音，避免忙时请求无限排队、回复延迟持续增长
- 新增风格切换内存保护：`allow_style_override`（默认开）可彻底禁用单次风格切换；`style_switch_min_free_mb`（默认 800）在切换到**尚未加载**的风格前检查可用内存，不足则拒绝并提示（引擎对风格是懒加载且常驻，且无卸载接口）

**健壮性**
- 语音消息统一改用 AstrBot 官方 `Record.fromFileSystem()` 构造（生成 `file:///` URI + `path`），跨平台与含空格路径更稳

**可观测性 / 运维**
- Web UI 新增「引擎日志」卡片：`GET /logs` 尾读引擎日志（最多 64KB），可在面板内直接排障
- Web UI 新增「重启引擎」按钮：`POST /engine_restart` 重启**由插件拉起**的引擎（释放已加载风格内存的唯一途径）；外部启动的引擎会明确提示无法自动重启
- 运行状态卡新增「已加载风格（常驻内存）」与在途合成数展示

**可配置化**
- 新增 `max_synth_segments`（默认 6）：长文本分段朗读的段数上限由硬编码改为可配置

**其他**
- 新增 `tests/test_plugin_behavior.py`（12 个用例）：覆盖队列上限、风格保护、分段配置、临时清理、风格加载记录（累计 67 个用例）
- README/STRUCTURE 同步；版本号 1.7.0

## 1.6.2

- 修复 CI：`actions/setup-python` 的 pip 缓存需要仓库内存在 `pyproject.toml`/`requirements.txt`，此前 1.6.0 的 CI 因此在初始化步骤直接失败（代码本身无问题）
- 新增 `pyproject.toml` 承载 ruff 配置（替代 `ruff.toml`），同时满足 CI 依赖缓存条件
- STRUCTURE 目录树同步

## 1.6.1

- Web UI：「管理面板」标签页移除重复的“测试合成”区块，只保留「安装向导」标签页第 5 步的合成验证（两个按钮不再与区块贴得过近）
- 「保存配置 / 重新加载」按钮容器补充下边距
- README 同步：管理面板功能列表、安装向导第 5 步说明

## 1.6.0

按 ROADMAP.md 评审结论实施的改进：

**P0 缺陷修复**
- 配置面板保留基础引导项（enabled / enable_webui / engine_dir / mode / style_id），未开启 Web UI 也能完成配置（P0-1）
- 新增 `max_concurrent_synth`（默认 1）：限制并发合成，避免内存峰值叠加触发 OOM（P0-2）
- 新增后台定时任务，自动清理临时目录中超过 2 小时的合成音频，防止磁盘无限增长（P0-3）

**P1 兼容性与体验**
- `astrbot_version` 放宽为 `>=4.24.5,<5`（插件 Pages 最早可用版本）；`astrbot.api.web` 改为容错导入，老版本自动降级为「无 Web UI」（P1-1）
- 长文本不再整段跳过：关闭 `skip_if_too_long` 时按句分段朗读（最多 6 段，串行合成）（P1-2）
- `probability` 夹取到 0~1，非法值告警（P1-3）
- macOS 可用内存检查（vm_stat），内存门槛不再失效（P1-4）

**P2 工程质量**
- 新增 pytest 单元测试（风格归一化/文本工具/分段/路径解析链/回环校验/信号量）与 GitHub Actions CI（ruff + 编译 + 测试）（P2-1/P2-2）
- logo 压缩为 256×256（119KB → 38KB）（P2-4）
- 安全加固：默认仅允许向回环地址的引擎发送文本，新增 `allow_remote_engine` 开关（P2-5）

## 1.5.4

- 修正了作者，把最初写项目的agent的名字改了
- 版本号改为标准三段式（1.5.3.1 为 AstrBot 不支持的写法，已撤销）

## 1.5.3

- 作者署名更正为 Aurora & deepseek（原「索拉」为最初编写插件的 AstrBot agent 人格名）

## 1.5.2

- 以 CC BY-NC 4.0（署名-非商业性使用）许可发布，新增 LICENSE
- README 新增「十、许可证」：代码 CC BY-NC 4.0，第三方资源（音源/软件/Logo）按各自条款

## 1.5.1

- 插件市场上架准备：补齐 repo / short_desc / support_platforms / astrbot_version / category / tags
- 版本号改用纯语义化格式（去 `v` 前缀）

## 1.5.0

- 管理面板与安装向导合并为单页双标签（AstrBot 页面 iframe 沙箱不支持跨页跳转）
- 启动引擎就绪后自动返回管理面板标签
- 新增 `.astrbot-plugin/i18n`，Dashboard 页面标题显示为「管理面板 / Dashboard」

## 1.4.1

- 安装向导磁盘自检按文件系统去重，避免同一磁盘重复显示

## 1.4.0

- Web UI 新增安装向导：环境自检 / 官方下载指引 / 引擎目录校验 / 一键后台启动 / 合成验证

## 1.3.1

- 添加本地 TTS 宣传介绍（隐私、免费、图形化、agent 可协助安装引擎）

## 1.3.0

- 内置 Web UI 图形化管理面板（可开关；开启后原配置面板仅保留开关）
- 新增配置项 `enable_webui`，注册 4 条 Web API（config/status/test）

## 1.2.3

- 补充渠道支持说明（可联动 NapCat 使用 QQ 语音，其他渠道欢迎提交测试报告）

## 1.2.2

- 补充 Logo 作者声明（ノザラシ）

## 1.2.1

- 显示名改为 Tsukuyomi-chan COEIROINK 日语语音
- 附音源与软件致谢（月读酱 / COEIROINK）

## 1.2.0

- 通用化重构：不再内置任何机器相关路径（配置项 → 环境变量 → 自动推导）
- 跨平台：Windows 进程拉起（DETACHED_PROCESS）与内存门槛检查（GlobalMemoryStatusEx）
- 内存优化：`MALLOC_TRIM_THRESHOLD_`、合成流式落盘、轻量探活 + 15s 缓存、共享 HTTP 连接池
- 性能：日语文本跳过 LLM 翻译、ffmpeg 转码移入线程池

## 1.1.0 及更早

- 初始版本：四种触发模式、三种情绪风格、COEIROINK 本地引擎合成
