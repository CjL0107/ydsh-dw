-- ============================================================
-- 第 3 周 · 子任务 1：DWS 汇总层建表
-- 数据库：ydsh_dws
-- 设计原则：
--   1) 只做"轻度汇总"，保留分析维度，不做跨主题计算（那是 ADS 的事）
--   2) GMV 口径统一为「有效订单实付金额之和」，有效 = OS02 已支付 / OS03 已完成 / OS05 退款中
--   3) 粒度自下而上：天 → 周 → 月；维度：品类 / 门店 / 渠道 / 用户
--   4) 当前数据量 15,000 单 / 366 天，单表日均 41 行，故不建分区；
--      若按年累积到千万级，再改 stat_month 分区（见文档"设计取舍"一节）
-- ============================================================

CREATE DATABASE IF NOT EXISTS ydsh_dws COMMENT '汇总层：按维度轻度聚合';

-- ------------------------------------------------------------
-- 1. 销售日汇总（全站）
-- ------------------------------------------------------------
DROP TABLE IF EXISTS ydsh_dws.dws_sales_day;
CREATE TABLE ydsh_dws.dws_sales_day (
  stat_date        STRING         COMMENT '统计日期 yyyy-MM-dd',
  order_cnt        INT            COMMENT '下单量（含全部状态）',
  paid_order_cnt   INT            COMMENT '有效订单量（OS02/OS03/OS05）',
  cancel_order_cnt INT            COMMENT '取消订单量（OS04）',
  wait_pay_cnt     INT            COMMENT '待支付订单量（OS01）',
  user_cnt         INT            COMMENT '下单用户数（去重）',
  paid_user_cnt    INT            COMMENT '有效订单用户数（去重）',
  gmv              DECIMAL(16,2)  COMMENT 'GMV=有效订单实付金额之和',
  discount_amount  DECIMAL(16,2)  COMMENT '优惠金额合计（有效订单）',
  item_qty         INT            COMMENT '销售件数（有效订单对应明细）',
  aov              DECIMAL(14,2)  COMMENT '客单价=GMV/有效订单量',
  cancel_rate      DECIMAL(8,4)   COMMENT '取消率=取消订单/下单量'
)
COMMENT 'DWS 销售日汇总（全站粒度）'
STORED AS ORC;

-- ------------------------------------------------------------
-- 2. 品类 × 天
-- 说明：品类 GMV 采用「明细占比分摊订单实付」口径。
--   原因：明细表 amount 合计 3.05 亿，订单实付合计 2,734 万，差 11.19 倍
--   明细金额是标价口径、订单实付是成交口径，直接相加品类合计对不上总 GMV。
-- ------------------------------------------------------------
DROP TABLE IF EXISTS ydsh_dws.dws_sales_category_day;
CREATE TABLE ydsh_dws.dws_sales_category_day (
  stat_date     STRING         COMMENT '统计日期',
  category      STRING         COMMENT '一级品类',
  order_cnt     INT            COMMENT '涉及该品类的有效订单数',
  item_qty      INT            COMMENT '销售件数',
  item_amount   DECIMAL(16,2)  COMMENT '明细标价金额（未分摊）',
  gmv_alloc     DECIMAL(16,2)  COMMENT '按明细占比分摊后的 GMV'
)
COMMENT 'DWS 品类日汇总（GMV 为分摊口径）'
STORED AS ORC;

-- ------------------------------------------------------------
-- 3. 门店 × 天
-- ------------------------------------------------------------
DROP TABLE IF EXISTS ydsh_dws.dws_sales_store_day;
CREATE TABLE ydsh_dws.dws_sales_store_day (
  stat_date      STRING         COMMENT '统计日期',
  store_id       STRING         COMMENT '门店 ID',
  store_name     STRING         COMMENT '门店名称',
  province       STRING         COMMENT '省份',
  city           STRING         COMMENT '城市',
  store_level    STRING         COMMENT '门店分级（旗舰店/标准店/社区店）',
  paid_order_cnt INT            COMMENT '有效订单量',
  user_cnt       INT            COMMENT '有效订单用户数',
  gmv            DECIMAL(16,2)  COMMENT 'GMV',
  aov            DECIMAL(14,2)  COMMENT '客单价'
)
COMMENT 'DWS 门店日汇总'
STORED AS ORC;

-- ------------------------------------------------------------
-- 4. 渠道 × 天
-- ------------------------------------------------------------
DROP TABLE IF EXISTS ydsh_dws.dws_sales_channel_day;
CREATE TABLE ydsh_dws.dws_sales_channel_day (
  stat_date      STRING         COMMENT '统计日期',
  channel_code   STRING         COMMENT '渠道编码 CH01~CH04',
  channel        STRING         COMMENT '渠道名称',
  paid_order_cnt INT            COMMENT '有效订单量',
  user_cnt       INT            COMMENT '有效订单用户数',
  gmv            DECIMAL(16,2)  COMMENT 'GMV',
  aov            DECIMAL(14,2)  COMMENT '客单价'
)
COMMENT 'DWS 渠道日汇总'
STORED AS ORC;

-- ------------------------------------------------------------
-- 5. 销售周汇总
-- ------------------------------------------------------------
DROP TABLE IF EXISTS ydsh_dws.dws_sales_week;
CREATE TABLE ydsh_dws.dws_sales_week (
  year_week      STRING         COMMENT '年周标识 yyyy-Www',
  week_start     STRING         COMMENT '周一日期',
  week_end       STRING         COMMENT '周日日期',
  paid_order_cnt INT            COMMENT '有效订单量',
  paid_user_cnt  INT            COMMENT '有效订单用户数',
  gmv            DECIMAL(16,2)  COMMENT 'GMV',
  aov            DECIMAL(14,2)  COMMENT '客单价'
)
COMMENT 'DWS 销售周汇总'
STORED AS ORC;

-- ------------------------------------------------------------
-- 6. 销售月汇总
-- ------------------------------------------------------------
DROP TABLE IF EXISTS ydsh_dws.dws_sales_month;
CREATE TABLE ydsh_dws.dws_sales_month (
  year_month     STRING         COMMENT '年月 yyyy-MM',
  paid_order_cnt INT            COMMENT '有效订单量',
  paid_user_cnt  INT            COMMENT '有效订单用户数',
  gmv            DECIMAL(16,2)  COMMENT 'GMV',
  aov            DECIMAL(14,2)  COMMENT '客单价',
  mom_rate       DECIMAL(10,4)  COMMENT '环比增长率（本月 vs 上月）'
)
COMMENT 'DWS 销售月汇总（含环比）'
STORED AS ORC;

-- ------------------------------------------------------------
-- 7. 用户级汇总（复购率与 RFM 的输入）
-- ------------------------------------------------------------
DROP TABLE IF EXISTS ydsh_dws.dws_user_order_agg;
CREATE TABLE ydsh_dws.dws_user_order_agg (
  user_id          STRING         COMMENT '用户 ID',
  first_order_date STRING         COMMENT '首单日期',
  last_order_date  STRING         COMMENT '末单日期',
  order_cnt        INT            COMMENT '有效订单数',
  total_gmv        DECIMAL(16,2)  COMMENT '累计 GMV',
  avg_order_gmv    DECIMAL(14,2)  COMMENT '笔单价',
  active_days      INT            COMMENT '有下单行为的天数',
  province         STRING         COMMENT '省份（来自维度表）',
  region           STRING         COMMENT '大区',
  member_level     STRING         COMMENT '会员等级'
)
COMMENT 'DWS 用户订单汇总（RFM 输入）'
STORED AS ORC;
