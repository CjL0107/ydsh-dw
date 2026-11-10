## 这次改动做了什么

- 对应子任务：第 3 周 · 子任务 4（NL2SQL 构建）
- 改动内容：把 8 个维度列（省/市/渠道/品类/门店分级/月份/KPI 指标键与中文名）的**真实取值**
  抽出来注入 Prompt，让模型做等值匹配时照抄库里的写法，而不是猜简称。

修的是两类具体错误：

| 现象 | 原因 |
|---|---|
| `WHERE province = '广东'` 查出 NULL | 库里存的是 `广东省`，这个信息 Schema 里没有 |
| `WHERE metric_name = '客单价'` 查 0 行 | 库里存的是 `客单价 AOV` |

顺带发现并修正了一个更根本的问题：**Few-shot 示例本身就是错的**
（示例里写的是 `province = '广东'`），模型照着抄，我之前却把它记成"模型的错误"。

## 改了哪些文件

| 文件 | 改了什么 |
|---|---|
| `python/nl2sql_schema.py` | 新增 `DIM_SPECS`、`extract_dims()`、`to_dim_text()`，抽取维度取值写 `dim_values.json` |
| `python/nl2sql.py` | Prompt 注入维度取值；修正 Few-shot 示例；新增 `check_dim_literals()` 执行前拦截取值错误 |
| `python/hive_runner.py` | **修 bug**：容器不可用时不再静默返回空列表；表头错位时抛异常（详见下） |
| `python/validate_llm.py` | **修 bug**：两边都返回 0 行时不再判"一致" |
| `python/outputs/dim_values.json` | 新增，8 个维度 93 个取值（生成的，可重跑） |
| `docs/第3周_NL2SQL维度值注入改造.md` | 改造记录，含根因、方案、实测、事故复盘 |

## 怎么验证的

**单题验证（Hive 正常时执行，可信）**：

```powershell
python python/nl2sql_schema.py
python python/nl2sql.py -q "广东省上月销售额是多少？"
python python/nl2sql.py -q "客单价是多少？"
```

实际结果：

```
# 广东省
SQL：SELECT ROUND(SUM(gmv), 2) AS gmv FROM ydsh_dws.dws_sales_store_day
     WHERE province = '广东省' AND stat_date LIKE '2024-12%'
gmv = 47992.90

# 客单价
SQL：SELECT metric_name, metric_value, unit FROM ydsh_ads.ads_kpi_summary
     WHERE metric_key = 'aov'
客单价 AOV | 2529.6400 | 元/单
```

**校验函数单元测试**：

```
省份简称      province = '广东'        → 拦截，建议 '广东省'
城市多写市    city = '深圳市'           → 拦截，建议 '深圳'
LIKE 模糊     province LIKE '%广东%'    → 放行
```

**整轮 8 题复测：未完成。** Docker Desktop 在本机异常退出，`docker desktop restart`
未能拉起引擎，所以没有改造后的整轮对比数据。详见文档第五、六节。

## 检查清单

- [x] 提交信息符合 `<type>(<scope>): <subject>` 规范
- [x] 已把 `main` 合并进本分支，无冲突
- [x] 没有提交 `data/*.csv`、`.env`、`*.log` 等不该入库的文件
- [ ] 改了公共脚本（`hive_runner.py` / `validate_llm.py`），请 @ 相关同学 review

## 对别人的影响

**有**，`hive_runner.py` 的异常行为变了：

- 以前容器不可用时 `run_sql()` 返回 `[]`，调用方会当成"查询没数据"
- 现在直接抛 `RuntimeError("容器不可用：...")`

拉下来之后需要跑一次 `python python/nl2sql_schema.py` 生成 `dim_values.json`，
否则 `nl2sql.py` 会打印警告并按"无维度取值"模式退化运行（不报错，但准确率回落到改造前）。

## 关联 Issue

无（单人实训环境，问题背景记在 `docs/第3周_NL2SQL维度值注入改造.md`）
