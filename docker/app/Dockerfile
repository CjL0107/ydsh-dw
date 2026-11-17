# 应用镜像：一个镜像同时跑「数据服务 + 大屏」和「Streamlit 问答」
# 两者共享 python/ 下的查询层与 nl2sql，不必拆成两个镜像（省一次 1GB 拉取）。
#
# 关键点：容器里没有 docker 命令，hive_runner 的默认后端（docker exec beeline）
# 跑不通，所以运行时用环境变量 HIVE_BACKEND=pyhive 切到 Thrift 直连。
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=5 \
    TZ=Asia/Shanghai

COPY docker/app/requirements.txt /app/requirements.txt

# 用国内镜像源装依赖。
# 直连 files.pythonhosted.org 实测会 Read timed out（15s 读超时），
# 一次构建白等 6 分钟还失败；换清华源后同样的包 40 秒装完。
# PIP_INDEX_URL 做成构建参数，换到海外网络时 `--build-arg` 覆盖即可，不用改文件。
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
RUN pip install --index-url "${PIP_INDEX_URL}" --timeout 120 --retries 5 \
        -r /app/requirements.txt \
    && python -c "import flask, streamlit, pyhive, thrift_sasl, pymysql; print('deps ok')"

# 只拷运行期需要的东西：查询层、应用层、静态大屏
COPY python/ /app/python/
COPY apps/ /app/apps/

EXPOSE 8000 8501

# 默认跑数据服务（大屏 + JSON 接口）；streamlit 服务在 compose 里覆盖 command
CMD ["python", "apps/api/server.py"]
