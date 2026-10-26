# -*- coding: utf-8 -*-
"""
validate_cleaning.py —— 清洗逻辑离线验算

作用：在宿主机上用 DuckDB 把 clean_*.sql 的**同一套逻辑**跑一遍，
     得到「清洗前 / 清洗后」的真实数字。

为什么要它：
  1. Hive 跑完后的数字应该与这里一致，不一致说明 SQL 写错了（交叉校验）；
  2. 《数据质量预评估表》《清洗报告》里的每个数字都出自这里，可复现；
  3. 答辩时被问"你这个 71% 修复率哪来的"，能当场跑给人看。

运行：
  python tools/validate_cleaning.py --data-dir data
依赖：duckdb（pip install duckdb）
"""
import argparse
import os
import duckdb

# Hive 里 STRING 列的空串不会变 NULL，只有 INT/DECIMAL 才会。
# 所以这里统一按 VARCHAR 读入，再用 TRY_CAST 模拟 Hive 的类型转换行为。
Q = {
    # ---------------- 完整性 ----------------
    "完整性": """
        SELECT 'ods_user.age'      AS 字段, COUNT(*) AS 总行数,
               COUNT(*) - SUM(CASE WHEN age IS NULL THEN 1 ELSE 0 END) AS 非空数,
               SUM(CASE WHEN age IS NULL THEN 1 ELSE 0 END) AS 空值数
        FROM ods_user
        UNION ALL SELECT 'ods_user.gender', COUNT(*), COUNT(*) - SUM(CASE WHEN COALESCE(trim(gender),'')='' THEN 1 ELSE 0 END), SUM(CASE WHEN COALESCE(trim(gender),'')='' THEN 1 ELSE 0 END) FROM ods_user
        UNION ALL SELECT 'ods_user.email', COUNT(*), COUNT(*) - SUM(CASE WHEN COALESCE(trim(email),'')='' THEN 1 ELSE 0 END), SUM(CASE WHEN COALESCE(trim(email),'')='' THEN 1 ELSE 0 END) FROM ods_user
        UNION ALL SELECT 'ods_user.member_level', COUNT(*), COUNT(*) - SUM(CASE WHEN COALESCE(trim(member_level),'')='' THEN 1 ELSE 0 END), SUM(CASE WHEN COALESCE(trim(member_level),'')='' THEN 1 ELSE 0 END) FROM ods_user
        UNION ALL SELECT 'ods_product.launch_date', COUNT(*), COUNT(*) - SUM(CASE WHEN COALESCE(trim(launch_date),'')='' THEN 1 ELSE 0 END), SUM(CASE WHEN COALESCE(trim(launch_date),'')='' THEN 1 ELSE 0 END) FROM ods_product
    """,
    # ---------------- 唯一性 ----------------
    "唯一性_主键重复": """
        SELECT 'ods_order' AS 表, COUNT(*) AS 重复组数, SUM(c) AS 涉及行数, SUM(c)-COUNT(*) AS 多出行数
        FROM (SELECT order_id, COUNT(*) c FROM ods_order GROUP BY order_id HAVING COUNT(*)>1)
    """,
    "唯一性_重复形态": """
        SELECT COUNT(*) AS 重复订单数,
               SUM(CASE WHEN dv=1 THEN 1 ELSE 0 END) AS 整行完全重复组,
               SUM(CASE WHEN dv>1 THEN 1 ELSE 0 END) AS 主键重复内容不同组,
               SUM(cnt-1) AS 需删除行数
        FROM (
          SELECT order_id, COUNT(*) cnt, COUNT(DISTINCT rn_key) dv
          FROM ods_order GROUP BY order_id HAVING COUNT(*)>1
        )
    """,
    # ---------------- 有效性 ----------------
    "有效性_枚举分布": """
        SELECT 'gender' AS 字段, gender AS 取值, COUNT(*) AS 条数 FROM ods_user GROUP BY gender
        UNION ALL SELECT 'member_level', member_level, COUNT(*) FROM ods_user GROUP BY member_level
        UNION ALL SELECT 'pay_type', pay_type, COUNT(*) FROM ods_order GROUP BY pay_type
        UNION ALL SELECT 'order_status', order_status, COUNT(*) FROM ods_order GROUP BY order_status
        UNION ALL SELECT 'channel', channel, COUNT(*) FROM ods_order GROUP BY channel
        UNION ALL SELECT 'behavior_type', behavior_type, COUNT(*) FROM ods_user_behavior GROUP BY behavior_type
        ORDER BY 字段, 条数 DESC
    """,
    "有效性_日期格式": """
        SELECT 'register_date' AS 字段,
               SUM(CASE WHEN regexp_matches(register_date,'^[0-9]{4}-[0-9]{2}-[0-9]{2}$') THEN 1 ELSE 0 END) AS 标准横杠,
               SUM(CASE WHEN regexp_matches(register_date,'^[0-9]{4}/[0-9]{2}/[0-9]{2}$') THEN 1 ELSE 0 END) AS 斜杠,
               SUM(CASE WHEN regexp_matches(register_date,'^[0-9]{8}$') THEN 1 ELSE 0 END) AS 紧凑八位,
               COUNT(*) AS 总行数 FROM ods_user
        UNION ALL SELECT 'order_date',
               SUM(CASE WHEN regexp_matches(order_date,'^[0-9]{4}-[0-9]{2}-[0-9]{2}$') THEN 1 ELSE 0 END),
               SUM(CASE WHEN regexp_matches(order_date,'^[0-9]{4}/[0-9]{2}/[0-9]{2}$') THEN 1 ELSE 0 END),
               SUM(CASE WHEN regexp_matches(order_date,'^[0-9]{8}$') THEN 1 ELSE 0 END), COUNT(*) FROM ods_order
        UNION ALL SELECT 'behavior_time',
               SUM(CASE WHEN regexp_matches(behavior_time,'^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}$') THEN 1 ELSE 0 END),
               SUM(CASE WHEN regexp_matches(behavior_time,'^[0-9]{4}/[0-9]{2}/[0-9]{2} [0-9]{2}:[0-9]{2}$') THEN 1 ELSE 0 END),
               0, COUNT(*) FROM ods_user_behavior
    """,
    "有效性_手机号": """
        SELECT COUNT(*) AS 总数,
               SUM(CASE WHEN regexp_matches(phone,'^1[0-9]{10}$') THEN 1 ELSE 0 END) AS 规范11位,
               SUM(CASE WHEN regexp_matches(phone,'^1[0-9]{2}-[0-9]{4}-[0-9]{4}$') THEN 1 ELSE 0 END) AS 带横杠
        FROM ods_user
    """,
    # ---------------- 一致性 ----------------
    "一致性_外键孤儿": """
        SELECT 'ods_order.user_id→ods_user' AS 关系,
               (SELECT COUNT(*) FROM ods_order o LEFT JOIN ods_user u USING(user_id) WHERE u.user_id IS NULL) AS 孤儿数
        UNION ALL SELECT 'ods_order.store_id→ods_store',
               (SELECT COUNT(*) FROM ods_order o LEFT JOIN ods_store s USING(store_id) WHERE s.store_id IS NULL)
        UNION ALL SELECT 'ods_order_item.order_id→ods_order',
               (SELECT COUNT(*) FROM ods_order_item i LEFT JOIN (SELECT DISTINCT order_id FROM ods_order) o USING(order_id) WHERE o.order_id IS NULL)
        UNION ALL SELECT 'ods_order_item.product_id→ods_product',
               (SELECT COUNT(*) FROM ods_order_item i LEFT JOIN ods_product p USING(product_id) WHERE p.product_id IS NULL)
        UNION ALL SELECT 'ods_user_behavior.user_id→ods_user',
               (SELECT COUNT(*) FROM ods_user_behavior b LEFT JOIN ods_user u USING(user_id) WHERE u.user_id IS NULL)
        UNION ALL SELECT 'ods_user_behavior.product_id→ods_product',
               (SELECT COUNT(*) FROM ods_user_behavior b LEFT JOIN ods_product p USING(product_id) WHERE p.product_id IS NULL)
    """,
    "一致性_省市冲突": """
        SELECT COUNT(*) AS 冲突城市数 FROM (
          SELECT city, COUNT(DISTINCT province) pc
          FROM (SELECT city, province FROM ods_user UNION ALL SELECT city, province FROM ods_store)
          GROUP BY city
        ) WHERE pc > 1
    """,
    # ---------------- 准确性 ----------------
    "准确性_数值区间": """
        SELECT '用户年龄 age' AS 指标, SUM(CASE WHEN age IS NULL THEN 1 ELSE 0 END) AS 空值,
               SUM(CASE WHEN age<0 THEN 1 ELSE 0 END) AS 负数, SUM(CASE WHEN age=0 THEN 1 ELSE 0 END) AS 零值,
               SUM(CASE WHEN age>120 THEN 1 ELSE 0 END) AS 超范围, MIN(age) AS 最小值, MAX(age) AS 最大值 FROM ods_user
        UNION ALL SELECT '订单实付 pay_amount', SUM(CASE WHEN pay_amount IS NULL THEN 1 ELSE 0 END),
               SUM(CASE WHEN pay_amount<0 THEN 1 ELSE 0 END), SUM(CASE WHEN pay_amount=0 THEN 1 ELSE 0 END),
               SUM(CASE WHEN pay_amount>100000 THEN 1 ELSE 0 END), MIN(pay_amount), MAX(pay_amount) FROM ods_order
        UNION ALL SELECT '订单优惠 discount_amount', SUM(CASE WHEN discount_amount IS NULL THEN 1 ELSE 0 END),
               SUM(CASE WHEN discount_amount<0 THEN 1 ELSE 0 END), SUM(CASE WHEN discount_amount=0 THEN 1 ELSE 0 END),
               SUM(CASE WHEN discount_amount>pay_amount THEN 1 ELSE 0 END), MIN(discount_amount), MAX(discount_amount) FROM ods_order
        UNION ALL SELECT '商品售价 price', SUM(CASE WHEN price IS NULL THEN 1 ELSE 0 END),
               SUM(CASE WHEN price<0 THEN 1 ELSE 0 END), SUM(CASE WHEN price=0 THEN 1 ELSE 0 END),
               SUM(CASE WHEN price>100000 THEN 1 ELSE 0 END), MIN(price), MAX(price) FROM ods_product
        UNION ALL SELECT '明细数量 quantity', SUM(CASE WHEN quantity IS NULL THEN 1 ELSE 0 END),
               SUM(CASE WHEN quantity<0 THEN 1 ELSE 0 END), SUM(CASE WHEN quantity=0 THEN 1 ELSE 0 END),
               SUM(CASE WHEN quantity>100 THEN 1 ELSE 0 END), MIN(quantity), MAX(quantity) FROM ods_order_item
        UNION ALL SELECT '明细单价 unit_price', SUM(CASE WHEN unit_price IS NULL THEN 1 ELSE 0 END),
               SUM(CASE WHEN unit_price<0 THEN 1 ELSE 0 END), SUM(CASE WHEN unit_price=0 THEN 1 ELSE 0 END),
               SUM(CASE WHEN unit_price>100000 THEN 1 ELSE 0 END), MIN(unit_price), MAX(unit_price) FROM ods_order_item
    """,
    "准确性_业务逻辑": """
        SELECT '成本价≥售价的商品数' AS 检查项, COUNT(*) AS 条数 FROM ods_product WHERE cost >= price
        UNION ALL SELECT '库存为0但在售', COUNT(*) FROM ods_product WHERE stock = 0 AND status = '在售'
        UNION ALL SELECT '折扣率>1的明细', COUNT(*) FROM ods_order_item WHERE discount > 1
    """,
    # ---------------- 清洗后 ----------------
    "清洗后_用户表": """
        WITH base AS (
          SELECT *, AVG(CASE WHEN age BETWEEN 1 AND 120 THEN age END) OVER () AS avg_age FROM ods_user
        ), dwd AS (
          SELECT user_id,
                 CASE WHEN COALESCE(trim(gender),'')='' THEN '未知' ELSE trim(gender) END AS gender,
                 CAST(CASE WHEN age BETWEEN 1 AND 120 THEN age ELSE round(avg_age) END AS INT) AS age,
                 CASE WHEN age BETWEEN 1 AND 120 THEN 0 ELSE 1 END AS age_is_filled,
                 CASE WHEN COALESCE(trim(member_level),'')='' THEN '未知' ELSE trim(member_level) END AS member_level,
                 CASE WHEN COALESCE(trim(email),'')='' THEN 1 ELSE 0 END AS email_is_missing
          FROM base
        )
        SELECT COUNT(*) AS 总行数, SUM(age_is_filled) AS 年龄被填充,
               SUM(CASE WHEN age IS NULL OR age<1 OR age>120 THEN 1 ELSE 0 END) AS 年龄仍异常,
               (SELECT CAST(round(AVG(age)) AS INT) FROM base WHERE age BETWEEN 1 AND 120) AS 填充用均值,
               SUM(CASE WHEN gender='未知' THEN 1 ELSE 0 END) AS 性别未知,
               SUM(CASE WHEN member_level='未知' THEN 1 ELSE 0 END) AS 等级未知,
               SUM(email_is_missing) AS 邮箱仍缺失
        FROM dwd
    """,
    "清洗后_商品表": """
        WITH cat AS (
          SELECT category, MIN(launch_date) AS min_launch
          FROM ods_product WHERE regexp_matches(launch_date,'^[0-9]{4}-[0-9]{2}-[0-9]{2}$')
          GROUP BY category
        ), dwd AS (
          SELECT p.product_id,
                 CASE WHEN COALESCE(trim(p.launch_date),'')='' THEN COALESCE(c.min_launch,'1970-01-01') ELSE trim(p.launch_date) END AS launch_date,
                 CASE WHEN COALESCE(trim(p.launch_date),'')='' THEN 1 ELSE 0 END AS f
          FROM ods_product p LEFT JOIN cat c USING(category)
        )
        SELECT COUNT(*) AS 总行数, SUM(f) AS 上架日期被填充,
               SUM(CASE WHEN COALESCE(trim(launch_date),'')='' THEN 1 ELSE 0 END) AS 仍缺失 FROM dwd
    """,
    "清洗后_订单表": """
        WITH ranked AS (
          SELECT *, ROW_NUMBER() OVER (PARTITION BY order_id
                   ORDER BY order_time DESC, pay_amount DESC, order_date DESC, rn_key DESC) AS rn
          FROM ods_order
        )
        SELECT COUNT(*) AS 去重后行数, COUNT(DISTINCT order_id) AS 不同订单数,
               (SELECT COUNT(*) FROM ods_order) AS 去重前行数 FROM ranked WHERE rn = 1
    """,
}


def _fmt(v):
    """把 DuckDB 返回值格式化成对齐友好的字符串。"""
    if v is None:
        return "NULL"
    if isinstance(v, float):
        return f"{v:.2f}"
    return str(v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data")
    args = ap.parse_args()

    con = duckdb.connect()
    con.execute("SET threads=4")

    # 全部按 VARCHAR 读入，模拟 ODS 贴源层的"原样保留"
    tables = {
        "ods_user": "user_id,user_name,gender,age,city,province,register_date,member_level,phone,email",
        "ods_product": "product_id,product_name,category,sub_category,brand,price,cost,stock,status,launch_date",
        "ods_store": "store_id,store_name,province,city,district,address,open_date,area,manager",
        "ods_order": "order_id,user_id,store_id,order_date,order_time,pay_amount,discount_amount,pay_type,order_status,channel",
        "ods_order_item": "item_id,order_id,product_id,quantity,unit_price,discount",
        "ods_user_behavior": "behavior_id,user_id,product_id,behavior_type,behavior_time,session_id",
    }
    for name, cols in tables.items():
        path = os.path.join(args.data_dir, name + ".csv")
        cols_sql = ", ".join(f"'{c}': 'VARCHAR'" for c in cols.split(","))
        con.execute(f"CREATE VIEW raw_{name} AS SELECT * FROM read_csv('{path}', header=true, columns={{{cols_sql}}})")

    # 类型转换 + 派生列，与 Hive DDL 对齐
    con.execute("""
        CREATE OR REPLACE VIEW ods_user AS
        SELECT user_id, user_name, gender, TRY_CAST(age AS INT) AS age, city, province,
               register_date, member_level, phone, email FROM raw_ods_user
    """)
    con.execute("""
        CREATE OR REPLACE VIEW ods_product AS
        SELECT product_id, product_name, category, sub_category, brand,
               TRY_CAST(price AS DECIMAL(12,2)) AS price, TRY_CAST(cost AS DECIMAL(12,2)) AS cost,
               TRY_CAST(stock AS INT) AS stock, status, launch_date FROM raw_ods_product
    """)
    con.execute("""
        CREATE OR REPLACE VIEW ods_order AS
        SELECT order_id, user_id, store_id, order_date, order_time,
               TRY_CAST(pay_amount AS DECIMAL(14,2)) AS pay_amount,
               TRY_CAST(discount_amount AS DECIMAL(12,2)) AS discount_amount,
               pay_type, order_status, channel,
               concat_ws('|', user_id, store_id, order_date, order_time,
                         CAST(pay_amount AS VARCHAR), CAST(discount_amount AS VARCHAR),
                         pay_type, order_status, channel) AS rn_key
        FROM raw_ods_order
    """)
    con.execute("""
        CREATE OR REPLACE VIEW ods_order_item AS
        SELECT item_id, order_id, product_id, TRY_CAST(quantity AS INT) AS quantity,
               TRY_CAST(unit_price AS DECIMAL(12,2)) AS unit_price,
               TRY_CAST(discount AS DECIMAL(6,4)) AS discount FROM raw_ods_order_item
    """)
    con.execute("""
        CREATE OR REPLACE VIEW ods_store AS
        SELECT store_id, store_name, province, city, district, address, open_date,
               TRY_CAST(area AS INT) AS area, manager FROM raw_ods_store
    """)
    con.execute("""
        CREATE OR REPLACE VIEW ods_user_behavior AS
        SELECT behavior_id, user_id, product_id, behavior_type, behavior_time, session_id
        FROM raw_ods_user_behavior
    """)

    for title, sql in Q.items():
        print(f"\n{'='*72}\n## {title}\n{'='*72}")
        try:
            cur = con.execute(sql)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
            widths = [max(len(str(c)), *(len(_fmt(r[i])) for r in rows)) if rows else len(str(c))
                      for i, c in enumerate(cols)]
            print("  ".join(str(c).ljust(widths[i]) for i, c in enumerate(cols)))
            print("  ".join("-" * w for w in widths))
            for r in rows:
                print("  ".join(_fmt(r[i]).ljust(widths[i]) for i in range(len(cols))))
        except Exception as e:  # noqa: BLE001
            print(f"[执行失败] {e}")


if __name__ == "__main__":
    main()
