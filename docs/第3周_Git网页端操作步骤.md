# 第 3 周 · Fork / Pull Request / Code Review 网页端操作步骤

> 前提：本地已完成分支开发并推送。仓库：`github.com/CjL0107/ydsh-dw`
> 已推送分支：`feature/nl2sql-dim-values`（领先 `main` 若干提交，具体数字以页面为准；本地用 `git log --oneline origin/main..HEAD` 核对）
> 本文按"点哪里、填什么、看到什么"的顺序写，照着点即可。

---

## 零、先解释上次为什么建不出 PR

上次试着为 `feature/w3-aov-nullfix` 建 PR，页面提示
**"main is up to date with all commits from feature/w3-aov-nullfix"**。
这不是网络或权限问题，是**那个分支没有独有的提交**——`2e75afd`（客单价 NULLIF 修复）
早已在 `main` 的历史里，分支和主干指向的内容一致，自然"没有东西可比较"。

可以用命令自己确认这一点：

```bash
git merge-base --is-ancestor 2e75afd main && echo "已并入 main"
```

GitHub 上也能直接看：<https://github.com/CjL0107/ydsh-dw/compare/main...feature/w3-aov-nullfix>
会显示 "There isn't anything to compare"。

所以这次换成真正有独有提交的分支来走 PR 流程。**建 PR 前先确认 `ahead_by > 0`**，
这是比"点按钮"更重要的一步：

```bash
git log --oneline origin/main..feature/nl2sql-dim-values
```

---

## 一、先确认分支已经在远端

打开 <https://github.com/CjL0107/ydsh-dw/branches>，应该能看到：

| 分支 | 最新提交 |
| --- | --- |
| `main` | `6b96d8d` docs: 周报更新为真实调用结果 |
| `feature/nl2sql-dim-values` | `7f8581e` docs: 改造记录补第七节 |
| `feature/w3-aov-nullfix` | `2e75afd` fix(ads): 客单价分母加 NULLIF（已并入 main） |
| `feature/git-standard` | 第 1 周留下的 |

远程仓库无法访问时，可能是本机代理端口变了。`tools/push.sh` 会自动扫描监听端口并逐个做
git 握手探测，直接用它重推即可，不用手改代理配置：

```bash
bash tools/push.sh origin feature/nl2sql-dim-values
```

---

## 二、创建 Pull Request（约 2 分钟）

1. 直接打开这个链接（已带好 base/compare，省掉手选）：
   <https://github.com/CjL0107/ydsh-dw/compare/main...feature/nl2sql-dim-values?expand=1>
   - 也可以走：仓库首页 → 点黄色提示条 **「Compare & pull request」**；
   - 或 **「Pull requests」** → 绿色 **「New pull request」** → **base** 选 `main`，
     **compare** 选 `feature/nl2sql-dim-values`；
2. 进入填写页，确认顶部显示 `base: main ← compare: feature/nl2sql-dim-values`（方向别反），
   并且下方列出 **N commits / M files changed**（N、M 均大于 0）——看不到提交就回到第零节；
3. 标题填：

   ```
   feat(nl2sql): 注入维度实际取值，并修复 schema 抽取丢失 42 列
   ```

4. 描述框粘贴 `docs/pr-nl2sql-dim-values.md` 的全文（可直接复制该文件内容）；
5. 右侧 **「Reviewers」** 点齿轮 → 如果只有你自己一个账号，选自己（GitHub 不允许给自己的 PR 点
   Approve，但可以留 Comment，这本身就是"自我 Review"的留痕）；
6. 右侧 **「Labels」** 加 `bug` 和 `enhancement`；如果有 **「Milestone」** 可以建一个 `第3周` 挂上；
7. 点绿色 **「Create pull request」**。

创建成功后地址类似 `https://github.com/CjL0107/ydsh-dw/pull/8`，**把这个链接记下来**，
答辩要展示"PR 记录可追溯"。

---

## 三、做一次 Code Review（约 3 分钟）

进入刚创建的 PR 页面，点 **「Files changed」**，重点看 `python/hive_runner.py` 的 diff。

鼠标移到某一行左侧行号，出现蓝色 **「+」**，点开写行内评论。建议在这几处留：

**① `describe()` 改用 tsv 那一行**

```
这里改的是根因，不是症状。Hive 的 DESCRIBE 输出类型名是 decimal(16,2)，
自带一个逗号；原来走 csv2，csv.reader 会把 gmv,decimal(16,2),GMV 切成 4 段，
与 3 列的表头对不齐，被 continue 掉。14 张表里 42 个 decimal 列就这么全没了，
包括所有表的 gmv / gmv_share / aov / metric_value。
换成 tsv2 后 Tab 分隔，逗号不再是分隔符。
```

**② `run_sql()` 里新增的 stderr 告警**

```
这一条比修复本身更重要。原来的兜底只在"一行都对不齐"时抛异常，
丢一部分完全无声；脚本打印"4 个字段"看着太正常，没人会去数一个 7 列的表
为什么只抽到 4 列。先叫出来，哪怕不中断执行。
```

3. 点右上角 **「Review changes」**（绿色按钮旁的下拉）；
4. 在弹出框里选 **「Comment」**（不要选 Approve，GitHub 不允许自己批准自己的 PR），
   写一段总结：

   ```
   自审结论：
   1. 改动分两块：维度值注入（新功能）+ describe 解析修复（bug）。
      bug 这块的影响面更大——它让 42 个 decimal 列从 schema 里消失，
      模型 NL2SQL 漏查 metric_value、选 order_cnt 而非 gmv_share，根因都在这里。
   2. 合并前重跑了整轮 8 题：执行 8/8、宽松一致 8/8、人工正确 7/8
      （改造前 5/8）。明细在 python/outputs/llm_eval.md。
   3. 遗留风险一：仍有 1 题部分正确（"销量"歧义未澄清）。
   4. 遗留风险二：两轮之间仍有 4 题 SQL 被改写，temperature=0 不等于确定性，
      所以 7/8 是单次运行结果，不能外推成 87.5%。
   5. 遗留风险三：describe 返回的列数会比以前多，如有下游按列数写死的逻辑需复核。
   ```

5. 点 **「Submit review」**。

---

## 四、合并 PR

1. 回到 **「Conversation」** 标签；
2. 底部绿色 **「Merge pull request」** → 选 **「Create a merge commit」**
   （保留合并节点，便于展示冲突解决过程；不要选 Squash，会把多个提交压成一个，
   提交规范钩子那条实践就看不见了）；
3. 确认提交信息，点 **「Confirm merge」**；
4. 合并后点 **「Delete branch」** 删掉远端分支（本地分支保留，作为记录）。

---

## 五、用 Issue 追踪任务（约 3 分钟）

打开 <https://github.com/CjL0107/ydsh-dw/issues> → 绿色 **「New issue」**。

建议按 `docs/第3周_Git协同实践记录.md` 里的 7 条建 Issue，**建完直接关闭**（因为已经修完了）。
这次新增两条，是本周真正的新发现：

| 标题 | 标签 | 备注 |
| --- | --- | --- |
| Schema 抽取丢失 42 个 decimal 列（gmv/aov/metric_value 全缺） | `bug` | 正文写清 `decimal(16,2)` 逗号被 CSV 切分这条链；关联 PR |
| NL2SQL 维度值等值匹配查空（广东/广东省、客单价/客单价 AOV） | `bug` | 关联 PR |
| Hive describe 中文注释显示为 ? | `bug` | 正文里贴 `docs/第3周_Git协同实践记录.md` 第四节那段排查链 |
| 预测模型特征重要度输出为空 | `bug` | |
| NL2SQL 时间序列提问被单指标分支拦截 | `bug` | |
| 异常归因渠道基线口径错误 | `bug` | |
| 日序列取数被 200 行上限截断 | `bug` | |
| push.sh 探测判据改用 git ls-remote | `enhancement` | |

**关闭 Issue 的技巧**：在 Issue 正文或 PR 描述里写 `Closes #编号`，合并 PR 时 Issue 会自动关闭——
这是最能体现"可追溯"的细节，答辩可以讲。注意编号要在 Issue 建好之后才拿得到，
所以 `Closes` 建议写在后续的补充评论里，而不是一开始就写。

---

## 六、打版本标签（可选，但答辩加分）

```bash
cd /c/Users/Lenovo/Desktop/ydsh-dw
git tag -a v0.3.0 -m "第3周：DWS/ADS 建设 + KPI + 预测建模 + NL2SQL 链路"
bash tools/push.sh origin v0.3.0
```

推送后在 <https://github.com/CjL0107/ydsh-dw/tags> 能看到标签；
点标签右侧 **「Create release」** 可以写发布说明，把本周 6 项交付物列上去。

---

## 七、答辩时的展示顺序（建议）

1. **Branches 页面** — 展示 main / feature 分支模型；
2. **PR 页面 → Files changed** — 展示 diff 与行内 Review 评论；
3. **PR 页面 → Conversation** — 展示合并记录与提交信息；
4. **Issues 页面** — 展示 Issue 全关闭；
5. **本地终端** — `git log --oneline --graph -8`，展示合并冲突那个分叉；
6. **说一句关键的**：单人实训里 Code Review 是自己审自己，价值不在"别人挑错"，
   而在**强制在提交前把改动逐行讲一遍理由**——本周 6 条缺陷里有 3 条是跑起来不报错的，
   只在逐行解释时才发现。最典型的就是这次：`describe` 少抽 42 列，
   从运行日志到脚本输出全是"正常"的。

---

## 八、如果卡住了

| 现象 | 处理 |
| --- | --- |
| 首页没有黄色 "Compare & pull request" 提示条 | 直接走 Pull requests → New pull request，手选 base/compare |
| PR 页面显示 "There isn't anything to compare" | 两个分支内容一致，先跑 `git log --oneline origin/main..<分支>` 确认有没有独有提交 |
| 提示 "A pull request already exists" | 已经有 PR 了，去 Pull requests 里找，不用新建 |
| Review 页面的 Approve 灰掉 | 正常，GitHub 禁止批准自己的 PR，选 Comment 即可 |
| 合并按钮显示 "This branch has conflicts" | 本地 `git pull origin main` 解决冲突后再推 |
| `git push` 报 502 / CONNECT tunnel failed | 代理端口变了，用 `bash tools/push.sh origin <分支>`，它会自动找可用端口 |
