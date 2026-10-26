-- ================================================================
-- 03_数据质量预评估（五维度扫描）
-- 周次：第 1 周 · 子任务 5
-- 维度：完整性 / 唯一性 / 有效性 / 一致性 / 准确性
-- 说明：
--   本脚本只做"诊断"，不改任何数据。所有结果用于填写
--   docs/数据质量预评估表.md，也是第 2 周《数据质量清洗报告》的基线。
-- 执行：docker exec -i hive-server beeline -u 'jdbc:hive2://localhost:10000/default' \
--         --outputformat=table -f /sql/quality/quality_assess.sql
-- ================================================================

USE ydsh_ods;

-- ================================================================
-- 维度一：完整性（Completeness）—— 字段空值率
-- 判定：Hive 从 CSV 读入时，空串在 STRING 列会保留为 ''，
--       在 INT/DECIMAL 列会变 NULL，所以两种都要判。
-- ================================================================

SELECT 'ods_user' AS tbl, 'gender' AS col,
       SUM(IF(nvl(trim(gender),'') = '', 1, 0)) AS null_cnt, COUNT(*) AS total
FROM ods_user
UNION ALL SELECT 'ods_user','age',        SUM(IF(age IS NULL, 1, 0)), COUNT(*) FROM ods_user
UNION ALL SELECT 'ods_user','email',      SUM(IF(nvl(trim(email),'') = '', 1, 0)), COUNT(*) FROM ods_user
UNION ALL SELECT 'ods_user','member_level',SUM(IF(nvl(trim(member_level),'') = '', 1, 0)), COUNT(*) FROM ods_user
UNION ALL SELECT 'ods_user','phone',      SUM(IF(nvl(trim(phone),'') = '', 1, 0)), COUNT(*) FROM ods_user
UNION ALL SELECT 'ods_product','launch_date', SUM(IF(nvl(trim(launch_date),'') = '', 1, 0)), COUNT(*) FROM ods_product
UNION ALL SELECT 'ods_product','price',   SUM(IF(price IS NULL, 1, 0)), COUNT(*) FROM ods_product
UNION ALL SELECT 'ods_product','stock',   SUM(IF(stock IS NULL, 1, 0)), COUNT(*) FROM ods_product
UNION ALL SELECT 'ods_order','pay_amount',SUM(IF(pay_amount IS NULL, 1, 0)), COUNT(*) FROM ods_order
UNION ALL SELECT 'ods_order','pay_type',  SUM(IF(nvl(trim(pay_type),'') = '', 1, 0)), COUNT(*) FROM ods_order
UNION ALL SELECT 'ods_order','order_status',SUM(IF(nvl(trim(order_status),'') = '', 1, 0)), COUNT(*) FROM ods_order
UNION ALL SELECT 'ods_order_item','quantity', SUM(IF(quantity IS NULL, 1, 0)), COUNT(*) FROM ods_order_item
UNION ALL SELECT 'ods_user_behavior','behavior_type', SUM(IF(nvl(trim(behavior_type),'') = '', 1, 0)), COUNT(*) FROM ods_user_behavior;

-- ================================================================
-- 维度二：唯一性（Uniqueness）—— 主键重复
-- 关键区分：整行完全重复（重复导入）vs 主键重复但内容不同（脏主键）
-- ================================================================

-- 2.1 六张表主键重复扫描（重复组数 / 涉及行数 / 多出行数）
SELECT 'ods_user' AS tbl, COUNT(*) AS dup_pk_groups, IFNULL(SUM(c), 0) AS dup_rows,
       IFNULL(SUM(c), 0) - COUNT(*) AS excess_rows
FROM (SELECT user_id AS k, COUNT(*) AS c FROM ods_user GROUP BY user_id HAVING COUNT(*) > 1) t
UNION ALL
SELECT 'ods_product', COUNT(*), IFNULL(SUM(c), 0), IFNULL(SUM(c), 0) - COUNT(*)
FROM (SELECT product_id AS k, COUNT(*) AS c FROM ods_product GROUP BY product_id HAVING COUNT(*) > 1) t
UNION ALL
SELECT 'ods_store', COUNT(*), IFNULL(SUM(c), 0), IFNULL(SUM(c), 0) - COUNT(*)
FROM (SELECT store_id AS k, COUNT(*) AS c FROM ods_store GROUP BY store_id HAVING COUNT(*) > 1) t
UNION ALL
SELECT 'ods_order', COUNT(*), IFNULL(SUM(c), 0), IFNULL(SUM(c), 0) - COUNT(*)
FROM (SELECT order_id AS k, COUNT(*) AS c FROM ods_order GROUP BY order_id HAVING COUNT(*) > 1) t
UNION ALL
SELECT 'ods_order_item', COUNT(*), IFNULL(SUM(c), 0), IFNULL(SUM(c), 0) - COUNT(*)
FROM (SELECT item_id AS k, COUNT(*) AS c FROM ods_order_item GROUP BY item_id HAVING COUNT(*) > 1) t
UNION ALL
SELECT 'ods_user_behavior', COUNT(*), IFNULL(SUM(c), 0), IFNULL(SUM(c), 0) - COUNT(*)
FROM (SELECT behavior_id AS k, COUNT(*) AS c FROM ods_user_behavior GROUP BY behavior_id HAVING COUNT(*) > 1) t;

-- 2.2 订单重复的两类形态拆分（去重策略不同的根本原因）
SELECT
  COUNT(*)                                              AS dup_order_ids,
  -- 判据：组内去重后只剩 1 种内容 => 整行完全重复；否则 => 主键重复但内容不同
  SUM(IF(distinct_rows = 1, 1, 0))                      AS full_dup_groups,
  SUM(IF(distinct_rows > 1, 1, 0))                      AS content_diff_groups
FROM (
  SELECT order_id,
         COUNT(*) AS rows_cnt,
         COUNT(DISTINCT CONCAT_WS('|', user_id, store_id, order_date, order_time,
                                  CAST(pay_amount AS STRING), CAST(discount_amount AS STRING),
                                  pay_type, order_status, channel)) AS distinct_rows
  FROM ods_order
  GROUP BY order_id
  HAVING COUNT(*) > 1
) t;

-- 2.3 全表逐行去重后行数（与 15600 对比，差值即需删除的重复行）
SELECT COUNT(*) AS total_rows, COUNT(DISTINCT order_id) AS distinct_order_ids
FROM ods_order;

-- ================================================================
-- 维度三：有效性（Validity）—— 枚举取值域、日期格式、手机号格式
-- ================================================================

-- 3.1 枚举字段取值分布（找出同义异写）
SELECT 'gender' AS col, gender AS val, COUNT(*) AS cnt FROM ods_user GROUP BY gender
UNION ALL SELECT 'member_level', member_level, COUNT(*) FROM ods_user GROUP BY member_level
UNION ALL SELECT 'pay_type', pay_type, COUNT(*) FROM ods_order GROUP BY pay_type
UNION ALL SELECT 'order_status', order_status, COUNT(*) FROM ods_order GROUP BY order_status
UNION ALL SELECT 'channel', channel, COUNT(*) FROM ods_order GROUP BY channel
UNION ALL SELECT 'behavior_type', behavior_type, COUNT(*) FROM ods_user_behavior GROUP BY behavior_type
ORDER BY col, cnt DESC;

-- 3.2 日期格式合规性（有效性核心：是否符合 yyyy-MM-dd）
SELECT 'register_date' AS col,
       SUM(IF(register_date RLIKE '^[0-9]{4}-[0-9]{2}-[0-9]{2}$', 1, 0)) AS std_cnt,
       SUM(IF(register_date RLIKE '^[0-9]{4}/[0-9]{2}/[0-9]{2}$', 1, 0)) AS slash_cnt,
       SUM(IF(register_date RLIKE '^[0-9]{8}$', 1, 0))                   AS compact_cnt,
       COUNT(*) AS total
FROM ods_user
UNION ALL
SELECT 'order_date',
       SUM(IF(order_date RLIKE '^[0-9]{4}-[0-9]{2}-[0-9]{2}$', 1, 0)),
       SUM(IF(order_date RLIKE '^[0-9]{4}/[0-9]{2}/[0-9]{2}$', 1, 0)),
       SUM(IF(order_date RLIKE '^[0-9]{8}$', 1, 0)),
       COUNT(*)
FROM ods_order
UNION ALL
SELECT 'behavior_time',
       SUM(IF(behavior_time RLIKE '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}$', 1, 0)),
       SUM(IF(behavior_time RLIKE '^[0-9]{4}/[0-9]{2}/[0-9]{2} [0-9]{2}:[0-9]{2}$', 1, 0)),
       0,
       COUNT(*)
FROM ods_user_behavior;

-- 3.3 手机号格式合规性（规范：11 位纯数字，1 开头）
SELECT COUNT(*) AS total,
       SUM(IF(phone RLIKE '^1[0-9]{10}$', 1, 0))    AS valid_cnt,
       SUM(IF(phone RLIKE '^1[0-9]{2}-[0-9]{4}-[0-9]{4}$', 1, 0)) AS dash_cnt
FROM ods_user;

-- ================================================================
-- 维度四：一致性（Consistency）—— 主外键关联完整、跨表口径一致
-- ================================================================

-- 4.1 事实表外键是否存在孤儿记录
SELECT 'ods_order.user_id -> ods_user' AS relation,
       COUNT(*) AS orphan_rows
FROM ods_order o LEFT JOIN ods_user u ON o.user_id = u.user_id
WHERE u.user_id IS NULL
UNION ALL
SELECT 'ods_order.store_id -> ods_store',
       COUNT(*)
FROM ods_order o LEFT JOIN ods_store s ON o.store_id = s.store_id
WHERE s.store_id IS NULL
UNION ALL
SELECT 'ods_order_item.order_id -> ods_order',
       COUNT(*)
FROM ods_order_item i LEFT JOIN (SELECT DISTINCT order_id FROM ods_order) o
  ON i.order_id = o.order_id
WHERE o.order_id IS NULL
UNION ALL
SELECT 'ods_order_item.product_id -> ods_product',
       COUNT(*)
FROM ods_order_item i LEFT JOIN ods_product p ON i.product_id = p.product_id
WHERE p.product_id IS NULL
UNION ALL
SELECT 'ods_user_behavior.user_id -> ods_user',
       COUNT(*)
FROM ods_user_behavior b LEFT JOIN ods_user u ON b.user_id = u.user_id
WHERE u.user_id IS NULL
UNION ALL
SELECT 'ods_user_behavior.product_id -> ods_product',
       COUNT(*)
FROM ods_user_behavior b LEFT JOIN ods_product p ON b.product_id = p.product_id
WHERE p.product_id IS NULL;

-- 4.2 省市口径一致性：同城市在不同表中归属的省份是否冲突
SELECT COUNT(*) AS conflict_city_cnt
FROM (
  SELECT city, COUNT(DISTINCT province) AS p_cnt
  FROM (
    SELECT city, province FROM ods_user
    UNION ALL
    SELECT city, province FROM ods_store
  ) t
  GROUP BY city
) c
WHERE p_cnt > 1;

-- ================================================================
-- 维度五：准确性（Accuracy）—— 数值是否落在业务合理区间
-- ================================================================

SELECT '用户年龄 age' AS metric,
       SUM(IF(age IS NULL, 1, 0))                 AS null_cnt,
       SUM(IF(age < 0, 1, 0))                     AS neg_cnt,
       SUM(IF(age = 0, 1, 0))                     AS zero_cnt,
       SUM(IF(age > 120, 1, 0))                   AS over_cnt,
       MIN(age) AS min_v, MAX(age) AS max_v
FROM ods_user
UNION ALL
SELECT '订单实付 pay_amount',
       SUM(IF(pay_amount IS NULL, 1, 0)),
       SUM(IF(pay_amount < 0, 1, 0)),
       SUM(IF(pay_amount = 0, 1, 0)),
       SUM(IF(pay_amount > 100000, 1, 0)),
       MIN(pay_amount), MAX(pay_amount)
FROM ods_order
UNION ALL
SELECT '订单优惠 discount_amount',
       SUM(IF(discount_amount IS NULL, 1, 0)),
       SUM(IF(discount_amount < 0, 1, 0)),
       SUM(IF(discount_amount = 0, 1, 0)),
       SUM(IF(discount_amount > pay_amount, 1, 0)),
       MIN(discount_amount), MAX(discount_amount)
FROM ods_order
UNION ALL
SELECT '商品售价 price',
       SUM(IF(price IS NULL, 1, 0)),
       SUM(IF(price < 0, 1, 0)),
       SUM(IF(price = 0, 1, 0)),
       SUM(IF(price > 100000, 1, 0)),
       MIN(price), MAX(price)
FROM ods_product
UNION ALL
SELECT '明细数量 quantity',
       SUM(IF(quantity IS NULL, 1, 0)),
       SUM(IF(quantity < 0, 1, 0)),
       SUM(IF(quantity = 0, 1, 0)),
       SUM(IF(quantity > 100, 1, 0)),
       MIN(quantity), MAX(quantity)
FROM ods_order_item
UNION ALL
SELECT '明细单价 unit_price',
       SUM(IF(unit_price IS NULL, 1, 0)),
       SUM(IF(unit_price < 0, 1, 0)),
       SUM(IF(unit_price = 0, 1, 0)),
       SUM(IF(unit_price > 100000, 1, 0)),
       MIN(unit_price), MAX(unit_price)
FROM ods_order_item;

-- ================================================================
-- 维度六（附加）：业务逻辑准确性 —— 成本高于售价、缺货却仍可售
-- ================================================================

SELECT '成本价 >= 售价 的商品数' AS check_item, COUNT(*) AS cnt
FROM ods_product WHERE cost >= price
UNION ALL
SELECT '库存为 0 但状态为在售', COUNT(*)
FROM ods_product WHERE stock = 0 AND status = '在售'
UNION ALL
SELECT '折扣率超过 1（>100%）的明细', COUNT(*)
FROM ods_order_item WHERE discount > 1;
