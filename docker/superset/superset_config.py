"""
Superset 配置（挂载到容器 /app/pythonpath/superset_config.py）

三条跟安全/部署有关的设置：
1) SECRET_KEY 不写死，从环境变量读。硬编码的 SECRET_KEY 在公开仓库里等于
   把会话签名密钥交出去，别人可以伪造登录态。
2) 元数据库用 MySQL（不是默认的 SQLite）。容器重启后 SQLite 文件丢失，
   做好的仪表板会一起消失 —— 这件事我在本地试过，代价是重做 5 个图。
3) 关闭示例数据加载和遥测，减少启动时间与外部请求（答辩现场不连外网）。
"""
import os

# 图表查询走的是 MySQL 引擎的默认驱动名 MySQLdb，
# 官方镜像里没装 mysqlclient（需要编译），但装了纯 Python 的 pymysql。
# 这一行让 `import MySQLdb` 落到 pymysql 上，省掉一次重新构建镜像。
# 不写的话图表会报 "Error: No module named 'MySQLdb'"。
import pymysql
pymysql.install_as_MySQLdb()

SECRET_KEY = os.environ["SUPERSET_SECRET_KEY"]

SQLALCHEMY_DATABASE_URI = (
    "mysql+pymysql://{user}:{pwd}@{host}:{port}/{db}?charset=utf8mb4".format(
        user=os.getenv("MYSQL_USER", "root"),
        pwd=os.getenv("MYSQL_PASSWORD", "root"),
        host=os.getenv("MYSQL_HOST", "mysql"),
        port=os.getenv("MYSQL_PORT", "3306"),
        db=os.getenv("SUPERSET_DB", "superset"),
    )
)

# 中文标签不乱码
BABEL_DEFAULT_LOCALE = "zh"
LANGUAGES = {"zh": {"flag": "cn", "name": "Chinese"}}

# 关闭示例仪表板与遥测
SUPERSET_LOAD_EXAMPLES = False
SUPERSET_ANALYTICS = False

# 单机演示：允许匿名看板，方便答辩时投屏不用登录（生产环境不要开）
PUBLIC_ROLE_LIKE = "Gamma"

# Hive 走 NOSASL：本项目的 HiveServer2 没开 Kerberos/LDAP，
# 用 SASL 反而会卡在握手阶段
FEATURE_FLAGS = {
    "ENABLE_TEMPLATE_PROCESSING": True,
    "DASHBOARD_CROSS_FILTERS": True,
    "EMBEDDED_SUPERSET": False,
}

# 查询超时：Hive 查询本来就慢，给到 5 分钟；ClickHouse 远小于此
SQLLAB_ASYNC_TIME_LIMIT_SEC = 300
