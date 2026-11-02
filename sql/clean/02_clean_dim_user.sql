-- ================================================================
-- 子任务 1+2+3：用户维度清洗（枚举标准化 / 年龄异常 / 日期手机号格式 / region 合并）
-- 输入：ydsh_ods.ods_user        输出：ydsh_dwd.dim_user
-- 周次：第 2 周
-- 作者：梁常金
--
-- 表结构由 sql/dwd/00_ddl_dwd.sql 统一定义，本脚本只做 INSERT OVERWRITE 装载。
-- 为什么不在这里用 CREATE TABLE AS SELECT？
--   1) CTAS 会丢掉字段注释，DWD 层是给别人用的，注释就是文档；
--   2) 显式 DDL + INSERT 相当于 schema-on-write 校验，类型对不上会当场报错，
--      比 CTAS"悄悄推断成 string"安全得多。
--
-- 清洗规则（与 docs/数据质量清洗报告.md 第 3 节一一对应）：
--   1) gender        : 8 种写法 -> 男/女/未知（case when 覆盖全部大写变体）
--   2) member_level  : 12 种写法 -> 普通/银卡/金卡/钻石会员 + 未知；并挂 level_code
--   3) age           : 合理域 [1,120]，域外(NULL/负数/0/200) 用有效样本均值填充，
--                      同时打 age_is_filled 标记 —— 填充必须可追溯，不能悄悄改
--   4) register_date : yyyy-MM-dd / yyyy/MM/dd / yyyyMMdd 三分支统一
--   5) phone         : 去掉分隔符 '-'，统一 11 位纯数字
--   6) region        : province + city 合并（子任务 3 字段合并）
--   7) email         : 缺失率 19.3%，保留 NULL 并打标记，不编造假邮箱
--
-- 为什么年龄用均值填充而不是删行：
--   用户维度删一行，会让该用户全部订单变成"孤儿事实"（事实表外键悬空），
--   代价远大于"年龄略有偏差"。这是维度表与事实表处理缺失的关键差异。
-- ================================================================

USE ydsh_dwd;

INSERT OVERWRITE TABLE ydsh_dwd.dim_user
SELECT
  t.user_id,
  trim(t.user_name)                                                AS user_name,

  -- 1) 性别枚举标准化
  CASE
    WHEN trim(t.gender) IN ('男','M','male','Male','MALE')          THEN '男'
    WHEN trim(t.gender) IN ('女','F','female','Female','FEMALE')    THEN '女'
    ELSE '未知'                                                      -- 含空串、'未知'及未枚举到的值
  END                                                              AS gender,

  -- 3) 年龄：域内保留原值，域外填均值（均值只统计有效样本，否则脏值会拉偏均值）
  CAST(IF(t.age BETWEEN 1 AND 120, t.age, ROUND(t.avg_age)) AS INT) AS age,
  IF(t.age BETWEEN 1 AND 120, 0, 1)                                AS age_is_filled,

  trim(t.province)                                                 AS province,
  trim(t.city)                                                     AS city,
  -- 6) 字段合并：province + city -> region
  concat(trim(t.province), '-', trim(t.city))                      AS region,

  -- 4) 注册日期格式统一
  CASE
    WHEN trim(t.register_date) RLIKE '^[0-9]{8}$'
      THEN concat(substr(trim(t.register_date),1,4), '-',
                  substr(trim(t.register_date),5,2), '-',
                  substr(trim(t.register_date),7,2))
    ELSE regexp_replace(trim(t.register_date), '/', '-')
  END                                                              AS register_date,

  -- 2) 会员等级枚举标准化
  t.member_level_std                                               AS member_level,
  lv.level_code                                                    AS level_code,

  -- 5) 手机号：去分隔符
  regexp_replace(trim(t.phone), '-', '')                            AS phone,

  t.email,
  IF(t.email IS NULL OR trim(t.email) = '', 1, 0)                   AS email_is_missing
FROM (
  SELECT
    u.user_id, u.user_name, u.gender, u.age, u.province, u.city,
    u.register_date, u.member_level, u.phone, u.email,
    AVG(IF(u.age BETWEEN 1 AND 120, u.age, NULL)) OVER ()           AS avg_age,
    CASE
      WHEN trim(u.member_level) IN ('金卡会员','金卡','gold','Gold','GOLD')    THEN '金卡会员'
      WHEN trim(u.member_level) IN ('银卡会员','银卡','silver','Silver')       THEN '银卡会员'
      WHEN trim(u.member_level) IN ('钻石会员','钻石','diamond','Diamond')     THEN '钻石会员'
      WHEN trim(u.member_level) IN ('普通会员','普通','normal','Normal')       THEN '普通会员'
      ELSE '未知'
    END                                                             AS member_level_std
  FROM ydsh_ods.ods_user u
) t
LEFT JOIN ydsh_dwd.dim_code_member_level lv
       ON t.member_level_std = lv.level_name;

-- 装载后自检（应全部为 0 / 1000）
SELECT COUNT(*)                                              AS total_rows,
       SUM(age_is_filled)                                    AS age_filled_rows,
       SUM(IF(age IS NULL OR age < 1 OR age > 120, 1, 0))     AS age_bad,
       SUM(IF(gender NOT IN ('男','女','未知'), 1, 0))         AS gender_bad,
       SUM(IF(member_level NOT IN ('普通会员','银卡会员','金卡会员','钻石会员','未知'), 1, 0)) AS level_bad,
       SUM(IF(register_date NOT RLIKE '^[0-9]{4}-[0-9]{2}-[0-9]{2}$', 1, 0)) AS date_bad,
       SUM(IF(phone NOT RLIKE '^[0-9]{11}$', 1, 0))           AS phone_bad,
       SUM(email_is_missing)                                  AS email_missing
FROM ydsh_dwd.dim_user;
