-- ================================================================
-- 子任务 1+2：用户行为事实表清洗与装载
-- 输入：ydsh_ods.ods_user_behavior   输出：ydsh_dwd.dwd_user_behavior（按 behavior_date 分区）
-- 周次：第 2 周
-- 作者：梁常金
--
-- 行为类型实况：10 种写法，其中 57551 行（96%）是英文缩写 pv/buy/cart/fav，
--   只有 2449 行是中文。也就是说这个字段"脏"的不是少数派，而是绝大多数派 ——
--   统一成中文等于把主流量改一遍。做法上仍然收敛到中文，理由是：
--   订单/支付/渠道三个字段的标准值都是中文，行为类型如果保留英文，
--   下游做"全站行为口径"时会出现"pv 和 浏览 到底是不是一个东西"的歧义。
--   改动量大的风险由 dq_enum_scan 前后对比 + 值域校验兜住。
--
-- 行为时间实况：6088 行（10.1%）是 2024/11/14 00:13 这种"斜杠 + 缺秒"格式，
--   其余是标准的 2024-11-05 10:20:30。统一为 yyyy-MM-dd HH:mm:ss，缺秒补 :00。
--   注意：缺秒的行日期拿不到秒级精度，补 00 是一种"精度降级"而不是数据错误，
--   所以单独打 time_is_fixed 标记，后续做"小时分布"分析时不受影响。
-- ================================================================

USE ydsh_dwd;

SET hive.exec.dynamic.partition = true;
SET hive.exec.dynamic.partition.mode = nonstrict;
SET hive.exec.max.dynamic.partitions = 2000;
SET hive.exec.max.dynamic.partitions.pernode = 2000;

INSERT OVERWRITE TABLE ydsh_dwd.dwd_user_behavior PARTITION (behavior_date)
SELECT
  b.behavior_id,
  b.user_id,
  b.product_id,
  b.behavior_type,
  c.behavior_code,
  b.behavior_time,
  b.session_id,
  b.time_is_fixed,
  substr(b.behavior_time, 1, 10)                                    AS behavior_date
FROM (
  SELECT
    d.behavior_id, d.user_id, d.product_id, d.session_id,

    -- 行为类型标准化
    CASE
      WHEN lower(trim(d.behavior_type)) = 'pv'   THEN '浏览'
      WHEN lower(trim(d.behavior_type)) = 'cart' THEN '加购'
      WHEN lower(trim(d.behavior_type)) = 'fav'  THEN '收藏'
      WHEN lower(trim(d.behavior_type)) = 'buy'  THEN '购买'
      WHEN trim(d.behavior_type) = '浏览'        THEN '浏览'
      WHEN trim(d.behavior_type) = '加购'        THEN '加购'
      WHEN trim(d.behavior_type) = '收藏'        THEN '收藏'
      WHEN trim(d.behavior_type) = '购买'        THEN '购买'
      ELSE '未知'
    END                                                             AS behavior_type,

    -- 行为时间统一：先换日期分隔符，再按长度补秒
    concat(regexp_replace(substr(trim(d.behavior_time), 1, 10), '/', '-'),
           ' ',
           CASE WHEN length(trim(d.behavior_time)) = 16
                THEN concat(substr(trim(d.behavior_time), 12), ':00')
                ELSE substr(trim(d.behavior_time), 12)
           END)                                                     AS behavior_time,

    IF(length(trim(d.behavior_time)) = 16, 1, 0)                    AS time_is_fixed
  FROM ydsh_ods.ods_user_behavior d
) b
-- 编码映射
LEFT JOIN ydsh_dwd.dim_code_behavior c ON b.behavior_type = c.behavior_name;

-- 装载后自检
SELECT COUNT(*)                                                    AS total_rows,
       SUM(IF(behavior_time NOT RLIKE '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}$', 1, 0)) AS time_bad,
       SUM(IF(behavior_type NOT IN ('浏览','加购','收藏','购买'), 1, 0)) AS type_bad,
       SUM(IF(behavior_code IS NULL, 1, 0))                        AS code_null,
       SUM(time_is_fixed)                                          AS time_fixed
FROM ydsh_dwd.dwd_user_behavior;

-- 行为类型分布（标准化后应只剩 4 类）
SELECT behavior_type, behavior_code, COUNT(*) AS cnt
FROM ydsh_dwd.dwd_user_behavior
GROUP BY behavior_type, behavior_code
ORDER BY cnt DESC;
