# 云购生活 · 企业级数据仓库与经营分析可视化系统

> 课程：企业项目实战实训｜数据科学与大数据技术
> 团队：<团队名称>｜口号：<团队口号>
> 当前进度：**第 1 周 —— 项目启动 · 环境搭建 · 数据导入与清洗（一）**

---

## 一、项目背景

云购生活（虚构）是一家覆盖全国 15 个省份、拥有 60 家线下门店的连锁新零售企业，经营模式为「线上商城 + 线下门店」融合。2024 年积累的订单、用户、商品、行为数据分散在不同业务系统中，存在缺失、重复、格式混乱等问题。

本项目以真实企业交付标准，从 0 到 1 建设一套**数据仓库 + 经营分析可视化系统**：数据采集 → 清洗 → 四层数仓建模 → 指标计算 → 预测建模 → NL2SQL 智能问答 → 可视化大屏 → Docker 一键部署。

## 二、六层系统架构

```
┌─────────────────────────────────────────────────────────────┐
│  展示层    Superset 仪表板  +  ECharts 数据大屏              │ 第4周
├─────────────────────────────────────────────────────────────┤
│  智能层    NL2SQL 自然语言查询  +  AI 经营洞察               │ 第3周
├─────────────────────────────────────────────────────────────┤
│  分析层    KPI 指标计算  +  时间序列预测（sklearn）          │ 第3周
├─────────────────────────────────────────────────────────────┤
│  数仓层    ODS → DWD → DWS → ADS（Hive 四层建模）            │ 第2-3周
├─────────────────────────────────────────────────────────────┤
│  数据层    6 张业务表采集导入 + 五类脏数据清洗               │ 第1-2周
├─────────────────────────────────────────────────────────────┤
│  工程层    Git 版本控制  +  Docker Compose 一键部署          │ 全程
└─────────────────────────────────────────────────────────────┘
```

## 三、技术栈

| 类别 | 技术 / 工具 | 用途 |
|---|---|---|
| 容器 | Docker Desktop + Docker Compose | 环境隔离与一键部署 |
| 数仓 | Apache Hive 4.0.0 + MySQL 8.0 | 四层数仓存储与 SQL 计算 |
| 计算 | Hive SQL（窗口函数 / 分区表） | 清洗与指标计算 |
| 分析 | Python 3.11、pandas、scikit-learn | 探索性分析与预测建模 |
| 智能 | DeepSeek / 通义千问 API + LangChain | NL2SQL 与 AI 洞察 |
| 可视化 | Apache Superset、ECharts、Streamlit | 仪表板与数据大屏 |
| 协作 | Git / GitHub、Conventional Commits | 版本管理与团队协作 |

## 四、目录结构

```
ydsh-dw/
├── docker-compose.yml          # Hive + MySQL 编排（三个容器）
├── Dockerfile.hive             # 为 Hive 镜像补装 MySQL JDBC 驱动
├── .gitignore                  # 忽略数据文件/密钥/日志
├── data/                       # 模拟数据 CSV（不进版本库）
├── sql/
│   ├── ods/
│   │   ├── create_ods.sql      # 6 张 ODS 贴源表建表
│   │   └── load_ods.sql        # 数据导入 + 行数校验
│   ├── quality/
│   │   └── quality_assess.sql  # 五维度数据质量扫描
│   └── clean/
│       ├── clean_user.sql      # 用户表缺失值处理 → dwd_user
│       ├── clean_product.sql   # 商品表缺失值处理 → dwd_product
│       └── clean_order.sql     # 订单表去重 → dwd_order
├── scripts/                    # 一键执行脚本
├── tools/
│   ├── profile_data.py         # 离线数据画像（建表前置）
│   └── validate_cleaning.py    # 清洗逻辑离线验算（交叉校验）
├── docs/
│   ├── 环境搭建笔记.md
│   └── 数据质量预评估表.md
└── deliverables/               # 交付物汇总
```

## 五、快速开始

### 1. 准备数据

把 6 个 CSV 放到 `data/` 目录（文件名必须一致）：

```
data/ods_user.csv  ods_product.csv  ods_store.csv
data/ods_order.csv ods_order_item.csv ods_user_behavior.csv
```

### 2. 启动环境

```powershell
docker compose up -d --build          # 首次需要 --build
docker ps                             # 确认三个容器 Up
```

### 3. 建表 + 导入 + 校验

```powershell
# Windows PowerShell
.\scripts\run_all.ps1
```

```bash
# Git Bash / WSL
bash scripts/run_all.sh
```

### 4. 单独执行某个脚本

```powershell
docker exec -i hive-server beeline -u "jdbc:hive2://localhost:10000/default" -f /sql/clean/clean_order.sql
```

### 5. 离线验算（不需要 Docker，用来交叉核对 Hive 结果）

```bash
pip install duckdb
python tools/profile_data.py --data-dir data
python tools/validate_cleaning.py --data-dir data
```

## 六、数据说明

| 文件 | ODS 表 | 行数 | 说明 |
|---|---|---:|---|
| ods_user.csv | ods_user | 1,000 | 用户维度 |
| ods_product.csv | ods_product | 600 | 商品维度 |
| ods_store.csv | ods_store | 60 | 门店维度 |
| ods_order.csv | ods_order | 15,600 | 订单事实（含 600 条重复） |
| ods_order_item.csv | ods_order_item | 37,549 | 订单明细事实 |
| ods_user_behavior.csv | ods_user_behavior | 60,000 | 用户行为日志 |
| **合计** | — | **114,809** | >10 万条，满足企业级数据规模要求 |

> CSV 为 UTF-8 带 BOM，字段内不含逗号与引号，可直接用 `FIELDS TERMINATED BY ','` 导入。

## 七、团队分工

| 姓名 | 角色 | 主要职责 | 第 1 周任务 |
|---|---|---|---|
| <姓名> | 项目经理 | 进度管控、文档汇总、答辩汇报 | 项目规划、README、答辩 PPT |
| <姓名> | 数据工程师 | 环境搭建、数仓建模、ETL | Docker/Hive 环境、ODS 建表导入 |
| <姓名> | 数据分析师 | 数据质量、清洗、指标计算 | 五维度评估、清洗脚本 |
| <姓名> | 可视化工程师 | 仪表板、大屏、前端 | Git 仓库规范、可视化准备 |

## 八、Git 提交规范

采用 [Conventional Commits](https://www.conventionalcommits.org/)：

| 前缀 | 用途 | 示例 |
|---|---|---|
| `feat:` | 新增功能/脚本 | `feat: 新增 ODS 层 6 张表建表脚本` |
| `fix:` | 修复问题 | `fix: 修正订单去重排序规则` |
| `docs:` | 文档变更 | `docs: 补充数据质量预评估表` |
| `refactor:` | 重构（不改功能） | `refactor: 抽取 run_sql 公共脚本` |
| `chore:` | 杂项（配置/依赖） | `chore: 配置 .gitignore` |
| `test:` | 测试相关 | `test: 新增行数校验用例` |

**提交要求**
- 一个提交只做一件事，message 用中文描述清楚"做了什么"
- 禁止把 `data/*.csv`、`.env`、`*.log` 提交上去
- 每天结束前 `git push`，确保答辩时远程仓库有完整历史

## 九、进度看板

| 周次 | 主题 | 状态 |
|---|---|---|
| 第 1 周 | 启动 · 环境 · Git · 数据导入与清洗（一） | 🚧 进行中 |
| 第 2 周 | 清洗进阶 · 四层数仓建模 · 探索性分析 | ⬜ 未开始 |
| 第 3 周 | 数据分析 · 预测建模 · NL2SQL | ⬜ 未开始 |
| 第 4 周 | 可视化（Superset + ECharts）· Docker 部署 | ⬜ 未开始 |
| 第 5 周 | 系统集成 · 项目验收 · 答辩 | ⬜ 未开始 |
