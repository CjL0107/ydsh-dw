# Superset 定制镜像：官方镜像缺两个驱动
#   clickhouse-connect —— 仪表板图表直接查 ClickHouse（ADS 服务层）
#   pyhive + thrift-sasl —— 连接 HiveServer2 做即席分析
# 官方镜像以 superset 用户运行，装驱动必须切 root，否则写不进 /usr/local/lib
FROM apache/superset:4.1.1

USER root
RUN pip install --no-cache-dir \
        pymysql==1.1.1 \
        clickhouse-connect==0.8.6 \
        pyhive==0.7.0 \
        thrift==0.16.0 \
        thrift-sasl==0.4.3 \
        pure-sasl==0.6.2 \
    && python -c "import clickhouse_connect, pyhive; print('drivers ok')"

USER superset
