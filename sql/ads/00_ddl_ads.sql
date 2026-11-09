-- ============================================================
-- 第 3 周 · 子任务 1：ADS 应用层建表
-- 数据库：ydsh_ads
-- 定位：面向具体业务场景的"成品表"，可直接被报表 / 看板 / NL2SQL 消费
-- 设计原则：ADS 不再做复杂聚合，只做排名、占比、分层这类"业务可直接读"的加工
-- ============================================================

CREATE DATABASE IF NOT EXISTS ydsh_ads COMMENT '应用层：面向业务场景的输出表';

-- ------------------------------------------------------------
-- 1. 核心 KPI 汇总（竖表，一行一个指标，便于看板与 NL2SQL 检索）
-- ------------------------------------------------------------
DROP TABLE IF EXISTS ydsh_ads.ads_kpi_summary;
CREATE TABLE ydsh_ads.ads_kpi_summary (
  metric_key    STRING         COMMENT '指标英文键',
  metric_name   STRING         COMMENT '指标中文名',
  metric_value  DECIMAL(18,4)  COMMENT '指标值',
  unit          STRING         COMMENT '单位（元/单/人/%）',
  definition    STRING         COMMENT '口径说明',
  stat_period   STRING         COMMENT '统计周期 2024 全年'
)
COMMENT 'ADS 核心 KPI 汇总表'
STORED AS ORC;

-- ------------------------------------------------------------
-- 2. 销售日报（含环比、7 日移动平均、上周同日对比）
-- ------------------------------------------------------------
DROP TABLE IF EXISTS ydsh_ads.ads_sales_daily_report;
CREATE TABLE ydsh_ads.ads_sales_daily_report (
  stat_date        STRING         COMMENT '日期',
  weekday_name     STRING         COMMENT '星期',
  is_weekend       INT            COMMENT '是否周末',
  gmv              DECIMAL(16,2)  COMMENT 'GMV',
  paid_order_cnt   INT            COMMENT '有效订单量',
  aov              DECIMAL(14,2)  COMMENT '客单价',
  gmv_dod          DECIMAL(10,4)  COMMENT 'GMV 日环比',
  gmv_ma7          DECIMAL(16,2)  COMMENT 'GMV 7 日移动平均',
  gmv_last_week_same_day DECIMAL(16,2) COMMENT '上周同星期 GMV',
  gmv_wow_same_day DECIMAL(10,4)  COMMENT '周同比（vs 上周同星期）'
)
COMMENT 'ADS 销售日报'
STORED AS ORC;

-- ------------------------------------------------------------
-- 3. 品类 TopN（含销售占比）
-- ------------------------------------------------------------
DROP TABLE IF EXISTS ydsh_ads.ads_category_top10;
CREATE TABLE ydsh_ads.ads_category_top10 (
  rank_no       INT            COMMENT '排名',
  category      STRING         COMMENT '品类',
  gmv           DECIMAL(16,2)  COMMENT '分摊后 GMV',
  gmv_share     DECIMAL(10,4)  COMMENT 'GMV 占比',
  order_cnt     INT            COMMENT '订单数',
  item_qty      INT            COMMENT '销售件数',
  avg_unit_price DECIMAL(14,2) COMMENT '件单价'
)
COMMENT 'ADS 品类销售 TopN'
STORED AS ORC;

-- ------------------------------------------------------------
-- 4. 用户价值分层（RFM 八分层）
-- ------------------------------------------------------------
DROP TABLE IF EXISTS ydsh_ads.ads_user_value_segmentation;
CREATE TABLE ydsh_ads.ads_user_value_segmentation (
  segment        STRING         COMMENT '客户分层名称',
  user_cnt       INT            COMMENT '用户数',
  user_share     DECIMAL(10,4)  COMMENT '用户占比',
  gmv            DECIMAL(16,2)  COMMENT 'GMV 合计',
  gmv_share      DECIMAL(10,4)  COMMENT 'GMV 占比',
  avg_gmv        DECIMAL(14,2)  COMMENT '人均消费',
  avg_freq       DECIMAL(10,2)  COMMENT '人均订单数',
  avg_recency    DECIMAL(10,1)  COMMENT '平均最近购买间隔（天）'
)
COMMENT 'ADS 用户 RFM 价值分层'
STORED AS ORC;

-- ------------------------------------------------------------
-- 5. 渠道贡献
-- ------------------------------------------------------------
DROP TABLE IF EXISTS ydsh_ads.ads_channel_contribution;
CREATE TABLE ydsh_ads.ads_channel_contribution (
  channel        STRING         COMMENT '渠道',
  channel_code   STRING         COMMENT '渠道编码',
  gmv            DECIMAL(16,2)  COMMENT 'GMV',
  gmv_share      DECIMAL(10,4)  COMMENT 'GMV 占比',
  order_cnt      INT            COMMENT '订单数',
  user_cnt       INT            COMMENT '用户数',
  aov            DECIMAL(14,2)  COMMENT '客单价'
)
COMMENT 'ADS 渠道贡献表'
STORED AS ORC;

-- ------------------------------------------------------------
-- 6. 省份 TopN
-- ------------------------------------------------------------
DROP TABLE IF EXISTS ydsh_ads.ads_province_top10;
CREATE TABLE ydsh_ads.ads_province_top10 (
  rank_no        INT            COMMENT '排名',
  province       STRING         COMMENT '省份',
  gmv            DECIMAL(16,2)  COMMENT 'GMV',
  gmv_share      DECIMAL(10,4)  COMMENT 'GMV 占比',
  order_cnt      INT            COMMENT '订单数',
  user_cnt       INT            COMMENT '用户数',
  aov            DECIMAL(14,2)  COMMENT '客单价'
)
COMMENT 'ADS 省份销售 TopN'
STORED AS ORC;

-- ------------------------------------------------------------
-- 7. 门店 TopN
-- ------------------------------------------------------------
DROP TABLE IF EXISTS ydsh_ads.ads_store_top10;
CREATE TABLE ydsh_ads.ads_store_top10 (
  rank_no        INT            COMMENT '排名',
  store_id       STRING         COMMENT '门店 ID',
  store_name     STRING         COMMENT '门店名称',
  city           STRING         COMMENT '城市',
  store_level    STRING         COMMENT '门店分级',
  gmv            DECIMAL(16,2)  COMMENT 'GMV',
  gmv_share      DECIMAL(10,4)  COMMENT 'GMV 占比',
  order_cnt      INT            COMMENT '订单数',
  aov            DECIMAL(14,2)  COMMENT '客单价'
)
COMMENT 'ADS 门店销售 TopN'
STORED AS ORC;
