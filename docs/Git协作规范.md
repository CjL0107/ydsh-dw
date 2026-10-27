# Git 协作规范

> 对应《第 1 周任务书》子任务 3「Git 基础与版本管理规范」，以及评分项「Git 规范（20 分）」。
> 适用范围：云购生活数仓项目全体成员，5 周开发期。

---

## 一、为什么学生项目也要有规范

评分项写的是「提交历史清晰」。什么叫清晰？教师翻你的 `git log` 时，能在 10 秒内回答三个问题：

1. **什么时候做了什么** —— 提交信息能不能看懂，而不是一堆「更新」「修改」「111」
2. **谁做的** —— 每个人的贡献能不能区分开，不是全部挂在一个人名下
3. **能不能回退** —— 出了问题能不能定位到具体某次提交

下面这套规范就是为了让这三件事成立。

---

## 二、首次配置（每人必做一次）

### 2.1 身份信息

```powershell
git config --global user.name "张三"
git config --global user.email "zhangsan@example.com"
```

**邮箱必须和你 GitHub 账号绑定的邮箱一致**，否则 GitHub 无法把提交归属到你名下，贡献图上是空的——这一项会直接影响「每人都有提交记录」的验收。

验证：

```powershell
git config --global --list | Select-String "user\."
```

### 2.2 生成 SSH 密钥并添加到 GitHub

```powershell
ssh-keygen -t ed25519 -C "zhangsan@example.com"
```

一路回车即可（密码可留空）。生成后复制公钥：

```powershell
Get-Content $HOME\.ssh\id_ed25519.pub | Set-Clipboard
```

然后到 GitHub：`Settings` → `SSH and GPG keys` → `New SSH key` → 粘贴 → 保存。

验证（看到 `Hi 你的用户名! You've successfully authenticated` 就是成功）：

```powershell
ssh -T git@github.com
```

> 如果卡住或超时，多半是网络问题。可先把远程地址用 HTTPS 方式克隆，SSH 配好后再切换。
> 国内访问 `github.com:22` 常被阻断，可改用 `ssh.github.com` 的 443 端口：
> 在 `~/.ssh/config` 里写 `Host github.com` / `Hostname ssh.github.com` / `Port 443`。

### 2.3 换行符设置（Windows 必做）

```powershell
git config --global core.autocrlf false
```

项目里已有 `.gitattributes` 强制 shell 脚本用 LF。如果不关掉 `autocrlf`，你队友在 WSL 里执行 `run_all.sh` 会报 `$'\r': command not found`——这个坑我们第一周就踩过。

---

## 三、提交信息规范（核心）

采用 **Conventional Commits**，主题用中文。

### 3.1 格式

```
<type>(<scope>): <subject>

<body 可选：说明为什么改，而不是改了什么>

<footer 可选：关联 issue，如 Closes #12>
```

**只有 `<type>` 和 `<subject>` 是必需的**，scope 建议带。

### 3.2 type 允许的取值

| type | 含义 | 本项目例子 |
|---|---|---|
| `feat` | 新增功能/脚本/表 | `feat(ods): 新增订单明细表建表脚本` |
| `fix` | 修 bug | `fix(docker): 容器端口映射改为 3307 避开本机 MySQL` |
| `docs` | 只改文档 | `docs(readme): 补充团队分工与阶段计划` |
| `refactor` | 重构，行为不变 | `refactor(clean): 去重窗口函数抽成公共子查询` |
| `perf` | 性能优化 | `perf(quality): 质量扫描合并为单次全表扫描` |
| `test` | 加测试 | `test(clean): 增加去重前后行数校验` |
| `style` | 格式调整，不影响逻辑 | `style(sql): 统一关键字大写` |
| `build` | 构建/依赖 | `build(docker): 升级 Hive 镜像到 4.0.0` |
| `ci` | CI 配置 | `ci: 增加提交信息格式检查` |
| `chore` | 杂项（配置、忽略规则） | `chore: 新增 .gitattributes 统一换行符` |
| `revert` | 回滚某次提交 | `revert: 回滚 feat(ods) 建表脚本` |

### 3.3 scope 允许的取值（本项目约定）

`ods` / `dwd` / `dws` / `ads` / `quality` / `clean` / `docker` / `sql` / `docs` / `tools` / `readme` / `git`

不确定时可以不写 scope，但**不要自己造一个没人看得懂的**。

### 3.4 subject 的写法

- **动词开头，说清做了什么**：`补充缺失值填充策略`，不是 `缺失值`
- **不超过 50 个字符**，超了就该拆成多次提交
- **结尾不加句号**
- **不要写「更新」「修改」「优化一下」**——这类词等于没说

### 3.5 好例子与坏例子（都是本项目真实提交）

```
✅ feat(ods): 新增 6 张 ODS 表建表与导入脚本
✅ fix(docker): 修正 Tez 计数器配置挂载路径到 /opt/hive/conf
✅ docs(quality): 数据质量预评估表替换为 Hive 实测值

❌ 更新
❌ 改了改
❌ 111
❌ 为了答辩修改了一下数据质量报告顺便把端口也改了
```

最后一条虽然信息量大，但一次提交干了三件事——应该拆成 `docs` 和 `fix` 两次提交。
**一次提交只做一件事**，这样出问题才能精确回退。

### 3.6 需要 body 的场景

改动原因不显然时，在空一行后写 body：

```
fix(docker): 端口映射改为 3307 避开本机 MySQL80

本机已安装 MySQL80 服务并监听 3306，compose 启动时报
"port is already allocated"。容器内仍使用 3306，Hive 的
JDBC 连接串无需改动。
```

---

## 四、分支策略

小团队不需要 Git Flow 那么重，用 **main + feature** 两级就够：

```
main ────●────────────●────────────●──────────►  始终可运行，只接受合并
          \          /  \          /
           ●───●────●    ●────●───●              功能分支，做完即合并删除
         feature/ods     fix/port-3307
```

| 分支 | 用途 | 谁能推 |
|---|---|---|
| `main` | 主干，任何时刻都应能跑通 `run_all` | **不直接推**，只通过 PR 合并 |
| `feature/<模块>-<简述>` | 新功能、新脚本 | 各自推 |
| `fix/<简述>` | 修 bug | 各自推 |
| `docs/<简述>` | 纯文档改动 | 各自推 |

### 4.1 命名规则

- 全小写，用连字符分隔：`feature/ods-load-script`
- 带上模块名，一眼能看出改哪个部分
- 别用 `dev1`、`test`、`a` 这种名字

### 4.2 标准五步

```powershell
# 1. 先同步主干，避免基于旧代码开发
git switch main
git pull origin main

# 2. 建分支并切过去
git switch -c feature/ods-load-script

# 3. 干活，分多次小提交
git add sql/ods/load_ods.sql
git commit -m "feat(ods): 新增 6 张表 LOAD DATA 导入脚本"

# 4. 推送自己的分支
git push -u origin feature/ods-load-script

# 5. 到 GitHub 提 PR，合并后删掉本地分支
git switch main
git pull origin main
git branch -d feature/ods-load-script
```

**为什么不在 main 上直接改？** 两个人同时改 main 必然冲突，而且主干一旦被推了半成品，别人拉下来就跑不起来。分支隔离是成本最低的解决办法。

---

## 五、Pull Request 流程

### 5.1 提 PR 前自检

```powershell
git switch main
git pull origin main
git switch -
git merge main            # 把主干最新代码合进来，提前解决冲突
git push
```

先在自己分支上合并 main，冲突就由你解决——而不是让 review 的人替你收拾。

### 5.2 PR 描述

仓库里已有模板 `.github/PULL_REQUEST_TEMPLATE.md`，提 PR 时自动带出。至少写清三件事：

1. **做了什么**（对应哪个子任务）
2. **怎么验证的**（跑了什么命令、看到什么结果）
3. **有没有影响别人**（是否改了公共脚本、表结构、端口）

### 5.3 合并方式：用 `--no-ff`

团队统一使用**非快进合并**，这样历史里能看出"曾经有过一个分支"，而不是所有提交平铺成一条线：

```powershell
git switch main
git merge --no-ff feature/ods-load-script -m "merge: 合并 feature/ods-load-script（ODS 导入脚本）"
```

合并后分支可以删，但合并节点会留在 `git log --graph` 里——这正是「提交历史清晰」的加分项。

### 5.4 Code Review（第 3 周起强制）

- 每个 PR 至少 **1 人 review 通过**才能合并
- 作者不能自己批准自己的 PR
- review 意见要具体到行，不要只说"看起来没问题"

---

## 六、冲突处理

冲突不可怕，可怕的是慌了乱点。原则：**永远不要用 `git checkout --theirs` 一把梭**，那会丢掉别人的改动。

```powershell
git merge main
# 冲突文件会标记为 both modified
git status                       # 看哪些文件冲突

# 打开冲突文件，会看到这几行：
# <<<<<<< HEAD
# 你写的
# =======
# 别人写的
# >>>>>>> main

# 手工改成最终想要的版本，删掉 <<<< ==== >>>> 三行标记
git add 冲突文件
git commit                       # 完成合并
```

不想手工改可以放弃这次合并：

```powershell
git merge --abort
```

**冲突高发区**（本项目实际情况）：

| 文件 | 为什么容易冲突 | 预防 |
|---|---|---|
| `docker-compose.yml` | 大家都要加挂载、改端口 | 只由一人负责，其余人提需求 |
| `README.md` | 都往里补内容 | 按段落分工，别都改同一段 |
| `sql/ods/create_ods.sql` | 各自加自己的表 | 一人一表一个文件，或按表分包 |

---

## 七、绝对不进版本库的东西

`.gitignore` 已经覆盖，这里说明原因，答辩会问：

| 内容 | 为什么不能进库 |
|---|---|
| `data/*.csv`、`*.csv` | 体积大（本项目 6 个文件约 20 MB），且属于企业数据资产；Git 会永久保存每个版本，仓库会被撑爆 |
| `.env` | 含密码、密钥。一旦推到公开仓库，只能算泄露 |
| `*.log`、`.tmp/` | 运行时产物，每次都不一样，提交了全是噪音 |
| `__pycache__/`、`node_modules/` | 可重新生成的依赖，不该入库 |

**.env.example 是例外**，它只有键名没有值，必须入库，队友靠它知道要配哪些变量。

如果不小心提交了 CSV，见下一节。

---

## 八、三种常见事故怎么救

### 8.1 误提交了大文件 / 密钥（还没 push）

```powershell
git rm --cached data/ods_order.csv     # 从暂存区移除，文件本身不删
git commit --amend -m "feat(ods): 新增导入脚本"
```

### 8.2 提交信息写错了（还没 push）

```powershell
git commit --amend -m "fix(ods): 修正表头跳过参数"
```

**已经 push 的就别 amend**，会改写历史，队友拉取会冲突。直接补一次新提交说明即可。

### 8.3 想撤销最近一次提交但保留改动

```powershell
git reset --soft HEAD~1
```

改动仍在暂存区，重新组织后可以分两次提交。

---

## 九、提交信息格式校验（自动化）

仓库自带 `commit-msg` 钩子，格式不对会直接拒绝提交。安装一次即可（钩子本身不入库，需每人装）：

```powershell
.\tools\install-hooks.ps1
```

装好后，下面这种提交会被拦下来：

```
$ git commit -m "更新"
[提交被拒绝]
提交信息不符合规范：缺少 type 前缀
正确格式：<type>(<scope>): <subject>
可用 type：feat fix docs style refactor perf test build ci chore revert
示例：feat(ods): 新增订单表建表脚本
```

紧急情况确实要绕过（不建议）：

```powershell
git commit --no-verify -m "..."
```

---

## 十、答辩时的自检与演示

```powershell
"=== 提交规范合规率、敏感文件、作者分布 ==="
python tools/check_repo.py
```

这个脚本输出一张评分卡，逐项对应「Git 规范 20 分」的观察点。答辩现场跑一次，比口头说"我们规范很好"有说服力。

再补一条最能说明问题的命令：

```powershell
git log --graph --oneline --all --decorate
```

这条能看到分支分叉、合并节点、每次提交的 type 前缀——一张图讲完整个协作过程。
