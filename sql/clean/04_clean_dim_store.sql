-- ================================================================
-- 子任务 3：门店维度清洗（trim / region 合并 / 日期统一 / 门店分级派生）
-- 输入：ydsh_ods.ods_store        输出：ydsh_dwd.dim_store
-- 周次：第 2 周
-- 作者：梁常金
--
-- 门店表本身脏数据很少（60 行，province 15 种、city 36 种、open_date 全部规范），
-- 本周对它主要做三件事：
--   1) 全字段 trim，防止"广东省 "与"广东省"被当成两个省（维度表最怕这种隐性分裂）；
--   2) province + city 合并成 region，供"大区分析"直接 group by；
--   3) 按营业面积派生 store_level —— 这是维度表里典型的"派生维度"，
--      把"面积"这种连续值转成业务能直接用的分类，避免每次分析都写 case when。
--      分级阈值参考门店实际面积分布（121~783 ㎡，均值 445）。
-- ================================================================

USE ydsh_dwd;

INSERT OVERWRITE TABLE ydsh_dwd.dim_store
SELECT
  trim(store_id)                                     AS store_id,
  trim(store_name)                                   AS store_name,
  trim(province)                                     AS province,
  trim(city)                                         AS city,
  concat(trim(province), '-', trim(city))             AS region,
  trim(district)                                     AS district,
  trim(address)                                      AS address,
  CASE
    WHEN trim(open_date) RLIKE '^[0-9]{8}$'
      THEN concat(substr(trim(open_date),1,4), '-',
                  substr(trim(open_date),5,2), '-',
                  substr(trim(open_date),7,2))
    ELSE regexp_replace(trim(open_date), '/', '-')
  END                                                AS open_date,
  area,
  CASE
    WHEN area >= 600 THEN '旗舰店'
    WHEN area >= 300 THEN '标准店'
    ELSE '社区店'
  END                                                AS store_level,
  trim(manager)                                      AS manager
FROM ydsh_ods.ods_store;

-- 自检：门店数、分级分布、日期格式
SELECT store_level, COUNT(*) AS store_cnt, ROUND(AVG(area)) AS avg_area
FROM ydsh_dwd.dim_store
GROUP BY store_level
ORDER BY avg_area DESC;

SELECT COUNT(*) AS total_rows,
       COUNT(DISTINCT province) AS prov_cnt,
       COUNT(DISTINCT city)     AS city_cnt,
       SUM(IF(open_date NOT RLIKE '^[0-9]{4}-[0-9]{2}-[0-9]{2}$', 1, 0)) AS date_bad
FROM ydsh_dwd.dim_store;
