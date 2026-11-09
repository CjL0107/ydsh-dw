-- ============================================================
-- 第 3 周 · 子任务 1：DWS 汇总层 ETL
-- 前置：sql/dws/00_ddl_dws.sql 已执行
-- 口径常量：有效订单 = status_code IN ('OS02' 已支付, 'OS03' 已完成, 'OS05' 退款中)
-- ============================================================

-- ------------------------------------------------------------
-- 1. 销售日汇总
-- 件数单独聚合再 LEFT JOIN，避免明细表参与主聚合造成订单数被放大
-- ------------------------------------------------------------
INSERT OVERWRITE TABLE ydsh_dws.dws_sales_day
SELECT
  d.stat_date,
  d.order_cnt,
  d.paid_order_cnt,
  d.cancel_order_cnt,
  d.wait_pay_cnt,
  d.user_cnt,
  d.paid_user_cnt,
  CAST(d.gmv AS DECIMAL(16,2))                                        AS gmv,
  CAST(d.discount_amount AS DECIMAL(16,2))                            AS discount_amount,
  NVL(i.item_qty, 0)                                                  AS item_qty,
  CAST(IF(d.paid_order_cnt > 0, d.gmv / d.paid_order_cnt, 0)
       AS DECIMAL(14,2))                                              AS aov,
  CAST(IF(d.order_cnt > 0, d.cancel_order_cnt / d.order_cnt, 0)
       AS DECIMAL(8,4))                                               AS cancel_rate
FROM (
  SELECT
    order_date                                                        AS stat_date,
    COUNT(*)                                                          AS order_cnt,
    SUM(IF(status_code IN ('OS02','OS03','OS05'), 1, 0))              AS paid_order_cnt,
    SUM(IF(status_code = 'OS04', 1, 0))                               AS cancel_order_cnt,
    SUM(IF(status_code = 'OS01', 1, 0))                               AS wait_pay_cnt,
    COUNT(DISTINCT user_id)                                           AS user_cnt,
    COUNT(DISTINCT IF(status_code IN ('OS02','OS03','OS05'), user_id, NULL)) AS paid_user_cnt,
    SUM(IF(status_code IN ('OS02','OS03','OS05'), pay_amount, 0))      AS gmv,
    SUM(IF(status_code IN ('OS02','OS03','OS05'), discount_amount, 0)) AS discount_amount
  FROM ydsh_dwd.dwd_order
  GROUP BY order_date
) d
LEFT JOIN (
  SELECT o.order_date AS stat_date, SUM(i.quantity) AS item_qty
  FROM ydsh_dwd.dwd_order_item i
  JOIN (SELECT order_id, order_date FROM ydsh_dwd.dwd_order
        WHERE status_code IN ('OS02','OS03','OS05')) o
    ON i.order_id = o.order_id AND i.order_date = o.order_date
  GROUP BY o.order_date
) i ON d.stat_date = i.stat_date;

-- ------------------------------------------------------------
-- 2. 品类日汇总（GMV 按明细占比分摊订单实付）
-- ------------------------------------------------------------
INSERT OVERWRITE TABLE ydsh_dws.dws_sales_category_day
SELECT
  o.order_date                                                        AS stat_date,
  p.category                                                          AS category,
  COUNT(DISTINCT o.order_id)                                          AS order_cnt,
  SUM(i.quantity)                                                     AS item_qty,
  CAST(SUM(i.amount) AS DECIMAL(16,2))                                AS item_amount,
  CAST(SUM(IF(t.tot > 0, i.amount / t.tot * o.pay_amount, 0))
       AS DECIMAL(16,2))                                              AS gmv_alloc
FROM ydsh_dwd.dwd_order o
JOIN ydsh_dwd.dwd_order_item i
  ON o.order_id = i.order_id AND o.order_date = i.order_date
JOIN ydsh_dwd.dim_product p
  ON i.product_id = p.product_id
JOIN (
  SELECT order_id, order_date, SUM(amount) AS tot
  FROM ydsh_dwd.dwd_order_item
  GROUP BY order_id, order_date
) t ON t.order_id = o.order_id AND t.order_date = o.order_date
WHERE o.status_code IN ('OS02','OS03','OS05')
GROUP BY o.order_date, p.category;

-- ------------------------------------------------------------
-- 3. 门店日汇总
-- ------------------------------------------------------------
INSERT OVERWRITE TABLE ydsh_dws.dws_sales_store_day
SELECT
  o.order_date                                                        AS stat_date,
  o.store_id                                                          AS store_id,
  s.store_name                                                        AS store_name,
  s.province                                                          AS province,
  s.city                                                              AS city,
  s.store_level                                                       AS store_level,
  COUNT(*)                                                            AS paid_order_cnt,
  COUNT(DISTINCT o.user_id)                                           AS user_cnt,
  CAST(SUM(o.pay_amount) AS DECIMAL(16,2))                             AS gmv,
  CAST(SUM(o.pay_amount) / COUNT(*) AS DECIMAL(14,2))                  AS aov
FROM ydsh_dwd.dwd_order o
JOIN ydsh_dwd.dim_store s ON o.store_id = s.store_id
WHERE o.status_code IN ('OS02','OS03','OS05')
GROUP BY o.order_date, o.store_id, s.store_name, s.province, s.city, s.store_level;

-- ------------------------------------------------------------
-- 4. 渠道日汇总
-- ------------------------------------------------------------
INSERT OVERWRITE TABLE ydsh_dws.dws_sales_channel_day
SELECT
  o.order_date                                                        AS stat_date,
  o.channel_code                                                      AS channel_code,
  o.channel                                                           AS channel,
  COUNT(*)                                                            AS paid_order_cnt,
  COUNT(DISTINCT o.user_id)                                           AS user_cnt,
  CAST(SUM(o.pay_amount) AS DECIMAL(16,2))                             AS gmv,
  CAST(SUM(o.pay_amount) / COUNT(*) AS DECIMAL(14,2))                  AS aov
FROM ydsh_dwd.dwd_order o
WHERE o.status_code IN ('OS02','OS03','OS05')
GROUP BY o.order_date, o.channel_code, o.channel;

-- ------------------------------------------------------------
-- 5. 周汇总（周一为一周起点）
-- ------------------------------------------------------------
INSERT OVERWRITE TABLE ydsh_dws.dws_sales_week
SELECT
  w.year_week,
  w.week_start,
  w.week_end,
  w.paid_order_cnt,
  w.paid_user_cnt,
  w.gmv,
  CAST(w.gmv / w.paid_order_cnt AS DECIMAL(14,2))                      AS aov
FROM (
  SELECT
    CONCAT(SUBSTR(order_date, 1, 4), '-W',
           LPAD(CAST(WEEKOFYEAR(order_date) AS STRING), 2, '0'))       AS year_week,
    DATE_FORMAT(DATE_SUB(order_date, PMOD(DAYOFWEEK(order_date) + 5, 7)),
                'yyyy-MM-dd')                                          AS week_start,
    DATE_FORMAT(DATE_ADD(DATE_SUB(order_date, PMOD(DAYOFWEEK(order_date) + 5, 7)), 6),
                'yyyy-MM-dd')                                          AS week_end,
    COUNT(*)                                                           AS paid_order_cnt,
    COUNT(DISTINCT user_id)                                            AS paid_user_cnt,
    SUM(pay_amount)                                                    AS gmv
  FROM ydsh_dwd.dwd_order
  WHERE status_code IN ('OS02','OS03','OS05')
  GROUP BY
    CONCAT(SUBSTR(order_date, 1, 4), '-W',
           LPAD(CAST(WEEKOFYEAR(order_date) AS STRING), 2, '0')),
    DATE_FORMAT(DATE_SUB(order_date, PMOD(DAYOFWEEK(order_date) + 5, 7)), 'yyyy-MM-dd'),
    DATE_FORMAT(DATE_ADD(DATE_SUB(order_date, PMOD(DAYOFWEEK(order_date) + 5, 7)), 6), 'yyyy-MM-dd')
) w;

-- ------------------------------------------------------------
-- 6. 月汇总（含环比）
-- ------------------------------------------------------------
INSERT OVERWRITE TABLE ydsh_dws.dws_sales_month
SELECT
  m.year_month,
  m.paid_order_cnt,
  m.paid_user_cnt,
  CAST(m.gmv AS DECIMAL(16,2))                                         AS gmv,
  CAST(m.gmv / m.paid_order_cnt AS DECIMAL(14,2))                      AS aov,
  CAST(IF(m.prev_gmv IS NULL OR m.prev_gmv = 0, NULL,
          (m.gmv - m.prev_gmv) / m.prev_gmv) AS DECIMAL(10,4))         AS mom_rate
FROM (
  SELECT
    SUBSTR(order_date, 1, 7)                                           AS year_month,
    COUNT(*)                                                           AS paid_order_cnt,
    COUNT(DISTINCT user_id)                                            AS paid_user_cnt,
    SUM(pay_amount)                                                    AS gmv,
    LAG(SUM(pay_amount)) OVER (ORDER BY SUBSTR(order_date, 1, 7))      AS prev_gmv
  FROM ydsh_dwd.dwd_order
  WHERE status_code IN ('OS02','OS03','OS05')
  GROUP BY SUBSTR(order_date, 1, 7)
) m;

-- ------------------------------------------------------------
-- 7. 用户级汇总（RFM 与复购率输入）
-- ------------------------------------------------------------
INSERT OVERWRITE TABLE ydsh_dws.dws_user_order_agg
SELECT
  a.user_id,
  a.first_order_date,
  a.last_order_date,
  a.order_cnt,
  CAST(a.total_gmv AS DECIMAL(16,2))                                   AS total_gmv,
  CAST(a.total_gmv / a.order_cnt AS DECIMAL(14,2))                     AS avg_order_gmv,
  a.active_days,
  u.province,
  u.region,
  u.member_level
FROM (
  SELECT
    user_id,
    MIN(order_date)                                                    AS first_order_date,
    MAX(order_date)                                                    AS last_order_date,
    COUNT(*)                                                           AS order_cnt,
    SUM(pay_amount)                                                    AS total_gmv,
    COUNT(DISTINCT order_date)                                          AS active_days
  FROM ydsh_dwd.dwd_order
  WHERE status_code IN ('OS02','OS03','OS05')
  GROUP BY user_id
) a
LEFT JOIN ydsh_dwd.dim_user u ON a.user_id = u.user_id;
