-- ================================================================
-- 06_数据清洗（一）：订单表重复值检测与去重
-- 周次：第 1 周 · 子任务 7
-- 输入：ydsh_ods.ods_order（15600 行）
-- 输出：ydsh_dwd.dwd_order（预期 15000 行）
--
-- 核心考点：区分两类重复，它们的成因和处理方式完全不同
--   类型 A｜整行完全重复：所有字段一模一样 → 典型的"重复导入/重跑任务"
--          处理：直接保留任意一条（ROW_NUMBER 取 1 即可）
--   类型 B｜主键重复但内容不同：同一 order_id 出现两次，金额/状态不一样
--          → 典型的"业务侧重复提交或状态未回写"
--          处理：不能随便删，要按业务规则选"最可信"的一条
--
-- 本项目去重规则（写进报告的"业务依据"）：
--   按 order_id 分组，按以下优先级排序后保留 rn = 1：
--     1) order_time 降序   —— 保留最后一次写入的那条
--     2) pay_amount 降序   —— 时间相同时，保留实际成交金额更大的
--     3) order_date 降序
--     4) 整行内容降序      —— 最后的确定性兜底，保证多次运行结果一致
--   注意：没有用"保留第一条"这种做法，因为 Hive 无序，
--         不给出确定排序的 ROW_NUMBER() 每次结果可能不同（不可复现 → 扣分项）
-- ================================================================

USE ydsh_dwd;

DROP TABLE IF EXISTS ydsh_dwd.dwd_order;

CREATE TABLE ydsh_dwd.dwd_order
COMMENT '订单事实表-已去重'
STORED AS PARQUET
AS
SELECT
  order_id,
  user_id,
  store_id,
  order_date,
  order_time,
  pay_amount,
  discount_amount,
  pay_type,
  order_status,
  channel
FROM (
  SELECT
    *,
    ROW_NUMBER() OVER (
      PARTITION BY order_id
      ORDER BY order_time   DESC,
               pay_amount  DESC,
               order_date  DESC,
               rn_key      DESC
    ) AS rn,
    COUNT(*) OVER (PARTITION BY order_id) AS dup_cnt
  FROM (
    SELECT
      *,
      -- 整行指纹：作为最终排序兜底，保证结果可复现
      CONCAT_WS('|', nvl(user_id, ''), nvl(store_id, ''), nvl(order_date, ''),
                nvl(order_time, ''), CAST(pay_amount AS STRING),
                CAST(discount_amount AS STRING), nvl(pay_type, ''),
                nvl(order_status, ''), nvl(channel, '')) AS rn_key
    FROM ydsh_ods.ods_order
  ) t1
) t2
WHERE rn = 1;

-- ---------------------------------------------------------------
-- 去重效果自检：三行结果都必须满足
-- ---------------------------------------------------------------
-- 1) 去重后行数应为 15000，重复 order_id 应为 0
SELECT COUNT(*)                        AS rows_after_dedup,
       COUNT(DISTINCT order_id)        AS distinct_order_ids,
       COUNT(*) - COUNT(DISTINCT order_id) AS still_duplicated
FROM ydsh_dwd.dwd_order;

-- 2) 去重前后对比（用于报告"行数差 = 预评估阶段发现的重复行数"）
SELECT '去重前 ods_order' AS stage, COUNT(*) AS rows_cnt FROM ydsh_ods.ods_order
UNION ALL
SELECT '去重后 dwd_order', COUNT(*) FROM ydsh_dwd.dwd_order
UNION ALL
SELECT '删除的重复行数',
       (SELECT COUNT(*) FROM ydsh_ods.ods_order) - (SELECT COUNT(*) FROM ydsh_dwd.dwd_order);

-- 3) 两类重复形态的数量（报告里要有这张表）
SELECT
  COUNT(*)                                                        AS 重复订单数,
  SUM(IF(distinct_cnt = 1, 1, 0))                                 AS 整行完全重复_组,
  SUM(IF(distinct_cnt > 1, 1, 0))                                 AS 主键重复内容不同_组,
  SUM(cnt_in_group - 1)                                           AS 需删除行数
FROM (
  SELECT order_id,
         COUNT(*) AS cnt_in_group,
         COUNT(DISTINCT rn_key) AS distinct_cnt
  FROM (
    SELECT order_id,
           CONCAT_WS('|', nvl(user_id, ''), nvl(store_id, ''), nvl(order_date, ''),
                     nvl(order_time, ''), CAST(pay_amount AS STRING),
                     CAST(discount_amount AS STRING), nvl(pay_type, ''),
                     nvl(order_status, ''), nvl(channel, '')) AS rn_key
    FROM ydsh_ods.ods_order
  ) a
  GROUP BY order_id
  HAVING COUNT(*) > 1
) b;
