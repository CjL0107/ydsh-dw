# NL2SQL 维度值注入改造

> 分支：`feature/nl2sql-dim-values`
> 动机：第 3 周评估报告里 8 题实测，`广东省全年销售额` 一题模型写成 `WHERE province = '广东'`，
> 查出 NULL；`客单价` 一题写成 `WHERE metric_name = '客单价'`，查出 0 行。
> 两次都不是语法错误，SQL 都执行成功了，只是答案不在里面。

---

## 一、根因：模型不知道库里存的是什么

Schema 注入只告诉了模型"表叫什么、字段叫什么、字段什么意思"，
但没告诉它**字段里存的是什么值**。

问「广东省销售额」，模型看到字段注释是「省份」，很自然地写 `= '广东'`。
而库里存的是 `广东省`。这个信息在 Schema 里根本不存在，模型只能猜。

更难看的是：追下去发现**我自己的 Few-shot 示例就是错的**。

```python
# 改造前
{"q": "2024 年广东省的销售额是多少？",
 "sql": "... WHERE province = '广东'"}   # ← 示例教给模型的就是错的写法
```

模型照着示例抄，我却在报告里把它记成"模型的错误"。这条得改回来：
**错的不是模型，是我给的示例。** 提示词里的每个字都是训练数据。

---

## 二、方案：三层，各自独立生效

### 第 1 层：抽取维度实际取值，注入 Prompt

`nl2sql_schema.py` 新增 `extract_dims()`，对 8 个维度列做 `GROUP BY` 取真实值，
写入 `python/outputs/dim_values.json`，再拼成一段文本插进 system prompt。

```
维度实际取值（WHERE 等值匹配必须逐字照抄，不要简化成简称）：
- ydsh_dws.dws_sales_store_day.province（省份）：'浙江省'、'湖南省'、…、'广东省'
- ydsh_dws.dws_sales_store_day.city（城市）：'上海'、'德阳'、…、'深圳'
- ydsh_ads.ads_kpi_summary.metric_name（KPI 指标中文名）：'客单价 AOV'、'复购率'、…
```

抽取过程本身暴露了一个事先想不到的事实：

| 维度 | 存法 | 例子 |
|---|---|---|
| 省份 | **带**行政区后缀 | `广东省`、`北京市`、`重庆市` |
| 城市 | **不带**"市" | `深圳`、`上海`、`北京` |

同一个库里两套规则。这种不一致靠读注释看不出来，只能查数据。
所以"注入取值"不是锦上添花，是唯一可靠的办法。

### 第 2 层：修正 Few-shot 示例

把示例里的 `province = '广东'` 改成 `province = '广东省'`。
成本为零，但影响最大——示例的示范作用强于任何一条文字约束。

### 第 3 层：结果侧校验（执行前拦截）

生成侧管不住就守结果侧。`check_dim_literals()` 在执行前扫描
`col = 'v'` 与 `col IN (...)`，比对字面量是否真在取值表里；
不在就用 `difflib` 给个候选建议，把提示回灌给模型重试一次。

```
省份简称      province = '广东'   → 报错，建议 '广东省'
城市多写市    city = '深圳市'      → 报错，建议 '深圳'
LIKE 模糊     province LIKE '%广东%' → 放行（模糊匹配本来就能命中）
品类正确      category = '运动户外'  → 放行
```

`LIKE` 不做检查是刻意的：写 `%广东%` 能命中 `广东省`，本来就没错，
把它拦下来是误伤。

---

## 三、实测（单题验证，在 Hive 正常时执行）

| 题 | 改造前 SQL | 改造后 SQL | 结果 |
|---|---|---|---|
| 广东省上月销售额 | `province = '广东'` | `province = '广东省'` | 47,992.90 元 |
| 客单价是多少 | 漏 `metric_value`，`metric_name='客单价'` 查 0 行 | `WHERE metric_key = 'aov'`，带 `metric_value` | 2,529.64 元 |
| 深圳市 2024 销售额 | — | `WHERE city = '深圳'` | 未写"深圳市"，一次就对 |
| 数码家电品类全年 | — | `WHERE category = '数码家电'` | 口径正确 |

**成本**：prompt 从约 1,500 tokens 涨到约 3,050 tokens，翻了一倍；schema 补齐 42 列后再涨到约 3,700。
8 个维度、108 个取值全量注入，没有做相关性裁剪。

这个代价需要说清楚：如果只注入与问题相关的维度（问题里出现"广东"才注入省份表），
可以把增量压到 200 tokens 以内。但裁剪依赖意图识别，识别错了就漏注入，
反而更糟。当前选择"宁可多塞"，等有更多样本再评估裁剪的收益。

---

## 四、意外发现：验证脚本给出过假的 8/8

改造中途跑整轮 8 题，结果是 **执行 8/8、严格一致 8/8**。
比改造前的 4/8 翻了一倍，好得可疑，于是去核对生成的 SQL。

结果发现 json 里 8 条记录有 7 条 `rows = 0`、`ref_rows = 0`。
两边都是空列表，数值集合相等，`same_result()` 就判了"一致"。

真正的根因是 Docker Desktop 挂了，`docker exec` 把错误写进 stderr、
stdout 为空，`run_sql()` 解析出零行并 `return []`；
调用方以为"查询没有数据"，比对函数以为"两个空结果相同"。
三层都没有报错，一路安静地走到满分。

三个修法，缺一不可：

1. `run_sql()` 检查 `returncode` 与 stderr，容器不可用直接抛异常，不再返回空列表
2. 表头取错导致一行都对不上时抛异常，而不是静默返回空
3. `same_result()` 两边都为空时判"无法判定"，不再回退成"相等"

这件事比原本要解决的问题更值得记：

- **验证脚本自身也需要被验证**。它能报错、能给出可疑的高分，但没有机制检查自己是否在工作。
- **"结果变好了"是最该警惕的信号**。如果这次改造只把 4/8 提到 5/8，我可能不会去查 json；
  正是"翻倍"这种不合常理的提升，才逼出了核对。
- 三层判据（执行成功 / 严格一致 / 宽松一致）当时设计了三个维度，
  但三个维度**共用同一份空数据**，所以一起失效。判据多不等于判据独立。

---

## 五、状态：整轮复测已完成

Docker Desktop 恢复、`mysql` 与 `hive-metastore` 两个容器重新拉起后，整轮复测已跑完。

环境恢复过程中还确认了一件事：容器 `State` 显示 `Up` 不代表服务可用。
这次 `hive-server` 是 `Up`，但 `mysql` 与 `hive-metastore` 都是 `Exited (255)`，
HS2 连不上 metastore，10000 端口根本没有监听。所以"容器起来了"要看的是**端口通不通、SQL 能不能跑**，
不是 `docker ps` 里那行绿字。

| 阶段 | Schema 字段 | 执行成功 | 严格一致 | 宽松一致 | 人工正确 |
|---|---|---|---|---|---|
| 改造前 | 70 | 8/8 | 4/8 | 6/8 | 5/8 |
| 维度值注入后（当时 Docker 挂、为假 8/8，已作废） | 70 | — | — | — | — |
| 最终（维度注入 + 修复 decimal 列丢失） | 112 | 8/8 | 7/8 | 8/8 | 7/8 |

期间发现的第二个 bug（42 个 decimal 列被静默丢弃）见第七节——它比维度注入本身影响更大。

仓库里的 `python/outputs/llm_eval.*` 已用本轮真实数据覆盖，作废那次假的 8/8 没有留存。

复现命令：

```bash
python python/nl2sql_schema.py      # 重抽 schema + 维度取值
python python/validate_llm.py       # 整轮 8 题复测
python python/validate_llm.py --reuse   # 只改人工复核、重渲染报表，不再花 token
```

推送当时也确实卡住过，这里留一份记录，因为它和"容器 Up 不等于可用"是同一类经验——**环境报的绿灯不算数**：

```
$ curl https://api.github.com/zen          # 通
200

$ curl https://github.com/                 # 不通
000

$ git push origin feature/nl2sql-dim-values
fatal: unable to access '...': CONNECT tunnel failed, response 502
```

`api.github.com` 能通、`github.com` 主域不通，代理对所有本地端口都返回 502，
`ssh.github.com:443` 与 `github.com:22` 均不可达，也没有配置 SSH 密钥。

绕行尝试：写了个本地 CONNECT 转发器（`tools/local_connect_proxy.py`），
把 `github.com:443` 直接打到 `20.205.243.166`，`curl --resolve` 能拿到 200，
但 TCP 随后被重置，转发器报 `TimeoutError`。说明那个 IP 的"200"是残缺响应，
链路层本来就不通，代码绕不过去——这一条留着免得以后再试一遍。

最终是代理恢复后推上去的。`tools/push.sh` 会自动扫描本机监听端口、
逐个做 git 握手探测，找到能用的那个再推，所以代理端口每次重启都变也不用手改：

```
==> 2/3 逐个探测能否穿透 GitHub
    ✔ 127.0.0.1:21560  git 握手成功
==> 3/3 使用 127.0.0.1:21560 推送 origin/feature/nl2sql-dim-values
 * [new branch]      feature/nl2sql-dim-values -> feature/nl2sql-dim-values
```

---

## 六、顺带发现：`客单价` 暴露的是同一类问题

第一次修完只注入了省/市/渠道/品类，`客单价` 那题仍然错。
排查发现 `metric_name` 字段存的也是"维度值"——库里的值是 `客单价 AOV`，
不是 `客单价`。KPI 表是竖表，指标名本质上和省份一样，都是需要精确匹配的枚举。

所以维度注入的清单从 6 个扩到 8 个（加 `metric_key`、`metric_name`）。
教训是：**"维度列"不等于"业务维度"**，凡是用来做等值过滤的字符串列，都在这个范围内。

---

## 七、没预料到的主线：Schema 少了 42 列

维度注入做完、整轮复测跑通之后，还有两题不对：客单价漏查 `metric_value`、
渠道占比选了 `order_cnt` 而不是 `gmv_share`。我一开始仍按"模型不稳"解释，
直到收尾时顺手核了一下 `ads_channel_contribution` 的列：

```sql
-- Hive 里 7 列
describe ydsh_ads.ads_channel_contribution;
-- channel, channel_code, gmv, gmv_share, order_cnt, user_cnt, aov

-- 抽取脚本只认得 4 列
抽取 ydsh_ads.ads_channel_contribution: 4 个字段
```

缺的正好是 `gmv`、`gmv_share`、`aov`——三个 decimal 列。继续查：

Hive 的 `DESCRIBE` 输出的类型名是 `decimal(16,2)`，**自带一个逗号**。
`describe()` 用的是 `--outputformat=csv2`，`csv.reader` 把
`gmv,decimal(16,2),GMV` 切成 **4** 段，而表头只有 **3** 列，长度不等 → 计入 `bad` → `continue` 丢弃。

静默是最要命的部分：

- 原兜底只在「**一行都**对不齐」时抛异常，丢一部分完全无声；
- 脚本打印的 `4 个字段` 太正常了，没人会去数一个 7 列的表为什么只抽到 4 列；
- 于是 `schema.json` 从 112 列缩到 70 列，14 张表里所有 decimal 指标列全部没进 Prompt。

丢的列清点下来是 42 个：`gmv`、`gmv_share`、`aov`、`discount_amount`、`cancel_rate`、
`mom_rate`、`gmv_dod`、`gmv_ma7`、`metric_value`、`avg_unit_price`、`total_gmv`、`user_share`……
**一张表都没躲过。**

摆在一起看就很荒谬：整个项目天天在算 GMV，而模型从头到尾没在 schema 里见过 `gmv` 这一列。
它"漏查 `metric_value`"、"选了 `order_cnt` 而不是 `gmv_share`"，不是判断失误，是眼前只有这几个字段可选。

修复两处：

1. `describe()` 改用 `--outputformat=tsv2`（Tab 分隔），类型名里的逗号不再被当分隔符；
2. `run_sql()` 在"部分行对不齐"时向 stderr 告警，这类丢失必须先叫出来，哪怕不中断执行。

修完字段数 70 → 112，同一模型同一题集人工判定 5/8 → 7/8。

这件事和第四节暴露的"假 8/8"是同一个病根：**静默返回不完整的结果，比直接报错危险得多。**
报错会被看见，少一半列的 schema 会被当成正常继续用。
所以下一个该补的护栏是「Schema 完整性自检」——抽取完成后校验
「每张表抽到的列数 == `SHOW COLUMNS` 的真实列数」，不一致就报错。
它防的不是模型，是防自己骗自己。

还有一层更该记住的：这次涨分**不能当成功故事讲**。
如果只看 5/8 → 7/8，很容易归因为"多跑几次模型就稳了"；
真相是上一版的坏成绩由我自己制造的坏输入测出来，不该记在模型账上。
**分数变好和事情变对，不是同一件事。**
