# 云购生活 · 第 1 周 AI 协同日志

- 团队：云购生活　成员：梁常金（独立完成，一人多岗）　填写日期：2026-10-27

> 说明：本表只记录**真实发生**的协作过程。凡 AI 给出错误结论的，均在「人工修改与验证」列如实写明是错在哪里、怎么纠正的，不做美化。

---

## 一、使用的 AI 工具 / 模型

- 工具名称：WorkBuddy（桌面端 AI 编程协作工具）
- 模型版本：DeepSeek-V4.1-Flash
- 配套使用：Docker 29.9.2 命令行、Hive 4.0.0 beeline、Git 2.55.0、Python 3.13（DuckDB / openpyxl / Pillow）
- 使用方式：AI 负责出方案、写脚本、判读报错；我负责在真机上执行、核对结果、决定是否采纳

---

## 二、AI 协同记录表（10 条）

| 序号 | 提问 / 任务描述 | AI 产出摘要 | 人工修改与验证 | 是否采纳 |
|---|---|---|---|---|
| 1 | 按任务书搭 Docker + Hive + MySQL 环境，给出可复现的编排文件 | 输出三服务 `docker-compose.yml`（mysql / hive-metastore / hive-server）与 `Dockerfile.hive` | 实机 `docker compose up -d --build` 失败：官方 `apache/hive:4.0.0` 镜像**缺 MySQL JDBC 驱动**，Metastore 起不来。按 AI 第二轮方案改为多阶段构建补装 `mysql-connector-j 8.4.0`，重建后正常 | 是（改后） |
| 2 | Docker Desktop 装完报 `WSL not installed`，怎么办 | 首轮建议执行 `wsl --install` | **这是错的**。该命令会去下载 367MB 商店包，本机实测速度仅 100~300 KB/s，三次均超时。改查系统服务发现 `LxssManager` 不存在，确认是 `Microsoft-Windows-Subsystem-Linux` 功能未启用，最终用 `dism /online /enable-feature` 离线启用两个功能后重启解决 | 否（首轮方案被推翻） |
| 3 | `registry-1.docker.io` 拉不动镜像，要配置加速器 | 给出 `daemon.json` 的 `registry-mirrors` 配置与多个候选源 | 实机逐个验证：`docker.1panel.live` 可匿名拉取（峰值 1.3 MB/s），`docker.1ms.run`/`daocloud` 需 token 不可用，`mirror.gcr.io`、`public.ecr.aws` 均不可达。最终只保留可用源，其余删除 | 部分采纳 |
| 4 | 本机 3306 被 MySQL80 占用，Hive 容器起不来 | 建议容器端口映射改为 `3307:3306` | 验证：宿主 `mysql -P 3307` 可连元数据库，容器内仍走 `mysql:3306`，Hive 配置无需改动。README 同步补充说明 | 是 |
| 5 | 宽表 `UNION ALL` 报 `Too many counters: 121 max=120` | 首轮给 `beeline --hiveconf tez.am.counters.max.keys=5000` | **无效**，报错依旧（Hive 不转发 `tez.*` 前缀参数）。第二轮定位到必须落配置文件：新增 `conf/tez-site.xml`（`max.keys=5000`、`max.groups=1000`），并挂载到 **`/opt/hive/conf`**。首次误挂 `/opt/tez/conf` 仍不生效，因为该目录不在 classpath 上；改挂后通过 | 是（改后） |
| 6 | 写 ODS 六表建表 + 导入脚本 | 输出 `create_ods.sql` / `load_ods.sql`，含 `skip.header.line.count=1` | 首版 SQL 用了 `IFNULL`，Hive 报 `Invalid function IFNULL`（那是 MySQL 函数）。人工全部替换为 `NVL` 后执行成功。行数校验结果 1000/600/60/15600/37549/60000 与数据字典一致 | 是（改后） |
| 7 | 质量扫描脚本里中文别名报 `ParseException` | 建议给中文别名加反引号 `` ` `` | 验证通过。由此把「Hive 中中文别名必须反引号」写进《环境搭建笔记》坑 13 | 是 |
| 8 | 清洗脚本要在 Hive 里跑，怎么批量执行 | 给 `docker exec -i hive-server beeline -f /sql/...` | 在 Git Bash 下执行报 `No FileSystem for scheme "C"`——Git Bash 会把 `/sql` 改写成 `C:/Program Files/Git/sql`。人工加 `MSYS_NO_PATHCONV=1` 前缀后正常，已写进 `scripts/run_all.sh` | 是（需补丁） |
| 9 | 搭本地裸仓库演练远程推送，克隆出来是空目录 | 定位到裸仓库 `HEAD` 仍指向 `refs/heads/master`，而分支已改名 `main` | 执行 `git --git-dir=<裸仓库> symbolic-ref HEAD refs/heads/main` 修复，重新克隆得到完整 11 次提交 | 是 |
| 10 | 把仓库推到真实 GitHub，`git push` 报 `502` 与 `could not read Username` | 先判为网关抖动，再定位到环境变量 `GIT_TERMINAL_PROMPT=0` 禁止终端输入且凭据库为空 | 按提示在浏览器完成一次 GCM 授权，凭据写入 Windows 凭据管理器后重推成功。事后复测 `git-receive-pack` 端点经代理/直连均返回 401（正常待认证），确认 502 确属抖动 | 是 |

---

## 三、AI 产出成果清单

**由 AI 生成（我逐条实机验证后采纳）**

- 编排与构建：`docker-compose.yml`、`Dockerfile.hive`、`conf/tez-site.xml`
- SQL：`sql/ods/create_ods.sql`、`sql/ods/load_ods.sql`、`sql/quality/quality_assess.sql`、`sql/clean/clean_{user,product,order}.sql`
- 工具脚本：`tools/profile_data.py`、`tools/validate_cleaning.py`、`tools/check_repo.py`、`tools/git-hooks/commit-msg`、`tools/install-hooks.{ps1,sh}`、`scripts/run_all.{ps1,sh}`
- 文档初稿：`docs/环境搭建笔记.md`、`docs/数据质量预评估表.md`、`docs/Git协作规范.md`、`docs/Docker常用命令手册.md`、`README.md`
- 表格：`docs/数据质量预评估表.xlsx`（8 个工作表）

**由人工编写 / 决策**

- 全部实机执行、报错判读与最终取舍（如推翻 `wsl --install` 方案、废弃 `--hiveconf` 方案）
- 15 个踩坑的现象描述与复现步骤（AI 只给结论，现象是我一条条试出来的）
- 清洗策略的取值决策：邮箱**保留空值不编造**、上架日期用同品类最早日期而非 `1970-01-01`、订单去重按 `order_date + order_time` 排序取首条
- 数据质量各维度的口径认定（如「用户城市 ≠ 下单门店城市」属口径问题而非脏数据）

**人机协作完成**

- 环境搭建笔记：AI 出结构，我补现象与截图
- 数据质量预评估表：AI 组织表格，我提供实测数字并核对
- Git 规范：AI 出规则文本，我用 6 种边界用例实测钩子是否真的拦得住

---

## 四、问题与解决

**遇到的问题**

本周共遇到 15 个阻塞性问题，其中 6 个属于「AI 首轮方案不可用，必须人工纠正」：

1. Docker Hub 直连不可达 → 镜像加速器（AI 给的 4 个源里只有 1 个可用）
2. Docker Desktop 报 WSL 未启用 → `dism` 离线启用（AI 首轮 `wsl --install` 会超时）
3. Hive 镜像缺 JDBC 驱动 → 多阶段构建补装
4. 3306 端口冲突 → 映射改 3307
5. Tez 计数器超限 → 挂 `tez-site.xml` 到 `/opt/hive/conf`（`--hiveconf` 与 `/opt/tez/conf` 两种方案均无效）
6. Hive 不支持 MySQL 函数 `IFNULL` → 全量改 `NVL`

**AI 是否有效解决**

部分有效。AI 在**定位方向**上价值很大：`Too many counters` 这类 Tez 内部报错，靠自己翻文档至少半天，AI 一次就点到「配置未落到 classpath」。但 AI 在**具体参数与方言细节**上不可靠，本周至少 3 次给出「看起来对、跑起来错」的命令（`wsl --install`、`--hiveconf tez.*`、`IFNULL`），全部靠实机执行才暴露。

**最终如何解决**

统一改成「AI 出方案 → 我实机跑 → 失败则带完整报错回问 → 迭代」的闭环，并把每次修正落进《环境搭建笔记》，避免同类问题重复踩。最终 6 张表行数、清洗结果在 Hive 与 DuckDB 两条独立路径上交叉验证一致，7 个质量扫描结果集逐项吻合。

---

## 五、反思与总结

**AI 协同带来的价值**

- 环境搭建的「黑暗时间」被大幅压缩。像 Tez 计数器上限、Hive 不转发 `tez.*` 参数这类冷门知识，AI 能直接给方向，省掉大量检索。
- 样板代码生成快。6 张建表语句、清洗脚本骨架、自检工具，AI 一次成型，我只做校对与跑通。
- 报错解读。`open //./pipe/dockerDesktopLinuxEngine`、`remote HEAD refers to nonexistent ref` 这类信息，AI 能一句话点出根因。

**AI 的局限与风险**

- **方言幻觉**：把 MySQL 函数当 Hive 函数写（`IFNULL`），不跑就发现不了。
- **参数编造**：`--hiveconf tez.am.counters.max.keys` 是真实存在的参数名，但通过 CLI 传不进 Tez 上下文，AI 没有主动提示这个前提。
- **过度自信**：`wsl --install` 是官方推荐命令，但它不告诉你国内网络会卡在下载环节。AI 给的是「通用正确」，不是「此处正确」。
- **依赖风险**：如果我不实机验证就直接采用，本周至少 6 处会埋雷，答辩现场演示时必然翻车。

**本次协作中我的能力提升**

- 学会用「最小复现」定位问题：先用 `docker ps` 看容器状态、再用 `describe` 看表结构、最后才看日志，比上来就翻日志快得多。
- 理解了 Hive 的配置加载顺序（`/opt/hive/conf` 在 classpath、`/opt/tez/conf` 不在），这类知识靠 AI 一句话 + 自己验证一次才真正记住。
- Git 从「会 add/commit」推进到能处理裸仓库 HEAD、合并节点、提交信息钩子校验这类真实协作问题。

**对 AI 协同的改进建议**

1. **凡涉及具体命令，一律先小成本试跑**，不要让 AI 的方案直接进主流程。本周 `wsl --install` 如果直接照做，会白等半小时。
2. **让 AI 给出「验证方法」而不只是「解决方法」**。判断是否真的解决了，靠的是 `docker ps` 的输出，不是 AI 说「已修复」。
3. **AI 的错误要留痕**。本日志第 2、5、6 条就是反例记录，比只写成功案例更有参考价值。
4. **数据类结论必须双路交叉**。Hive 跑一遍、DuckDB 再算一遍，两边数字一致才敢写进交付物。
