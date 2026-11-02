-- ================================================================
-- 子任务 1+2+3：订单事实表清洗与装载
-- 输入：ydsh_ods.ods_order        输出：ydsh_dwd.dwd_order（按 order_date 分区）
-- 周次：第 2 周
-- 作者：梁常金
--
-- 这一步是本周最重的清洗，顺序不能乱：
--   ① 去重（15600 -> 15000）——必须先做，否则后面算中位数、算 GMV 全被重复单污染
--   ② 枚举标准化（order_status / pay_type / channel，共 12+7+6 种写法）
--   ③ 日期统一 + 时间戳合并（order_date + order_time -> order_timestamp）
--   ④ 金额异常修正（负值取绝对值、超大值用门店中位数替换）
--   ⑤ 挂编码（pay_code / status_code / channel_code，关联编码映射维度表）
--   ⑥ 动态分区写入
--
-- 去重规则（为什么这么定）：
--   同一 order_id 保留 1 行，排序优先级：
--     1) 金额在合理区间 (0,10000] 的优先 —— 冲突重复里常有一条金额被改成了脏值，
--        留脏的那条等于把刚清洗好的数据又弄脏；
--     2) 下单时间更晚的优先 —— 业务上后写覆盖先写（binlog 消费顺序）；
--     3) 金额更大的优先 —— 兜底，保证结果确定（同一份数据跑两次结果必须一致）。
-- ================================================================

USE ydsh_dwd;

-- 动态分区：分区值来自 SELECT 的最后一列，不能写死
SET hive.exec.dynamic.partition = true;
SET hive.exec.dynamic.partition.mode = nonstrict;
SET hive.exec.max.dynamic.partitions = 2000;
SET hive.exec.max.dynamic.partitions.pernode = 2000;

INSERT OVERWRITE TABLE ydsh_dwd.dwd_order PARTITION (order_date)
SELECT
  x.order_id,
  x.user_id,
  x.store_id,
  x.order_time,
  -- ③ 字段合并：下单日期 + 下单时间 -> 完整时间戳
  concat(x.order_date_std, ' ', x.order_time)                      AS order_timestamp,
  CAST(substr(x.order_time, 1, 2) AS INT)                          AS order_hour,

  -- ④ 金额修正
  CAST(CASE
         WHEN x.pay_amount IS NULL      THEN 0
         WHEN x.pay_amount < 0          THEN -x.pay_amount
         WHEN x.pay_amount > 10000      THEN coalesce(m.med_amount, x.pay_amount)
         ELSE x.pay_amount
       END AS DECIMAL(14,2))                                       AS pay_amount,
  CAST(x.pay_amount AS DECIMAL(14,2))                              AS pay_amount_raw,
  CAST(GREATEST(x.discount_amount, 0) AS DECIMAL(12,2))            AS discount_amount,

  -- ② 标准化后的中文名 + ⑤ 编码
  x.pay_type,
  pt.pay_code,
  x.order_status,
  st.status_code,
  x.channel,
  ch.channel_code,

  CASE
    WHEN x.pay_amount IS NULL  THEN 'NULL'
    WHEN x.pay_amount < 0      THEN 'SIGN_FIXED'
    WHEN x.pay_amount > 10000  THEN 'OUTLIER_REPAIRED'
    ELSE 'NORMAL'
  END                                                              AS amount_flag,

  x.order_date_std                                                 AS order_date
FROM (
  SELECT
    d.order_id, d.user_id, d.store_id, d.order_time,
    d.pay_amount, d.discount_amount,

    -- ③ 日期统一：yyyy-MM-dd / yyyy/MM/dd / yyyyMMdd -> yyyy-MM-dd
    CASE
      WHEN trim(d.order_date) RLIKE '^[0-9]{8}$'
        THEN concat(substr(trim(d.order_date),1,4), '-',
                    substr(trim(d.order_date),5,2), '-',
                    substr(trim(d.order_date),7,2))
      ELSE regexp_replace(trim(d.order_date), '/', '-')
    END                                                            AS order_date_std,

    -- ② 订单状态：5 个规范值（数字码语义见 dim_code_order_status 注释）
    CASE
      WHEN trim(d.order_status) IN ('待支付','1')                     THEN '待支付'
      WHEN trim(d.order_status) IN ('已支付','已付款','2')            THEN '已支付'
      WHEN trim(d.order_status) IN ('已完成','完成','3')              THEN '已完成'
      WHEN trim(d.order_status) IN ('已取消','cancelled','Cancelled','4') THEN '已取消'
      WHEN trim(d.order_status) IN ('退款中')                         THEN '退款中'
      ELSE '未知'
    END                                                            AS order_status,

    -- ② 支付方式
    CASE
      WHEN trim(d.pay_type) IN ('支付宝','alipay','Alipay')           THEN '支付宝'
      WHEN trim(d.pay_type) IN ('微信支付','微信','wechat','WeChat')  THEN '微信支付'
      WHEN trim(d.pay_type) IN ('银行卡','bank','Bank')               THEN '银行卡'
      WHEN trim(d.pay_type) IN ('货到付款','cod','COD')               THEN '货到付款'
      ELSE '未知'
    END                                                            AS pay_type,

    -- ② 下单渠道
    CASE
      WHEN trim(d.channel) IN ('APP','app','App')                     THEN 'APP'
      WHEN trim(d.channel) IN ('小程序','mini')                       THEN '小程序'
      WHEN trim(d.channel) IN ('PC官网','pc','PC')                    THEN 'PC官网'
      WHEN trim(d.channel) IN ('门店','store')                        THEN '门店'
      ELSE '未知'
    END                                                            AS channel
  FROM (
    -- ① 去重
    SELECT t.*,
           ROW_NUMBER() OVER (
             PARTITION BY t.order_id
             ORDER BY IF(t.pay_amount > 0 AND t.pay_amount <= 10000, 0, 1) ASC,
                      t.order_time DESC,
                      t.pay_amount DESC
           ) AS rn
    FROM ydsh_ods.ods_order t
  ) d
  WHERE d.rn = 1
) x
-- ⑤ 编码映射：中文名 -> 编码（关联不上为 NULL，说明编码表缺项，08 脚本会查出来）
LEFT JOIN ydsh_dwd.dim_code_pay_type     pt ON x.pay_type     = pt.pay_name
LEFT JOIN ydsh_dwd.dim_code_order_status st ON x.order_status = st.status_name
LEFT JOIN ydsh_dwd.dim_code_channel      ch ON x.channel      = ch.channel_name
-- ④ 门店金额中位数（用于超大金额替换）
--    这里直接从 ODS 算，不再对去重后的集合算第二遍：中位数对重复行不敏感
--    （重复行与原行同值，等于给该值加权，2 倍权重对 40 行/店的中位数几乎无影响），
--    而多一次 1.5 万行的全量去重 + shuffle 是实打实的开销。
LEFT JOIN (
  SELECT store_id,
         CAST(percentile_approx(CAST(ABS(pay_amount) AS DOUBLE), CAST(0.5 AS DOUBLE)) AS DECIMAL(14,2)) AS med_amount
  FROM ydsh_ods.ods_order
  WHERE ABS(pay_amount) > 0 AND ABS(pay_amount) <= 10000
  GROUP BY store_id
) m ON x.store_id = m.store_id;

-- 装载后自检
SELECT COUNT(*)                                              AS total_rows,
       COUNT(DISTINCT order_id)                              AS distinct_orders,
       SUM(IF(pay_amount < 0, 1, 0))                         AS neg_amount,
       SUM(IF(pay_amount > 10000, 1, 0))                     AS huge_amount,
       SUM(IF(amount_flag = 'SIGN_FIXED', 1, 0))             AS sign_fixed,
       SUM(IF(amount_flag = 'OUTLIER_REPAIRED', 1, 0))       AS outlier_repaired,
       SUM(IF(order_status = '未知', 1, 0))                   AS status_unknown,
       SUM(IF(pay_code IS NULL, 1, 0))                       AS pay_code_null,
       SUM(IF(order_timestamp NOT RLIKE '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}$', 1, 0)) AS ts_bad
FROM ydsh_dwd.dwd_order;

-- 分区情况：应该能看到 300+ 个 order_date 分区
SHOW PARTITIONS ydsh_dwd.dwd_order;
