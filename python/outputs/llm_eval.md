# 大模型真实调用验证结果

- 执行时间：2026-11-10 09:38:09
- 服务商 / 模型：deepseek / deepseek-flash
- 题目数：8　执行成功：8/8　严格一致：7/8　宽松一致：8/8　人工判定正确：7/8
- 累计 prompt tokens：29,653　累计耗时：12.13s　平均单题：1.52s

> 三个判据回答三个不同问题：**执行成功**=SQL 能在 Hive 上跑通；**严格一致**=数值集合与参考 SQL 完全对应；**宽松一致**=参考结果的数值都被覆盖（允许多查列）。**都不是「语义正确」**——语义由人工复核，见最后一列。

## 逐题结果

| # | 问题 | 执行 | 严格 | 宽松 | 耗时(s) | tokens | 人工复核 |
|---|------|------|------|------|---------|--------|----------|
| 1 | 2024 年全年 GMV 是多少？ | 成功 | ✔ | ✔ | 1.3 | 3708 | 正确 |
| 2 | 客单价是多少？ | 成功 | ✔ | ✔ | 1.42 | 3702 | 正确 |
| 3 | 销量最高的前 5 个品类有哪些？ | 成功 | ✔ | ✔ | 2.42 | 3708 | 部分正确 |
| 4 | 各渠道的 GMV 占比是多少？ | 成功 | — | ✔ | 0.88 | 3707 | 正确 |
| 5 | 复购率是多少？ | 成功 | ✔ | ✔ | 1.03 | 3703 | 正确 |
| 6 | 广东省全年销售额是多少？ | 成功 | ✔ | ✔ | 1.18 | 3703 | 正确 |
| 7 | 最近 7 天每天的下单量 | 成功 | ✔ | ✔ | 2.88 | 3707 | 正确 |
| 8 | 2024 年 11 月 6 日的 GMV 是多少？ | 成功 | ✔ | ✔ | 1.02 | 3715 | 正确 |

## 模型生成的 SQL 与比对说明

**1. 2024 年全年 GMV 是多少？**　`全表聚合，考表选择与聚合函数`

```sql
SELECT metric_name, ROUND(metric_value, 2) AS metric_value, unit FROM ydsh_ads.ads_kpi_summary WHERE metric_key = 'gmv' AND stat_period = '2024 全年'
```
结果 1 行，与参考 SQL：一致；重试 0 次

人工复核：**正确** — 改走 KPI 宽表：metric_key = 'gmv' AND stat_period = '2024 全年'，取 metric_value 得 27,342,828.20。与 DWS 全表汇总、渠道合计、门店合计三处交叉验证完全一致。上一轮它写的是 SUM(gmv) 直接扫 dws_sales_day；两种写法都对，这次选了带口径定义的 KPI 表，反而更稳。stat_period = '2024 全年' 这个取值不在原始 Prompt 里，是它按 schema 注释推出来的。

**2. 客单价是多少？**　`考 KPI 宽表的 metric_key 过滤；易错点是自己去算 SUM/COUNT 导致口径不一致`

```sql
SELECT metric_name, metric_value, unit FROM ydsh_ads.ads_kpi_summary WHERE metric_key = 'aov'
```
结果 1 行，与参考 SQL：一致；重试 0 次

人工复核：**正确** — metric_key = 'aov' 直查 metric_value = 2529.6400，与参考一致。上一轮同类题它选对了过滤条件却漏选 metric_value（只返回元信息、行数非空但答案不在里面）；本轮 metric_value 已进入 schema，这个坑随根因一起消失了——说明那次的错不是模型不稳，是我们没把列给它。

**3. 销量最高的前 5 个品类有哪些？**　`考 TopN 语义；易错点是拿 DWS 明细自己排而不是用 ADS 现成结果`

```sql
SELECT rank_no, category, gmv FROM ydsh_ads.ads_category_top10 WHERE rank_no <= 5 ORDER BY rank_no
```
结果 5 行，与参考 SQL：一致；重试 0 次

人工复核：**部分正确** — 返回 rank_no、category、gmv，按 rank_no 取前 5，结果本身正确（GMV 前 5 名）。但 rank_no 在 ETL 里是 ROW_NUMBER() OVER (ORDER BY gmv DESC)，即按 GMV 排名；题目写的是「销量最高」，若「销量」指件数，这道题的答案应当是另一组品类。模型既没按件数重排，也没澄清歧义，属于照搬现成排名。判据判「一致」是因为它和参考 SQL 写法相同，参考 SQL 本身也带这个歧义。

**4. 各渠道的 GMV 占比是多少？**　`考占比字段；易错点是自己算分母导致与 ADS 口径不符`

```sql
SELECT channel, gmv, gmv_share FROM ydsh_ads.ads_channel_contribution ORDER BY gmv DESC
```
结果 4 行，与参考 SQL：宽松一致（模型多返回 4 个数值，列选多了）；重试 0 次

人工复核：**正确** — SELECT channel, gmv, gmv_share ... ORDER BY gmv DESC，四渠道占比 0.2520 / 0.2499 / 0.2492 / 0.2488，合计 0.9999（四舍五入残差），与 ADS 层一致。严格判据判「不一致」只因它多带了 gmv 一列（4 行 × 2 列 = 8 个数值 vs 参考 4 个），不是算错。上一轮同一道题它选了 order_cnt、user_cnt，压根没碰占比——原因是 gmv_share 是 decimal(10,4)，被 schema 抽取吃掉了，模型眼前没有这一列。

**5. 复购率是多少？**　`考指标别名；易错点是编一个自己没有的指标口径`

```sql
SELECT metric_name, metric_value, unit, definition FROM ydsh_ads.ads_kpi_summary WHERE metric_key = 'repeat_rate'
```
结果 1 行，与参考 SQL：一致；重试 0 次

人工复核：**正确** — metric_key = 'repeat_rate' 直查 KPI 表得 100.0000%。数值本身没问题，但要在报告里说清：本数据集 1000 个用户全部 order_cnt > 1，所以复购率必然是 100%，这是数据特征不是算错。上一轮它绕开 KPI 表自己写 SUM(CASE WHEN order_cnt > 1 ...) 去算，口径虽一致但等于把定义复制进 SQL，KPI 定义一改就会静默失真。

**6. 广东省全年销售额是多少？**　`考维度过滤；易错点是写成 = '广东' 而非 LIKE，或漏掉省字`

```sql
SELECT ROUND(SUM(gmv), 2) AS gmv FROM ydsh_dws.dws_sales_store_day WHERE province = '广东省'
```
结果 1 行，与参考 SQL：一致；重试 0 次

人工复核：**正确** — 写成 province = '广东省'，命中 472,606.11，与参考的 LIKE '%广东%' 结果完全相同。这是维度值注入改造的直接效果：改造前它写的是 = '广东'，等值匹配 0 行、SUM 返回 NULL，执行成功、无报错，只能靠人看出来。

**7. 最近 7 天每天的下单量**　`考时间序列 + 排序 + 限行三件事同时成立；这是规则兜底最容易翻车的题`

```sql
SELECT stat_date, order_cnt FROM (SELECT stat_date, order_cnt FROM ydsh_dws.dws_sales_day ORDER BY stat_date DESC LIMIT 7) t ORDER BY stat_date
```
结果 7 行，与参考 SQL：一致；重试 0 次

人工复核：**正确** — 子查询先 ORDER BY stat_date DESC LIMIT 7 取最近 7 天，外层再按时间正序输出，12-25 至 12-31 共 7 行，与参考一致且更贴合阅读顺序。

**8. 2024 年 11 月 6 日的 GMV 是多少？**　`考日期字面量格式；易错点是写成 2024/11/06 导致查出 0 行`

```sql
SELECT ROUND(SUM(gmv), 2) AS gmv FROM ydsh_dws.dws_sales_day WHERE stat_date = '2024-11-06'
```
结果 1 行，与参考 SQL：一致；重试 0 次

人工复核：**正确** — 日期字面量写成 '2024-11-06'，格式正确，命中 1 行，结果 37,739.92 与 DWS 一致，没踩 2024/11/06 查出 0 行的坑。
