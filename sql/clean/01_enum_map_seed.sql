-- ================================================================
-- 子任务 1（清洗二）：枚举字段标准化 —— 规范字典 + 清洗前值域扫描
-- 项目：云购生活 · 企业级数据仓库与经营分析可视化系统
-- 周次：第 2 周 · 子任务 1
-- 作者：梁常金
--
-- 本脚本做两件事：
--   1) 建立「枚举规范字典」dim_enum_map：把每个字段的每一种脏写法映射到唯一标准值。
--      这张表既是 case when 分支的"单一事实来源"，也是答辩时证明
--      "我说覆盖了全部取值"的证据（取值集合是扫出来的，不是拍脑袋想的）。
--   2) 建立 dq_enum_scan 值域扫描表，把清洗前（ODS）的取值分布落库；
--      清洗完成后再扫一遍 DWD 写 phase='after'，两批数据直接对比，
--      就能量化"值域从 N 种收敛到 M 种"。
--
-- 执行：
--   docker exec -i hive-server beeline -u 'jdbc:hive2://localhost:10000/default' -f /sql/clean/01_enum_map_seed.sql
-- ================================================================

USE ydsh_dwd;

-- ----------------------------------------------------------------
-- 1. 枚举规范字典
-- ----------------------------------------------------------------
DROP TABLE IF EXISTS ydsh_dwd.dim_enum_map;
CREATE TABLE ydsh_dwd.dim_enum_map (
  domain     STRING COMMENT '字段域：gender/member_level/order_status/pay_type/channel/behavior_type',
  raw_value  STRING COMMENT '源系统原始写法（区分大小写、含空串）',
  std_value  STRING COMMENT '标准化后的唯一中文值'
)
COMMENT '枚举字段规范字典：raw_value -> std_value 一对一映射，ETL 的 case when 分支与其一一对应'
STORED AS ORC;

INSERT INTO ydsh_dwd.dim_enum_map VALUES
  -- 性别：8 种写法 -> 3 个规范值
  ('gender','男','男'),      ('gender','女','女'),
  ('gender','M','男'),       ('gender','male','男'),    ('gender','Male','男'),
  ('gender','F','女'),       ('gender','female','女'),  ('gender','Female','女'),
  ('gender','未知','未知'),  ('gender','','未知'),
  -- 扫描表里空串统一显示为 <空>，这里同步登记一条，保证"字典覆盖率 100%"可被 join 验证
  ('gender','<空>','未知'),

  -- 会员等级：12 种写法 -> 4 个规范值 + 未知
  ('member_level','普通会员','普通会员'), ('member_level','普通','普通会员'),
  ('member_level','normal','普通会员'),   ('member_level','Normal','普通会员'),
  ('member_level','银卡会员','银卡会员'), ('member_level','银卡','银卡会员'),
  ('member_level','silver','银卡会员'),   ('member_level','Silver','银卡会员'),
  ('member_level','金卡会员','金卡会员'), ('member_level','金卡','金卡会员'),
  ('member_level','gold','金卡会员'),     ('member_level','Gold','金卡会员'),
  ('member_level','钻石会员','钻石会员'), ('member_level','钻石','钻石会员'),
  ('member_level','','未知'),
  ('member_level','<空>','未知'),

  -- 订单状态：12 种写法 -> 5 个规范值（数字码语义见 dim_code_order_status 注释）
  ('order_status','待支付','待支付'),  ('order_status','1','待支付'),
  ('order_status','已支付','已支付'),  ('order_status','已付款','已支付'),
  ('order_status','2','已支付'),
  ('order_status','已完成','已完成'),  ('order_status','完成','已完成'),
  ('order_status','3','已完成'),
  ('order_status','已取消','已取消'),  ('order_status','cancelled','已取消'),
  ('order_status','Cancelled','已取消'), ('order_status','4','已取消'),
  ('order_status','退款中','退款中'),

  -- 支付方式：7 种写法 -> 4 个规范值
  ('pay_type','支付宝','支付宝'),   ('pay_type','alipay','支付宝'),  ('pay_type','Alipay','支付宝'),
  ('pay_type','微信支付','微信支付'), ('pay_type','微信','微信支付'),
  ('pay_type','wechat','微信支付'),  ('pay_type','WeChat','微信支付'),
  ('pay_type','银行卡','银行卡'),
  ('pay_type','货到付款','货到付款'),

  -- 下单渠道：6 种写法 -> 4 个规范值
  ('channel','APP','APP'),   ('channel','app','APP'),   ('channel','App','APP'),
  ('channel','小程序','小程序'),
  ('channel','PC官网','PC官网'), ('channel','pc','PC官网'), ('channel','PC','PC官网'),
  ('channel','门店','门店'),

  -- 行为类型：10 种写法 -> 4 个规范值
  ('behavior_type','pv','浏览'),   ('behavior_type','PV','浏览'),   ('behavior_type','浏览','浏览'),
  ('behavior_type','cart','加购'), ('behavior_type','Cart','加购'), ('behavior_type','加购','加购'),
  ('behavior_type','fav','收藏'),  ('behavior_type','Fav','收藏'),  ('behavior_type','收藏','收藏'),
  ('behavior_type','buy','购买'),   ('behavior_type','购买','购买');

-- ----------------------------------------------------------------
-- 2. 值域扫描表：清洗前 / 清洗后 两批数据放同一张表，用 phase 区分
-- ----------------------------------------------------------------
DROP TABLE IF EXISTS ydsh_dwd.dq_enum_scan;
CREATE TABLE ydsh_dwd.dq_enum_scan (
  phase     STRING COMMENT 'before=ODS 清洗前 / after=DWD 清洗后',
  domain    STRING COMMENT '字段域',
  raw_value STRING COMMENT '取值（空串统一记为 <空>）',
  cnt       BIGINT COMMENT '行数'
)
COMMENT '枚举字段值域扫描结果：清洗前后各一份，用于量化值域收敛程度'
STORED AS ORC;

-- 扫描清洗前取值分布（为什么必须先扫描？因为"枚举分支遗漏"是本节最大坑，
-- 只凭任务书举的例子写 case when，一定会漏掉 Male/WeChat/App 这类首字母大写变体）
INSERT INTO ydsh_dwd.dq_enum_scan
SELECT 'before', 'gender', IF(gender = '', '<空>', gender), COUNT(*)
FROM ydsh_ods.ods_user GROUP BY gender
UNION ALL
SELECT 'before', 'member_level', IF(member_level = '', '<空>', member_level), COUNT(*)
FROM ydsh_ods.ods_user GROUP BY member_level
UNION ALL
SELECT 'before', 'order_status', IF(order_status = '', '<空>', order_status), COUNT(*)
FROM ydsh_ods.ods_order GROUP BY order_status
UNION ALL
SELECT 'before', 'pay_type', IF(pay_type = '', '<空>', pay_type), COUNT(*)
FROM ydsh_ods.ods_order GROUP BY pay_type
UNION ALL
SELECT 'before', 'channel', IF(channel = '', '<空>', channel), COUNT(*)
FROM ydsh_ods.ods_order GROUP BY channel
UNION ALL
SELECT 'before', 'behavior_type', IF(behavior_type = '', '<空>', behavior_type), COUNT(*)
FROM ydsh_ods.ods_user_behavior GROUP BY behavior_type;

-- 清洗前值域概况：字段 -> 取值种类数 -> 不规范行数
-- （join 里两边都有 domain/raw_value，必须全部加表别名，否则 Hive 报
--   "Column domain Found in more than One Tables/Subqueries"）
SELECT s.domain,
       COUNT(DISTINCT s.raw_value)                                     AS distinct_values,
       SUM(s.cnt)                                                      AS total_rows,
       SUM(IF(m.r2 IS NULL, s.cnt, 0))                                 AS unmapped_rows
FROM (SELECT * FROM ydsh_dwd.dq_enum_scan WHERE phase = 'before') s
LEFT JOIN (SELECT DISTINCT domain AS d2, raw_value AS r2 FROM ydsh_dwd.dim_enum_map) m
       ON s.domain = m.d2 AND s.raw_value = m.r2
GROUP BY s.domain
ORDER BY distinct_values DESC;

-- 覆盖性自检：这里应该返回 0 行。返回非 0 说明字典漏了取值，
-- 必须先把漏掉的写法补进 dim_enum_map，否则 DWD 层会出现"标准值以外"的脏值。
SELECT s.domain, s.raw_value, s.cnt
FROM (SELECT * FROM ydsh_dwd.dq_enum_scan WHERE phase = 'before') s
LEFT JOIN (SELECT DISTINCT domain AS d2, raw_value AS r2 FROM ydsh_dwd.dim_enum_map) m
       ON s.domain = m.d2 AND s.raw_value = m.r2
WHERE m.r2 IS NULL;
