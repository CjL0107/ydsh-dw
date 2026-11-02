-- ================================================================
-- 子任务 1+2+3 收尾：清洗后全面校验 + 值域收敛统计
-- 周次：第 2 周
-- 作者：梁常金
--
-- 这个脚本是"交卷前的检查清单"，每一段对应一条验收标准：
--   ① 值域收敛：枚举字段 distinct 取值个数必须收敛到规范值域大小
--   ② 行数守恒：清洗不应该凭空多行/少行（去重与剔除是例外，必须能对上账）
--   ③ 格式合规：日期、手机号、时间戳全部能用正则卡住
--   ④ 异常归零：负金额、超大金额、负年龄、0/负数量全部为 0
--   ⑤ 外键完整：事实表的维度键必须都能在维度表里找到（星型模型的命门）
--
-- 本脚本可以反复执行：① 用 INSERT OVERWRITE 重建扫描表（before 部分沿用 01
-- 脚本写入的快照，after 部分每次从 DWD 现算），因此不会出现重复行。
-- ================================================================

USE ydsh_dwd;

-- ----------------------------------------------------------------
-- ① 值域收敛：重建 dq_enum_scan（before 保留 01 的快照 + after 现算）
-- ----------------------------------------------------------------
INSERT OVERWRITE TABLE ydsh_dwd.dq_enum_scan
SELECT phase, domain, raw_value, cnt FROM ydsh_dwd.dq_enum_scan WHERE phase = 'before'
UNION ALL
SELECT 'after', 'gender', gender, COUNT(*) FROM ydsh_dwd.dim_user GROUP BY gender
UNION ALL
SELECT 'after', 'member_level', member_level, COUNT(*) FROM ydsh_dwd.dim_user GROUP BY member_level
UNION ALL
SELECT 'after', 'order_status', order_status, COUNT(*) FROM ydsh_dwd.dwd_order GROUP BY order_status
UNION ALL
SELECT 'after', 'pay_type', pay_type, COUNT(*) FROM ydsh_dwd.dwd_order GROUP BY pay_type
UNION ALL
SELECT 'after', 'channel', channel, COUNT(*) FROM ydsh_dwd.dwd_order GROUP BY channel
UNION ALL
SELECT 'after', 'behavior_type', behavior_type, COUNT(*) FROM ydsh_dwd.dwd_user_behavior GROUP BY behavior_type;

-- 收敛对比：before_distinct -> after_distinct
SELECT domain,
       SUM(IF(phase = 'before', 1, 0))                                  AS before_distinct,
       SUM(IF(phase = 'after',  1, 0))                                  AS after_distinct,
       SUM(IF(phase = 'before', cnt, 0))                                 AS before_rows,
       SUM(IF(phase = 'after',  cnt, 0))                                 AS after_rows
FROM ydsh_dwd.dq_enum_scan
GROUP BY domain
ORDER BY before_distinct DESC;

-- 反向校验：DWD 里的取值必须全部落在规范值域内（返回 0 行才算合格）
SELECT 'gender' AS domain, gender AS bad_value FROM ydsh_dwd.dim_user
 WHERE gender NOT IN ('男','女','未知')
UNION ALL
SELECT 'member_level', member_level FROM ydsh_dwd.dim_user
 WHERE member_level NOT IN ('普通会员','银卡会员','金卡会员','钻石会员','未知')
UNION ALL
SELECT 'order_status', order_status FROM ydsh_dwd.dwd_order
 WHERE order_status NOT IN ('待支付','已支付','已完成','已取消','退款中','未知')
UNION ALL
SELECT 'pay_type', pay_type FROM ydsh_dwd.dwd_order
 WHERE pay_type NOT IN ('支付宝','微信支付','银行卡','货到付款','未知')
UNION ALL
SELECT 'channel', channel FROM ydsh_dwd.dwd_order
 WHERE channel NOT IN ('APP','小程序','PC官网','门店','未知')
UNION ALL
SELECT 'behavior_type', behavior_type FROM ydsh_dwd.dwd_user_behavior
 WHERE behavior_type NOT IN ('浏览','加购','收藏','购买','未知');

-- ----------------------------------------------------------------
-- ② 行数对账
--    ODS 15600 - 去重 600 = 15000（订单）
--    ODS 37549 - 数量为 0 的 386 条 = 37163（明细）
--    ODS 60000 原样保留（行为）
-- ----------------------------------------------------------------
SELECT 'ods_order' AS tbl, COUNT(*) AS rows_cnt FROM ydsh_ods.ods_order
UNION ALL SELECT 'dwd_order', COUNT(*) FROM ydsh_dwd.dwd_order
UNION ALL SELECT 'ods_order_item', COUNT(*) FROM ydsh_ods.ods_order_item
UNION ALL SELECT 'dwd_order_item', COUNT(*) FROM ydsh_dwd.dwd_order_item
UNION ALL SELECT 'ods_user_behavior', COUNT(*) FROM ydsh_ods.ods_user_behavior
UNION ALL SELECT 'dwd_user_behavior', COUNT(*) FROM ydsh_dwd.dwd_user_behavior
UNION ALL SELECT 'ods_user', COUNT(*) FROM ydsh_ods.ods_user
UNION ALL SELECT 'dim_user', COUNT(*) FROM ydsh_dwd.dim_user
UNION ALL SELECT 'ods_product', COUNT(*) FROM ydsh_ods.ods_product
UNION ALL SELECT 'dim_product', COUNT(*) FROM ydsh_dwd.dim_product
UNION ALL SELECT 'ods_store', COUNT(*) FROM ydsh_ods.ods_store
UNION ALL SELECT 'dim_store', COUNT(*) FROM ydsh_dwd.dim_store;

-- ----------------------------------------------------------------
-- ③ 格式合规（每一项都应为 0）
-- ----------------------------------------------------------------
SELECT 'user.register_date' AS chk, SUM(IF(register_date NOT RLIKE '^[0-9]{4}-[0-9]{2}-[0-9]{2}$',1,0)) AS bad FROM ydsh_dwd.dim_user
UNION ALL SELECT 'user.phone', SUM(IF(phone NOT RLIKE '^[0-9]{11}$',1,0)) FROM ydsh_dwd.dim_user
UNION ALL SELECT 'product.launch_date', SUM(IF(launch_date IS NOT NULL AND launch_date NOT RLIKE '^[0-9]{4}-[0-9]{2}-[0-9]{2}$',1,0)) FROM ydsh_dwd.dim_product
UNION ALL SELECT 'store.open_date', SUM(IF(open_date NOT RLIKE '^[0-9]{4}-[0-9]{2}-[0-9]{2}$',1,0)) FROM ydsh_dwd.dim_store
UNION ALL SELECT 'order.timestamp', SUM(IF(order_timestamp NOT RLIKE '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}$',1,0)) FROM ydsh_dwd.dwd_order
UNION ALL SELECT 'order.partition', SUM(IF(order_date NOT RLIKE '^[0-9]{4}-[0-9]{2}-[0-9]{2}$',1,0)) FROM ydsh_dwd.dwd_order
UNION ALL SELECT 'behavior.time', SUM(IF(behavior_time NOT RLIKE '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}$',1,0)) FROM ydsh_dwd.dwd_user_behavior;

-- ----------------------------------------------------------------
-- ④ 异常值归零（每一项都应为 0）
-- ----------------------------------------------------------------
SELECT 'user.age' AS chk, SUM(IF(age IS NULL OR age < 1 OR age > 120,1,0)) AS bad FROM ydsh_dwd.dim_user
UNION ALL SELECT 'product.price', SUM(IF(price <= 0 OR price > 10000,1,0)) FROM ydsh_dwd.dim_product
UNION ALL SELECT 'order.pay_amount', SUM(IF(pay_amount < 0 OR pay_amount > 10000,1,0)) FROM ydsh_dwd.dwd_order
UNION ALL SELECT 'order.discount', SUM(IF(discount_amount < 0,1,0)) FROM ydsh_dwd.dwd_order
UNION ALL SELECT 'item.quantity', SUM(IF(quantity <= 0 OR quantity > 5,1,0)) FROM ydsh_dwd.dwd_order_item
UNION ALL SELECT 'item.unit_price', SUM(IF(unit_price < 0,1,0)) FROM ydsh_dwd.dwd_order_item
UNION ALL SELECT 'item.amount', SUM(IF(amount < 0,1,0)) FROM ydsh_dwd.dwd_order_item;

-- ----------------------------------------------------------------
-- ⑤ 外键完整性（星型模型：事实表每个维度键都要能在维度表命中）
--    维度表只有 60~1000 行，Tez 会自动选择 Map-Side Join 广播小表，
--    这里不需要再手写 MAPJOIN 提示（提示语法位置写错会直接报 ParseException）。
-- ----------------------------------------------------------------
SELECT 'order.user_id -> dim_user' AS fk,
       COUNT(*) AS total,
       SUM(IF(u.user_id IS NULL, 1, 0)) AS orphan
FROM ydsh_dwd.dwd_order o
LEFT JOIN (SELECT user_id FROM ydsh_dwd.dim_user) u ON o.user_id = u.user_id;

SELECT 'order.store_id -> dim_store' AS fk,
       COUNT(*) AS total,
       SUM(IF(s.store_id IS NULL, 1, 0)) AS orphan
FROM ydsh_dwd.dwd_order o
LEFT JOIN (SELECT store_id FROM ydsh_dwd.dim_store) s ON o.store_id = s.store_id;

SELECT 'order.order_date -> dim_date' AS fk,
       COUNT(*) AS total,
       SUM(IF(d.full_date IS NULL, 1, 0)) AS orphan
FROM (SELECT DISTINCT order_date FROM ydsh_dwd.dwd_order) o
LEFT JOIN (SELECT full_date FROM ydsh_dwd.dim_date) d
       ON o.order_date = CAST(d.full_date AS STRING);

SELECT 'item.product_id -> dim_product' AS fk,
       COUNT(*) AS total,
       SUM(IF(p.product_id IS NULL, 1, 0)) AS orphan
FROM ydsh_dwd.dwd_order_item i
LEFT JOIN (SELECT product_id FROM ydsh_dwd.dim_product) p ON i.product_id = p.product_id;

SELECT 'order.pay_code -> dim_code_pay_type' AS fk,
       COUNT(*) AS total,
       SUM(IF(p.pay_code IS NULL, 1, 0)) AS orphan
FROM ydsh_dwd.dwd_order o
LEFT JOIN (SELECT pay_code FROM ydsh_dwd.dim_code_pay_type) p ON o.pay_code = p.pay_code;
