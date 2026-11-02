-- ================================================================
-- 子任务 1+2：商品维度清洗（品类/品牌空格与大小写、价格异常）
-- 输入：ydsh_ods.ods_product        输出：ydsh_dwd.dim_product
-- 周次：第 2 周
-- 作者：梁常金
--
-- 脏数据实况（扫描得出，不是猜的）：
--   category      : 16 种取值 = 8 个规范值 × (正常 / 带尾空格)，27 行带尾空格
--   sub_category  : 50 种，全部规范
--   brand         : 45 个品牌，无大小写变体，但统一做 trim + 首字母大写防御
--   price         : 20 行为负（-626.74 这类符号错误）、16 行超大（最高 84.98 万）
--                   正常区间 0 < price <= 10000（正常样本 P99 = 8917.92，最大 8992.57）
--   cost          : 无负值；20 行 cost > price（全部对应"价格为负"的脏记录）
--   launch_date   : 11 行为空
--
-- 价格异常为什么不用"全表 P99"当阈值：
--   表里 16 行 84 万级脏值会把 P99 直接顶到 41 万（实测 percentile(price,0.99)=410186），
--   按这个阈值清洗等于没洗。必须先在"正常子集"(0,10000] 上算分位数，
--   再拿 10000 当绝对阈值 —— 这是本次最容易踩的坑，已写进质量报告。
-- ================================================================

USE ydsh_dwd;

INSERT OVERWRITE TABLE ydsh_dwd.dim_product
SELECT
  p.product_id,
  trim(p.product_name)                                             AS product_name,
  trim(p.category)                                                 AS category,
  trim(p.sub_category)                                             AS sub_category,
  -- 品牌：纯 ASCII 品牌统一首字母大写（Nike 而不是 NIKE/nike），中文品牌只 trim
  CASE WHEN trim(p.brand) RLIKE '^[A-Za-z0-9 .&_-]+$'
       THEN initcap(trim(p.brand))
       ELSE trim(p.brand) END                                      AS brand,

  CAST(p.price_fixed AS DECIMAL(12,2))                             AS price,
  CAST(p.cost AS DECIMAL(12,2))                                    AS cost,
  -- 毛利率：price 为 0 时按 NULL 处理，避免除零
  CAST(CASE WHEN p.price_fixed > 0
            THEN round((p.price_fixed - p.cost) / p.price_fixed, 4) END AS DECIMAL(6,4)) AS gross_margin_rate,

  p.stock,
  trim(p.status)                                                   AS status,

  -- 上架日期：与用户注册日期同一套规则，三分支统一
  CASE
    WHEN trim(p.launch_date) RLIKE '^[0-9]{8}$'
      THEN concat(substr(trim(p.launch_date),1,4), '-',
                  substr(trim(p.launch_date),5,2), '-',
                  substr(trim(p.launch_date),7,2))
    WHEN trim(p.launch_date) = '' THEN NULL
    ELSE regexp_replace(trim(p.launch_date), '/', '-')
  END                                                              AS launch_date,
  IF(p.launch_date IS NULL OR trim(p.launch_date) = '', 1, 0)       AS launch_date_is_missing,

  p.price_flag
FROM (
  SELECT
    d.*,
    -- 价格修正：负值取绝对值（符号错误）；超阈值取同二级品类正常价格中位数
    CASE
      WHEN d.price IS NULL                       THEN NULL
      WHEN d.price < 0                           THEN -d.price
      WHEN d.price > 10000                       THEN coalesce(m.med_price, 999.00)
      ELSE d.price
    END                                                             AS price_fixed,
    CASE
      WHEN d.price IS NULL            THEN 'NULL'
      WHEN d.price < 0                THEN 'SIGN_FIXED'
      WHEN d.price > 10000            THEN 'OUTLIER_REPAIRED'
      ELSE 'NORMAL'
    END                                                             AS price_flag
  FROM ydsh_ods.ods_product d
  LEFT JOIN (
    -- 阈值内的正常样本，按二级品类算中位数（同品类商品价格量级接近，比全局中位数更合理）
    SELECT sub_category,
           CAST(percentile_approx(CAST(price AS DOUBLE), CAST(0.5 AS DOUBLE)) AS DECIMAL(12,2)) AS med_price
    FROM ydsh_ods.ods_product
    WHERE price > 0 AND price <= 10000
    GROUP BY sub_category
  ) m ON trim(d.sub_category) = m.sub_category
) p;

-- 装载后自检
SELECT COUNT(*)                                                    AS total_rows,
       SUM(IF(price <= 0, 1, 0))                                   AS price_nonpos,
       SUM(IF(price > 10000, 1, 0))                                AS price_outlier,
       SUM(IF(price_flag = 'SIGN_FIXED', 1, 0))                    AS sign_fixed,
       SUM(IF(price_flag = 'OUTLIER_REPAIRED', 1, 0))              AS outlier_repaired,
       SUM(IF(category <> trim(category), 1, 0))                   AS cat_ws,
       COUNT(DISTINCT category)                                    AS distinct_category,
       COUNT(DISTINCT brand)                                       AS distinct_brand,
       SUM(launch_date_is_missing)                                 AS launch_missing,
       SUM(IF(cost > price, 1, 0))                                 AS cost_gt_price
FROM ydsh_dwd.dim_product;
