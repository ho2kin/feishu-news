# 派单中心新工单监控 → 飞书通知

每 15 分钟自动检查正泰安能「审核中心 → 派单中心」的新工单，推送到飞书群。

## 功能

- **监控口径**：项目地址=河南省、派单状态=待派单、已审次数=0
- **推送排序**：按创建时间从旧到新（与网页列表显示顺序一致）
- **推送内容**：所属代理商 | 电站编号 | 审核节点
- **增量通知**：只推上次检查之后新增的工单，已推送过的不会重复推
- **过期提醒**：登录态失效时自动往飞书群发提醒（12 小时冷却，不会轰炸）
- **断电补跑**：关机期间错过的检查，开机后自动补跑一次
- **严格只读**：全程在本地电脑运行，对网站只打开页面和查询接口，不做任何写操作
- **不自动登录**：从不自动执行登录，登录态由人工登录一次后持久复用

## 工作原理

登录有图形验证码，自动登录不可行；因此使用一个**专用浏览器配置目录**
（`state/chrome-profile`，与日常浏览器完全隔离）。人工在这个专用窗口里登录一次，
登录态（cookie）即持久保存；监控程序通过调试端口连接该浏览器实例，
直接调用派单中心的工单分页接口查询数据。

网站的"已审次数""排序"没有接口参数，由程序在本地完成过滤和排序，
效果与手动在网页上筛选一致。

审核节点显示名映射：0=建档、1=并网、2=完工、3=变更。

## 环境要求

| 项目 | 要求 |
| --- | --- |
| 操作系统 | Windows 10/11（计划任务依赖 Windows） |
| Python | 3.9 及以上，安装时勾选 Add to PATH |
| 浏览器 | Chrome 或 Edge 任一（脚本自动查找安装路径） |
| 第三方库 | 仅 `playwright`、`requests`、`PyYAML` 三个包 |

说明：**不需要** `playwright install` 下载浏览器，也不需要 ZCode 等任何
AI 开发工具——脚本通过 CDP 连接系统里已装的 Chrome/Edge，
由 Windows 计划任务直接调度，运行全程与开发工具无关。

## 使用方法

### 第 1 步：安装依赖

在项目目录打开命令行，执行：

```
pip install -r requirements.txt
```

### 第 2 步：配置飞书机器人

1. 飞书群 → 设置 → **群机器人** → 添加 → 选**自定义机器人**；
2. 安全设置建议勾选**签名校验**；
3. 复制 **Webhook 地址** 填入 `config.yaml` 的 `feishu.webhook`
   （开了签名就把密钥填到 `feishu.secret`）。

### 第 3 步：手动登录一次

```
python login.py
```

会弹出**专用浏览器窗口**（不是你的日常浏览器），在窗口里输入账号、密码、
验证码完成登录。脚本检测到登录成功后提示登录态已保存，窗口可开可关。

### 第 4 步：验证

```
python monitor.py          # 第一次运行：记录基线（只发一条"监控已启动"）
python monitor.py          # 第二次运行：应显示 新数据 0 条
```

### 第 5 步：注册计划任务

双击 `install_task.bat`，注册 Windows 计划任务（任务名 `FeishuDataMonitor`，
每 15 分钟自动运行一次 `monitor.py`）。

## 日常运行

- **全自动**：开机状态下每 15 分钟自动检查，无需人工参与。
- **重新登录**：网站 token 有效期约 24 小时，飞书群收到"登录态已过期"提醒后，
  运行一次 `python login.py` 重登即可。这是日常唯一需要人工做的事。
- **专用浏览器**：检查时若专用浏览器没开，会自动以无头模式拉起
  （看不到窗口，属正常现象）。
- **日志**：`logs/monitor.log`。

## 配置说明

所有配置集中在 `config.yaml`。

### monitor（监控口径）

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `url` | 派单中心页面地址 | 用于登录态校验和接口调用上下文 |
| `login_url_keyword` | `/login` | 页面被重定向到含此关键字的地址即判定登录过期 |
| `wait_seconds` | `8` | 页面加载后等待秒数 |
| `api_url` | 工单分页接口 | 已实测锁定的数据接口 |
| `query_params.dispatchStatus` | `0` | 0=待派单 |
| `query_params.pageType` | `"9"` | 9=派单中心视图 |
| `query_params.projectProvinceCode` | `"41"` | 项目省份，41=河南省（GB 省份代码） |
| `page_rows` / `max_pages` | `100` / `5` | 每页条数 / 最多翻页数（防失控） |
| `filter_audit_count` | `0` | 本地过滤：只保留已审次数=0 的工单；留空不过滤 |
| `sort_field` / `sort_order` | `creationDate` / `asc` | 本地排序：按创建时间从旧到新 |
| `item_key_fields` | `[id]` | 判定"新数据"的字段；想监控状态流转可加如 `[id, auditStatus]` |
| `summary_fields` | `[agentName, stationNo, auditNode]` | 通知摘要里展示的字段 |
| `audit_node_labels` | 0~3 | 审核节点代码 → 显示名映射 |
| `notify_max_lines` | `15` | 通知里最多列出的明细条数 |

### feishu（飞书推送）

| 配置项 | 说明 |
| --- | --- |
| `webhook` | 群自定义机器人的 Webhook 地址（必填） |
| `secret` | 签名校验密钥，机器人开了签名才填，没开留空 |
| `expired_alert_cooldown_hours` | 登录过期提醒冷却小时数，避免每 15 分钟重复轰炸 |

### browser（专用浏览器）

| 配置项 | 说明 |
| --- | --- |
| `debug_port` | 调试端口，默认 `9222`，一般不用改 |
| `chrome_path` | 浏览器路径，留空自动查找；特殊安装位置时手动指定 |

## 常用调整

| 想改什么 | 改哪里 |
| --- | --- |
| 推送字段 | `monitor.summary_fields` |
| 监控省份 | `monitor.query_params.projectProvinceCode`（41=河南，按 GB 省份代码） |
| 已审次数口径 | `monitor.filter_audit_count` |
| 排序字段/方向 | `monitor.sort_field` / `monitor.sort_order` |
| 新数据判定字段 | `monitor.item_key_fields` |
| 检查频率 | 改 `install_task.ps1` 里的 `-RepetitionInterval` 后重跑 `install_task.bat` |
| 删除定时任务 | `schtasks /Delete /TN FeishuDataMonitor /F` |

## 迁移到新电脑

项目没有任何写死的绝对路径，迁移只需以下步骤：

1. **复制整个项目文件夹**（`logs\`、`__pycache__\`、`.zcode\` 可不带；
   `config.yaml` 里的飞书 webhook 已填好，直接生效）；
2. 新电脑装 **Python 3.9+**（勾选 Add to PATH），确认装有 **Chrome 或 Edge**；
3. 项目目录执行 `pip install -r requirements.txt`
   （**不需要** `playwright install`，脚本用的是系统浏览器）；
4. `python login.py` 手动登录一次（专用登录态不建议跨机器复制，重登最可靠）；
5. `python monitor.py` 跑两次验证；
6. 双击 `install_task.bat` 注册计划任务；
7. **旧电脑**执行 `schtasks /Delete /TN FeishuDataMonitor /F` 删除旧任务，
   避免两台机器重复推送。

`state\seen_items.json`（已推送工单指纹）带不带均可：不带则新机器首次运行重新
记基线，不会刷屏；带着则推送记录无缝延续。

## 故障排查

| 现象 | 处理 |
| --- | --- |
| 飞书收到"登录态已过期" | 运行 `python login.py` 重新登录一次 |
| 报错"找不到可用的 Chrome/Edge" | 在 `config.yaml` 的 `browser.chrome_path` 手动指定浏览器 exe 路径 |
| 专用浏览器启动失败 | 检查 `debug_port` 是否被其他程序占用 |
| 收不到通知 | 看 `logs/monitor.log`；确认计划任务存在（`schtasks /Query /TN FeishuDataMonitor`） |
| 计划任务不执行 | 确认电脑开机状态；脚本支持开机后补跑错过的检查 |

## 文件结构

```
config.yaml        所有配置（监控口径、推送字段、飞书、浏览器；不入库，
                   按 config.example.yaml 复制创建）
config.example.yaml 配置模板（脱敏，入库）
login.py           手动登录入口（人工输验证码，登录态持久保存）
monitor.py         监控主程序（计划任务每 15 分钟调用，只读）
feishu.py          飞书 webhook 推送（支持签名校验）
browser.py         专用浏览器管理（独立配置目录 + 调试端口）
requirements.txt   Python 依赖清单（playwright / requests / PyYAML）
install_task.bat   一键注册 Windows 计划任务（调用 install_task.ps1）
install_task.ps1   计划任务注册逻辑（自动探测路径，每 15 分钟一次）
state/             登录态（chrome-profile）、已见工单指纹（seen_items.json）、提醒冷却记录
logs/              运行日志、接口分析日志
```
