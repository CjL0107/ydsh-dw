-- ============================================================
-- 第 3 周 · 子任务 1：ADS 应用层 ETL
-- 前置：sql/ads/00_ddl_ads.sql + sql/dws/01_etl_dws.sql 已执行
-- ============================================================

-- ------------------------------------------------------------
-- 1. 核心 KPI（竖表输出，便于看板与 NL2SQL 直接检索）
-- 用 named_struct + explode 把一行的多个聚合值摊成多行，避免重复扫表
-- ------------------------------------------------------------
INSERT OVERWRITE TABLE ydsh_ads.ads_kpi_summary
SELECT
  m.metric_key,
  m.metric_name,
  CAST(m.metric_value AS DECIMAL(18,4)) AS metric_value,
  m.unit,
  m.definition,
  '2024 全年' AS stat_period
FROM (
  SELECT
    k.gmv,
    k.orders,
    k.users,
    k.aov,
    k.arpu,
    k.avg_orders,
    k.repeat_users,
    k.repeat_rate,
    k.cancel_rate,
    k.refund_rate,
    k.wait_pay_rate,
    k.item_qty,
    k.unit_price,
    k.cat_cnt,
    k.cat_top1_share
  FROM (
    SELECT
      o.gmv,
      o.orders,
      o.users,
      o.aov,
      o.cancel_rate,
      o.refund_rate,
      o.wait_pay_rate,
      IF(o.users > 0, o.gmv / o.users, 0)                       AS arpu,
      IF(o.users > 0, o.orders / o.users, 0)                    AS avg_orders,
      u.repeat_users,
      IF(u.all_users > 0, u.repeat_users / u.all_users, 0)      AS repeat_rate,
      s.item_qty,
      IF(s.item_qty > 0, o.gmv / s.item_qty, 0)                 AS unit_price,
      c.cat_cnt,
      c.top1_share                                             AS cat_top1_share
    FROM (
      SELECT
        SUM(IF(status_code IN ('OS02','OS03','OS05'), pay_amount, 0))              AS gmv,
        SUM(IF(status_code IN ('OS02','OS03','OS05'), 1, 0))                     AS orders,
        COUNT(DISTINCT IF(status_code IN ('OS02','OS03','OS05'), user_id, NULL))  AS users,
        SUM(IF(status_code = 'OS04', 1, 0)) / COUNT(*)                            AS cancel_rate,
        SUM(IF(status_code = 'OS05', 1, 0)) / COUNT(*)                            AS refund_rate,
        SUM(IF(status_code = 'OS01', 1, 0)) / COUNT(*)                            AS wait_pay_rate,
        CAST(SUM(IF(status_code IN ('OS02','OS03','OS05'), pay_amount, 0))
          / NULLIF(SUM(IF(status_code IN ('OS02','OS03','OS05'), 1, 0)), 0)
             AS DECIMAL(14,2))                                                   AS aov
      FROM ydsh_dwd.dwd_order
    ) o
    CROSS JOIN (
      SELECT
        COUNT(*)                                        AS all_users,
        SUM(IF(order_cnt >= 2, 1, 0))                   AS repeat_users
      FROM ydsh_dws.dws_user_order_agg
    ) u
    CROSS JOIN (
      SELECT SUM(item_qty) AS item_qty FROM ydsh_dws.dws_sales_day
    ) s
    CROSS JOIN (
      SELECT
        COUNT(*)                                        AS cat_cnt,
        MAX(cat_gmv) / SUM(cat_gmv)                     AS top1_share
      FROM (
        SELECT category, SUM(gmv_alloc) AS cat_gmv
        FROM ydsh_dws.dws_sales_category_day
        GROUP BY category
      ) cc
    ) c
  ) k
) kk
LATERAL VIEW explode(array(
  named_struct('metric_key','gmv','metric_name','GMV','metric_value',CAST(kk.gmv AS DOUBLE),
               'unit','元','definition','有效订单（已支付/已完成/退款中）实付金额之和'),
  named_struct('metric_key','order_cnt','metric_name','有效订单量','metric_value',CAST(kk.orders AS DOUBLE),
               'unit','单','definition','有效订单去重计数'),
  named_struct('metric_key','aov','metric_name','客单价 AOV','metric_value',CAST(kk.aov AS DOUBLE),
               'unit','元/单','definition','GMV ÷ 有效订单量'),
  named_struct('metric_key','active_users','metric_name','活跃用户数','metric_value',CAST(kk.users AS DOUBLE),
               'unit','人','definition','有有效订单的去重用户数'),
  named_struct('metric_key','repeat_users','metric_name','复购用户数','metric_value',CAST(kk.repeat_users AS DOUBLE),
               'unit','人','definition','有效订单数 ≥ 2 的用户数'),
  named_struct('metric_key','repeat_rate','metric_name','复购率','metric_value',CAST(kk.repeat_rate*100 AS DOUBLE),
               'unit','%','definition','复购用户数 ÷ 下单用户数'),
  named_struct('metric_key','arpu','metric_name','人均消费 ARPU','metric_value',CAST(kk.arpu AS DOUBLE),
               'unit','元/人','definition','GMV ÷ 活跃用户数'),
  named_struct('metric_key','avg_orders','metric_name','人均订单数','metric_value',CAST(kk.avg_orders AS DOUBLE),
               'unit','单/人','definition','有效订单量 ÷ 活跃用户数'),
  named_struct('metric_key','unit_price','metric_name','件单价','metric_value',CAST(kk.unit_price AS DOUBLE),
               'unit','元/件','definition','GMV ÷ 销售件数'),
  named_struct('metric_key','item_qty','metric_name','销售件数','metric_value',CAST(kk.item_qty AS DOUBLE),
               'unit','件','definition','有效订单对应明细数量合计'),
  named_struct('metric_key','cancel_rate','metric_name','取消率','metric_value',CAST(kk.cancel_rate*100 AS DOUBLE),
               'unit','%','definition','已取消订单 ÷ 全部下单量'),
  named_struct('metric_key','refund_rate','metric_name','退款率','metric_value',CAST(kk.refund_rate*100 AS DOUBLE),
               'unit','%','definition','退款中订单 ÷ 有效订单量'),
  named_struct('metric_key','wait_pay_rate','metric_name','待支付占比','metric_value',CAST(kk.wait_pay_rate*100 AS DOUBLE),
               'unit','%','definition','待支付订单 ÷ 全部下单量'),
  named_struct('metric_key','category_cnt','metric_name','在售品类数','metric_value',CAST(kk.cat_cnt AS DOUBLE),
               'unit','个','definition','有销售记录的品类数量'),
  named_struct('metric_key','category_top1_share','metric_name','品类集中度 TOP1','metric_value',CAST(kk.cat_top1_share*100 AS DOUBLE),
               'unit','%','definition','销售额最高品类占全部品类 GMV 的比例')
)) t AS m;

-- ------------------------------------------------------------
-- 2. 销售日报
-- ------------------------------------------------------------
INSERT OVERWRITE TABLE ydsh_ads.ads_sales_daily_report
SELECT
  d.stat_date,
  dd.weekday_name,
  dd.is_weekend,
  CAST(d.gmv AS DECIMAL(16,2))                                          AS gmv,
  d.paid_order_cnt,
  CAST(d.aov AS DECIMAL(14,2))                                          AS aov,
  CAST(IF(d.prev_gmv IS NULL OR d.prev_gmv = 0, NULL,
          (d.gmv - d.prev_gmv) / d.prev_gmv) AS DECIMAL(10,4))          AS gmv_dod,
  CAST(d.ma7 AS DECIMAL(16,2))                                          AS gmv_ma7,
  CAST(d.prev_week_gmv AS DECIMAL(16,2))                                AS gmv_last_week_same_day,
  CAST(IF(d.prev_week_gmv IS NULL OR d.prev_week_gmv = 0, NULL,
          (d.gmv - d.prev_week_gmv) / d.prev_week_gmv) AS DECIMAL(10,4)) AS gmv_wow_same_day
FROM (
  SELECT
    stat_date,
    gmv,
    paid_order_cnt,
    aov,
    LAG(gmv) OVER (ORDER BY stat_date)                                  AS prev_gmv,
    AVG(gmv) OVER (ORDER BY stat_date ROWS BETWEEN 6 PRECEDING AND CURRENT ROW) AS ma7,
    LAG(gmv, 7) OVER (ORDER BY stat_date)                               AS prev_week_gmv
  FROM ydsh_dws.dws_sales_day
) d
LEFT JOIN ydsh_dwd.dim_date dd
       ON DATE_FORMAT(dd.full_date, 'yyyy-MM-dd') = d.stat_date;

-- ------------------------------------------------------------
-- 3. 品类 TopN（占比按分摊后 GMV 计算）
-- ------------------------------------------------------------
INSERT OVERWRITE TABLE ydsh_ads.ads_category_top10
SELECT
  r.rank_no,
  r.category,
  r.gmv,
  r.gmv_share,
  r.order_cnt,
  r.item_qty,
  CAST(IF(r.item_qty > 0, r.gmv / r.item_qty, 0) AS DECIMAL(14,2)) AS avg_unit_price
FROM (
  SELECT
    c.category,
    CAST(c.gmv AS DECIMAL(16,2))                                     AS gmv,
    CAST(c.gmv / t.tot AS DECIMAL(10,4))                             AS gmv_share,
    c.order_cnt,
    c.item_qty,
    ROW_NUMBER() OVER (ORDER BY c.gmv DESC)                          AS rank_no
  FROM (
    SELECT
      category,
      SUM(gmv_alloc)      AS gmv,
      SUM(order_cnt)      AS order_cnt,
      SUM(item_qty)       AS item_qty
    FROM ydsh_dws.dws_sales_category_day
    GROUP BY category
  ) c
  CROSS JOIN (
    SELECT SUM(gmv_alloc) AS tot FROM ydsh_dws.dws_sales_category_day
  ) t
) r
WHERE r.rank_no <= 10;

-- ------------------------------------------------------------
-- 4. 用户 RFM 八分层
-- R 分档：距今天数越短得分越高；F/M 分档：数值越大得分越高，均用 NTILE(5)
-- ------------------------------------------------------------
INSERT OVERWRITE TABLE ydsh_ads.ads_user_value_segmentation
SELECT
  s.segment,
  COUNT(*)                                                             AS user_cnt,
  CAST(COUNT(*) / MAX(s.total_users) AS DECIMAL(10,4))                 AS user_share,
  CAST(SUM(s.m) AS DECIMAL(16,2))                                      AS gmv,
  CAST(SUM(s.m) / MAX(s.total_gmv) AS DECIMAL(10,4))                   AS gmv_share,
  CAST(AVG(s.m) AS DECIMAL(14,2))                                      AS avg_gmv,
  CAST(AVG(s.f) AS DECIMAL(10,2))                                      AS avg_freq,
  CAST(AVG(s.r) AS DECIMAL(10,1))                                      AS avg_recency
FROM (
  SELECT
    b.user_id,
    b.r,
    b.f,
    b.m,
    nt.r_score,
    nt.f_score,
    nt.m_score,
    nt.total_users,
    nt.total_gmv,
    CASE
      WHEN nt.r_score >= 4 AND nt.f_score >= 4 AND nt.m_score >= 4 THEN '重要价值客户'
      WHEN nt.r_score <  4 AND nt.f_score >= 4 AND nt.m_score >= 4 THEN '重要唤回客户'
      WHEN nt.r_score >= 4 AND nt.f_score <  4 AND nt.m_score >= 4 THEN '重要发展客户'
      WHEN nt.r_score <  4 AND nt.f_score <  4 AND nt.m_score >= 4 THEN '重要挽留客户'
      WHEN nt.r_score >= 4 AND nt.f_score >= 4 AND nt.m_score <  4 THEN '一般价值客户'
      WHEN nt.r_score <  4 AND nt.f_score >= 4 AND nt.m_score <  4 THEN '一般保持客户'
      WHEN nt.r_score >= 4 AND nt.f_score <  4 AND nt.m_score <  4 THEN '一般发展客户'
      ELSE '一般挽留客户'
    END                                                              AS segment
  FROM (
    SELECT
      a.user_id,
      DATEDIFF(ref.ref_date, a.last_order_date)                      AS r,
      a.order_cnt                                                    AS f,
      a.total_gmv                                                    AS m
    FROM ydsh_dws.dws_user_order_agg a
    CROSS JOIN (SELECT MAX(stat_date) AS ref_date FROM ydsh_dws.dws_sales_day) ref
  ) b
  JOIN (
    SELECT
      user_id,
      NTILE(5) OVER (ORDER BY r DESC)                                AS r_score,
      NTILE(5) OVER (ORDER BY f ASC)                                 AS f_score,
      NTILE(5) OVER (ORDER BY m ASC)                                 AS m_score,
      COUNT(*) OVER ()                                               AS total_users,
      SUM(m) OVER ()                                                 AS total_gmv
    FROM (
      SELECT
        a.user_id,
        DATEDIFF(ref.ref_date, a.last_order_date)                    AS r,
        a.order_cnt                                                  AS f,
        a.total_gmv                                                  AS m
      FROM ydsh_dws.dws_user_order_agg a
      CROSS JOIN (SELECT MAX(stat_date) AS ref_date FROM ydsh_dws.dws_sales_day) ref
    ) b2
  ) nt ON b.user_id = nt.user_id
) s
GROUP BY s.segment;

-- ------------------------------------------------------------
-- 5. 渠道贡献
-- ------------------------------------------------------------
INSERT OVERWRITE TABLE ydsh_ads.ads_channel_contribution
SELECT
  c.channel,
  c.channel_code,
  CAST(c.gmv AS DECIMAL(16,2))                                         AS gmv,
  CAST(c.gmv / t.tot AS DECIMAL(10,4))                                 AS gmv_share,
  c.order_cnt,
  c.user_cnt,
  CAST(c.gmv / c.order_cnt AS DECIMAL(14,2))                           AS aov
FROM (
  SELECT
    channel,
    channel_code,
    SUM(gmv)             AS gmv,
    SUM(paid_order_cnt)  AS order_cnt,
    SUM(user_cnt)        AS user_cnt
  FROM ydsh_dws.dws_sales_channel_day
  GROUP BY channel, channel_code
) c
CROSS JOIN (
  SELECT SUM(gmv) AS tot FROM ydsh_dws.dws_sales_channel_day
) t;

-- ------------------------------------------------------------
-- 6. 省份 TopN
-- ------------------------------------------------------------
INSERT OVERWRITE TABLE ydsh_ads.ads_province_top10
SELECT
  r.rank_no,
  r.province,
  r.gmv,
  r.gmv_share,
  r.order_cnt,
  r.user_cnt,
  r.aov
FROM (
  SELECT
    p.province,
    CAST(p.gmv AS DECIMAL(16,2))                                     AS gmv,
    CAST(p.gmv / t.tot AS DECIMAL(10,4))                             AS gmv_share,
    p.order_cnt,
    p.user_cnt,
    CAST(p.gmv / p.order_cnt AS DECIMAL(14,2))                       AS aov,
    ROW_NUMBER() OVER (ORDER BY p.gmv DESC)                          AS rank_no
  FROM (
    SELECT
      province,
      SUM(gmv)            AS gmv,
      SUM(paid_order_cnt) AS order_cnt,
      SUM(user_cnt)       AS user_cnt
    FROM ydsh_dws.dws_sales_store_day
    GROUP BY province
  ) p
  CROSS JOIN (
    SELECT SUM(gmv) AS tot FROM ydsh_dws.dws_sales_store_day
  ) t
) r
WHERE r.rank_no <= 10;

-- ------------------------------------------------------------
-- 7. 门店 TopN
-- ------------------------------------------------------------
INSERT OVERWRITE TABLE ydsh_ads.ads_store_top10
SELECT
  r.rank_no,
  r.store_id,
  r.store_name,
  r.city,
  r.store_level,
  r.gmv,
  r.gmv_share,
  r.order_cnt,
  r.aov
FROM (
  SELECT
    s.store_id,
    s.store_name,
    s.city,
    s.store_level,
    CAST(s.gmv AS DECIMAL(16,2))                                     AS gmv,
    CAST(s.gmv / t.tot AS DECIMAL(10,4))                             AS gmv_share,
    s.order_cnt,
    CAST(s.gmv / s.order_cnt AS DECIMAL(14,2))                       AS aov,
    ROW_NUMBER() OVER (ORDER BY s.gmv DESC)                          AS rank_no
  FROM (
    SELECT
      store_id,
      MAX(store_name)     AS store_name,
      MAX(city)           AS city,
      MAX(store_level)    AS store_level,
      SUM(gmv)            AS gmv,
      SUM(paid_order_cnt) AS order_cnt
    FROM ydsh_dws.dws_sales_store_day
    GROUP BY store_id
  ) s
  CROSS JOIN (
    SELECT SUM(gmv) AS tot FROM ydsh_dws.dws_sales_store_day
  ) t
) r
WHERE r.rank_no <= 10;
