# Docker 常用命令手册（云购生活数仓项目版）

> 所有命令均在本项目 `ydsh-dw` 上实机验证过，输出为真实结果。
> 环境：Docker 29.9.2 + Docker Desktop（WSL 2 后端）+ Compose v5.6.1

---

## 零、先理解三个对象

| 对象 | 是什么 | 类比 |
|---|---|---|
| **镜像 image** | 只读模板，2.78 GB 的 `ydsh/hive:4.0.0-mysql` 就是它 | 装系统的 ISO |
| **容器 container** | 镜像跑起来的实例，可读写、可销毁 | 装好的那台虚拟机 |
| **数据卷 volume** | 独立于容器的持久化存储，删容器不删它 | 外挂的移动硬盘 |

一个镜像可以生成 100 个容器；容器删了数据卷里的数据还在；镜像删了容器就没法重建。这三句话是 Docker 全部行为的解释。

**本项目的实际对应关系：**

| 容器 | 镜像 | 宿主端口 → 容器内 | 作用 |
|---|---|---|---|
| `mysql` | `mysql:8.0` | 3307 → 3306 | Metastore 元数据库 |
| `hive-metastore` | `ydsh/hive:4.0.0-mysql` | 9083 → 9083 | 存表结构 |
| `hive-server` | `ydsh/hive:4.0.0-mysql` | 10000 → 10000 | 执行 SQL（HiveServer2） |

> 端口写成 `3307:3306` 是因为你本机装了 MySQL80 服务占着 3306。冒号左边是宿主，右边是容器内，**容器内部永远只认 3306**，所以 Hive 连元数据库不受影响。

---

## 一、日常 90% 的场景：只用这 6 条

### 1. 看有哪些容器在跑

```bash
docker ps                    # 只看运行中的
docker ps -a                 # 包括已停止的（排查"容器怎么没了"必用）
```

真实输出：

```
NAMES            IMAGE                   STATUS
hive-server      ydsh/hive:4.0.0-mysql   Up 25 minutes
mysql            mysql:8.0               Up 33 minutes (healthy)
hive-metastore   ydsh/hive:4.0.0-mysql   Up 33 minutes (healthy)
```

`(healthy)` 是 compose 里配的 healthcheck 在生效，表示容器内的服务真的能响应，不只是进程还活着。

### 2. 看日志（排错第一动作）

```bash
docker logs hive-metastore              # 全部日志
docker logs hive-metastore --tail 50    # 只看最后 50 行
docker logs -f hive-server              # 实时跟随（Ctrl+C 退出）
docker logs hive-metastore 2>&1 | grep -i error
```

Metastore 首次初始化成功的关键一行：

```
Initialized schema successfully..
```

看到这行说明 MySQL JDBC 驱动通了。如果看到 `No suitable driver found`，就是 `Dockerfile.hive` 里补装的驱动没生效。

### 3. 进容器执行命令

```bash
docker exec <容器名> <命令>
```

例子（都是本项目实际用到的）：

```bash
# 查 MySQL 元数据库里建了多少张表
docker exec mysql mysql -uroot -proot -e "use metastore; show tables;"

# 在容器内跑 beeline
docker exec -i hive-server beeline -u 'jdbc:hive2://localhost:10000/default' -e "show databases;"

# 进容器拿交互式 shell（需要真终端）
docker exec -it hive-server bash
```

**⚠️ 两个必踩的坑：**

- **`-it` 必须省略**。`-i` 是保持 stdin 打开，`-t` 是分配伪终端。脚本、管道、CI 里用 `-it` 会报 `cannot attach stdin to a TTY-enabled container`。只有人手动敲、且要有交互界面时才加。
- **Git Bash 下 `-f /sql/xxx.sql` 会被改写成 `C:/...`**，报 `No FileSystem for scheme "C"`。加环境变量压掉：

```bash
MSYS_NO_PATHCONV=1 docker exec -i hive-server beeline -u '...' -f /sql/ods/create_ods.sql
```

项目的 `scripts/run_all.sh` 第一行就 export 了这个变量。

### 4. 启停容器

```bash
docker stop hive-server      # 停（容器还在，docker ps -a 能看到）
docker start hive-server     # 启
docker restart hive-server   # 重启
docker rm hive-server        # 删（必须先 stop）
```

**⚠️ 本项目踩过的坑：`docker restart hive-server` 会让容器起不来。**
原因：`apache/hive` 镜像的 entrypoint 检测到旧 HiveServer2 进程还占着，就判定"已在运行"直接退出。正确做法是用 compose 重建：

```bash
docker compose up -d --force-recreate hive-server
```

### 5. 查容器细节和资源

```bash
docker inspect hive-server --format '镜像={{.Config.Image}} 挂载={{len .Mounts}}'
docker stats --no-stream          # 各容器 CPU/内存占用
docker top hive-server            # 容器内进程列表
```

本项目实测：

```
镜像=ydsh/hive:4.0.0-mysql  挂载数=5  重启策略=no

NAME             CPU %     MEM USAGE / LIMIT
hive-server      1.15%     1.544GiB / 15.43GiB
mysql            1.39%     435.9MiB / 15.43GiB
hive-metastore   0.11%     597.1MiB / 15.43GiB
```

Hive 吃 1.5 GB 内存是正常的（Tez + JVM 堆），笔记本至少要留 4 GB 给 Docker。

### 6. 进容器拷文件

```bash
docker cp hive-server:/opt/hive/conf/hive-site.xml .    # 容器 → 宿主
docker cp ./data/ods_user.csv mysql:/tmp/               # 宿主 → 容器
```

比进容器敲 `cat` 靠谱，尤其导出日志的时候。

---

## 二、本项目主用：docker compose

单容器用 `docker run`，**多容器一律用 compose**。Compose 把三个容器的网络、端口、挂载、启动顺序写进一个 `docker-compose.yml`，一条命令全起。

| 命令 | 作用 | 什么时候用 |
|---|---|---|
| `docker compose up -d` | 后台启动全部服务 | 每天开工 |
| `docker compose up -d --build` | 先重建镜像再启动 | **改了 `Dockerfile.hive` 必须加 `--build`** |
| `docker compose up -d --force-recreate` | 强制重建容器 | 改了配置挂载、容器起不来 |
| `docker compose ps` | 看编排内服务状态 | 代替 `docker ps` |
| `docker compose logs -f hive-server` | 跟某服务日志 | 排错 |
| `docker compose down` | 停止并删除容器+网络（**卷保留**） | 收工、彻底重来 |
| `docker compose down -v` | 连数据卷一起删 | **慎用，Hive 数仓数据全没** |
| `docker compose restart hive-server` | 重启单个服务 | 改配置后 |

**关键区别，答辩会被问：**

- `docker compose stop` / `down` → **不删数据卷**，`up` 回来表和数据都在
- `docker compose down -v` → 删卷，`ydsh_ods`、`ydsh_dwd` 全部消失，要重跑 `run_all`

项目的 SQL 都是幂等的（`DROP TABLE IF EXISTS` + `CREATE TABLE AS`），所以重跑 `run_all` 能恢复，但会多花几分钟。

---

## 三、镜像与磁盘

```bash
docker images                          # 本地镜像列表
docker pull mysql:8.0                  # 只拉不跑
docker rmi mysql:8.0                   # 删镜像（有容器在用会报错）
docker system df                       # 磁盘占用总览
docker system prune                    # 清理无用容器/网络/悬空镜像
docker builder prune                   # 清理构建缓存
```

本项目当前占用：

```
Images          2   3.879GB
Containers      3   104.7MB
Local Volumes   2   229.2MB
Build Cache    19   3.487GB   ← 可回收 709 MB
```

**磁盘告警的信号**：`docker system df` 里 Build Cache 涨到几个 GB 就可以 `docker builder prune` 清一次。但**别动 `docker system prune -a`**，那会把 `ydsh/hive:4.0.0-mysql` 也删掉，重下要一个多小时（你那 1.1 GB 镜像下了快 40 分钟）。

---

## 四、国内网络：镜像加速

Docker Hub 在国内基本拉不动。本项目已在 `C:\Users\Lenovo\.docker\daemon.json` 配好：

```json
{
  "registry-mirrors": [
    "https://docker.1panel.live",
    "https://docker.1ms.run",
    "https://docker.m.daocloud.io"
  ]
}
```

改完必须 `docker desktop restart`（或 Docker Desktop 里 Restart）才生效，验证：

```bash
docker info | grep -A4 "Registry Mirrors"
```

三个地址都列出来才算生效。实测速度 0.5～2.2 MB/s 之间波动，**镜像源会限速，慢的时候别以为卡死了**。

---

## 五、排错顺序（照这个顺序查，别乱试）

1. **`docker info` 有没有 Server 版本？**
   只有 Client 没有 Server = 引擎没起来。开 Docker Desktop 等左下角绿。
2. **`docker ps -a` 容器在不在？**
   状态 `Exited (1)` → `docker logs <名>` 看退出原因。
3. **`docker compose logs <服务> --tail 50`**
   90% 的问题在这一步能看出来（端口占用、驱动缺失、配置解析失败）。
4. **进容器看配置到底有没有生效**
   `docker exec <名> cat /opt/hive/conf/hive-site.xml`
   挂载路径写错时，宿主机改了文件容器里根本没有——只能靠这一步确认。
5. **端口冲突**
   ```powershell
   Get-NetTCPConnection -LocalPort 3306 -State Listen
   ```
   本项目就是在这步发现 MySQL80 占了 3306，改成 3307。

---

## 六、答辩可直接演示的 30 秒

```bash
# 1. 环境活着（3 个 Up + 2 个 healthy）
docker compose ps

# 2. 元数据库真的初始化了（应输出 83）
docker exec mysql mysql -uroot -proot -e \
  "select count(*) from information_schema.tables where table_schema='metastore';"

# 3. 数据真的进了 Hive（应输出 1000 / 600 / 60 / 15600 / 37549 / 60000）
docker exec -i hive-server beeline -u 'jdbc:hive2://localhost:10000/default' -e \
  "select 'user',count(*) from ydsh_ods.ods_user
   union all select 'order',count(*) from ydsh_ods.ods_order;"
```

三条命令跑完，环境的真实性就没得质疑了。
