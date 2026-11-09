# 第 3 周 · Fork / Pull Request / Code Review 网页端操作步骤

> 前提：本地已完成分支开发并推送。仓库：`github.com/CjL0107/ydsh-dw`
> 已推送分支：`feature/w3-aov-nullfix`（提交 `2e75afd`）
> 本文按"点哪里、填什么、看到什么"的顺序写，照着点即可。

---

## 一、先确认分支已经在远端

打开 <https://github.com/CjL0107/ydsh-dw/branches>，应该能看到三个分支：

| 分支 | 最新提交 |
| --- | --- |
| `main` | `cf433b9` merge(ads): 合并客单价除零修复与精度统一 |
| `feature/w3-aov-nullfix` | `2e75afd` fix(ads): 客单价分母加 NULLIF 避免除零 |
| `feature/git-standard` | 第 1 周留下的 |

如果看不到 `feature/w3-aov-nullfix`，先在本地重推一次：

```bash
bash tools/push.sh origin feature/w3-aov-nullfix
```

---

## 二、创建 Pull Request（约 2 分钟）

1. 打开仓库首页 <https://github.com/CjL0107/ydsh-dw>；
2. 页面顶部偏右会出现一条黄色提示条：**"feature/w3-aov-nullfix had recent pushes less than a minute ago"**，
   右边有绿色按钮 **「Compare & pull request」**，点它。
   - 如果没有这条提示条，点 **「Pull requests」** 标签页 → 绿色 **「New pull request」** →
     **base** 选 `main`，**compare** 选 `feature/w3-aov-nullfix`；
3. 进入填写页，确认顶部显示 `base: main ← compare: feature/w3-aov-nullfix`（方向别反）；
4. 标题填：

   ```
   fix(ads): 客单价分母加 NULLIF 避免除零，并统一精度
   ```

5. 描述框粘贴下面这段（可直接复制）：

   ```markdown
   ## 变更内容
   修复 ADS 层客单价计算的除零风险，并统一精度。

   ## 背景
   `aov = SUM(实付金额) / SUM(有效订单数)`，当某天没有有效订单时分母为 0，
   Hive 会抛除零异常导致整条 ETL 中断。

   ## 改动
   - 分母包 `NULLIF(..., 0)`，无有效订单时返回 NULL 而不是报错
   - 外层 `CAST AS DECIMAL(14,2)`，与 DWS 层 aov 精度对齐

   ## 验证
   重跑 `sql/ads/01_etl_ads.sql` 全流程通过，KPI 与改动前一致：
   GMV 27,342,828.20 / 有效订单 10,809 / AOV 2,529.64

   ## 影响范围
   仅 `ydsh_ads` 层 aov 字段，不改口径，历史结果不变。
   ```

6. 右侧 **「Reviewers」** 点齿轮 → 如果只有你自己一个账号，选自己（GitHub 不允许给自己的 PR 点
   Approve，但可以留 Comment，这本身就是"自我 Review"的留痕）；
7. 右侧 **「Labels」** 加 `bug`；如果有 **「Milestone」** 可以建一个 `第3周` 挂上；
8. 点绿色 **「Create pull request」**。

创建成功后地址类似 `https://github.com/CjL0107/ydsh-dw/pull/3`，**把这个链接记下来**，
答辩要展示"PR 记录可追溯"。

---

## 三、做一次 Code Review（约 3 分钟）

进入刚创建的 PR 页面：

1. 点 **「Files changed」** 标签，能看到 `sql/ads/01_etl_ads.sql` 的 diff（红色删除、绿色新增）；
2. 鼠标移到某一行左侧行号，出现蓝色 **「+」**，点开可以写行内评论；
   建议至少在 `NULLIF` 那一行留一条，内容例如：

   ```
   这里 NULLIF 是必要的：11-06 那天有效订单只剩 18 单，如果某天降到 0，
   分母为 0 会让整条 ETL 抛异常中断，而不是返回空值。
   ```

3. 点右上角 **「Review changes」**（绿色按钮旁的下拉）；
4. 在弹出框里选 **「Comment」**（不要选 Approve，GitHub 不允许自己批准自己的 PR），
   写一段总结：

   ```
   自审结论：
   1. 改动范围仅 aov 一个表达式，不影响其他指标；
   2. 合并前重跑了整条 ADS ETL，GMV / 订单量 / AOV 三项与改动前一致；
   3. 遗留风险：若某天有效订单为 0，aov 会是 NULL 而不是 0，
      下游报表需要能处理 NULL，这一点在 ADS 层建表时已用 DECIMAL 允许 NULL。
   ```

5. 点 **「Submit review」**。

---

## 四、合并 PR

1. 回到 **「Conversation」** 标签；
2. 底部绿色 **「Merge pull request」** → 选 **「Create a merge commit」**
   （保留合并节点，便于展示冲突解决过程；不要选 Squash，会把两次提交压掉）；
3. 确认提交信息，点 **「Confirm merge」**；
4. 合并后点 **「Delete branch」** 删掉远端分支（本地分支保留，作为记录）。

---

## 五、用 Issue 追踪任务（约 3 分钟）

打开 <https://github.com/CjL0107/ydsh-dw/issues> → 绿色 **「New issue」**。

建议按 `docs/第3周_Git协同实践记录.md` 里的 7 条建 Issue，**建完直接关闭**（因为已经修完了），
标题和标签如下：

| 标题 | 标签 | 备注 |
| --- | --- | --- |
| Hive describe 中文注释显示为 ? | `bug` | 正文里贴 `docs/第3周_Git协同实践记录.md` 第四节那段排查链 |
| 预测模型特征重要度输出为空 | `bug` | |
| NL2SQL 时间序列提问被单指标分支拦截 | `bug` | |
| 异常归因渠道基线口径错误 | `bug` | |
| 日序列取数被 200 行上限截断 | `bug` | |
| push.sh 探测判据改用 git ls-remote | `enhancement` | |
| 客单价分母除零风险 | `bug` | 关联 PR #3，正文写 `Closes #3` 之类 |

**关闭 Issue 的技巧**：如果想让 Issue 和 PR 自动关联，在 Issue 正文或 PR 描述里写
`Closes #编号`，合并 PR 时 Issue 会自动关闭——这是最能体现"可追溯"的细节，答辩可以讲。

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
4. **Issues 页面** — 展示 7 条 Issue 全关闭；
5. **本地终端** — `git log --oneline --graph -8`，展示合并冲突那个分叉；
6. **说一句关键的**：单人实训里 Code Review 是自己审自己，价值不在"别人挑错"，
   而在**强制在提交前把改动逐行讲一遍理由**——本周 6 条缺陷里有 3 条是跑起来不报错的，
   只在逐行解释时才发现。

---

## 八、如果卡住了

| 现象 | 处理 |
| --- | --- |
| 首页没有黄色 "Compare & pull request" 提示条 | 直接走 Pull requests → New pull request，手选 base/compare |
| PR 页面显示 "There isn't anything to compare" | 说明两个分支内容一致，检查 compare 分支是否选错 |
| 提示 "A pull request already exists" | 已经有 PR 了，去 Pull requests 里找，不用新建 |
| Review 页面的 Approve 灰掉 | 正常，GitHub 禁止批准自己的 PR，选 Comment 即可 |
| 合并按钮显示 "This branch has conflicts" | 本地 `git pull origin main` 解决冲突后再推 |
