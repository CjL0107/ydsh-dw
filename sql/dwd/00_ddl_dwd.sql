-- ================================================================
-- 02_1_DWD 层建表脚本（星型模型 + 编码映射维度表 + 分区/分桶）
-- 项目：云购生活 · 企业级数据仓库与经营分析可视化系统
-- 周次：第 2 周 · 子任务 5 / 子任务 6
-- 作者：梁常金
--
-- 分层约定（详见 docs/数仓架构设计文档.md）：
--   ODS  贴源层  ydsh_ods      表前缀 ods_    原样接入，不做任何清洗
--   DWD  明细层  ydsh_dwd      表前缀 dim_ / dwd_   清洗+标准化+维度建模
--   DWS  汇总层  ydsh_dws      表前缀 dws_    第 3 周建设
--   ADS  应用层  ydsh_ads      表前缀 ads_    第 3 周建设
--
-- 建模方法：Kimball 维度建模 · 星型模型
--   事实表 = 业务过程 + 外键(维度主键) + 度量
--   维度表 = 描述性属性（谁、什么、哪里、何时）
--
-- 【本周对第 1 周 DWD 表的处理】
--   第 1 周的 dwd_user / dwd_order / dwd_product 只是"缺失值清洗"的中间结果，
--   表结构没有编码列、没有分区、也没有维度/事实的区分。
--   本周按星型模型重构：dwd_user -> dim_user，dwd_product -> dim_product，
--   dwd_order 重建成按 order_date 分区的 ORC 事实表。
--   旧表先 DROP，避免同名表结构与新模型冲突（ODS 层原样保留，故可随时重建）。
--
-- 执行：
--   docker exec -i hive-server beeline -u 'jdbc:hive2://localhost:10000/default' -f /sql/dwd/00_ddl_dwd.sql
-- ================================================================

CREATE DATABASE IF NOT EXISTS ydsh_dwd
  COMMENT '云购生活-DWD明细层：清洗、标准化、维度建模后的明细数据';
CREATE DATABASE IF NOT EXISTS ydsh_dws
  COMMENT '云购生活-DWS汇总层：按主题轻度汇总（第3周建设）';
CREATE DATABASE IF NOT EXISTS ydsh_ads
  COMMENT '云购生活-ADS应用层：面向指标与报表的结果表（第3周建设）';

USE ydsh_dwd;

-- 第 1 周的中间结果表，本周由星型模型替代
DROP TABLE IF EXISTS ydsh_dwd.dwd_order;
DROP TABLE IF EXISTS ydsh_dwd.dwd_user;
DROP TABLE IF EXISTS ydsh_dwd.dwd_product;

-- ================================================================
-- 第一部分：编码映射维度表（子任务 3 —— 编码到中文名称的转换载体）
--
-- 为什么单独建表而不是把 case when 写死在 ETL 里？
--   1) 映射关系可维护：业务新增一个支付方式，只 INSERT 一行，不用改 ETL；
--   2) 可审计：ETL 里 JOIN 不上（映射缺失）会直接暴露成 NULL，
--      比 case when 的 else '其他' 悄悄吞掉脏值更安全；
--   3) 事实表存中文名，编码留在这几张表里，DWS/ADS 按 code 聚合更稳定。
-- ================================================================

DROP TABLE IF EXISTS ydsh_dwd.dim_code_member_level;
CREATE TABLE ydsh_dwd.dim_code_member_level (
  level_code  STRING COMMENT '等级编码（源系统字典值）',
  level_name  STRING COMMENT '等级标准中文名（DWD 落库值）',
  level_rank  INT    COMMENT '等级序号，1 最低',
  min_points  INT    COMMENT '升级门槛成长值（业务规则假设）',
  remark      STRING COMMENT '备注'
)
COMMENT '会员等级编码映射维度表'
STORED AS ORC;

INSERT INTO ydsh_dwd.dim_code_member_level VALUES
  ('ML01','普通会员',1,0,    '源值：普通 / normal / 空'),
  ('ML02','银卡会员',2,1000, '源值：银卡 / silver'),
  ('ML03','金卡会员',3,5000, '源值：金卡 / gold'),
  ('ML04','钻石会员',4,20000,'源值：钻石');

DROP TABLE IF EXISTS ydsh_dwd.dim_code_pay_type;
CREATE TABLE ydsh_dwd.dim_code_pay_type (
  pay_code   STRING COMMENT '支付方式编码',
  pay_name   STRING COMMENT '支付方式标准中文名',
  is_online  INT    COMMENT '是否线上支付：1 是 / 0 否',
  remark     STRING COMMENT '备注'
)
COMMENT '支付方式编码映射维度表'
STORED AS ORC;

INSERT INTO ydsh_dwd.dim_code_pay_type VALUES
  ('PT01','支付宝',  1,'源值：支付宝 / alipay'),
  ('PT02','微信支付',1,'源值：微信支付 / 微信 / wechat'),
  ('PT03','银行卡',  1,'源值：银行卡'),
  ('PT04','货到付款',0,'源值：货到付款');

DROP TABLE IF EXISTS ydsh_dwd.dim_code_order_status;
CREATE TABLE ydsh_dwd.dim_code_order_status (
  status_code STRING COMMENT '订单状态编码（源系统字典值，含数字码）',
  status_name STRING COMMENT '订单状态标准中文名',
  is_paid     INT    COMMENT '是否已付款：1 是 / 0 否',
  is_final    INT    COMMENT '是否终态：1 是（不再流转）/ 0 否',
  remark      STRING COMMENT '备注'
)
COMMENT '订单状态编码映射维度表'
STORED AS ORC;

-- 数字码语义说明：
--   源数据中 order_status 存在 1/2/3/4 四个数字码，源系统未提供字典表。
--   本表按"订单生命周期顺序"给出映射假设：1 待支付 -> 2 已支付 -> 3 已完成，
--   4 为已取消（与字符串值 cancelled 同义，属于同一状态的两种写法）。
--   该假设已写入《数据质量清洗报告》的"遗留问题"，若业务方给出真实字典，
--   只需改本表 4 行数据、重跑 ETL，不需要改任何 SQL 逻辑。
INSERT INTO ydsh_dwd.dim_code_order_status VALUES
  ('OS01','待支付',0,0,'源值：待支付 / 1'),
  ('OS02','已支付',1,0,'源值：已支付 / 已付款 / 2'),
  ('OS03','已完成',1,1,'源值：已完成 / 完成 / 3'),
  ('OS04','已取消',0,1,'源值：已取消 / cancelled / 4'),
  ('OS05','退款中',1,0,'源值：退款中');

DROP TABLE IF EXISTS ydsh_dwd.dim_code_channel;
CREATE TABLE ydsh_dwd.dim_code_channel (
  channel_code STRING COMMENT '渠道编码',
  channel_name STRING COMMENT '渠道标准中文名',
  is_online    INT    COMMENT '是否线上渠道：1 是 / 0 否',
  remark       STRING COMMENT '备注'
)
COMMENT '下单渠道编码映射维度表'
STORED AS ORC;

INSERT INTO ydsh_dwd.dim_code_channel VALUES
  ('CH01','APP',   1,'源值：APP / app'),
  ('CH02','小程序',1,'源值：小程序'),
  ('CH03','PC官网',1,'源值：PC官网 / pc'),
  ('CH04','门店',  0,'源值：门店');

DROP TABLE IF EXISTS ydsh_dwd.dim_code_behavior;
CREATE TABLE ydsh_dwd.dim_code_behavior (
  behavior_code STRING COMMENT '行为类型编码',
  behavior_name STRING COMMENT '行为类型标准中文名',
  en_alias      STRING COMMENT '源系统英文别名',
  funnel_step   INT    COMMENT '转化漏斗层级：1 浏览 2 收藏/加购 3 购买',
  remark        STRING COMMENT '备注'
)
COMMENT '用户行为类型编码映射维度表'
STORED AS ORC;

INSERT INTO ydsh_dwd.dim_code_behavior VALUES
  ('BT01','浏览','pv',  1,'源值：pv / PV / 浏览'),
  ('BT02','加购','cart',2,'源值：cart / Cart / 加购'),
  ('BT03','收藏','fav', 2,'源值：fav / 收藏'),
  ('BT04','购买','buy', 3,'源值：buy / 购买');

-- 说明：行为类型字段 96% 是英文缩写（pv/buy/cart/fav），统一收敛到中文后，
-- 与 order_status / pay_type 等字段的取值风格一致，BI 直接取数即可展示。

-- ================================================================
-- 第二部分：时间维度 dim_date
--   粒度为"天"，覆盖订单/行为数据实际涉及的 2024 全年。
--   事实表只存 order_date 字符串，需要"按月/按季/是否周末"聚合时
--   一律 JOIN dim_date，避免在事实表里堆一堆派生列。
-- ================================================================
DROP TABLE IF EXISTS ydsh_dwd.dim_date;
CREATE TABLE ydsh_dwd.dim_date (
  date_key     INT    COMMENT '日期代理键 yyyyMMdd',
  full_date    DATE   COMMENT '完整日期',
  year         INT    COMMENT '年',
  quarter      INT    COMMENT '季度 1-4',
  month        INT    COMMENT '月 1-12',
  year_month   STRING COMMENT '年月 yyyy-MM',
  day_of_month INT    COMMENT '日',
  week_of_year INT    COMMENT '年内第几周',
  day_of_week  INT    COMMENT '星期几，1=周一 ... 7=周日',
  weekday_name STRING COMMENT '星期中文名',
  is_weekend   INT    COMMENT '是否周末：1 是 / 0 否',
  is_promo_day INT    COMMENT '是否大促日：1 是 / 0 否（618/双11/双12/元旦/五一/国庆）'
)
COMMENT '时间维度表（2024-01-01 ~ 2024-12-31）'
STORED AS ORC;

-- 生成 2024 全年日期序列：
--   本环境 Hive 4.0.0 未注册 sequence() UDTF（实测报 Invalid function SEQUENCE），
--   因此改用"两位数表交叉连接"生成 0~365 的序号，再 date_add 出日期。
--   这样不依赖任何版本特性，任何 Hive 2.x+ 都能跑。
INSERT OVERWRITE TABLE ydsh_dwd.dim_date
SELECT
  CAST(date_format(d, 'yyyyMMdd') AS INT)                        AS date_key,
  d                                                              AS full_date,
  YEAR(d)                                                        AS year,
  QUARTER(d)                                                     AS quarter,
  MONTH(d)                                                       AS month,
  date_format(d, 'yyyy-MM')                                      AS year_month,
  DAY(d)                                                         AS day_of_month,
  WEEKOFYEAR(d)                                                  AS week_of_year,
  -- 星期几：dayofweek() 是 1=周日 ... 7=周六，这里换算成 1=周一 ... 7=周日
  -- 注意：不能用 date_format(d,'u') —— 本环境 Hive 的 date_format 把 'u' 解释为"年"，
  --       实测返回 2024 而不是星期几，这是一个必须记下来的坑。
  pmod(dayofweek(d) + 5, 7) + 1                                  AS day_of_week,
  elt(pmod(dayofweek(d) + 5, 7) + 1,
      '周一','周二','周三','周四','周五','周六','周日')            AS weekday_name,
  IF(dayofweek(d) IN (1, 7), 1, 0)                               AS is_weekend,
  IF(date_format(d, 'MMdd') IN ('0601','0618','1111','1212','0101','0501','1001'), 1, 0) AS is_promo_day
FROM (
  SELECT date_add(TO_DATE('2024-01-01'), n) AS d
  FROM (
    SELECT (h * 100 + t * 10 + o) AS n
    FROM (SELECT 0 h UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3) hh
    CROSS JOIN (SELECT 0 t UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3
                UNION ALL SELECT 4 UNION ALL SELECT 5 UNION ALL SELECT 6
                UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL SELECT 9) tt
    CROSS JOIN (SELECT 0 o UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3
                UNION ALL SELECT 4 UNION ALL SELECT 5 UNION ALL SELECT 6
                UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL SELECT 9) oo
  ) nums
  WHERE n <= 365   -- 2024 为闰年，1/1 起偏移 0~365 共 366 天
) t;

-- ================================================================
-- 第三部分：维度表（SCD1 全量覆盖，本周不做历史拉链）
-- ================================================================

-- 3.1 用户维度
DROP TABLE IF EXISTS ydsh_dwd.dim_user;
CREATE TABLE ydsh_dwd.dim_user (
  user_id            STRING COMMENT '用户ID，维度主键',
  user_name          STRING COMMENT '用户姓名',
  gender             STRING COMMENT '性别（标准化：男/女/未知）',
  age                INT    COMMENT '年龄（1-120，异常值已按均值填充）',
  age_is_filled      INT    COMMENT '年龄是否被填充：1 是 / 0 否',
  province           STRING COMMENT '省份',
  city               STRING COMMENT '城市',
  region             STRING COMMENT '大区/地区（province-city 合并派生）',
  register_date      STRING COMMENT '注册日期（统一 yyyy-MM-dd）',
  member_level       STRING COMMENT '会员等级（标准化中文名）',
  level_code         STRING COMMENT '会员等级编码，关联 dim_code_member_level',
  phone              STRING COMMENT '手机号（11 位纯数字）',
  email              STRING COMMENT '邮箱（缺失保留 NULL，不编造）',
  email_is_missing   INT    COMMENT '邮箱是否缺失：1 是 / 0 否'
)
COMMENT '用户维度表（清洗+标准化后，SCD1）'
STORED AS ORC;

-- 3.2 商品维度
DROP TABLE IF EXISTS ydsh_dwd.dim_product;
CREATE TABLE ydsh_dwd.dim_product (
  product_id            STRING        COMMENT '商品ID，维度主键',
  product_name          STRING        COMMENT '商品名称',
  category              STRING        COMMENT '一级品类（已 trim）',
  sub_category          STRING        COMMENT '二级品类（已 trim）',
  brand                 STRING        COMMENT '品牌（已 trim + 大小写统一）',
  price                 DECIMAL(12,2) COMMENT '售价（异常值已修正）',
  cost                  DECIMAL(12,2) COMMENT '成本价',
  gross_margin_rate     DECIMAL(6,4)  COMMENT '毛利率 =(price-cost)/price',
  stock                 INT           COMMENT '库存',
  status                STRING        COMMENT '商品状态：在售/下架/缺货',
  launch_date           STRING        COMMENT '上架日期（缺失保留 NULL）',
  launch_date_is_missing INT          COMMENT '上架日期是否缺失：1 是 / 0 否',
  price_flag            STRING        COMMENT '价格修正标记：NORMAL/SIGN_FIXED/OUTLIER_REPAIRED'
)
COMMENT '商品维度表（清洗+标准化后，SCD1）'
STORED AS ORC;

-- 3.3 门店维度
DROP TABLE IF EXISTS ydsh_dwd.dim_store;
CREATE TABLE ydsh_dwd.dim_store (
  store_id    STRING COMMENT '门店ID，维度主键',
  store_name  STRING COMMENT '门店名称',
  province    STRING COMMENT '省份',
  city        STRING COMMENT '城市',
  region      STRING COMMENT '地区（province-city 合并派生）',
  district    STRING COMMENT '区县',
  address     STRING COMMENT '详细地址',
  open_date   STRING COMMENT '开业日期（统一 yyyy-MM-dd）',
  area        INT    COMMENT '营业面积（㎡）',
  store_level STRING COMMENT '门店分级：旗舰店/标准店/社区店（按面积派生）',
  manager     STRING COMMENT '店长'
)
COMMENT '门店维度表（清洗+派生分级）'
STORED AS ORC;

-- ================================================================
-- 第四部分：事实表（分区表）
--   订单事实表按 order_date（下单日期）分区 —— 这是最常用的查询维度
--   （"看某天/某月的 GMV"），分区裁剪后只扫对应目录，避免全表 1.5 万行扫描。
--   为了让分区加载可一次完成，使用动态分区（见 sql/clean/05_clean_order_fact.sql）。
-- ================================================================

-- 4.1 订单事实表（粒度：一行一个订单）
DROP TABLE IF EXISTS ydsh_dwd.dwd_order;
CREATE TABLE ydsh_dwd.dwd_order (
  order_id             STRING        COMMENT '订单ID，业务主键（已去重）',
  user_id              STRING        COMMENT '用户ID，外键 -> dim_user',
  store_id             STRING        COMMENT '门店ID，外键 -> dim_store',
  order_time           STRING        COMMENT '下单时间 HH:mm:ss',
  order_timestamp      STRING        COMMENT '下单时间戳 yyyy-MM-dd HH:mm:ss（date+time 合并）',
  order_hour           INT           COMMENT '下单小时，用于时段分析',
  pay_amount           DECIMAL(14,2) COMMENT '实付金额（异常值已修正）',
  pay_amount_raw       DECIMAL(14,2) COMMENT '实付金额原始值（可追溯）',
  discount_amount      DECIMAL(12,2) COMMENT '优惠金额（负值已置 0）',
  pay_type             STRING        COMMENT '支付方式（标准中文名）',
  pay_code             STRING        COMMENT '支付方式编码 -> dim_code_pay_type',
  order_status         STRING        COMMENT '订单状态（标准中文名）',
  status_code          STRING        COMMENT '订单状态编码 -> dim_code_order_status',
  channel              STRING        COMMENT '下单渠道（标准中文名）',
  channel_code         STRING        COMMENT '下单渠道编码 -> dim_code_channel',
  amount_flag          STRING        COMMENT '金额修正标记：NORMAL/SIGN_FIXED/OUTLIER_REPAIRED'
)
COMMENT '订单事实表（星型模型中心表，按 order_date 分区）'
PARTITIONED BY (order_date STRING COMMENT '下单日期 yyyy-MM-dd，分区字段')
STORED AS ORC;

-- 4.2 订单明细事实表（粒度：一行一个订单商品明细）
DROP TABLE IF EXISTS ydsh_dwd.dwd_order_item;
CREATE TABLE ydsh_dwd.dwd_order_item (
  item_id       STRING        COMMENT '明细ID，业务主键',
  order_id      STRING        COMMENT '订单ID，外键 -> dwd_order',
  product_id    STRING        COMMENT '商品ID，外键 -> dim_product',
  quantity      INT           COMMENT '购买数量（异常值已修正）',
  quantity_raw  INT           COMMENT '购买数量原始值',
  unit_price    DECIMAL(12,2) COMMENT '成交单价（负值已取绝对值）',
  discount      DECIMAL(6,4)  COMMENT '折扣率 0~1',
  amount        DECIMAL(14,2) COMMENT '明细金额 = 数量*单价*(1-折扣率)',
  qty_flag      STRING        COMMENT '数量修正标记：NORMAL/SIGN_FIXED/OUTLIER_REPAIRED'
)
COMMENT '订单明细事实表（按 order_date 分区，与订单事实表分区对齐）'
PARTITIONED BY (order_date STRING COMMENT '所属订单的下单日期，分区字段')
STORED AS ORC;

-- 4.3 用户行为事实表（粒度：一行一次行为）
DROP TABLE IF EXISTS ydsh_dwd.dwd_user_behavior;
CREATE TABLE ydsh_dwd.dwd_user_behavior (
  behavior_id   STRING COMMENT '行为ID，业务主键',
  user_id       STRING COMMENT '用户ID，外键 -> dim_user',
  product_id    STRING COMMENT '商品ID，外键 -> dim_product',
  behavior_type STRING COMMENT '行为类型（标准中文名）',
  behavior_code STRING COMMENT '行为类型编码 -> dim_code_behavior',
  behavior_time STRING COMMENT '行为时间 yyyy-MM-dd HH:mm:ss（已补秒）',
  session_id    STRING COMMENT '会话ID',
  time_is_fixed INT    COMMENT '时间是否被补全：1 是 / 0 否'
)
COMMENT '用户行为事实表（按 behavior_date 分区）'
PARTITIONED BY (behavior_date STRING COMMENT '行为日期 yyyy-MM-dd，分区字段')
STORED AS ORC;

-- ================================================================
-- 第五部分：分桶表演示（子任务 6 "尝试分桶表的使用"）
--   dwd_order 已经按天分区，同一天内还有约 40 行；数据量小的时候分桶
--   收益不明显，但企业真实量级下（每天百万级）按 user_id 分桶可以让
--   "订单 JOIN 用户"走 Map-Side Join，避免 Reduce 端 Shuffle。
--   这里用 8 个桶做技术验证，桶数取 2 的幂、且远小于 (行数/块大小)。
-- ================================================================
DROP TABLE IF EXISTS ydsh_dwd.dwd_order_bucketed;
CREATE TABLE ydsh_dwd.dwd_order_bucketed (
  order_id        STRING        COMMENT '订单ID',
  user_id         STRING        COMMENT '用户ID（分桶键）',
  store_id        STRING        COMMENT '门店ID',
  order_date      STRING        COMMENT '下单日期',
  order_timestamp STRING        COMMENT '下单时间戳',
  pay_amount      DECIMAL(14,2) COMMENT '实付金额',
  order_status    STRING        COMMENT '订单状态'
)
COMMENT '订单事实表-分桶版（按 user_id 哈希分 8 桶，用于 Map-Side Join 验证）'
CLUSTERED BY (user_id) INTO 8 BUCKETS
STORED AS ORC;
