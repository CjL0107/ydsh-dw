# 第 4 周 · API 配置指南

> 项目：云购生活 · 企业级数据仓库与经营分析可视化系统
> 作者：梁常金（20230308013）
> 覆盖：密钥管理、Superset 数据源、大屏 JSON 接口、NL2SQL 大模型接口、Streamlit 配置

---

## 一、整体链路：为什么有四层

```
Hive（ydsh_ads，ORC）         ← 唯一计算入口，重聚合在这里做
   ├── MySQL ydsh_ads         ← 421 行结果，Superset 图表数据源（26 ms）
   └── ClickHouse ydsh_ads    ← 同样 421 行，列存压测对比（7.7 ms）
                                  ↓
                        Flask JSON 接口（8000）→ ECharts 大屏
                                  ↓
                     Streamlit（8501）→ nl2sql → Hive → 表格 + 图表
                                  ↓
                        Superset（8088）→ 仪表板
```

四层不是堆着好看，每一层解决一个具体问题：

| 层 | 解决的问题 | 不这么做会怎样 |
|---|---|---|
| Hive 预聚合（ADS） | 明细 1.5 万行现算要 4.2 秒 | 前端每次刷新等 4 秒 |
| MySQL 服务层 | 筛选器交互要毫秒级 | 点一下筛选器等 4 秒，用户放弃 |
| ClickHouse | 列存 vs 行存的对比证据 | 拿不出"为什么选列存"的数据 |
| Flask 快照接口 | 大屏 10 个面板不能各查一次 | 首屏 43 秒，且 Hive 被并发打爆 |

**快照机制**：`refresh_snapshot.py` 把 10 个面板一次性算完（实测 43.09 秒）落成 29 KB
的 `snapshot.json`，接口只读文件。所以接口 P95 是 36 ms，而不是 43 秒。
代价是数据有延迟（默认手动刷新），这个取舍对经营看板完全可接受。

---

## 二、密钥与环境变量

### 2.1 所有密钥只有一个来源：`.env`

代码里没有任何硬编码口令。三个关键位置：

```python
# docker/superset/superset_config.py
SECRET_KEY = os.environ["SUPERSET_SECRET_KEY"]   # 缺失直接抛 KeyError，启动失败

# apps/serving/load_clickhouse.py
CH_PASSWORD = os.getenv("CLICKHOUSE_PASSWORD", "")
if not CH_PASSWORD:
    raise SystemExit("CLICKHOUSE_PASSWORD 未设置...")   # 不留默认值

# apps/api/server.py
token = os.getenv("REFRESH_TOKEN", "")          # 设了就校验 X-Refresh-Token
```

`docker-compose.yml` 里用 `${VAR}` 引用，并对关键项用 `${VAR:?错误信息}` 强制：

```yaml
SUPERSET_SECRET_KEY: ${SUPERSET_SECRET_KEY:?SUPERSET_SECRET_KEY 未在 .env 中设置}
CLICKHOUSE_PASSWORD: ${CLICKHOUSE_PASSWORD:?CLICKHOUSE_PASSWORD 未在 .env 中设置}
```

这样漏配时的报错是「`SUPERSET_SECRET_KEY` 未在 .env 中设置」，
而不是运行到一半 `Invalid decryption key` 或者空口令连上库。

### 2.2 生成与轮换

| 密钥 | 生成方式 | 轮换影响 |
|---|---|---|
| `MYSQL_ROOT_PASSWORD` | 随机 12 字节 hex | 要同步 `docker compose up -d mysql hive-metastore superset`（三处引用） |
| `CLICKHOUSE_PASSWORD` | 随机 12 字节 hex | 需重建 clickhouse 容器 + 重跑 `load_clickhouse.py` |
| `SUPERSET_SECRET_KEY` | 随机 32 字节 urlsafe | **不能随意换**：元数据里的数据源口令用它加密，换了全部解不开 |
| `SUPERSET_ADMIN_PASSWORD` | 随机 15 字节 urlsafe | 已有账号需 `superset fab reset-password` 才生效 |
| `REFRESH_TOKEN` | 随机 24 字节 urlsafe | 改了要重启 `app` |
| `LLM_API_KEY` | 平台申请 | 改了要重启 `streamlit` |

生成命令：

```bash
python -c "import secrets; print(secrets.token_hex(12))"      # 数据库口令
python -c "import secrets; print(secrets.token_urlsafe(32))"  # SECRET_KEY
```

轮换 Superset admin 口令的正确姿势（`create-admin` 对已存在账号无效）：

```bash
PW=$(grep '^SUPERSET_ADMIN_PASSWORD=' .env | cut -d= -f2)
docker exec -e PW="$PW" superset sh -c 'superset fab reset-password --username admin --password "$PW"'
# 输出：User admin reseted.
```

### 2.3 权限最小化

| 账号 | 权限 | 用途 |
|---|---|---|
| MySQL `root` | 全库 | 仅 Metastore 元数据 + Superset 元数据 + ADS 服务层，本地单机无法避免 |
| Superset `admin` | 平台管理员 | 建数据集/图表；日常看板可用 `Gamma` 角色 |
| Hive | 无认证（单机） | 第 1 周就是这么配的，进入生产必须补 LDAP/Kerberos |
| ClickHouse `default` | 单库 | 只给 `ydsh_ads`，不开 `ACCESS_MANAGEMENT` 给应用 |

> 已知局限：本机 Hive 是 NOSASL/NONE 无认证，ClickHouse 用单一 `default` 用户。
> 这两点是**明确写在文档里的技术债**，不是"忘了配"。生产部署应换成：
> Hive 开 LDAP、ClickHouse 建只读用户并限定 `GRANT SELECT ON ydsh_ads.*`、
> MySQL 给 Superset 建独立账号而非 root。

---

## 三、Superset 数据源配置

### 3.1 两个数据源，用途不同

| 名称 | 类型 | URI | 用途 |
|---|---|---|---|
| `ydsh_mysql_ads` | MySQL | `mysql+pymysql://root:***@mysql:3306/ydsh_ads?charset=utf8mb4` | **6 个图表的实际数据源** |
| `ydsh_hive` | Hive | `hive://hive-server:10000/default` | SQL Lab 即席探索（慢，但数据全） |

### 3.2 为什么图表不直接连 Hive

实测同一问题「各省 GMV 排名」：

| 路径 | 耗时 |
|---|---|
| Hive 明细现算（join 维表） | 4207.7 ms |
| Hive ADS 预聚合 | 3546.3 ms |
| **MySQL ADS** | **26.8 ms** |
| ClickHouse ADS | 7.7 ms |

仪表板的筛选器每动一次就是一次查询。4 秒的响应意味着用户点两下就关页面。
所以：**重计算留 Hive，轻展示放 MySQL** —— 这是企业里最常见的分层做法。

### 3.3 为什么 ClickHouse 没接进 Superset

试过 `clickhouse+http` 方言，Superset 4.1 报
`Could not load database driver: ClickHouseEngineSpec`
（`sqlalchemy.dialects:clickhouse.http` 找不到）。

不为一个驱动去改官方镜像的内部结构。ClickHouse 仍留在编排里，
职责改成「查询优化的对比基线」，压测数据反而比接进仪表板更有说服力。

### 3.4 建资源的正确姿势（三个坑）

Superset 的资源全部脚本化（`apps/superset/provision.py`），可重复执行。踩过的坑：

1. **必须用表单登录拿 session cookie，不能用 JWT**。
   JWT 下 `g.user` 是匿名用户：写图表时 `ChartDAO.create` 要挂 `created_by`，
   抛 `AnonymousUserMixin object has no attribute _sa_instance_state`；读接口返回空列表。

2. **`POST /api/v1/dataset/` 不接受 `metrics` 字段**（返回 `{"metrics":["Unknown field."]}`）。
   要先建表映射，再 `PUT dataset/<id>` 补指标。

3. **仪表板与图表的关联必须写 `dashboard_slices` 表**。
   `position_json` 里的 `meta.chartId` 只是布局信息；`PUT dashboard/` 又不接受
   `slices` 字段（`Unknown field.`）。不写关联表，看板打开后每个格子都提示
   「没有与此组件关联的图表定义」。

4. **筛选器需要 `count` 指标才能初始化**。
   原生筛选器报 `Error: Metric 'count' does not exist`，而 `PUT dataset` 补
   `count` 会被 422 `One or more metrics already exist` 拒绝 —— 只能直接
   `INSERT INTO superset.sql_metrics`。

---

## 四、大屏 JSON 接口

基址 `http://127.0.0.1:8000`，与大屏静态页**同源**（免 CORS 折腾）。

### 4.1 端点清单

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` | 大屏页面（`?static=1` 关闭数字滚动动画） |
| GET | `/api/health` | 探活 + 快照生成时间 + 面板列表 |
| GET | `/api/overview` | **一次返回 10 个面板**（大屏首屏就用这个） |
| GET | `/api/<panel>` | 单个面板 |
| POST | `/api/refresh` | 重算快照（约 43 秒，需 `X-Refresh-Token`） |

`<panel>` 取值：`kpi` `trend` `category` `province` `channel` `payment`
`city_scatter` `store_top` `user_segment` `hour_category`

### 4.2 返回结构

**指标卡类**（`kpi` / `category` / `province` / `channel` / `payment` /
`city_scatter` / `store_top` / `user_segment`）统一是 `items` 数组：

```json
{
  "category": {
    "title": "品类结构",
    "source": "ydsh_ads.ads_category_top10",
    "items": [
      {"name": "运动户外", "gmv": 4064272.9, "share": 14.91,
       "orders": 3469, "qty": 12009, "avgPrice": 338.44, "rank": 1}
    ]
  },
  "meta": {"served_at": "2026-11-17 09:20:31", "snapshot_age_seconds": 12.4}
}
```

**折线类**（`trend`）是平行数组，直接喂 ECharts 的 `series.data`，前端不用二次变换：

```json
{"trend": {"dates": ["2024-01-01", "..."], "gmv": [74123.5, "..."],
           "ma7": [...], "orders": [...], "aov": [...], "weekend": [0, 1, ...]}}
```

**热力类**（`hour_category`）是 ECharts heatmap 要求的 `[x, y, value]` 三元组：

```json
{"hour_category": {"hours": ["00", "..."], "categories": ["..."],
                   "data": [[0, 0, 12345.6], "..."], "unit": "元", "slow": true}}
```

**数值口径**：金额一律转成 `float`（Hive 的 beeline 全返回字符串，
不转会变成前端拼接字符串）；占比一律乘 100（ADS 存 `0.1491`，展示要 `14.91`）。

### 4.3 刷新与令牌

```bash
# .env 里配了 REFRESH_TOKEN 就必须带头
curl -X POST http://127.0.0.1:8000/api/refresh \
     -H "X-Refresh-Token: <token>"
```

刷新失败**不会**覆盖旧快照（接口返回 500，但大屏仍是上一次的数）。
这个设计是刻意的：大屏宁可显示 10 分钟前的数，也不能变成空白。

### 4.4 前端如何接

```javascript
const data = await fetch('/api/overview').then((r) => r.json());
renderKpi(data.kpi.items);
renderTrend(data.trend);        // 平行数组直接进 series
renderMap(data.province.items);
```

ECharts 5.5.1 与 `china.json` 都放在 `apps/screen/vendor/` 本地，
不走 CDN —— 答辩现场断网也要能演示。

---

## 五、NL2SQL 大模型接口

Streamlit 问答复用的是第 3 周的 `python/nl2sql.py`，配置全在 `.env`：

| 变量 | 示例 | 说明 |
|---|---|---|
| `LLM_PROVIDER` | `deepseek` | 目前实现 DeepSeek 兼容接口（OpenAI 协议） |
| `LLM_BASE_URL` | `https://api.deepseek.com/v1` | 换模型只改这里 |
| `LLM_MODEL` | `deepseek-flash` | 模型名 |
| `LLM_API_KEY` | `sk-...` | 只在 `streamlit` 与 `app` 容器注入，不污染其他服务 |

### 5.1 调用链

```
用户提问
  → nl2sql.ask()：注入维表取值 + 表结构 → 大模型生成 SQL
  → validate_sql()：只允许 SELECT/WITH，禁 DDL/DML，校验引用表白名单
  → hive_runner.run_sql()：执行
  → 失败则把错误回灌给模型重试一次
  → 结果 → 表格 + 自动推断图表
```

### 5.2 错误码与处置

界面按类型给「下一步做什么」，不给堆栈：

| 现象 | 原因 | 处置 |
|---|---|---|
| 402 / `Insufficient Balance` | 账户余额不足 | 充值或换 Key |
| 401 / `Invalid API key` | Key 错或过期 | 改 `.env` → 重启 `streamlit` |
| 429 / `Rate limit` | 触发限流 | 降低并发，或换模型 |
| `SqlRejected` | 生成了 DDL/DML 或越权表 | 正常防护，重述问题即可 |
| `Table not found` | 表名幻觉 | 已注入表清单，仍错则补 Prompt |
| 0 行结果 | 维度取值不存在 | 已注入维表取值，界面提示"换一个口径试试" |

### 5.3 无 Key 时的降级

`nl2sql` 有规则兜底：常见问题（GMV、订单量、品类排名、省份分布）走模板匹配，
不依赖大模型也能跑通闭环。答辩现场没网也能演示，只是覆盖的问题少一些。

---

## 六、Streamlit 配置

容器启动命令在 `docker-compose.yml` 里覆盖：

```yaml
command: ["streamlit", "run", "apps/streamlit_app.py",
          "--server.port=8501", "--server.address=0.0.0.0",
          "--server.headless=true", "--browser.gatherUsageStats=false"]
```

- `headless=true`：不弹浏览器（容器里没有浏览器）
- `gatherUsageStats=false`：关掉遥测，避免现场无外网时卡在统计上报
- 健康检查走 `/_stcore/health`（Streamlit 内置），不是 `/`

---

## 七、配置自检

一条命令检查所有关键配置是否就位（不打印密钥，只判断有无）：

```bash
python - <<'PY'
import pathlib
need = ["MYSQL_ROOT_PASSWORD", "CLICKHOUSE_PASSWORD", "SUPERSET_SECRET_KEY",
        "SUPERSET_ADMIN_PASSWORD", "LLM_API_KEY", "REFRESH_TOKEN"]
env = dict(l.strip().split("=", 1) for l in
           pathlib.Path(".env").read_text(encoding="utf-8").splitlines()
           if "=" in l and not l.startswith("#"))
for k in need:
    print(f'{k:<24}', "已配置" if env.get(k) else "缺失")
print("行尾 CR 数量:", pathlib.Path(".env").read_bytes().count(b"\r"))
PY
```

`行尾 CR 数量` 必须是 0 —— 不为 0 就说明 `.env` 存成了 CRLF，
口令尾部会带 `\r`，表现为「密码对但登录失败」。
