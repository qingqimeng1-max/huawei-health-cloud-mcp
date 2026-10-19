# 测试汇总 · 华为健康数据接入（逆向云端方案）

> 执行日期：2026-10-19
> 任务书版本：v1.0（见 `任务书.md`）
> 执行结果：阶段 0–3 全部通过，阶段 4（Docker 部署）配置就绪

---

## 1. 背景

用户佩戴华为手环，用小米手机上的"华为运动健康"App 同步数据，希望云端 AI Agent 能读取步数、心率、睡眠、血氧、运动记录。

官方 Health Kit 路线对个人开发者不可行（心率/睡眠/血氧属于高级开放级别，个人拿不到），故采用逆向方案：直接调用华为运动健康 App 自己的云端接口，走官方 OAuth 授权码流程换 token，**不保存华为账号密码**。

## 2. 执行环境

- 一台长期在线的 Linux 云 VM，Python 3.12，时区 Asia/Shanghai
- 协议代码移植自 [and7ey/huawei_health](https://github.com/and7ey/huawei_health)（MIT，纯标准库），按任务书附录 C 加入中国大陆区 host 并优先

## 3. 阶段 0：授权链路 ✅ 通过

授权 URL（参数与任务书 §3.2 一致，勿改）：

```
https://oauth-login.cloud.huawei.com/oauth2/v3/authorize
  ?client_id=10414141
  &response_type=code
  &access_type=offline
  &display=touch
  &scope=https://www.huawei.com/auth/account/base.profile
          https://www.huawei.com/healthkit/step.both
          https://www.huawei.com/healthkit/activityrecord.both   （空格分隔）
  &redirect_uri=hms://redirect_url
  &state=<8位随机>&lang=zh-cn&prompt=consent
```

人工授权步骤（Chrome 桌面版）：
1. 打开授权 URL，登录华为账号（可能需短信验证）
2. F12 → Ctrl+Shift+M 切手机模式 → F5 刷新（同意按钮是 touch 元素，桌面模式点不动且不报错）
3. 点"允许"，浏览器跳转 `hms://` 失败是正常的
4. Console 找到 `Failed to launch 'hms://redirect_url?code=...'`，完整复制

**踩过的坑**：第一次授权返回 `error=1201&error_description=invalid+ticket`——登录会话票据无效。解决办法：在浏览器里退出华为账号后**干净重登一次**（把短信验证走完），换新 state 重走流程即成功。client_id 本身没问题。

**通过证据**：token 换取成功（`POST {CN token host}/commonAbility/userAccessToken/obtain`），`getSportsStat` 返回真实步数（示例：当天约 5200 步，前两天约 8100 步），与手机 App 首页一致。

**大陆区 host（实测可用，直接用）**：

| 用途 | Host |
|---|---|
| token 换取/刷新 | `https://healthcommon-drcn.things.dbankcloud.com` |
| 健康数据 | `https://healthdata.dbankcloud.cn` |

## 4. 阶段 1：数据端点探测 ✅

| 数据 | 端点 | 结果 |
|---|---|---|
| 日步数/热量/距离 | `/dataQuery/sport/v2/getSportsStat` | ✅ 真实数据 |
| 心率（max/min/静息） | `/dataQuery/health/getHealthStat` type=7 | ✅ 真实数据 |
| 睡眠（深睡/浅睡/REM/评分/入睡起床时间） | `/dataQuery/health/getHealthStat` type=9 | ✅ 真实数据 |
| 血氧 | 睡眠记录内 `lastAvgSpO2` | ⚠️ 返回 -1，该设备无血氧记录，如实标注无数据 |
| 运动记录 | `/dataQuery/sport/getSportsDataByTime` | ✅ 7 天 12 条（需按分钟分段合并） |
| 绑定设备 | `/profile/device/getBindDevice` | ✅ 返回 `deviceInfos`（注意不是 `data` 字段） |

字段坑（已验证）：`calorie` 是千分之一千卡（÷1000）；`distance` 是米；`*Stat` 用 YYYYMMDD 整数，`*ByTime` 顶层用毫秒、samples 用纳秒；所有 `*ByTime` 窗口 ≤10 天；不认识的 type 返回 `{}` 不报错；token 要同时放在 body、`Authorization` 头和 `x-token` 头。

## 5. 阶段 2+3：存储与 MCP ✅

- SQLite 两级表：`raw_health_point` / `workout` / `daily_summary` / `sync_cursor`（见 `app/store.py`）
- 首次全量同步：31 天步数、30 天心率、24 天睡眠、55 条运动记录，全部入库正常
- token 用 Fernet 加密存 SQLite，密钥与 DB 文件权限均为 600；access token 60 分钟有效期，提前 120 秒自动轮换；refresh token 180 天
- MCP Server（FastMCP，`127.0.0.1:8000/mcp`）：6 个 tool 全部实测可用
  - `get_steps` / `get_heart_rate` / `get_sleep` / `get_spo2` / `get_workouts` / `get_health_summary`
  - 每个返回 `data_completeness`（避免把"没测到"当成 0）；血氧无数据时如实标注而非报错
- 另有 `GET /health` 存活检查、`GET /debug/last_sync` 同步状态

## 6. 阶段 4：Docker 部署（配置就绪）

- `Dockerfile` + `docker-compose.yml` 已写好：只绑 `127.0.0.1:8000`，`./data` volume 落盘，容器内每 2 小时自动同步一次
- 对外访问需自配反向代理（Caddy/nginx）做 HTTPS + Basic Auth，MCP 本身无鉴权

## 7. 复用指南

1. 克隆本仓库，按 `任务书.md` §2 确认前置条件（云 VM、Docker、桌面浏览器、手机 App 已同步数据）
2. 跑 `probe/authorize.py` 生成授权 URL，按 §3 走完人工授权
3. 把复制的 `hms://redirect_url?code=...` 喂给 `probe/persist_tokens.py`（stdin），自动换 token、加密入库、验证
4. 跑 `app/sync.py` 做首次全量同步
5. `docker compose up -d` 启动服务，`GET /health` 确认存活

## 8. 安全红线（照任务书 §9 执行）

1. 不存华为账号密码；授权码/refresh token 不进聊天记录、不进日志、不进 git
2. 只读：不调用任何写接口
3. 不对华为侧高频请求：探测间隔 ≥2 秒，同步间隔 2 小时，请求间隔 ≥500ms
4. 健康数据不出本机：不上传第三方、不做云端备份
5. 端口只绑 127.0.0.1，对外必须 HTTPS + 鉴权
