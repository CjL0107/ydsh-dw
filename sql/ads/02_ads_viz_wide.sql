-- =============================================================
-- 可视化宽表 ads_viz_wide
-- =============================================================
-- 为什么需要这张表（第 4 周补做）：
--   原来的 8 张 ADS 结果表是"一图一表"，字段互不相交 ——
--   支付方式表里只有 pay_type，品类表里只有 category，没有公共维度。
--   Superset 的交叉筛选是"把 A 图点中的维度下发给 B 图"，B 图的数据集里
--   没有这一列就只能静默忽略：界面上标签出现了、查询也重发了，图却不变化。
--   所以联动做不成不是配置问题，是模型问题。
--
-- 这张表的定位：
--   粒度 = 订单明细（一行一个商品行），维度覆盖 日期/门店/省份/城市/品类/渠道/支付方式，
--   任取两列做分组、任一列做筛选都成立。行数 2.6 万，MySQL 直接扛。
--
-- 金额口径（容易错，写清楚）：
--   明细表的 amount 合计（3.05 亿）与订单实付（2734 万）差了 11 倍，
--   直接 sum(amount) 会和大屏、其他面板对不上。
--   这里按「该明细金额 / 订单内明细合计」把订单实付摊回每一行，
--   保证 sum(pay_amount) == sum(订单实付) == 27,342,828.20。
-- =============================================================

CREATE TABLE IF NOT EXISTS ydsh_ads.ads_viz_wide (
  stat_date     STRING        COMMENT '下单日期 yyyy-MM-dd',
  province      STRING        COMMENT '省份（来自 dim_store）',
  city          STRING        COMMENT '城市',
  store_id      STRING        COMMENT '门店ID',
  store_name    STRING        COMMENT '门店名称',
  store_level   STRING        COMMENT '门店分级',
  category      STRING        COMMENT '一级品类（来自 dim_product）',
  sub_category  STRING        COMMENT '二级品类',
  channel       STRING        COMMENT '下单渠道',
  pay_type      STRING        COMMENT '支付方式',
  order_id      STRING        COMMENT '订单ID（COUNT DISTINCT 用）',
  user_id       STRING        COMMENT '用户ID',
  order_hour    INT           COMMENT '下单小时',
  quantity      INT           COMMENT '购买件数',
  pay_amount    DECIMAL(16,2) COMMENT '摊回后的实付金额'
)
COMMENT '可视化宽表：明细粒度，供仪表板交叉筛选与任意维度组合'
STORED AS ORC;

INSERT OVERWRITE TABLE ydsh_ads.ads_viz_wide
SELECT
  o.order_date                AS stat_date,
  s.province, s.city, s.store_id, s.store_name, s.store_level,
  p.category, p.sub_category,
  o.channel, o.pay_type,
  o.order_id, o.user_id, o.order_hour,
  i.quantity,
  ROUND(i.amount * o.pay_amount / t.amt, 2) AS pay_amount
FROM ydsh_dwd.dwd_order o
JOIN ydsh_dwd.dim_store      s ON o.store_id   = s.store_id
JOIN ydsh_dwd.dwd_order_item i ON o.order_id   = i.order_id
JOIN ydsh_dwd.dim_product    p ON i.product_id = p.product_id
JOIN (
  SELECT order_id, SUM(amount) AS amt
  FROM ydsh_dwd.dwd_order_item
  GROUP BY order_id
) t ON t.order_id = o.order_id
WHERE o.status_code IN ('OS02','OS03','OS05')
  AND t.amt > 0;
