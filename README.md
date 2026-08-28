# 派单中心新工单监控 → 飞书通知

每 5 分钟自动检查正泰安能「审核中心 → 派单中心」的新工单，推送到飞书群。
**纯接口模式**：不启动浏览器，账号密码 + OCR 自动识别验证码登录，全程无人值守。

## 功能

- **监控口径**：项目地址=河南省、派单状态=待派单、已审次数=0
- **推送排序**：按创建时间从旧到新（与网页列表显示顺序一致）
- **推送内容**：所属代理商 | 电站编号 | 审核节点
- **增量通知**：只推上次检查之后新增的工单，已推送过的不会重复推
- **全自动登录**：token 过期自动用账号密码重登（ddddocr 识别图形验证码），
  无需任何人工介入
- **失败兜底**：自动登录也失败时（密码改动/账号锁定等）往飞书群发提醒
  （12 小时冷却，不会轰炸）
- **严格只读**：对网站只调用查询接口与登录接口，不做任何写操作

## 工作原理

1. `auto_login.py` 用 config.yaml 里的账号密码，自动拉取图形验证码并 OCR 识别，
   调用登录接口换取鉴权 JWT（约 24 小时有效），保存到 `state/auto_login_token.json`；
2. `monitor.py` 每 5 分钟带 token 直连工单分页接口查询；
3. token 失效（接口返回 PERMISSION_NOT_PASS）时自动重登换新 token 再查；
4. 数据在本地完成过滤（已审次数=0）和排序（创建时间从旧到新），
   与网页手动筛选效果一致，然后与 `state/seen_items.json` 的指纹对比，有新增才推送。

## 环境要求

| 项目 | 要求 |
| --- | --- |
| 操作系统 | Windows 10/11（计划任务依赖 Windows） |
| Python | 3.9 及以上，安装时勾选 Add to PATH |
| 第三方库 | 仅 `requests`、`PyYAML`、`ddddocr` 三个包 |

说明：**不需要浏览器、不需要 playwright**。`legacy/` 里保留了旧的浏览器登录
方案仅作故障备用，正常运行完全不依赖。

## 使用方法

### 第 1 步：安装依赖

在项目目录打开命令行，执行：

```
pip install -r requirements.txt
```

### 第 2 步：配置

1. 在 `config.yaml` 的 `account` 段填入**账号（手机号）和密码**——自动登录全靠它；
2. 飞书群 → 设置 → **群机器人** → 添加 → 选**自定义机器人**，
   把 **Webhook 地址**填入 `config.yaml` 的 `feishu.webhook`
   （开了签名就把密钥填到 `feishu.secret`）。

### 第 3 步：验证

```
python auto_login.py        # 测试自动登录，显示"接口验证通过"即 OK
python monitor.py           # 第一次运行：记录基线（只发一条"监控已启动"）
python monitor.py           # 第二次运行：应显示 新数据 0 条
```

### 第 4 步：注册计划任务

双击 `install_task.bat`，注册 Windows 计划任务（任务名 `FeishuDataMonitor`，
每 5 分钟自动运行一次 `monitor.py`）。

## 日常运行

- **全自动**：开机状态下每 5 分钟自动检查，登录态过期自动重登，无需人工参与。
- **飞书收到"自动登录失败"**：检查 `config.yaml` 的账号密码（是否改密/锁定），
  修正后无需其他操作，下一轮自动恢复。
- **日志**：`logs/monitor.log`（监控）、`logs/auto_login.log`（登录）。

## 配置说明

所有配置集中在 `config.yaml`。

### account（自动登录账号）

| 配置项 | 说明 |
| --- | --- |
| `username` | 登录账号（手机号，程序自动识别走"手机登录"） |
| `password` | 登录密码（明文本地保存，注意机器安全） |
| `captcha_retry` | 验证码识别失败时换图重试的最大次数 |

### monitor（监控口径）

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `url` | 派单中心页面地址 | 作为接口请求的 Referer |
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
| `expired_alert_cooldown_hours` | 自动登录失败提醒冷却小时数 |

## 常用调整

| 想改什么 | 改哪里 |
| --- | --- |
| 推送字段 | `monitor.summary_fields` |
| 监控省份 | `monitor.query_params.projectProvinceCode`（41=河南，按 GB 省份代码） |
| 已审次数口径 | `monitor.filter_audit_count` |
| 排序字段/方向 | `monitor.sort_field` / `monitor.sort_order` |
| 新数据判定字段 | `monitor.item_key_fields` |
| 检查频率 | 改 `install_task.ps1` 里的 `-RepetitionInterval` 后重跑 `install_task.bat` |
| 停止并删除定时任务 | 双击 `uninstall_task.bat`（或 `schtasks /Delete /TN FeishuDataMonitor /F`） |

## 故障排查

| 现象 | 处理 |
| --- | --- |
| 飞书收到"自动登录失败" | 检查 `config.yaml` 账号密码；改密后更新配置即可，下一轮自动恢复 |
| 报"缺少 OCR 库" | `pip install ddddocr` |
| 收不到通知 | 看 `logs/monitor.log` 和 `logs/auto_login.log`；确认计划任务存在（`schtasks /Query /TN FeishuDataMonitor`） |
| 计划任务不执行 | 确认电脑开机状态；关机期间错过的检查不补跑，开机后从当前时间按 5 分钟周期继续 |
| 验证码突然识别全错 | 网站可能更换了验证码类型，重跑 `python auto_login.py` 观察 `logs/auto_login.log` |

## 文件结构

```
config.yaml         所有配置（自动登录账号、监控口径、飞书）
auto_login.py       纯代码自动登录（OCR 验证码），monitor 失效时自动调用
monitor.py          监控主程序（计划任务每 5 分钟调用，只读）
feishu.py           飞书 webhook 推送（支持签名校验）
install_task.bat    一键注册 Windows 计划任务（调用 install_task.ps1）
install_task.ps1    计划任务注册逻辑（每 5 分钟一次）
uninstall_task.bat  一键停止并删除计划任务
legacy/             旧浏览器登录方案（login.py + browser.py，仅备用）
state/              登录 token（auto_login_token.json）、已见工单指纹、提醒冷却记录
logs/               运行日志（monitor.log / auto_login.log）
```
