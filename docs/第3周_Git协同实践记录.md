# 第 3 周 · Git 团队协同实践记录

> 仓库：`github.com/CjL0107/ydsh-dw`　分支：`main` / `feature/w3-aov-nullfix` / `feature/nl2sql-dim-values`
> 本周提交：27 次（含 1 次合并提交）　时间跨度：2026-11-09 至 2026-11-10
> 说明：本项目为单人实训，团队协作流程按"分支 + PR + Review + Issue + 冲突解决"完整走一遍，
> 除 Fork 与 PR 创建两项依赖 GitHub 网页端操作外，其余均在本地真实执行并有提交记录可追溯。

---

## 一、分支模型

采用简化版 Git Flow：`main` 保持随时可交付，所有改动走 `feature/*` 分支，合并前必须过提交规范钩子。

```
main
 ├── e600195  fix(tools): 修复 metastore 中文注释与推送脚本探测判据
 ├── cfe80e0  fix(ads): 客单价显式转 DECIMAL(14,2) 统一精度
 ├── 2e75afd  fix(ads): 客单价分母加 NULLIF 避免除零        ← feature/w3-aov-nullfix
 └── cf433b9  merge(ads): 合并客单价除零修复与精度统一
```

`main` 始终对应远端最新提交，`git rev-list --count origin/main..HEAD` 为 0 才算真正交付。

---

## 二、提交规范与钩子拦截（真实发生）

仓库在 `.git/hooks/commit-msg` 挂了校验脚本，只放行 `<type>(<scope>): <subject>` 格式，且 scope 必须取自
`ods dwd dws ads quality clean docker sql docs tools readme git`。

**第一次提交就被拦下了**：

```
$ git commit -m "feat(dws,ads): 新增汇总层与应用层建表及 ETL 脚本"
提交被拒绝：信息不符合规范
```

原因是用了复合 scope `dws,ads`。处理方式不是绕过钩子（`--no-verify`），而是**按职责把一次提交拆成两次**：

- `029e174 feat(dws): 新增汇总层 7 张表与 ETL 脚本`
- `42e7989 feat(ads): 新增应用层 7 张表与 KPI 计算脚本`

拆完之后反而更符合"一次提交一件事"的原则，回滚粒度也更细。这条经验写进了本周的 AI 协同日志——
**钩子拦住你的时候，先想是不是自己的操作确实有问题，而不是想办法关掉钩子。**

---

## 三、Code Review 记录

评审对象为本分支全部改动，评审人视角为"月底要对账的业务方"。以下 6 条都是**真实发现并已修复**的问题，
不是事后编的清单。

### CR-01　特征重要度取错了模型

| 项 | 内容 |
| --- | --- |
| 位置 | `python/predict_gmv.py` 特征重要度段 |
| 问题 | 用 `best_model.feature_importances_` 取重要度，但最优模型是线性回归，没有这个属性，导致 `prediction_metrics.json` 里 `feature_importance_top8` 恒为空对象 |
| 影响 | 报告缺一块解释性证据，且不会报错，属于"静默失效" |
| 修改 | 改为优先取树模型（`models[used_tree]`）的重要度，与"哪个模型最优"解耦 |
| 验证 | 重跑后输出 `is_weekend 0.1258 / day 0.1080 / lag7 0.1069 ...` 共 8 项 |

### CR-02　NL2SQL 规则被关键词抢走

| 项 | 内容 |
| --- | --- |
| 位置 | `python/nl2sql.py` `offline_sql()` |
| 问题 | 问"最近 7 天每天的下单量"，被"订单量"关键词命中单指标分支，返回全年 GMV 汇总 |
| 影响 | 答非所问，且用户看不出错 |
| 修改 | 把时间序列意图（每天/每日/趋势/逐日/最近N天）提到单指标分支**之前**；补充"上月/本月"时间修饰 |
| 验证 | 同一问题现返回 `SELECT stat_date, order_cnt FROM ydsh_dws.dws_sales_day ORDER BY stat_date DESC LIMIT 7` |

### CR-03　归因基线分母口径不一致

| 项 | 内容 |
| --- | --- |
| 位置 | `python/ai_insight.py` 渠道/品类归因 |
| 问题 | 用「累计值 ÷ 366 天」算日均基线，但累计值只统计到异常日为止，天数不匹配 |
| 影响 | 1 月初的异常日算出"APP 渠道 +1036%"这种荒谬结果，全部维度都被判为"上涨" |
| 修改 | 改为对基线窗口 `[day-30, day-1]` 单独 `SUM/COUNT`，与量价分解用同一窗口 |
| 验证 | 7-28 案例渠道基线合计 74,334 元，与当日 GMV 基线完全对齐 |

### CR-04　文案硬编码与数据矛盾

| 项 | 内容 |
| --- | --- |
| 位置 | `python/ai_insight.py` `template_narrative()` |
| 问题 | 边界说明里写死"取消率高于基线"，但 7-28 当天取消率 12.0% 实际**低于**基线 12.6% |
| 影响 | 报告的结论与自己的表格打架，这是最容易被答辩追问的错 |
| 修改 | 改为按差值分支：高于基线 1 个百分点才说"抬升"，否则说明"下跌不在履约/支付环节" |
| 验证 | 两个案例分别产出对应文案，7-28 走后者，11-06（取消率 30%）走前者 |

### CR-05　取数被安全上限截断

| 项 | 内容 |
| --- | --- |
| 位置 | `python/hive_runner.py` `MAX_ROWS = 200` |
| 问题 | 全量日序列 366 行被静默截断到 200 行，`ai_insight` 只看到前 200 天 |
| 影响 | 异常检测漏掉下半年全部数据，且不报错 |
| 修改 | `run_sql()` 增加 `max_rows` 参数，默认仍走安全上限，批处理脚本显式放宽并自行保证 SQL 有聚合/LIMIT |
| 验证 | 日志由"读入 200 天"变为"读入 366 天" |

### CR-06　推送脚本探测判据不可靠

| 项 | 内容 |
| --- | --- |
| 位置 | `tools/push.sh` 第 2 步 |
| 问题 | 用 `curl` 请求 `api.github.com/zen` 判断代理是否可用 |
| 影响 | 小 GET 能过不代表 git 的 packfile 传输能过，实测遇到过 curl 正常但 push 反复 502 的端口 |
| 修改 | 判据换成 `git ls-remote --heads`，并加 `timeout 8` 与 `http.lowSpeedLimit` 防止挂在死端口上 |
| 验证 | 本周实际推送识别到端口 21600，一次成功，远端 HEAD 与本地一致 |

### Review 结论

6 条中 **3 条属于"静默失效"**（CR-01/05 不报错但结果错，CR-03 结果荒谬但流程正常）。这类问题的共同点是：
**代码跑得通、日志没红线、结果看起来像真的。** 只靠"跑一下没报错"根本发现不了，必须对着数据反查。
这是本周在协作规范上最重要的一条收获。

---

## 四、Issue 追踪

以下 Issue 按"发现 → 处理 → 关闭"记录，编号为本地追踪号。GitHub 端可通过仓库 Issues 新建，标题可直接复用。

| 编号 | 标题 | 标签 | 状态 | 对应提交 |
| --- | --- | --- | --- | --- |
| #W3-01 | Hive `describe` 中文注释显示为 `?` | `bug` `env` | 已关闭 | `e600195` |
| #W3-02 | 预测模型特征重要度输出为空 | `bug` `model` | 已关闭 | （随 6cc0839 一并修复） |
| #W3-03 | NL2SQL 时间序列提问被单指标分支拦截 | `bug` `nl2sql` | 已关闭 | `7e64e32` |
| #W3-04 | 异常归因渠道基线口径错误 | `bug` `insight` | 已关闭 | `0d19470` |
| #W3-05 | 日序列取数被 200 行上限截断 | `bug` `perf` | 已关闭 | `0d19470` |
| #W3-06 | push.sh 探测判据改用 git ls-remote | `enhancement` `tools` | 已关闭 | `e600195` |
| #W3-07 | 客单价分母除零风险 | `bug` `sql` | 已关闭 | `2e75afd` + `cf433b9` |

### #W3-01 处理经过（本周最难的一个）

**现象**：`describe ydsh_dws.dws_sales_day` 的 comment 列全部显示 `????`。

**排查链**：
1. 先怀疑 beeline 输出编码 → 改 `--outputformat=tsv2`，仍为 `?`，排除；
2. 怀疑容器 Java 侧编码 → 查 `file.encoding=UTF-8`，正常，排除；
3. 直接查 metastore 库 → `COLUMNS_V2.COMMENT` 与 `TABLE_PARAMS.PARAM_VALUE` 的字符集是 **latin1**，找到根因；
4. 把列 `ALTER` 成 utf8mb4 后重启，**仍然显示 `?`** —— 因为 `?` 是写入时 latin1 转换失败留下的替换字符，原始字节早已丢失，改字符集救不回来；
5. 最终方案：写 `tools/fix_metastore_comment.py`，从 `sql/**/*.sql` 的 DDL 里解析出 36 张表 / 292 个字段的中文注释，生成 331 条 `UPDATE` 回填 metastore，再用 utf8mb4 连接执行。同时把 `PARTITION_KEYS.PKEY_COMMENT`、`DBS.DESC` 等 5 个同类列一并转 utf8mb4。

**验证**：`describe` 中文正常；四层行数不变（15,000 / 37,163 / 366 / 15），数据未受影响。

**教训**：字符集问题分两种——"显示不对"和"存进去就错了"。前者改配置能救，后者只能回填。
一开始没区分这两种，所以在第 4 步白跑了一轮重启。

---

## 五、合并冲突实战

### 冲突怎么产生的

两个分支**独立地**修同一个 `aov` 表达式（`sql/ads/01_etl_ads.sql` 第 61 行附近）：

- `feature/w3-aov-nullfix`：分母加 `NULLIF(..., 0)`，防止某天没有有效订单时除零导致 ETL 中断；
- `main`：外层套 `CAST(... AS DECIMAL(14,2))`，统一精度。

两边目的不同、改的行相同，必然冲突。

### 冲突现场

```
$ git merge feature/w3-aov-nullfix
Auto-merging sql/ads/01_etl_ads.sql
CONFLICT (content): Merge conflict in sql/ads/01_etl_ads.sql
Automatic merge failed; fix conflicts and then commit the result.

$ git status --short
UU sql/ads/01_etl_ads.sql
```

```sql
<<<<<<< HEAD
        CAST(SUM(IF(status_code IN ('OS02','OS03','OS05'), pay_amount, 0))
          / SUM(IF(status_code IN ('OS02','OS03','OS05'), 1, 0))
             AS DECIMAL(14,2))                                                   AS aov
=======
        SUM(IF(status_code IN ('OS02','OS03','OS05'), pay_amount, 0))
          / NULLIF(SUM(IF(status_code IN ('OS02','OS03','OS05'), 1, 0)), 0)       AS aov
>>>>>>> feature/w3-aov-nullfix
```

### 解决原则：不选边，做并集

这个冲突不是"谁的写法对"，而是两个改动都必要，正确解是把两者叠加：

```sql
        CAST(SUM(IF(status_code IN ('OS02','OS03','OS05'), pay_amount, 0))
          / NULLIF(SUM(IF(status_code IN ('OS02','OS03','OS05'), 1, 0)), 0)
             AS DECIMAL(14,2))                                                   AS aov
```

### 验证后才提交

冲突解决不能"看着对"就提交。实际重跑了整条 ADS ETL：

| 指标 | 合并前 | 合并后 | 结论 |
| --- | --- | --- | --- |
| GMV | 27,342,828.20 | 27,342,828.20 | 一致 |
| 有效订单量 | 10,809 | 10,809 | 一致 |
| 客单价 AOV | 2,529.6353 | 2,529.64 | 一致（精度按要求归整到 2 位） |

三项对得上才执行 `git add` + `git commit`，合并提交信息里把冲突位置、解决方式和验证结果都写清楚。

### 冲突解决的三个判断顺序

1. **先看两边改动的意图**，而不是先看哪边代码"更顺眼"；
2. **能并集就并集**，只有意图互斥时才需要选边，选边必须在提交信息里说明放弃另一边的理由；
3. **必须跑验证**。这次是 SQL，验证就是重跑 ETL 对 KPI；如果是纯文档冲突，也要至少确认没有残留 `<<<<<<<` 标记（`grep -c "^<<<<<<<"` 应为 0）。

---

## 六、Fork 与 Pull Request（流程说明 + 待网页端补完）

### Fork 工作流的标准动作

1. 在 GitHub 上 Fork 主仓库到自己的账号；
2. `git clone` 自己账号下的副本，`git remote add upstream <主仓库地址>`；
3. 开 `feature/*` 分支开发，提交后 push 到自己的副本；
4. 在网页端发起 Pull Request（源：自己副本的 feature 分支 → 目标：主仓库 main）；
5. 走 Code Review，按意见修改后再 push（PR 会自动更新）；
6. 合并后删分支，`git pull upstream main` 保持同步。

### 本项目实际执行情况

单人实训没有第二成员，Fork 与 PR 的**评审环节无法真实发生**。已真实完成的部分：

- 分支创建、开发、提交（`feature/w3-aov-nullfix` → `2e75afd`；`feature/nl2sql-dim-values` → `7f8581e`）
- 两个分支均已推送到远端，`git ls-remote` 核对 HEAD 与本地一致
- 合并与冲突解决（`cf433b9`）
- 提交规范钩子拦截与整改

**未完成**：PR 的创建与网页端 Review 评审。这一步需要登录 GitHub 网页操作，本地 CLI 环境未安装 `gh`，
也没有配置 Personal Access Token，因此**不作虚假记录**。PR 描述文本已按规范拟好，可在网页端直接粘贴。

### 一次真实的踩坑：为什么 PR 建不出来

第一次尝试为 `feature/w3-aov-nullfix` 建 PR，页面提示
**"main is up to date with all commits from feature/w3-aov-nullfix"**。
排查后确认不是权限或网络问题，而是**那个分支没有独有提交**：

```bash
$ git merge-base --is-ancestor 2e75afd main && echo "已并入 main"
已并入 main
```

`2e75afd` 早已在 `main` 的历史里，分支与主干内容一致，GitHub 自然"没有东西可比较"。
教训是：**建 PR 前先确认 `ahead_by > 0`**，看的是提交列表而不是页面按钮。

后来改用确实有独有提交的 `feature/nl2sql-dim-values`（领先 `main` 8 个提交 / 16 个文件），
API 核对 `compare` 返回 `status: ahead, ahead_by: 8`，PR 就可以正常创建了。

### PR 描述（`feature/nl2sql-dim-values`，待网页端提交）

```markdown
## 变更内容
把 8 个维度列的真实取值注入 Prompt，并修复 schema 抽取丢失 42 个 decimal 列的问题。

## 背景
1. 模型写 WHERE province = '广东'，而库里存的是「广东省」，等值匹配查出 NULL；
   这类错误靠看字段名推不出来，只能把实际取值告诉它。
2. 更根本的一处：describe() 用 CSV 解析 Hive 元信息，而 decimal(16,2) 的类型名自带逗号，
   整行被切成 4 段、与 3 列表头对不齐后被静默丢弃。
   14 张表里 42 个 decimal 列全没进 schema，包括所有表的 gmv / gmv_share / aov / metric_value。

## 改动
- nl2sql_schema.py：新增 DIM_SPECS / extract_dims()，抽 8 维度 108 个取值到 dim_values.json
- nl2sql.py：Prompt 注入维度取值；修正 Few-shot 里写错的 province = '广东'；
  新增 check_dim_literals() 拦截库里不存在的维度值
- hive_runner.py：describe() 改走 tsv2；run_sql() 增加 sep 参数；
  部分行对不齐时向 stderr 告警；报错信息先过滤 SLF4J 噪声
- validate_llm.py：两边都返回 0 行时不再判"一致"

## 验证
整轮 8 题：执行 8/8、严格一致 7/8、宽松一致 8/8、人工判定正确 7/8（改造前 5/8）。
schema 字段数 70 → 112。明细见 python/outputs/llm_eval.md。

## 影响范围
- describe() 返回的列数会比以前多（以前 decimal 列被吞），下游如按列数写死需复核
- 跑一次 nl2sql_schema.py 重新生成 schema.json 与 dim_values.json
- 遗留：1 题部分正确（"销量"歧义）、两轮间仍有 4 题 SQL 被改写，
  7/8 为单次运行结果，不可外推
```

---

## 七、本周协同小结

| 维度 | 实际做到 | 缺口 |
| --- | --- | --- |
| 分支管理 | main + feature 分支，远端同步 | — |
| 提交规范 | 钩子强制校验，被拦截 1 次并整改 | — |
| Code Review | 6 条真实缺陷，全部修复并验证 | 无第二评审人，属自审 |
| Issue 追踪 | 7 个 Issue 全流程记录 | 未同步到 GitHub Issues |
| 合并冲突 | 真实冲突 + 并集解决 + 跑验证 | — |
| Fork / PR | 分支已推送，PR 描述已备 | PR 未在网页端创建 |

最有价值的一条结论：**"代码跑通了"和"结果是对的"是两件事。** 本周 6 条缺陷里有 3 条完全不报错，
只是安静地给出错误结果。协作流程（尤其是 Review 和验证）的价值，主要就体现在拦这类问题上。
