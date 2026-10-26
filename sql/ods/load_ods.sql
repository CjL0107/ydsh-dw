-- ================================================================
-- 02_ODS 数据导入脚本
-- 周次：第 1 周 · 子任务 4
-- 说明：
--   使用 LOAD DATA LOCAL INPATH 从容器内 /data 目录加载 CSV。
--   /data 是 docker-compose.yml 里把宿主机 ./data 目录挂进来的只读目录，
--   所以必须先确认 data/ 下 6 个 csv 存在，否则会报 File does not exist。
--   OVERWRITE 保证本脚本可重复执行（幂等），不会重复追加数据。
-- 执行：docker exec -i hive-server beeline -u 'jdbc:hive2://localhost:10000/default' -f /sql/ods/load_ods.sql
-- ================================================================

USE ydsh_ods;

LOAD DATA LOCAL INPATH '/data/ods_user.csv'           OVERWRITE INTO TABLE ydsh_ods.ods_user;
LOAD DATA LOCAL INPATH '/data/ods_product.csv'        OVERWRITE INTO TABLE ydsh_ods.ods_product;
LOAD DATA LOCAL INPATH '/data/ods_store.csv'          OVERWRITE INTO TABLE ydsh_ods.ods_store;
LOAD DATA LOCAL INPATH '/data/ods_order.csv'          OVERWRITE INTO TABLE ydsh_ods.ods_order;
LOAD DATA LOCAL INPATH '/data/ods_order_item.csv'     OVERWRITE INTO TABLE ydsh_ods.ods_order_item;
LOAD DATA LOCAL INPATH '/data/ods_user_behavior.csv'  OVERWRITE INTO TABLE ydsh_ods.ods_user_behavior;

-- ---------------------------------------------------------------
-- 行数校验：实际行数 vs 数据字典预期行数
-- 判定标准：is_ok = OK 表示导入完整
-- 注意：ods_order 的 15600 是"含重复"的行数，与预期一致即为导入成功，
--       是否重复由数据质量评估负责发现，不由导入负责"修好"。
-- ---------------------------------------------------------------
SELECT 'ods_user' AS table_name, COUNT(*) AS actual_rows, 1000 AS expected_rows,
       IF(COUNT(*) = 1000, 'OK', 'CHECK') AS is_ok FROM ydsh_ods.ods_user
UNION ALL
SELECT 'ods_product', COUNT(*), 600,
       IF(COUNT(*) = 600, 'OK', 'CHECK') FROM ydsh_ods.ods_product
UNION ALL
SELECT 'ods_store', COUNT(*), 60,
       IF(COUNT(*) = 60, 'OK', 'CHECK') FROM ydsh_ods.ods_store
UNION ALL
SELECT 'ods_order', COUNT(*), 15600,
       IF(COUNT(*) = 15600, 'OK', 'CHECK') FROM ydsh_ods.ods_order
UNION ALL
SELECT 'ods_order_item', COUNT(*), 37549,
       IF(COUNT(*) = 37549, 'OK', 'CHECK') FROM ydsh_ods.ods_order_item
UNION ALL
SELECT 'ods_user_behavior', COUNT(*), 60000,
       IF(COUNT(*) = 60000, 'OK', 'CHECK') FROM ydsh_ods.ods_user_behavior;
