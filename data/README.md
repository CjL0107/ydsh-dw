# data 目录说明

本目录存放 6 张模拟数据 CSV。**CSV 不进版本库**（见根目录 `.gitignore`），
原因：数据体积大（约 6.7 MB），且属于企业资产，不应随代码分发。

## 文件清单

| 文件名 | 行数（不含表头） | 大小 |
|---|---:|---:|
| ods_user.csv | 1,000 | 90 KB |
| ods_product.csv | 600 | 54 KB |
| ods_store.csv | 60 | 6 KB |
| ods_order.csv | 15,600 | 1.3 MB |
| ods_order_item.csv | 37,549 | 1.7 MB |
| ods_user_behavior.csv | 60,000 | 3.4 MB |

## 新成员如何拿到数据

1. 从课程资料包 / 团队网盘下载 `模拟数据.zip`；
2. 解压，把 6 个 csv 直接放进本目录（**不要建子目录**）；
3. 校验数量：`ls data/*.csv | wc -l` 应输出 6；
4. 校验行数：`python tools/profile_data.py --data-dir data` 应与上表一致。

## 注意

- CSV 编码为 **UTF-8 带 BOM**，首行是表头。建表时用
  `TBLPROPERTIES ('skip.header.line.count'='1')` 跳过，
  BOM 会随表头一起被跳过，不影响字段名。
- 字段内**不含**逗号与引号，可直接 `FIELDS TERMINATED BY ','` 导入，
  无需处理转义。
- `docker-compose.yml` 已把本目录挂载到容器的 `/data`（只读），
  所以 SQL 里写的是 `/data/ods_user.csv`。
