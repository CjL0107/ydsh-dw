-- ================================================================
-- 05_数据清洗（一）：商品表缺失值处理
-- 周次：第 1 周 · 子任务 6
-- 输入：ydsh_ods.ods_product
-- 输出：ydsh_dwd.dwd_product
--
-- 清洗策略与业务依据：
--   launch_date 缺失 11 条（1.83%）。
--   处理方式：用**同品类（category）最早上架日期**回填。
--   理由：上架日期缺失不影响金额类指标，但对"商品在架时长""新品占比"
--         这类分析有影响；用同品类最早日期是一个保守且可解释的估计，
--         并在 launch_date_is_filled 上打标记，分析时可随时剔除。
--   不用"当前日期"回填 —— 那会把老商品误判为新品，方向错了。
--
--   注意：price 的 20 条负值、849897.9 的超大值属于"异常值"，
--         是第 2 周子任务 2 的内容，本周不处理，保留在 ODS 层。
-- ================================================================

USE ydsh_dwd;

DROP TABLE IF EXISTS ydsh_dwd.dwd_product;

CREATE TABLE ydsh_dwd.dwd_product
COMMENT '商品维度表-已处理缺失值'
STORED AS PARQUET
AS
SELECT
  p.product_id,
  p.product_name,
  p.category,
  p.sub_category,
  p.brand,
  p.price,
  p.cost,
  p.stock,
  p.status,
  -- 缺失上架日期用同品类最早日期回填；品类内也无有效日期时兜底 1970-01-01
  IF(nvl(trim(p.launch_date), '') = '',
     nvl(c.min_launch, '1970-01-01'),
     trim(p.launch_date))                          AS launch_date,
  IF(nvl(trim(p.launch_date), '') = '', 1, 0)      AS launch_date_is_filled
FROM ydsh_ods.ods_product p
LEFT JOIN (
  SELECT category, MIN(launch_date) AS min_launch
  FROM ydsh_ods.ods_product
  WHERE launch_date RLIKE '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
  GROUP BY category
) c ON p.category = c.category;

-- ---------------------------------------------------------------
-- 清洗效果自检：launch_date_blank 应为 0
-- ---------------------------------------------------------------
SELECT
  COUNT(*)                                          AS total_rows,
  SUM(IF(nvl(trim(launch_date), '') = '', 1, 0))    AS launch_date_blank,
  SUM(launch_date_is_filled)                        AS launch_date_filled_rows
FROM ydsh_dwd.dwd_product;
