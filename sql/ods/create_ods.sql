-- ================================================================
-- 01_ODS 贴源层建表脚本
-- 项目：云购生活 · 企业级数据仓库与经营分析可视化系统
-- 周次：第 1 周 · 子任务 4（数据导入 ODS 层）
-- 作者：<你的姓名>
-- 说明：
--   1) ODS 层原则是「贴源」——字段类型与 CSV 保持一致，不做任何清洗；
--   2) 脏数据（负年龄、负金额、枚举混写）原样保留，留给 DWD 层处理，
--      这样"清洗前 vs 清洗后"才能对比得出修复率；
--   3) 日期/时间字段一律用 STRING 承接，因为源数据存在
--      2024-07-26 / 2024/07/26 / 20240726 三种格式混写，
--      直接转 DATE 会导致整行变 NULL（这是最常见的翻车点）。
-- 执行：docker exec -i hive-server beeline -u 'jdbc:hive2://localhost:10000/default' -f /sql/ods/create_ods.sql
-- ================================================================

CREATE DATABASE IF NOT EXISTS ydsh_ods COMMENT '云购生活-ODS贴源层：与源系统结构一致，不做清洗';
CREATE DATABASE IF NOT EXISTS ydsh_dwd COMMENT '云购生活-DWD明细层：清洗后的明细数据';

USE ydsh_ods;

-- ----------------------------------------------------------------
-- 1. 用户表 ods_user  (预期 1000 行)
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ydsh_ods.ods_user (
  user_id       STRING  COMMENT '用户ID，主键',
  user_name     STRING  COMMENT '用户姓名',
  gender        STRING  COMMENT '性别（原始值：男/女/M/F/male/female/未知 混写）',
  age           INT     COMMENT '年龄（存在负数、0、200 等异常值）',
  city          STRING  COMMENT '城市',
  province      STRING  COMMENT '省份',
  register_date STRING  COMMENT '注册日期（原始格式，混有 yyyy-MM-dd / yyyy-MM-dd / yyyyMMdd）',
  member_level  STRING  COMMENT '会员等级（原始值：金卡会员/金卡/gold 混写）',
  phone         STRING  COMMENT '手机号（原始格式，存在 179-4507-0500 带分隔符）',
  email         STRING  COMMENT '邮箱（缺失率高，约 19%）'
)
COMMENT '用户维度表-贴源'
ROW FORMAT DELIMITED
  FIELDS TERMINATED BY ','
STORED AS TEXTFILE
TBLPROPERTIES (
  'skip.header.line.count' = '1',   -- 跳过 CSV 首行表头，否则会被当成一条数据
  'serialization.encoding'  = 'UTF-8'
);

-- ----------------------------------------------------------------
-- 2. 商品表 ods_product  (预期 600 行)
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ydsh_ods.ods_product (
  product_id    STRING        COMMENT '商品ID，主键',
  product_name  STRING        COMMENT '商品名称',
  category      STRING        COMMENT '一级品类',
  sub_category  STRING        COMMENT '二级品类',
  brand         STRING        COMMENT '品牌',
  price         DECIMAL(12,2) COMMENT '售价（存在负值与超大值）',
  cost          DECIMAL(12,2) COMMENT '成本价',
  stock         INT           COMMENT '库存',
  status        STRING        COMMENT '商品状态：在售/下架/缺货',
  launch_date   STRING        COMMENT '上架日期（存在缺失）'
)
COMMENT '商品维度表-贴源'
ROW FORMAT DELIMITED
  FIELDS TERMINATED BY ','
STORED AS TEXTFILE
TBLPROPERTIES ('skip.header.line.count' = '1');

-- ----------------------------------------------------------------
-- 3. 门店表 ods_store  (预期 60 行)
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ydsh_ods.ods_store (
  store_id    STRING COMMENT '门店ID，主键',
  store_name  STRING COMMENT '门店名称',
  province    STRING COMMENT '省份',
  city        STRING COMMENT '城市',
  district    STRING COMMENT '区县',
  address     STRING COMMENT '详细地址',
  open_date   STRING COMMENT '开业日期',
  area        INT    COMMENT '门店面积（㎡）',
  manager     STRING COMMENT '店长'
)
COMMENT '门店维度表-贴源'
ROW FORMAT DELIMITED
  FIELDS TERMINATED BY ','
STORED AS TEXTFILE
TBLPROPERTIES ('skip.header.line.count' = '1');

-- ----------------------------------------------------------------
-- 4. 订单主表 ods_order  (预期 15600 行，其中约 600 行为重复导入)
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ydsh_ods.ods_order (
  order_id        STRING        COMMENT '订单ID，业务主键（存在重复）',
  user_id         STRING        COMMENT '用户ID',
  store_id        STRING        COMMENT '下单门店ID',
  order_date      STRING        COMMENT '下单日期（混有 - 与 / 分隔）',
  order_time      STRING        COMMENT '下单时间 HH:mm:ss',
  pay_amount      DECIMAL(14,2) COMMENT '实付金额（存在负值与超大值）',
  discount_amount DECIMAL(12,2) COMMENT '优惠金额（存在负值）',
  pay_type        STRING        COMMENT '支付方式（支付宝/微信支付/微信/wechat 混写）',
  order_status    STRING        COMMENT '订单状态（已完成/完成/4/completed 混写，共 12 种写法）',
  channel         STRING        COMMENT '下单渠道（APP/app/PC官网/pc 混写）'
)
COMMENT '订单事实表-贴源'
ROW FORMAT DELIMITED
  FIELDS TERMINATED BY ','
STORED AS TEXTFILE
TBLPROPERTIES ('skip.header.line.count' = '1');

-- ----------------------------------------------------------------
-- 5. 订单明细表 ods_order_item  (预期 37549 行)
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ydsh_ods.ods_order_item (
  item_id    STRING       COMMENT '明细ID，主键',
  order_id   STRING       COMMENT '订单ID',
  product_id STRING       COMMENT '商品ID',
  quantity   INT          COMMENT '购买数量（存在 0 与负数）',
  unit_price DECIMAL(12,2) COMMENT '成交单价（存在负值）',
  discount   DECIMAL(6,4) COMMENT '折扣率 0~0.3'
)
COMMENT '订单明细事实表-贴源'
ROW FORMAT DELIMITED
  FIELDS TERMINATED BY ','
STORED AS TEXTFILE
TBLPROPERTIES ('skip.header.line.count' = '1');

-- ----------------------------------------------------------------
-- 6. 用户行为日志表 ods_user_behavior  (预期 60000 行)
-- ----------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ydsh_ods.ods_user_behavior (
  behavior_id   STRING COMMENT '行为ID，主键',
  user_id       STRING COMMENT '用户ID',
  product_id    STRING COMMENT '商品ID',
  behavior_type STRING COMMENT '行为类型（pv/buy/cart/fav 与 浏览/加购/收藏/购买 混写）',
  behavior_time STRING COMMENT '行为时间（约 10% 为 2024/11/14 00:13 缺秒格式）',
  session_id    STRING COMMENT '会话ID'
)
COMMENT '用户行为日志表-贴源'
ROW FORMAT DELIMITED
  FIELDS TERMINATED BY ','
STORED AS TEXTFILE
TBLPROPERTIES ('skip.header.line.count' = '1');
