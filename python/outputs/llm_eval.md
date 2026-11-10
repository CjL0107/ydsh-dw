# 大模型真实调用验证结果

- 执行时间：2026-11-10 08:16:15
- 服务商 / 模型：deepseek / deepseek-flash
- 题目数：8　执行成功：8/8　严格一致：4/8　宽松一致：6/8　人工判定正确：5/8
- 累计 prompt tokens：12,093　累计耗时：16.02s　平均单题：2.00s

> 三个判据回答三个不同问题：**执行成功**=SQL 能在 Hive 上跑通；**严格一致**=数值集合与参考 SQL 完全对应；**宽松一致**=参考结果的数值都被覆盖（允许多查列）。**都不是「语义正确」**——语义由人工复核，见最后一列。

## 逐题结果

| # | 问题 | 执行 | 严格 | 宽松 | 耗时(s) | tokens | 人工复核 |
|---|------|------|------|------|---------|--------|----------|
| 1 | 2024 年全年 GMV 是多少？ | 成功 | ✔ | ✔ | 1.85 | 1513 | 正确 |
| 2 | 客单价是多少？ | 成功 | — | ✘ | 2.35 | 1507 | 错误 |
| 3 | 销量最高的前 5 个品类有哪些？ | 成功 | — | ✔ | 1.43 | 1513 | 部分正确 |
| 4 | 各渠道的 GMV 占比是多少？ | 成功 | — | ✔ | 1.08 | 1512 | 正确 |
| 5 | 复购率是多少？ | 成功 | ✔ | ✔ | 3.93 | 1508 | 正确 |
| 6 | 广东省全年销售额是多少？ | 成功 | — | ✘ | 1.4 | 1508 | 错误 |
| 7 | 最近 7 天每天的下单量 | 成功 | ✔ | ✔ | 2.53 | 1512 | 正确 |
| 8 | 2024 年 11 月 6 日的 GMV 是多少？ | 成功 | ✔ | ✔ | 1.45 | 1520 | 正确 |

## 模型生成的 SQL 与比对说明

**1. 2024 年全年 GMV 是多少？**　`全表聚合，考表选择与聚合函数`

```sql
SELECT ROUND(SUM(gmv), 2) AS gmv FROM ydsh_dws.dws_sales_day WHERE stat_date BETWEEN '2024-01-01' AND '2024-12-31'
```
结果 1 行，与参考 SQL：一致；重试 0 次

人工复核：**正确** — 显式加了 stat_date BETWEEN '2024-01-01' AND '2024-12-31'。本数据集恰好只有 2024 年数据，但模型不知道这点，主动加约束是对的。结果 27,342,828.20 与 DWS 一致。

**2. 客单价是多少？**　`考 KPI 宽表的 metric_key 过滤；易错点是自己去算 SUM/COUNT 导致口径不一致`

```sql
SELECT metric_key, metric_name, unit, definition FROM ydsh_ads.ads_kpi_summary WHERE metric_name LIKE '%客单价%'
```
结果 1 行，与参考 SQL：缺值：参考中的 2,529.64 在模型结果里找不到（模型 0 值 / 参考 1 值）；重试 0 次

人工复核：**错误** — 写的是 SELECT metric_key, metric_name, unit, definition FROM ads_kpi_summary WHERE metric_name LIKE '%客单价%'——选对了表、选对了过滤条件，但**把 metric_value 漏了**。结果返回 1 行，全是元信息，没有数值。这是最阴的一类错误：行数非空、执行成功、看起来一切正常，唯独答案不在里面。上一轮同一道题它写的是 SUM(gmv)/SUM(paid_order_cnt) 自算，两次结果完全不同。

**3. 销量最高的前 5 个品类有哪些？**　`考 TopN 语义；易错点是拿 DWS 明细自己排而不是用 ADS 现成结果`

```sql
SELECT rank_no, category, gmv, item_qty FROM ydsh_ads.ads_category_top10 WHERE rank_no <= 5 ORDER BY rank_no
```
结果 5 行，与参考 SQL：宽松一致（模型多返回 5 个数值，列选多了）；重试 0 次

人工复核：**部分正确** — 返回 rank_no、category、gmv、item_qty，但排序直接沿用 rank_no。而 rank_no 在 ETL 里是 ROW_NUMBER() OVER (ORDER BY gmv DESC)，按 GMV 排的。题目问「销量最高」，它给的是「GMV 前 5 名及其件数」。题目本身有歧义（销量指 GMV 还是件数），但模型既没按件数重排也没澄清歧义，属于照搬现成排名。

**4. 各渠道的 GMV 占比是多少？**　`考占比字段；易错点是自己算分母导致与 ADS 口径不符`

```sql
SELECT channel, gmv, gmv_share FROM ydsh_ads.ads_channel_contribution ORDER BY gmv DESC
```
结果 4 行，与参考 SQL：宽松一致（模型多返回 4 个数值，列选多了）；重试 0 次

人工复核：**正确** — 返回 channel、gmv、gmv_share 三列，比只给占比更有用。严格判据判「不一致」只因多带了一列 gmv，不是算错——这正是补宽松判据的原因。占比合计与 ADS 层一致。

**5. 复购率是多少？**　`考指标别名；易错点是编一个自己没有的指标口径`

```sql
SELECT ROUND(SUM(CASE WHEN order_cnt > 1 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 2) AS repurchase_rate FROM ydsh_dws.dws_user_order_agg
```
结果 1 行，与参考 SQL：一致；重试 0 次

人工复核：**正确** — 自己写了 SUM(CASE WHEN order_cnt > 1 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) 去 dws_user_order_agg 算，口径与 KPI 表的 repeat_rate（复购用户数/去重用户数）一致，实测 1000 用户全部 order_cnt>1，结果 100% 正确。但它绕开了已定义好的 KPI 表，等于把指标口径复制了一份到 SQL 里——KPI 定义一变，这条 SQL 会静默给出旧口径。上一轮它写的是 WHERE metric_name='复购率' OR metric_key='repurchase_rate'（后者根本不存在，靠中文名兜住）。

**6. 广东省全年销售额是多少？**　`考维度过滤；易错点是写成 = '广东' 而非 LIKE，或漏掉省字`

```sql
SELECT ROUND(SUM(gmv), 2) AS gmv FROM ydsh_dws.dws_sales_store_day WHERE province = '广东' AND stat_date BETWEEN '2024-01-01' AND '2024-12-31'
```
结果 1 行，与参考 SQL：缺值：参考中的 472,606.11 在模型结果里找不到（模型 0 值 / 参考 1 值）；重试 0 次

人工复核：**错误** — 写成 province = '广东'，而维度表实际取值是「广东省」，等值匹配 0 行，SUM 返回 NULL。正是本题 probe 预判的坑，也是 NL2SQL 最典型的一类错误：把「用户怎么说」当成「字段里怎么存」。正确写法是 LIKE '%广东%'。执行成功、无报错，只能靠人看出来。

**7. 最近 7 天每天的下单量**　`考时间序列 + 排序 + 限行三件事同时成立；这是规则兜底最容易翻车的题`

```sql
SELECT stat_date, order_cnt FROM (SELECT stat_date, order_cnt FROM ydsh_dws.dws_sales_day ORDER BY stat_date DESC LIMIT 7) t ORDER BY stat_date
```
结果 7 行，与参考 SQL：一致；重试 0 次

人工复核：**正确** — 子查询先 ORDER BY stat_date DESC LIMIT 7 取最近 7 天，外层再按时间正序输出，比参考 SQL 直接倒序更贴合阅读习惯。

**8. 2024 年 11 月 6 日的 GMV 是多少？**　`考日期字面量格式；易错点是写成 2024/11/06 导致查出 0 行`

```sql
SELECT ROUND(SUM(gmv), 2) AS gmv FROM ydsh_dws.dws_sales_day WHERE stat_date = '2024-11-06'
```
结果 1 行，与参考 SQL：一致；重试 0 次

人工复核：**正确** — 日期字面量写成 '2024-11-06'，格式正确，命中 1 行，结果 37,739.92 与 DWS 一致，没踩 2024/11/06 查出 0 行的坑。

## AI 洞察调用

- 案例：2024-11-06，端到端耗时 32.7s（含 Hive 查询）
- 完整输出见 `python/outputs/ai_insight.md`

```
读入 366 天日粒度数据
指定日期：2024-11-06（相对 7 日均线偏离 -47.1%，稳健 z=-1.97）
调用大模型：deepseek-flash
已输出 C:\Users\Lenovo\Desktop\ydsh-dw\python\outputs\ai_insight.md
归因来源：大模型 deepseek-flash
```