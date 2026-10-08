# 更新日志（Changelog）

## 1.5.3.1

- 修正了作者，把最初写项目的agent的名字改了

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
