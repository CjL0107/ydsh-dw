-- ================================================================
-- 子任务 7：基于 DWD 层的探索性数据分析（EDA）
-- 周次：第 2 周
-- 作者：梁常金
--
-- 分析口径（口径不统一，数字就会被质疑，所以先定死）：
--   1) 全部指标基于 dwd_order（已去重、金额已修正）+ dim_* 维度表；
--   2) "有效订单" = order_status ∈ (已支付, 已完成, 退款中)，即已付款口径，
--      对应 dim_code_order_status.is_paid = 1；
--      "下单订单" = 全部 15000 单（含待支付、已取消），仅用于算取消率；
--   3) GMV 用 pay_amount 汇总（实付金额，不是标价 amount）；
--   4) 客单价 AOV = GMV / 有效订单数（不是 / 下单数，否则被未付款单稀释）。
--
-- 分区裁剪：每条查询都尽量带上 order_date 条件或走聚合，避免全表扫 366 分区。
-- ================================================================

USE ydsh_dwd;

-- ----------------------------------------------------------------
-- Q1 核心 KPI 总览
-- ----------------------------------------------------------------
SELECT
  COUNT(*)                                                                  AS order_cnt_all,
  SUM(IF(status_code IN ('OS02','OS03','OS05'), 1, 0))                       AS paid_order_cnt,
  COUNT(DISTINCT user_id)                                                   AS user_cnt,
  COUNT(DISTINCT store_id)                                                  AS store_cnt,
  ROUND(SUM(IF(status_code IN ('OS02','OS03','OS05'), pay_amount, 0)), 2)    AS gmv_paid,
  ROUND(SUM(pay_amount), 2)                                                 AS gmv_all,
  ROUND(SUM(IF(status_code IN ('OS02','OS03','OS05'), pay_amount, 0))
        / SUM(IF(status_code IN ('OS02','OS03','OS05'), 1, 0)), 2)           AS aov,
  ROUND(SUM(discount_amount), 2)                                            AS total_discount,
  ROUND(100.0 * SUM(IF(status_code = 'OS04', 1, 0)) / COUNT(*), 2)           AS cancel_rate,
  ROUND(100.0 * SUM(IF(status_code = 'OS05', 1, 0)) / COUNT(*), 2)           AS refund_rate
FROM ydsh_dwd.dwd_order;

-- 订单状态结构（验证"已付款口径"占比）
SELECT o.status_code, s.status_name, s.is_paid, COUNT(*) AS cnt,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS pct
FROM ydsh_dwd.dwd_order o
JOIN ydsh_dwd.dim_code_order_status s ON o.status_code = s.status_code
GROUP BY o.status_code, s.status_name, s.is_paid
ORDER BY cnt DESC;

-- ----------------------------------------------------------------
-- Q2 月度趋势（GMV / 订单量 / 客单价 / 环比）
-- ----------------------------------------------------------------
SELECT d.year_month,
       COUNT(*)                                                              AS order_cnt,
       ROUND(SUM(o.pay_amount), 2)                                           AS gmv,
       ROUND(SUM(o.pay_amount) / COUNT(*), 2)                                AS aov,
       LAG(ROUND(SUM(o.pay_amount), 2), 1) OVER (ORDER BY d.year_month)      AS gmv_prev_month,
       ROUND(100.0 * (SUM(o.pay_amount) - LAG(SUM(o.pay_amount), 1) OVER (ORDER BY d.year_month))
             / LAG(SUM(o.pay_amount), 1) OVER (ORDER BY d.year_month), 2)    AS mom_growth_pct
FROM ydsh_dwd.dwd_order o
JOIN ydsh_dwd.dim_date d ON o.order_date = CAST(d.full_date AS STRING)
WHERE o.status_code IN ('OS02','OS03','OS05')
GROUP BY d.year_month
ORDER BY d.year_month;

-- ----------------------------------------------------------------
-- Q3 品类销售分布
--
-- 【必须做金额分摊，不能直接 SUM(明细金额)】
--   实测发现：明细金额汇总 ≈ 1.42 亿，而订单实付 GMV 只有 2734 万，差 5 倍多。
--   原因是模拟数据的 unit_price（商品级定价，均值约 3800）与订单 pay_amount
--   （均值约 2500）不是同一套数学生成出来的，明细金额和订单金额对不上账。
--   企业里遇到这种情况的正确做法是：把订单实付金额按明细金额占比分摊到各品类，
--   而不是直接汇总明细金额 —— 否则品类 GMV 之和会与总 GMV 差出天际。
--   分摊公式：品类GMV = Σ( order.pay_amount × 该行明细金额 / 该订单明细金额合计 )
-- ----------------------------------------------------------------
SELECT p.category,
       COUNT(DISTINCT i.order_id)                                    AS order_cnt,
       SUM(i.quantity)                                              AS qty,
       ROUND(SUM(o.pay_amount * i.amount / t.order_amt), 2)          AS gmv_alloc,
       ROUND(100.0 * SUM(o.pay_amount * i.amount / t.order_amt)
             / SUM(SUM(o.pay_amount * i.amount / t.order_amt)) OVER (), 2) AS gmv_pct,
       ROUND(SUM(i.amount) / SUM(i.quantity), 2)                    AS avg_unit_price
FROM ydsh_dwd.dwd_order_item i
JOIN ydsh_dwd.dwd_order   o ON i.order_id = o.order_id AND i.order_date = o.order_date
JOIN ydsh_dwd.dim_product p ON i.product_id = p.product_id
JOIN (SELECT order_id, SUM(amount) AS order_amt
      FROM ydsh_dwd.dwd_order_item GROUP BY order_id) t ON i.order_id = t.order_id
WHERE o.status_code IN ('OS02','OS03','OS05')
GROUP BY p.category
ORDER BY gmv_alloc DESC;

-- Q3b 明细金额与订单实付的对账差异（数据可信度检查，结果写进简报）
SELECT COUNT(*)                                                        AS paid_orders,
       ROUND(SUM(o.pay_amount), 2)                                     AS gmv_paid,
       ROUND(SUM(t.order_amt), 2)                                      AS item_amount_sum,
       ROUND(SUM(t.order_amt) / SUM(o.pay_amount), 2)                   AS diff_ratio,
       ROUND(AVG(t.order_amt - o.pay_amount), 2)                        AS avg_gap
FROM ydsh_dwd.dwd_order o
JOIN (SELECT order_id, SUM(amount) AS order_amt
      FROM ydsh_dwd.dwd_order_item GROUP BY order_id) t ON o.order_id = t.order_id
WHERE o.status_code IN ('OS02','OS03','OS05');

-- ----------------------------------------------------------------
-- Q4 省份销售分布 TOP10
-- ----------------------------------------------------------------
SELECT s.province,
       COUNT(DISTINCT o.order_id)                                    AS order_cnt,
       COUNT(DISTINCT o.user_id)                                     AS user_cnt,
       ROUND(SUM(o.pay_amount), 2)                                   AS gmv,
       ROUND(100.0 * SUM(o.pay_amount) / SUM(SUM(o.pay_amount)) OVER (), 2) AS gmv_pct,
       ROUND(SUM(o.pay_amount) / COUNT(*), 2)                        AS aov
FROM ydsh_dwd.dwd_order o
JOIN ydsh_dwd.dim_store s ON o.store_id = s.store_id
WHERE o.status_code IN ('OS02','OS03','OS05')
GROUP BY s.province
ORDER BY gmv DESC
LIMIT 10;

-- ----------------------------------------------------------------
-- Q5 门店 TOP10 与门店分级表现
-- ----------------------------------------------------------------
SELECT s.store_id, s.store_name, s.province, s.store_level,
       COUNT(*)                                                      AS order_cnt,
       ROUND(SUM(o.pay_amount), 2)                                   AS gmv
FROM ydsh_dwd.dwd_order o
JOIN ydsh_dwd.dim_store s ON o.store_id = s.store_id
WHERE o.status_code IN ('OS02','OS03','OS05')
GROUP BY s.store_id, s.store_name, s.province, s.store_level
ORDER BY gmv DESC
LIMIT 10;

SELECT s.store_level,
       COUNT(DISTINCT s.store_id)                                    AS store_cnt,
       COUNT(*)                                                      AS order_cnt,
       ROUND(SUM(o.pay_amount), 2)                                   AS gmv,
       ROUND(SUM(o.pay_amount) / COUNT(DISTINCT s.store_id), 2)      AS gmv_per_store
FROM ydsh_dwd.dwd_order o
JOIN ydsh_dwd.dim_store s ON o.store_id = s.store_id
WHERE o.status_code IN ('OS02','OS03','OS05')
GROUP BY s.store_level
ORDER BY gmv_per_store DESC;

-- ----------------------------------------------------------------
-- Q6 渠道与支付方式分布
-- ----------------------------------------------------------------
SELECT o.channel, c.is_online,
       COUNT(*)                                                      AS order_cnt,
       ROUND(SUM(o.pay_amount), 2)                                   AS gmv,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2)            AS order_pct,
       ROUND(SUM(o.pay_amount) / COUNT(*), 2)                        AS aov
FROM ydsh_dwd.dwd_order o
JOIN ydsh_dwd.dim_code_channel c ON o.channel_code = c.channel_code
WHERE o.status_code IN ('OS02','OS03','OS05')
GROUP BY o.channel, c.is_online
ORDER BY gmv DESC;

SELECT o.pay_type, p.is_online,
       COUNT(*)                                                      AS order_cnt,
       ROUND(SUM(o.pay_amount), 2)                                   AS gmv,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2)            AS order_pct
FROM ydsh_dwd.dwd_order o
JOIN ydsh_dwd.dim_code_pay_type p ON o.pay_code = p.pay_code
WHERE o.status_code IN ('OS02','OS03','OS05')
GROUP BY o.pay_type, p.is_online
ORDER BY gmv DESC;

-- ----------------------------------------------------------------
-- Q7 时段分布（下单小时）
-- ----------------------------------------------------------------
SELECT order_hour,
       COUNT(*)                                                      AS order_cnt,
       ROUND(SUM(pay_amount), 2)                                     AS gmv
FROM ydsh_dwd.dwd_order
WHERE status_code IN ('OS02','OS03','OS05')
GROUP BY order_hour
ORDER BY order_hour;

-- ----------------------------------------------------------------
-- Q8 窗口函数：用户累计消费 + 订单排名
--   sum(...) over(partition by user_id order by order_date) = 用户累计消费（到当前订单为止）
--   row_number/rank/dense_rank 对比 = 排名口径差异演示
-- ----------------------------------------------------------------
SELECT user_id, order_id, order_date, pay_amount,
       ROUND(SUM(pay_amount) OVER (PARTITION BY user_id ORDER BY order_date, order_id
                                   ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW), 2) AS user_cum_amount,
       ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY pay_amount DESC, order_id)        AS rn_desc,
       RANK()       OVER (PARTITION BY user_id ORDER BY pay_amount DESC)                  AS rk_desc,
       DENSE_RANK() OVER (PARTITION BY user_id ORDER BY pay_amount DESC)                  AS drk_desc,
       COUNT(*)     OVER (PARTITION BY user_id)                                            AS user_order_cnt
FROM ydsh_dwd.dwd_order
WHERE status_code IN ('OS02','OS03','OS05')
  AND user_id IN ('U000001','U000002','U000003')
ORDER BY user_id, order_date, order_id
LIMIT 30;

-- 高价值用户 TOP20（累计消费 + 排名）
SELECT user_id,
       COUNT(*)                                                      AS order_cnt,
       ROUND(SUM(pay_amount), 2)                                     AS total_amount,
       ROUND(AVG(pay_amount), 2)                                     AS avg_amount,
       RANK() OVER (ORDER BY SUM(pay_amount) DESC)                   AS amount_rank
FROM ydsh_dwd.dwd_order
WHERE status_code IN ('OS02','OS03','OS05')
GROUP BY user_id
ORDER BY total_amount DESC
LIMIT 20;

-- 用户消费分层（RFM 里的 M，本周只做金额分层）
SELECT CASE WHEN amt >= 50000 THEN 'A. >=5万'
            WHEN amt >= 20000 THEN 'B. 2-5万'
            WHEN amt >= 10000 THEN 'C. 1-2万'
            ELSE 'D. <1万' END                                        AS amount_band,
       COUNT(*)                                                      AS user_cnt,
       ROUND(SUM(amt), 2)                                            AS gmv,
       ROUND(100.0 * SUM(amt) / SUM(SUM(amt)) OVER (), 2)            AS gmv_pct
FROM (SELECT user_id, SUM(pay_amount) amt FROM ydsh_dwd.dwd_order
      WHERE status_code IN ('OS02','OS03','OS05') GROUP BY user_id) u
GROUP BY CASE WHEN amt >= 50000 THEN 'A. >=5万'
              WHEN amt >= 20000 THEN 'B. 2-5万'
              WHEN amt >= 10000 THEN 'C. 1-2万'
              ELSE 'D. <1万' END
ORDER BY amount_band;

-- ----------------------------------------------------------------
-- Q9 用户行为漏斗（行为事实表 + 行为类型维度）
-- ----------------------------------------------------------------
SELECT b.behavior_type, c.funnel_step,
       COUNT(*)                                                      AS behavior_cnt,
       COUNT(DISTINCT b.user_id)                                     AS user_cnt,
       COUNT(DISTINCT b.product_id)                                  AS product_cnt
FROM ydsh_dwd.dwd_user_behavior b
JOIN ydsh_dwd.dim_code_behavior c ON b.behavior_code = c.behavior_code
GROUP BY b.behavior_type, c.funnel_step
ORDER BY funnel_step DESC, behavior_cnt DESC;

-- ----------------------------------------------------------------
-- Q10 大促日与非大促日对比（验证 dim_date.is_promo_day 的用途）
-- ----------------------------------------------------------------
SELECT IF(d.is_promo_day = 1, '大促日', '平常日')                     AS day_type,
       COUNT(*)                                                      AS order_cnt,
       ROUND(SUM(o.pay_amount), 2)                                   AS gmv,
       ROUND(SUM(o.pay_amount) / COUNT(DISTINCT o.order_date), 2)     AS gmv_per_day,
       ROUND(SUM(o.pay_amount) / COUNT(*), 2)                        AS aov
FROM ydsh_dwd.dwd_order o
JOIN ydsh_dwd.dim_date d ON o.order_date = CAST(d.full_date AS STRING)
WHERE o.status_code IN ('OS02','OS03','OS05')
GROUP BY IF(d.is_promo_day = 1, '大促日', '平常日')
ORDER BY gmv DESC;
