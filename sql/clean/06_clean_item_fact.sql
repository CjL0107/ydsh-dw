-- ================================================================
-- 子任务 2：订单明细事实表清洗与装载（数量/单价异常）
-- 输入：ydsh_ods.ods_order_item  +  ydsh_dwd.dwd_order（取分区日期）
-- 输出：ydsh_dwd.dwd_order_item（按 order_date 分区，与订单事实表分区对齐）
-- 周次：第 2 周
-- 作者：梁常金
--
-- 数量异常实况：
--   quantity = 0 : 386 条   -> 剔除（0 件不构成交易，留着只会让"件数"指标虚高行数）
--   quantity < 0 : 332 条   -> 取绝对值（与负金额同源，都是录入时符号写反）
--   quantity > 5 : 363 条，取值全是 99 -> 明显是乱填，用同商品正常区间中位数替换
--                  （正常子集 1~5，P99 = 4.95，最大 5，所以 >5 必为异常）
--   unit_price < 0 : 798 条 -> 取绝对值
--
-- 分区对齐的意义：
--   明细表用所属订单的下单日期做分区，这样"查某天的订单+明细"两张表都只扫一个分区，
--   不会出现"订单表扫 1 天、明细表扫全表"的错位。
--   代价是装载时必须 JOIN 订单表取日期，因此明细表依赖订单表先装载完成。
-- ================================================================

USE ydsh_dwd;

SET hive.exec.dynamic.partition = true;
SET hive.exec.dynamic.partition.mode = nonstrict;
SET hive.exec.max.dynamic.partitions = 2000;
SET hive.exec.max.dynamic.partitions.pernode = 2000;

INSERT OVERWRITE TABLE ydsh_dwd.dwd_order_item PARTITION (order_date)
SELECT
  i.item_id,
  i.order_id,
  i.product_id,
  CAST(i.qty_fixed AS INT)                                          AS quantity,
  i.quantity                                                        AS quantity_raw,
  CAST(ABS(i.unit_price) AS DECIMAL(12,2))                          AS unit_price,
  CAST(i.discount AS DECIMAL(6,4))                                  AS discount,
  -- 明细金额：数量 × 单价 × (1 - 折扣率)
  CAST(round(i.qty_fixed * ABS(i.unit_price) * (1 - i.discount), 2) AS DECIMAL(14,2)) AS amount,
  i.qty_flag,
  o.order_date
FROM (
  SELECT
    d.*,
    CASE
      WHEN d.quantity < 0  THEN -d.quantity
      WHEN d.quantity > 5  THEN coalesce(q.med_qty, 2)
      ELSE d.quantity
    END                                                             AS qty_fixed,
    CASE
      WHEN d.quantity < 0 THEN 'SIGN_FIXED'
      WHEN d.quantity > 5 THEN 'OUTLIER_REPAIRED'
      ELSE 'NORMAL'
    END                                                             AS qty_flag
  FROM ydsh_ods.ods_order_item d
  LEFT JOIN (
    -- 正常区间 (1~5) 的商品数量中位数
    SELECT product_id,
           CAST(percentile_approx(CAST(quantity AS DOUBLE), CAST(0.5 AS DOUBLE)) AS INT) AS med_qty
    FROM ydsh_ods.ods_order_item
    WHERE quantity BETWEEN 1 AND 5
    GROUP BY product_id
  ) q ON d.product_id = q.product_id
  WHERE d.quantity <> 0        -- 数量为 0 的明细直接剔除，不进入 DWD
) i
JOIN ydsh_dwd.dwd_order o ON i.order_id = o.order_id;

-- 装载后自检
SELECT COUNT(*)                                                    AS total_rows,
       SUM(IF(quantity <= 0, 1, 0))                                AS qty_nonpos,
       SUM(IF(quantity > 5, 1, 0))                                 AS qty_outlier,
       SUM(IF(qty_flag = 'SIGN_FIXED', 1, 0))                      AS qty_sign_fixed,
       SUM(IF(qty_flag = 'OUTLIER_REPAIRED', 1, 0))                AS qty_outlier_repaired,
       SUM(IF(unit_price < 0, 1, 0))                               AS price_neg,
       SUM(IF(amount < 0, 1, 0))                                   AS amount_neg,
       SUM(IF(discount < 0 OR discount > 1, 1, 0))                 AS discount_bad
FROM ydsh_dwd.dwd_order_item;
