-- ================================================================
-- 子任务 4：清洗前后数据质量对比（五类脏数据修复率）
-- 周次：第 2 周
-- 作者：梁常金
--
-- 设计要点（这几点是"报告可信度"的关键）：
--   1) 清洗前口径一律取 ODS 全表（不做去重、不做剔除），
--      因为 ODS 就是"进来的样子"，拿它当分母才叫"清洗前"；
--   2) 清洗后口径取 DWD 层实际落库数据，数字不是手写的，是查出来的；
--   3) 修复率 = (清洗前数量 - 清洗后数量) / 清洗前数量，
--      分母为 0 时返回 NULL（不写 0%，避免"没有问题"和"修复率 0"混淆）；
--   4) 每一行都带"所属表/总行数"，方便答辩时解释 600 和 15600 的关系。
--
-- 注意一个口径细节：订单负金额清洗前 310 条、超大金额 291 条，合计 601；
--   但去重发生在金额修正之前（先删 600 条重复行），其中 5 条脏金额正好在
--   被删掉的重复行里，所以 DWD 侧只需要修正 596 条（297 + 299）。
--   这不影响修复率（都是 100%），但写进报告体现口径严谨。
-- ================================================================

USE ydsh_dwd;

DROP TABLE IF EXISTS ydsh_dwd.dq_compare;
CREATE TABLE ydsh_dwd.dq_compare (
  metric_no    INT    COMMENT '指标序号',
  dim_type     STRING COMMENT '脏数据类型：缺失值/重复值/异常值/格式不一致/业务逻辑错误',
  metric_name  STRING COMMENT '指标名称',
  table_name   STRING COMMENT '涉及表',
  before_cnt   BIGINT COMMENT '清洗前数量（ODS 口径）',
  after_cnt    BIGINT COMMENT '清洗后数量（DWD 口径）',
  total_before BIGINT COMMENT '该类数据总量（分母参考）',
  fix_rate     DECIMAL(6,2) COMMENT '修复率 %',
  strategy     STRING COMMENT '处理策略'
)
COMMENT '清洗前/后数据质量对比表（第 2 周 · 子任务 4）'
STORED AS ORC;

-- ----------------------------------------------------------------
-- 缺失值
-- ----------------------------------------------------------------
INSERT INTO ydsh_dwd.dq_compare
SELECT 1, '缺失值', '用户年龄缺失或越界（NULL/≤0/>120）', 'ods_user -> dim_user',
       b.v, a.v, 1000,
       ROUND(100.0 * (b.v - a.v) / b.v, 2), '有效样本均值 41 填充并打标记'
FROM (SELECT SUM(IF(age IS NULL OR age < 1 OR age > 120, 1, 0)) v FROM ydsh_ods.ods_user) b
-- 注意：这里统计的是"清洗后仍不合规的行数"，不是 age_is_filled 的行数。
--   age_is_filled = 78 恰恰说明有 78 行被填充过（即清洗前的问题数），
--   拿它当"清洗后剩余问题"会把修复率算成 0%，这是本表第一次跑出来的真实错误。
CROSS JOIN (SELECT SUM(IF(age IS NULL OR age < 1 OR age > 120, 1, 0)) v FROM ydsh_dwd.dim_user) a;

INSERT INTO ydsh_dwd.dq_compare
SELECT 2, '缺失值', '用户邮箱缺失', 'ods_user -> dim_user',
       b.v, a.v, 1000,
       ROUND(100.0 * (b.v - a.v) / b.v, 2), '保留 NULL + 缺失标记，不编造'
FROM (SELECT SUM(IF(email IS NULL OR trim(email) = '', 1, 0)) v FROM ydsh_ods.ods_user) b
CROSS JOIN (SELECT SUM(email_is_missing) v FROM ydsh_dwd.dim_user) a;

INSERT INTO ydsh_dwd.dq_compare
SELECT 3, '缺失值', '商品上架日期缺失', 'ods_product -> dim_product',
       b.v, a.v, 600,
       ROUND(100.0 * (b.v - a.v) / b.v, 2), '保留 NULL + 缺失标记（无可靠推断依据）'
FROM (SELECT SUM(IF(launch_date IS NULL OR trim(launch_date) = '', 1, 0)) v FROM ydsh_ods.ods_product) b
CROSS JOIN (SELECT SUM(launch_date_is_missing) v FROM ydsh_dwd.dim_product) a;

-- ----------------------------------------------------------------
-- 重复值
-- ----------------------------------------------------------------
INSERT INTO ydsh_dwd.dq_compare
SELECT 4, '重复值', '订单主键重复行', 'ods_order -> dwd_order',
       b.v, a.v, 15600,
       ROUND(100.0 * (b.v - a.v) / b.v, 2), '按 order_id 去重，保留金额合法+时间最新的一条'
FROM (SELECT COUNT(*) - COUNT(DISTINCT order_id) v FROM ydsh_ods.ods_order) b
CROSS JOIN (SELECT COUNT(*) - COUNT(DISTINCT order_id) v FROM ydsh_dwd.dwd_order) a;

-- ----------------------------------------------------------------
-- 异常值
-- ----------------------------------------------------------------
INSERT INTO ydsh_dwd.dq_compare
SELECT 5, '异常值', '订单实付金额为负', 'ods_order -> dwd_order',
       b.v, a.v, 15600,
       ROUND(100.0 * (b.v - a.v) / b.v, 2), '取绝对值（符号录错），打 SIGN_FIXED 标记'
FROM (SELECT SUM(IF(pay_amount < 0, 1, 0)) v FROM ydsh_ods.ods_order) b
CROSS JOIN (SELECT SUM(IF(pay_amount < 0, 1, 0)) v FROM ydsh_dwd.dwd_order) a;

INSERT INTO ydsh_dwd.dq_compare
SELECT 6, '异常值', '订单实付金额超大（>10000）', 'ods_order -> dwd_order',
       b.v, a.v, 15600,
       ROUND(100.0 * (b.v - a.v) / b.v, 2), '用同门店正常订单金额中位数替换，打 OUTLIER_REPAIRED 标记'
FROM (SELECT SUM(IF(pay_amount > 10000, 1, 0)) v FROM ydsh_ods.ods_order) b
CROSS JOIN (SELECT SUM(IF(pay_amount > 10000, 1, 0)) v FROM ydsh_dwd.dwd_order) a;

INSERT INTO ydsh_dwd.dq_compare
SELECT 7, '异常值', '订单优惠金额为负', 'ods_order -> dwd_order',
       b.v, a.v, 15600,
       ROUND(100.0 * (b.v - a.v) / b.v, 2), '负优惠无业务含义，置 0'
FROM (SELECT SUM(IF(discount_amount < 0, 1, 0)) v FROM ydsh_ods.ods_order) b
CROSS JOIN (SELECT SUM(IF(discount_amount < 0, 1, 0)) v FROM ydsh_dwd.dwd_order) a;

INSERT INTO ydsh_dwd.dq_compare
SELECT 8, '异常值', '商品售价为负', 'ods_product -> dim_product',
       b.v, a.v, 600,
       ROUND(100.0 * (b.v - a.v) / b.v, 2), '取绝对值，打 SIGN_FIXED 标记'
FROM (SELECT SUM(IF(price < 0, 1, 0)) v FROM ydsh_ods.ods_product) b
CROSS JOIN (SELECT SUM(IF(price < 0, 1, 0)) v FROM ydsh_dwd.dim_product) a;

INSERT INTO ydsh_dwd.dq_compare
SELECT 9, '异常值', '商品售价超大（>10000）', 'ods_product -> dim_product',
       b.v, a.v, 600,
       ROUND(100.0 * (b.v - a.v) / b.v, 2), '用同二级品类正常价格中位数替换'
FROM (SELECT SUM(IF(price > 10000, 1, 0)) v FROM ydsh_ods.ods_product) b
CROSS JOIN (SELECT SUM(IF(price > 10000, 1, 0)) v FROM ydsh_dwd.dim_product) a;

INSERT INTO ydsh_dwd.dq_compare
SELECT 10, '异常值', '明细购买数量为 0 或负', 'ods_order_item -> dwd_order_item',
       b.v, a.v, 37549,
       ROUND(100.0 * (b.v - a.v) / b.v, 2), '数量为 0 剔除（386 条）；为负取绝对值（332 条）'
FROM (SELECT SUM(IF(quantity <= 0, 1, 0)) v FROM ydsh_ods.ods_order_item) b
CROSS JOIN (SELECT SUM(IF(quantity <= 0, 1, 0)) v FROM ydsh_dwd.dwd_order_item) a;

INSERT INTO ydsh_dwd.dq_compare
SELECT 11, '异常值', '明细购买数量超大（>5）', 'ods_order_item -> dwd_order_item',
       b.v, a.v, 37549,
       ROUND(100.0 * (b.v - a.v) / b.v, 2), '用同商品正常区间中位数替换（原值全为 99）'
FROM (SELECT SUM(IF(quantity > 5, 1, 0)) v FROM ydsh_ods.ods_order_item) b
CROSS JOIN (SELECT SUM(IF(quantity > 5, 1, 0)) v FROM ydsh_dwd.dwd_order_item) a;

INSERT INTO ydsh_dwd.dq_compare
SELECT 12, '异常值', '明细成交单价为负', 'ods_order_item -> dwd_order_item',
       b.v, a.v, 37549,
       ROUND(100.0 * (b.v - a.v) / b.v, 2), '取绝对值（与负金额同源）'
FROM (SELECT SUM(IF(unit_price < 0, 1, 0)) v FROM ydsh_ods.ods_order_item) b
CROSS JOIN (SELECT SUM(IF(unit_price < 0, 1, 0)) v FROM ydsh_dwd.dwd_order_item) a;

-- ----------------------------------------------------------------
-- 格式不一致
-- ----------------------------------------------------------------
INSERT INTO ydsh_dwd.dq_compare
SELECT 13, '格式不一致', '订单下单日期非 yyyy-MM-dd', 'ods_order -> dwd_order',
       b.v, a.v, 15600,
       ROUND(100.0 * (b.v - a.v) / b.v, 2), '斜杠/紧凑格式统一为 yyyy-MM-dd'
FROM (SELECT SUM(IF(order_date NOT RLIKE '^[0-9]{4}-[0-9]{2}-[0-9]{2}$', 1, 0)) v FROM ydsh_ods.ods_order) b
CROSS JOIN (SELECT SUM(IF(order_date NOT RLIKE '^[0-9]{4}-[0-9]{2}-[0-9]{2}$', 1, 0)) v FROM ydsh_dwd.dwd_order) a;

INSERT INTO ydsh_dwd.dq_compare
SELECT 14, '格式不一致', '用户注册日期非 yyyy-MM-dd', 'ods_user -> dim_user',
       b.v, a.v, 1000,
       ROUND(100.0 * (b.v - a.v) / b.v, 2), '三分支（-/ / / yyyyMMdd）统一'
FROM (SELECT SUM(IF(register_date NOT RLIKE '^[0-9]{4}-[0-9]{2}-[0-9]{2}$', 1, 0)) v FROM ydsh_ods.ods_user) b
CROSS JOIN (SELECT SUM(IF(register_date NOT RLIKE '^[0-9]{4}-[0-9]{2}-[0-9]{2}$', 1, 0)) v FROM ydsh_dwd.dim_user) a;

INSERT INTO ydsh_dwd.dq_compare
SELECT 15, '格式不一致', '手机号含分隔符或非 11 位', 'ods_user -> dim_user',
       b.v, a.v, 1000,
       ROUND(100.0 * (b.v - a.v) / b.v, 2), '去除 - 分隔符，统一 11 位纯数字'
FROM (SELECT SUM(IF(phone NOT RLIKE '^[0-9]{11}$', 1, 0)) v FROM ydsh_ods.ods_user) b
CROSS JOIN (SELECT SUM(IF(phone NOT RLIKE '^[0-9]{11}$', 1, 0)) v FROM ydsh_dwd.dim_user) a;

INSERT INTO ydsh_dwd.dq_compare
SELECT 16, '格式不一致', '行为时间非 yyyy-MM-dd HH:mm:ss', 'ods_user_behavior -> dwd_user_behavior',
       b.v, a.v, 60000,
       ROUND(100.0 * (b.v - a.v) / b.v, 2), '斜杠改横杠 + 缺秒补 :00，打 time_is_fixed 标记'
FROM (SELECT SUM(IF(behavior_time NOT RLIKE '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}$', 1, 0)) v FROM ydsh_ods.ods_user_behavior) b
CROSS JOIN (SELECT SUM(IF(behavior_time NOT RLIKE '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}$', 1, 0)) v FROM ydsh_dwd.dwd_user_behavior) a;

INSERT INTO ydsh_dwd.dq_compare
SELECT 17, '格式不一致', '商品品类含首尾空格', 'ods_product -> dim_product',
       b.v, a.v, 600,
       ROUND(100.0 * (b.v - a.v) / b.v, 2), 'trim 后品类由 16 种收敛到 8 种'
FROM (SELECT SUM(IF(category <> trim(category), 1, 0)) v FROM ydsh_ods.ods_product) b
CROSS JOIN (SELECT SUM(IF(category <> trim(category), 1, 0)) v FROM ydsh_dwd.dim_product) a;

-- ----------------------------------------------------------------
-- 业务逻辑错误（枚举取值不规范）
-- ----------------------------------------------------------------
INSERT INTO ydsh_dwd.dq_compare
SELECT 18, '业务逻辑错误', '性别取值不规范', 'ods_user -> dim_user',
       b.v, a.v, 1000,
       ROUND(100.0 * (b.v - a.v) / b.v, 2), '8 种写法收敛为 男/女/未知'
FROM (SELECT SUM(IF(gender NOT IN ('男','女','未知'), 1, 0)) v FROM ydsh_ods.ods_user) b
CROSS JOIN (SELECT SUM(IF(gender NOT IN ('男','女','未知'), 1, 0)) v FROM ydsh_dwd.dim_user) a;

INSERT INTO ydsh_dwd.dq_compare
SELECT 19, '业务逻辑错误', '会员等级取值不规范', 'ods_user -> dim_user',
       b.v, a.v, 1000,
       ROUND(100.0 * (b.v - a.v) / b.v, 2), '12 种写法收敛为 4 级 + 未知'
FROM (SELECT SUM(IF(member_level NOT IN ('普通会员','银卡会员','金卡会员','钻石会员','未知'), 1, 0)) v FROM ydsh_ods.ods_user) b
CROSS JOIN (SELECT SUM(IF(member_level NOT IN ('普通会员','银卡会员','金卡会员','钻石会员','未知'), 1, 0)) v FROM ydsh_dwd.dim_user) a;

INSERT INTO ydsh_dwd.dq_compare
SELECT 20, '业务逻辑错误', '订单状态取值不规范', 'ods_order -> dwd_order',
       b.v, a.v, 15600,
       ROUND(100.0 * (b.v - a.v) / b.v, 2), '12 种写法收敛为 5 个状态'
FROM (SELECT SUM(IF(order_status NOT IN ('待支付','已支付','已完成','已取消','退款中'), 1, 0)) v FROM ydsh_ods.ods_order) b
CROSS JOIN (SELECT SUM(IF(order_status NOT IN ('待支付','已支付','已完成','已取消','退款中'), 1, 0)) v FROM ydsh_dwd.dwd_order) a;

INSERT INTO ydsh_dwd.dq_compare
SELECT 21, '业务逻辑错误', '支付方式取值不规范', 'ods_order -> dwd_order',
       b.v, a.v, 15600,
       ROUND(100.0 * (b.v - a.v) / b.v, 2), '7 种写法收敛为 4 种'
FROM (SELECT SUM(IF(pay_type NOT IN ('支付宝','微信支付','银行卡','货到付款'), 1, 0)) v FROM ydsh_ods.ods_order) b
CROSS JOIN (SELECT SUM(IF(pay_type NOT IN ('支付宝','微信支付','银行卡','货到付款'), 1, 0)) v FROM ydsh_dwd.dwd_order) a;

INSERT INTO ydsh_dwd.dq_compare
SELECT 22, '业务逻辑错误', '下单渠道取值不规范', 'ods_order -> dwd_order',
       b.v, a.v, 15600,
       ROUND(100.0 * (b.v - a.v) / b.v, 2), '6 种写法收敛为 4 种'
FROM (SELECT SUM(IF(channel NOT IN ('APP','小程序','PC官网','门店'), 1, 0)) v FROM ydsh_ods.ods_order) b
CROSS JOIN (SELECT SUM(IF(channel NOT IN ('APP','小程序','PC官网','门店'), 1, 0)) v FROM ydsh_dwd.dwd_order) a;

INSERT INTO ydsh_dwd.dq_compare
SELECT 23, '业务逻辑错误', '行为类型取值不规范', 'ods_user_behavior -> dwd_user_behavior',
       b.v, a.v, 60000,
       ROUND(100.0 * (b.v - a.v) / b.v, 2), '10 种写法收敛为 浏览/加购/收藏/购买'
FROM (SELECT SUM(IF(behavior_type NOT IN ('浏览','加购','收藏','购买'), 1, 0)) v FROM ydsh_ods.ods_user_behavior) b
CROSS JOIN (SELECT SUM(IF(behavior_type NOT IN ('浏览','加购','收藏','购买'), 1, 0)) v FROM ydsh_dwd.dwd_user_behavior) a;

-- ----------------------------------------------------------------
-- 明细清单（报告直接用这张表）
-- ----------------------------------------------------------------
SELECT metric_no, dim_type, metric_name, before_cnt, after_cnt, fix_rate, strategy
FROM ydsh_dwd.dq_compare
ORDER BY metric_no;

-- ----------------------------------------------------------------
-- 按五类脏数据汇总：修复率 = (Σ清洗前 - Σ清洗后) / Σ清洗前
-- ----------------------------------------------------------------
SELECT dim_type,
       COUNT(*)                                                        AS metric_cnt,
       SUM(before_cnt)                                                 AS before_total,
       SUM(after_cnt)                                                  AS after_total,
       ROUND(100.0 * (SUM(before_cnt) - SUM(after_cnt)) / SUM(before_cnt), 2) AS fix_rate,
       SUM(IF(after_cnt = 0, 1, 0))                                    AS fully_fixed_metrics,
       SUM(IF(after_cnt > 0, 1, 0))                                    AS remaining_metrics
FROM ydsh_dwd.dq_compare
GROUP BY dim_type
ORDER BY fix_rate DESC;

-- 总体修复率（23 项指标合并口径）
SELECT SUM(before_cnt) AS before_total,
       SUM(after_cnt)  AS after_total,
       ROUND(100.0 * (SUM(before_cnt) - SUM(after_cnt)) / SUM(before_cnt), 2) AS overall_fix_rate
FROM ydsh_dwd.dq_compare;
