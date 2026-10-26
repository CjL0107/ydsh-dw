-- ================================================================
-- 04_数据清洗（一）：用户表缺失值处理
-- 周次：第 1 周 · 子任务 6
-- 输入：ydsh_ods.ods_user
-- 输出：ydsh_dwd.dwd_user（PARQUET 列式存储）
--
-- 清洗策略与业务依据（这部分要写进报告，是评分点）：
--   1) age：以 [1,120] 为合理区间。区间外（负数/0/200）视为"脏值"，
--      与真正缺失一起用**全体有效年龄均值**填充。
--      为什么用均值而不是直接删除？用户维度表删行会连带删掉该用户的
--      所有订单关联，代价远大于"年龄略有偏差"。
--   2) gender / member_level：空值填 '未知'，不做同义归并
--      （男/M/male 的归并属于第 2 周"枚举标准化"，本周只处理缺失）。
--   3) email：缺失率 19.3%，无法可靠推断，**保留 NULL 并打标记**，
--      而不是编一个假邮箱 —— 编造数据比留空更危险。
--   4) phone / register_date：本周不动。格式标准化是第 2 周任务，
--      提前改会让"清洗前后对比"失真。
-- ================================================================

USE ydsh_dwd;

DROP TABLE IF EXISTS ydsh_dwd.dwd_user;

CREATE TABLE ydsh_dwd.dwd_user
COMMENT '用户维度表-已处理缺失值'
STORED AS PARQUET
AS
SELECT
  user_id,
  user_name,
  -- 性别空值填充
  IF(nvl(trim(gender), '') = '', '未知', trim(gender))          AS gender,
  -- 年龄：合法区间内保留原值，否则用全体有效年龄均值填充
  CAST(IF(age BETWEEN 1 AND 120, age, avg_age) AS INT)          AS age,
  -- 标记位：1 = 该行年龄被填充过，便于质量报告统计
  IF(age BETWEEN 1 AND 120, 0, 1)                               AS age_is_filled,
  city,
  province,
  register_date,
  IF(nvl(trim(member_level), '') = '', '未知', trim(member_level)) AS member_level,
  phone,
  email,
  IF(nvl(trim(email), '') = '', 1, 0)                           AS email_is_missing
FROM (
  SELECT
    *,
    -- 窗口函数在整表上算均值，避免额外 JOIN
    ROUND(AVG(IF(age BETWEEN 1 AND 120, age, NULL)) OVER ())    AS avg_age
  FROM ydsh_ods.ods_user
) t;

-- ---------------------------------------------------------------
-- 清洗效果自检：以下三项应全部为 0
-- ---------------------------------------------------------------
SELECT
  COUNT(*)                                                        AS total_rows,
  SUM(IF(age IS NULL, 1, 0))                                      AS age_null,
  SUM(IF(age < 1 OR age > 120, 1, 0))                             AS age_out_of_range,
  SUM(IF(nvl(trim(gender), '') = '', 1, 0))                       AS gender_blank,
  SUM(IF(nvl(trim(member_level), '') = '', 1, 0))                 AS member_level_blank,
  SUM(age_is_filled)                                              AS age_filled_rows,
  SUM(email_is_missing)                                           AS email_missing_rows
FROM ydsh_dwd.dwd_user;
