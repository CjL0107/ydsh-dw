# 第 3 周 · 大模型 API 接入与密钥管理

> 目标：拿到一个可用的 API Key，配到环境变量，跑通一次最小调用，并且**保证 Key 不会进版本库**。
> 代码侧已就绪：`python/llm_client.py`（客户端）+ `python/nl2sql.py`（NL2SQL）+ `python/ai_insight.py`（洞察）。

---

## 一、怎么拿到 API Key

国内学生最省事的是 **DeepSeek**：注册即送额度、OpenAI 兼容协议、不需要备案。以下按 DeepSeek 写，
通义千问 / 智谱 / Moonshot 的步骤基本一致，只是控制台地址不同。

### DeepSeek（推荐）

1. 打开 <https://platform.deepseek.com/>，用手机号注册并登录；
2. 左侧菜单进 **「API keys」** → 点 **「创建 API key」**；
3. 起个名字（比如 `ydsh-dw`），确认后**立刻复制**生成的 Key。
   - 格式形如 `sk-` 开头的一长串；
   - **只显示这一次**，关掉弹窗就再也看不到了，看不到只能重新创建；
4. 同一页面的 **「充值」** 里确认余额（新账号通常有赠送额度，够本次实训用）。

### 其他可选

| 服务商 | 控制台 | 免费额度 | 对应 `.env` 配置 |
| --- | --- | --- | --- |
| DeepSeek | platform.deepseek.com | 注册赠送 | `LLM_PROVIDER=deepseek` |
| 通义千问 | dashscope.console.aliyun.com | 每模型有免费 token | `LLM_PROVIDER=qwen` |
| 智谱 GLM | open.bigmodel.cn | 新用户赠送 | `LLM_PROVIDER=zhipu` |
| Moonshot | platform.moonshot.cn | 新用户赠送 | `LLM_PROVIDER=moonshot` |

换服务商时**只需要改 `LLM_PROVIDER`**，`llm_client.py` 里有内置的 base_url 映射，不用改代码。

---

## 二、把 Key 配进去

### 第 1 步：复制模板

```bash
cd /c/Users/Lenovo/Desktop/ydsh-dw
cp .env.example .env
```

### 第 2 步：编辑 `.env`

把 `LLM_API_KEY=` 后面填上刚复制的 Key：

```ini
LLM_PROVIDER=deepseek
LLM_API_KEY=sk-你的真实Key
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-flash
```

> 模型名别照抄网上的老教程。以 `python python/llm_client.py --models` 实际探到的为准，
> 配置的名字失效时客户端会自动降级，但报告里应该写真实调用的那个。

### 第 3 步：确认 `.env` 不会被提交

`.gitignore` 里已有这两条：

```
.env
.env.*
!.env.example
```

验证一下：

```bash
git check-ignore -v .env
```

**正确的结果是"有输出"**——打印出命中的规则，说明 `.env` 确实被忽略了：

```
.gitignore:17:.env	.env
```

如果**没有任何输出**、退出码为 1，说明 `.env` 没被忽略，**这是危险的**，先别提交，
回头检查 `.gitignore` 里 `.env` 那两行是不是被别的规则覆盖了。

---

## 三、验证连通性

### 最小调用自检

```bash
python python/llm_client.py
```

**没配 Key 时**会明确跳过（不是报错崩掉）：

```
model     = deepseek-flash
api_key   = 未配置
候选模型  = deepseek-flash, deepseek-v4-pro, deepseek-chat

[跳过] 未检测到 LLM_API_KEY。
       配置方式：在仓库根目录执行  cp .env.example .env
       然后把 .env 里的 LLM_API_KEY 填成真实 Key，再重跑本脚本。
```

**配好 Key 后**预期输出：

```
model     = deepseek-flash
api_key   = 已配置（长度 35，末四位 ****79e9）
候选模型  = deepseek-flash, deepseek-v4-pro, deepseek-chat

发起最小化调用 ...
[成功] 耗时 1.23s  实际使用模型 deepseek-flash
       返回：已就绪，随时可以开始。
       tokens：prompt=38, completion=9
```

看到 `[成功]` 就说明 Key、网络、协议三项都通了。

### 先分清「Key 无效」和「账户没钱」

这两个错误报出来完全不一样，但都表现为"调不通"，第一次接很容易搞混：

| 返回 | 含义 | 怎么确认 | 怎么办 |
|------|------|----------|--------|
| `401` | Key 无效 / 已被删除 | `curl https://api.deepseek.com/v1/models -H "Authorization: Bearer $KEY"` 也报 401 | 回控制台重新建 Key |
| `402` | **Key 有效，但账户余额为 0** | 同一个 Key 能正常拉到模型列表，只有 `chat/completions` 报 402 | 去 `https://platform.deepseek.com/top_up` 充值 |

判断技巧：**能拉到模型列表 = 鉴权一定通过**，此时若对话接口失败，问题在余额而不在密钥。
查余额：

```bash
curl -s https://api.deepseek.com/user/balance -H "Authorization: Bearer $LLM_API_KEY"
# {"is_available":false,"balance_infos":[{"currency":"CNY","total_balance":"0.00",...}]}
```

`is_available:false` + `total_balance:0.00` 就是需要充值。本客户端已把 402 翻译成这句提示，
不会再让人对着 SDK 的原始异常发愣。

### 模型名会变，别写死在代码里

实测发现服务商下线的模型名会让整条链路 404，而这个错误长得像"配置错了"。
所以 `llm_client.py` 里给了候选列表 `MODEL_CANDIDATES`，配置的模型名不可用时自动降级到下一个，
并把**实际使用的模型名**记进 trace，报告里写的就是真实调用的那个。

探一遍哪些模型可用（每次只消耗 4 个 token）：

```bash
python python/llm_client.py --models
```

### 跑 NL2SQL

```bash
python python/nl2sql.py -q "广东省上月销售额是多少？"
python python/nl2sql.py -q "销量最高的前5个品类" --explain
```

### 跑 AI 洞察

```bash
python python/ai_insight.py --date 2024-11-06
```

输出里「归因叙述（来源：…）」那行会从"规则模板"变成"大模型 deepseek-flash"。

### 批量验证：把「能跑」变成「有证据」

单跑一道题说明不了效果，用 `validate_llm.py` 跑固定题集，输出逐题的 SQL、耗时、tokens、
以及与人工核定参考 SQL 的一致性：

```bash
python python/validate_llm.py --insight
```

结果写到 `python/outputs/llm_eval.md`（可贴报告）和 `llm_eval.json`（机器可读）。

**注意判据的边界**：脚本判的是"结果是否与参考 SQL 一致"，不是"SQL 语义是否正确"。
两者不等价——比如时间过滤写漏了但恰好那天是全年唯一极值，结果照样对得上。
所以报表里留了一列「人工复核」，由人看图判断，程序不替人下结论。

---

## 四、密钥管理的四条硬规则

1. **Key 只放 `.env`，代码里永远不出现字面量。**
   `llm_client.py` 里只有 `os.getenv("LLM_API_KEY")`，没有任何 fallback 默认值——配错了就直接报错，
   而不是悄悄用一个写死的 Key。

2. **`.env` 必须在 `.gitignore` 里。** 已配好，并且用 `git check-ignore -v .env` 自查过。

3. **日志和截图里不能出现完整 Key。** 报告里如果要展示配置，一律写成 `sk-****`。
   本周所有文档里的 Key 都是脱敏的。

4. **Key 泄露了立刻去控制台删除重建。** 不要试图"改一下密码"，API Key 只能作废重发。
   如果曾经误提交过，光删文件没用——`git log` 里还留着，必须用 `git filter-repo` 清理历史并强制推送，
   同时立刻作废该 Key。

---

## 五、没有 Key 时会发生什么

链路不会崩，会自动降级：

| 场景 | 行为 |
| --- | --- |
| `LLM_API_KEY` 为空 | `llm_client.available()` 返回 False，NL2SQL 走规则兜底，洞察走模板兜底 |
| Key 填错 / 余额不足 | 调用抛异常，`ai_insight.py` 捕获后转模板，并在日志里打印失败原因 |
| 网络不通 | 同上，不会卡死（客户端有 60s 超时） |

这个设计是刻意的：**演示现场最怕的不是效果差，而是当场报错跑不下去。** 降级路径保证任何情况下
都能把"提问 → SQL → 结果"这条闭环走完，只是 SQL 由规则拼而不是模型生成。

但要注意：**降级模式不能算作"完成了子任务 3"**。任务要求的是真实调用，报告里必须区分这两种状态，
不能拿规则兜底的结果冒充模型效果。当前 `docs/第3周_AI智能洞察与反思.md` 里已经明确标注了这一点。

---

## 六、成本量级（给答辩用）

按本次实训的调用量估算：

| 项目 | 单次 token | 调用次数 | 合计 |
| --- | --- | --- | --- |
| NL2SQL（Schema 约 3,000 token + 问题） | ~3,200 | 20 | 64,000 |
| AI 洞察（事实清单 + 400 字输出） | ~1,200 | 10 | 12,000 |
| 合计 | | | 约 7.6 万 token |

DeepSeek 定价约 1 元 / 百万 input token 量级，**整个实训的 API 成本在 1 毛钱以内**。
这个数字值得在答辩时说出来——很多人以为接大模型很贵，实际瓶颈从来不是钱。
