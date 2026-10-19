# 华为健康数据接入（逆向云端方案）

不走官方 Health Kit 资质，直接调用华为运动健康 App 自己的云端接口，用 OAuth 授权码换 token，**不保存华为账号密码**，把手环/手表的健康数据（步数、心率、睡眠、血氧、运动记录）接到 MCP Server，供任意支持 MCP 的 AI Agent 读取。

> 为什么不用官方 Health Kit？个人开发者拿不到高级开放级别数据（心率/睡眠/血氧），此路不通。详见 `任务书.md` §1。

## 实测状态

2026-10-19 在中国大陆区账号上全流程跑通：阶段 0–3 通过，阶段 4 配置就绪。完整测试记录见 `TEST_SUMMARY.md`。

- 大陆区 token host：`https://healthcommon-drcn.things.dbankcloud.com`
- 大陆区数据 host：`https://healthdata.dbankcloud.cn`
- 首次同步：31 天步数 / 30 天心率 / 24 天睡眠 / 55 条运动记录

## 快速开始

```bash
# 1. 生成授权 URL（参数勿改）
python3 probe/authorize.py

# 2. 按 任务书.md §3.3 在 Chrome 里走完人工授权，
#    把 hms://redirect_url?code=... 喂给：
python3 probe/persist_tokens.py   # 经 stdin，自动换 token、加密入库、验证

# 3. 首次全量同步
python3 app/sync.py

# 4. 启动服务
docker compose up -d
curl http://127.0.0.1:8000/health
```

## 目录结构

```
hwclient/        协议层（移植自 and7ey/huawei_health，MIT，已加入大陆区 host）
probe/           一次性探测脚本：authorize / exchange / persist_tokens / explore
app/             token 加密存储 / SQLite 存储 / 同步逻辑 / MCP Server（6 个 tool）
任务书.md         完整执行任务书 v1.0（含协议细节、字段坑、安全红线）
TEST_SUMMARY.md  2026-10-19 实测汇总（含 invalid ticket 报错的解决办法）
Dockerfile / docker-compose.yml
```

## 安全

- 不存华为账号密码；授权码一次有效、几分钟过期
- token Fernet 加密存 SQLite，文件权限 600，不进 git
- 只读接口，不写不删；请求低频（同步每 2 小时，间隔 ≥500ms）
- 服务只绑 127.0.0.1，对外请自配 HTTPS + 鉴权

## 致谢

- 协议逆向：[and7ey/huawei_health](https://github.com/and7ey/huawei_health)（MIT）
- MCP 结构参考：[quango2304/huawei-mcp-connector](https://github.com/quango2304/huawei-mcp-connector)（MIT）
- 任务书：另一位云端 Agent 制定，本仓库按其 v1.0 执行验证
