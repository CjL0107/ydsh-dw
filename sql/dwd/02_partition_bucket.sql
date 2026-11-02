-- ================================================================
-- 子任务 6：分区表 / 分桶表 的使用验证
-- 周次：第 2 周
-- 作者：梁常金
--
-- 命题作文里"尝试分桶表的使用"这句话容易糊弄过去，所以这里把三件事都做实：
--   ① 分区裁剪：查询带 order_date 条件时，Hive 只扫对应目录（用 EXPLAIN 看分区数）
--   ② 分桶写入：按 user_id 哈希落 8 个桶文件，验证桶数与数据分布
--   ③ 桶内 JOIN：同键分桶的两表可以做 Map-Side Join，省掉 Reduce 端 Shuffle
--
-- 说明：本项目数据量小（订单 1.5 万行），分桶的性能收益体现不出来，
--   所以这里只做"能不能用、用对了没有"的技术验证，不虚构性能数字。
-- ================================================================

USE ydsh_dwd;

-- ----------------------------------------------------------------
-- ① 分区裁剪验证
-- ----------------------------------------------------------------
-- 全表 366 个分区，查单日只应命中 1 个分区
EXPLAIN
SELECT COUNT(*) FROM ydsh_dwd.dwd_order WHERE order_date = '2024-07-26';

-- 对比：不带分区条件 = 全分区扫描
EXPLAIN
SELECT COUNT(*) FROM ydsh_dwd.dwd_order WHERE substr(order_timestamp, 1, 10) = '2024-07-26';

-- 实测两条语句的耗时差（前者扫 1 个分区，后者扫 366 个分区）
SELECT COUNT(*) AS one_partition FROM ydsh_dwd.dwd_order WHERE order_date = '2024-07-26';
SELECT COUNT(*) AS full_scan      FROM ydsh_dwd.dwd_order WHERE substr(order_timestamp, 1, 10) = '2024-07-26';

-- 分区清单：366 个日期分区
SHOW PARTITIONS ydsh_dwd.dwd_order;

-- ----------------------------------------------------------------
-- ② 分桶表装载（按 user_id 哈希，8 桶）
--    Hive 3+ 向分桶表写入时会自动按 CLUSTERED BY 列做哈希，
--    hive.enforce.bucketing 在 4.0 已废弃，写在这里只是为兼容旧版本演示。
-- ----------------------------------------------------------------
SET hive.enforce.bucketing = true;

INSERT OVERWRITE TABLE ydsh_dwd.dwd_order_bucketed
SELECT order_id,
       user_id,
       store_id,
       order_date,
       order_timestamp,
       pay_amount,
       order_status
FROM ydsh_dwd.dwd_order;

-- 桶数校验：Num Buckets 应为 8
DESCRIBE FORMATTED ydsh_dwd.dwd_order_bucketed;

-- 桶内数据分布：8 个桶的订单数应大致均匀（哈希散列）
SELECT INPUT__FILE__NAME AS bucket_file, COUNT(*) AS rows_cnt
FROM ydsh_dwd.dwd_order_bucketed
GROUP BY INPUT__FILE__NAME
ORDER BY bucket_file;

-- ----------------------------------------------------------------
-- ③ 桶内 Map-Side Join 验证
--    自连接：同一用户的历史订单数。两表同键同桶，可走 bucket map join。
-- ----------------------------------------------------------------
SET hive.optimize.bucketmapjoin = true;
SET hive.auto.convert.join = true;

SELECT /*+ MAPJOIN(b) */ COUNT(*) AS joined_rows
FROM ydsh_dwd.dwd_order_bucketed a
JOIN ydsh_dwd.dwd_order_bucketed b ON a.user_id = b.user_id;

-- 单用户订单数 TOP10（顺便验证分桶表读出来和分区表一致）
SELECT user_id, COUNT(*) AS order_cnt
FROM ydsh_dwd.dwd_order_bucketed
GROUP BY user_id
ORDER BY order_cnt DESC
LIMIT 10;
