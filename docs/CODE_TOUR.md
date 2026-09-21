# 读代码指南

按这个顺序读，每个文件都建立在前一个的基础上。标 ✅ 的已经一起读过。

## 整体流程

```
Data/raw/（5 个平台的原始导出）
   │  ① 解析：aiusage/parsers/*，由 run_pipeline.py 调用
   ▼
Data/processed/
   sessions_meta_<平台>.parquet   每个 session 一行
   messages_<平台>.parquet        每个内容块一行
   │  ② 打 tag：tagging.py prepare → 子代理打 tag → tagging.py merge
   ▼
Data/processed/session_tags.parquet
   │  ③ 建库：build_db.py 运行 sql/01_load.sql、sql/02_sessions.sql
   ▼
Data/aiusage.duckdb（messages、sessions_meta、session_tags、sessions 四张表）
   │  ④ 报告：report.py 运行 sql/analysis/*.sql
   ▼
reports/tables/*.csv（公开）、reports/figures/*.png（公开）、Data/analysis/*.csv（私有完整版）
```

`python run_pipeline.py` 一次跑完 ①③④，② 只合并已有的打 tag 结果，不会重新调用模型。

**核心设计**：5 个平台的格式完全不同，但每个解析器都输出**同样的两张表**。所以后面的 SQL 只写一次，就能同时处理所有平台。

## 第一部分：解析

### 1. `aiusage/common.py` ✅

| 部分 | 作用 |
|---|---|
| `SESSION_COLUMNS` / `MESSAGE_COLUMNS` | 两张输出表的列定义，统一格式就定在这里 |
| `session_row()` / `message_row()` | 解析器用来"造一行"的函数，保证每行的键都一样 |
| `parse_iso()` / `from_epoch()` | 各种时间格式统一转成 UTC |
| `count_tokens()` | 用 tiktoken 估算 token；超长文本只算前 5 万字再按比例放大 |
| `save()` | 收尾：去掉空 session、排序、加 `seq`、算 token、截断工具文本、**指定列类型**、存 Parquet |

`save()` 最后给每列指定类型，是后来补上的：网页版的 `project_path` 整列为空，不指定类型的话，存出来是 NULL 类型，和 CLI 平台的字符串类型对不上，DuckDB 就没法用通配符一次读进来。

### 2. `aiusage/parsers/claude_web.py` ✅

三层循环（对话 → 消息 → 块），加上 `current_branch()` 处理分支、`BLOCK_TEXT_KEY` 查表取正文。

### 3. `aiusage/parsers/chatgpt.py` ✅

`visible_path()` 树回溯；`seen_message_ids` 去掉"分支到新对话"复制的消息。

### 4. `aiusage/parsers/gemini.py` ✅

BeautifulSoup 解析 HTML 卡片，以时间戳为界拆出提问和回答，正则提取对话 ID 后分组。

### 5. `aiusage/parsers/claude_code.py` ✅

| 坑 | 代码位置 |
|---|---|
| "继续对话"会把历史复制进新文件 | `record_owner` 字典 + `owner = next(...)` 那段合并逻辑 |
| 一次回复拆成多条记录，usage 重复 | `counted_message_ids` |
| user 记录大多不是你打的字 | `NOT_TYPED_BY_USER` + `user_blocks()` |
| 忙碌时排队发送的提问藏在附件里 | `queued_command` 那个分支 |

### 6. `aiusage/parsers/codex.py`（跳过了，结构和 Claude Code 类似）

| 坑 | 代码位置 |
|---|---|
| 一个线程分散在多个文件里 | `files_by_thread()` |
| 不是你发起的线程（自动审查、子代理） | `SKIP_THREAD_SOURCES` |
| 用户消息里夹着注入的上下文 | `typed_user_text()`：找 "## My request:" 标记 |
| 从 Claude Code 导入的副本 | `excluded_thread_ids()` 读 `Data/excluded/` 里的清单 |
| 每次恢复都重复写入的记录 | 按整条记录的指纹（md5）去重 |

## 第二部分：打 tag

### 7. `aiusage/tagging.py` + `aiusage/tagging_guide.md`

1. `prepare`：给每个 session 做摘要（标题 + 前 3 条和后面 2 条提问，每条 200 字），分成 5 批
2. 子代理打 tag：先用 252 个样本建一个共用的 tag 库，5 个子代理再按同一个库打，避免同义词分裂
3. `merge`：校验、统一拼写、加上从项目文件夹名派生的 `project:` tag，输出 `session_tags.parquet`。`project_name()` 会排除 `Desktop\claude` 这类通用工作文件夹，以及 Codex 按提问自动生成的临时文件夹

`Data/private_sessions.csv`：写进去的 session 不会被发给模型。

### 7b. `aiusage/tag_api.py` + `aiusage/llm/`（用 API 打 tag）

第 2 步的脚本版，读写的文件和子代理完全一样，所以 `merge` 不用改。

| 文件 | 作用 |
|---|---|
| `llm/__init__.py` | 统一接口：`Usage`（两家的 token 用量换算成同一口径）、`ModelRefused`、`get_backend()` 按 `--provider` 选后端 |
| `llm/claude_backend.py` | Claude：结构化输出 `output_config.format`、system 提示词加 `cache_control` 缓存、拒答时服务端自动换模型重试（`fallbacks="default"`） |
| `llm/openai_backend.py` | OpenAI Responses API：`text.format` 严格 JSON schema、`store=False`、识别 `refusal` 内容和 `incomplete` 状态 |
| `llm/openai_compatible_backend.py` | Gemini、通义、Kimi、豆包、DeepSeek、智谱、MiniMax 和任意兼容服务。`PRESETS` 里每家一组预设（地址、密钥变量、JSON 模式、token 参数名、额外字段）。严格 schema 被拒就逐级降到 JSON 模式、再到纯提示词；去掉 `<think>` 和代码块标记；拿到的 JSON 自己再按 schema 校验一遍 |
| `tag_api.py` | 与厂商无关的核心：建 tag 库、每 30 个 session 一个请求、4 个并行、漏掉的 session 补打一次、失败的块跳过不中断、已有输出就只补缺的（断点续跑） |

运行方式：

```
python -m aiusage.tag_api tag --provider claude --dry-run    # 只数请求数，不发送
python -m aiusage.tag_api tag --provider claude
python -m aiusage.tag_api tag --provider openai --model <模型名>
python -m aiusage.tag_api tag --provider deepseek --model <模型名>     # 其他：gemini qwen kimi doubao glm minimax
python -m aiusage.tag_api tag --provider kimi --model <模型名> --base-url https://api.moonshot.cn/v1   # 换国内地址
```

`tests/test_tag_api.py` 用假的客户端测试，不联网、不花钱。

## 第三部分：SQL

### 8. `aiusage/build_db.py`

很短：连接 `Data/aiusage.duckdb`，按文件名顺序运行 `sql/` 下的 `01_load.sql`、`02_sessions.sql`。

`SET file_search_path` 让 SQL 里的相对路径（`Data/processed/...`）总是从项目根目录算起，所以这些 SQL 文件复制到任何 DuckDB 客户端里也能直接运行。

### 9. `sql/01_load.sql`

三条 `CREATE OR REPLACE TABLE ... AS SELECT * FROM read_parquet('...')`。通配符 `messages_*.parquet` 一次读进 5 个平台。

### 10. `sql/02_sessions.sql`（SQL 部分最重要的文件）

用两个 CTE（`WITH ... AS`）分步骤算：
- **`message_stats`**：按 session 分组，用 `FILTER (WHERE ...)` 分别统计不同条件下的 token 和轮数
- **`first_prompt`**：`QUALIFY ROW_NUMBER() OVER (PARTITION BY session_id ORDER BY seq) = 1` 取每个 session 的第一条提问
- **最后 `JOIN`** 回 `sessions_meta`，只保留 `user_turns >= 1` 的

每一列的定义见 `docs/sql_tasks.md`，结果和那里的 pandas 标准答案逐格一致。

### 11. `sql/analysis/01–17_*.sql`

每个文件回答一个问题，文件开头的注释写了问题是什么。按用到的 SQL 技巧分：

| 技巧 | 文件 |
|---|---|
| 基础 `GROUP BY` + 聚合 | 01、02、06、11、12 |
| 时间截断（`DATE_TRUNC`、`HOUR`、`ISODOW`） | 03、05、09 |
| `CASE WHEN` 分桶 | 07 |
| 窗口函数算占比：`SUM() OVER (PARTITION BY ...)` | 04、07、10、13 |
| 窗口函数编号：`ROW_NUMBER() OVER (...)` | 08 |
| 窗口函数累计：`SUM() OVER (ORDER BY ...)` | 15 |
| 自连接 | 14 |
| 两个 CTE + `CROSS JOIN` 算 lift | 14、16 |
| `HAVING` 过滤分组 | 14、17 |
| `QUALIFY` 过滤窗口函数结果 | 13 |

**建议的读法**：从 02、06 这种简单的开始，每读一个就在 DuckDB 里运行一遍看结果，再读 04、08、14 这几个用了窗口函数和自连接的。

## 第四部分：报告

### 12. `aiusage/report.py`

- **`run_queries()`**：运行所有分析 SQL，完整结果存到 `Data/analysis/`（私有），去掉个人话题后的结果存到 `reports/tables/`（公开）
- **`PERSONAL_TAGS`**：外貌、健康、感情类话题，不进入公开的表格和图。**上传 GitHub 前你自己检查一遍这个集合**
- **9 个 `fig_*` 函数**：每个画一张图，浅色和深色各一版
- 图表规范：每个平台固定一个颜色（顺序经过色盲校验）、柱子 24px 以内、数据端 4px 圆角、堆叠之间 2px 间隙、网格线用最细的实线、文字不用系列颜色

## 第五部分：测试和入口

### 13. `tests/test_parsers.py`

- **任何数据都必须满足的**：主键唯一、角色/类型合法、时间合理、"用户输入"里没有系统文本、5 个平台的 Parquet 列类型完全一致
- **这份数据的精确数字**：之前探索数据时独立算出来的（75、1846、592……）
- **tag**：每个 session 恰好一个 activity tag、拼写规范

### 14. `tests/test_sql.py`

用 pandas 从 Parquet 独立重算每个 session 的轮数、token、开始时间、第一条提问，和 SQL 建的 `sessions` 表**逐个 session 对比**。这就是计划文档里"SQL 写一遍、pandas 算一遍对照"的方法。

### 14b. `tests/test_tag_api.py`

用假的客户端（Claude、OpenAI，以及每家兼容接口的预设）检查请求参数、JSON 模式降级、`<think>` 清理、拒答和截断的处理，以及核心逻辑的补打、跳过、断点续跑。

运行 `python -m pytest -q`，应该是 **61 passed**。

### 15. `run_pipeline.py`

四步：解析 → 合并 tag → 建库 → 报告。

## 文档

| 文件 | 内容 |
|---|---|
| `README.md` | 项目首页（英文）：发现、流程、数据质量、数据模型、运行方法 |
| `docs/data_quality.md` | 所有发现的数据问题和处理方式 |
| `docs/sql_tasks.md` | `sessions` 表每一列的定义，附 pandas 标准答案 |

## 面试时可能被问到的

1. 五个平台格式完全不同，你怎么让后面的分析只写一套？
2. 你是怎么发现 Codex 里一半数据是重复的？怎么处理的？
3. "轮数"为什么不能直接比较？你用什么指标代替？
4. token 是怎么算的？网页版没有 token 数据怎么办？为什么不能把每轮的 `cache_read` 加起来？
5. 你怎么保证清洗结果和 SQL 结果是对的？（独立核算的数字 + SQL 和 pandas 逐个对比）
6. 打 tag 为什么先建 tag 库再分批打？怎么检查 tag 质量？
7. "Claude 占比上升"这个结论有什么数据上的局限？（Claude Code 只保留约 30 天记录）
8. 共现分析里的 lift 是什么意思？为什么不直接看共同出现的次数？
9. 隐私怎么处理？（`.gitignore`、`PERSONAL_TAGS`、`private_sessions.csv`、只发摘要不发全文）
