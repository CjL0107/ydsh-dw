-- ============================================================
-- MySQL 初始化（仅在数据卷为空 / 首次部署时由官方镜像自动执行）
-- 位置：容器内 /docker-entrypoint-initdb.d/01-init.sql
--
-- 为什么需要这个文件：
--   Superset 的元数据库（superset）和 ADS 服务层库（ydsh_ads）都不是业务
--   元数据，Metastore 的 schematool 不会建它们。手工 `create database` 在
--   换机器、重装时一定会漏，所以固化到初始化脚本里，让 `docker compose up`
--   一次到位。
-- ============================================================

CREATE DATABASE IF NOT EXISTS `superset`
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE DATABASE IF NOT EXISTS `ydsh_ads`
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- 已有卷的机器不会重跑本脚本，这两个库在第 1~4 周已手工建好，不受影响。
