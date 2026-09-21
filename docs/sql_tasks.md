# SQL 说明

这份文档原本是给你写 SQL 的任务说明，现在 SQL 已经实现：
- 读取和建 `sessions` 表：`sql/01_load.sql`、`sql/02_sessions.sql`
- 分析查询：`sql/analysis/`

文档保留下来，作为**每张表、每一列的定义**，以及**用 pandas 独立算出的标准答案**（`tests/test_sql.py` 自动核对）。想练手的话，可以不看 `sql/` 里的实现，照着这份说明自己写一遍，再和标准答案对。

## 你手上的表

`Data/processed/` 下有 11 个文件：

| 文件 | 一行代表 | 说明 |
|---|---|---|
| `sessions_meta_<平台>.parquet`（5 个） | 一个 session | 解析器能直接拿到的信息 |
| `messages_<平台>.parquet`（5 个） | 一个内容块 | 所有对话内容 |
| `session_tags.parquet` | 一个 session 的一个 tag | 打 tag 的结果 |

五个平台的文件列完全相同，可以用通配符一次读进来。

### sessions_meta 的列

| 列 | 含义 |
|---|---|
| `session_id` | 主键，五个平台之间也不重复 |
| `platform` | chatgpt / claude_web / gemini / claude_code / codex |
| `source` | web / cli |
| `session_ai` | chatgpt / claude / gemini（Codex 有 1 个是 deepseek） |
| `session_model` | 模型名，Claude 网页版和 Gemini 为空 |
| `session_title` | 标题，可能为空 |
| `project_path` | 只有 CLI 有 |
| `record_count` | 原始导出里的记录数 |
| `api_context_tokens` | 只有 CLI：最后一轮的真实上下文大小 |
| `api_output_tokens` | 只有 CLI：API 报告的真实输出 token 总数 |

### messages 的列

| 列 | 含义 |
|---|---|
| `session_id` | 外键 |
| `platform` | 同上 |
| `msg_id` | 原始消息 ID。一条消息可能拆成多个块 |
| `block_idx` | 块在消息里的位置 |
| `seq` | 块在整个 session 里的顺序，从 0 开始 |
| `ts` / `ts_local` | UTC 时间 / 香港时间 |
| `role` | user / assistant / tool |
| `kind` | text / thinking / tool_use / tool_result |
| `is_human_input` | 只有你真正打字输入的内容为 True |
| `text` | 内容（工具相关的块只保留前 2000 字） |
| `n_chars` | 完整内容的字数 |
| `n_tokens` | token 估算（所有平台用同一个分词器） |

### session_tags 的列

| 列 | 含义 |
|---|---|
| `session_id` | 外键 |
| `tag` | 标签 |
| `tag_source` | ai（模型打的）/ auto（从项目文件夹名派生） |
| `tag_type` | activity（在做什么，每个 session 恰好 1 个）/ topic（关于什么）/ project |

## 任务 1：把文件读进 DuckDB

建一个数据库文件 `Data/aiusage.duckdb`，里面三张表：`sessions_meta`、`messages`、`session_tags`。

**检查**：`sessions_meta` 1060 行；`messages` 106,860 行；`session_tags` 2462 行（activity 1054、topic 1391、project 17），覆盖 1054 个 session。

## 任务 2：建 `sessions` 表

每个 session 一行，列的定义：

| 列 | 定义 |
|---|---|
| `session_id`、`platform`、`source`、`session_ai`、`session_model`、`project_path`、`record_count` | 直接来自 `sessions_meta` |
| `session_title` | `sessions_meta` 的标题；为空时用第一条用户输入的前 40 个字 |
| `session_type` | cli → `coding`，web → `chat` |
| `created_at` | 这个 session 里最早的 `ts` |
| `last_message_at` | 最晚的 `ts` |
| `duration_days` | 两者相差的天数 |
| `user_turns` | `is_human_input` 为真的**不同 `msg_id` 的个数**（不是块数） |
| `user_tokens` | `is_human_input` 为真的块的 `n_tokens` 之和 |
| `assistant_tokens` | `role = 'assistant'` 且 `kind = 'text'` 的 `n_tokens` 之和 |
| `tool_tokens` | `kind` 是 tool_use 或 tool_result 的 `n_tokens` 之和 |
| `total_tokens` | CLI 用 `api_context_tokens`；它为空时（网页版）用除 thinking 以外所有块的 `n_tokens` 之和 |
| `first_user_message` | 第一条用户输入（按 `seq` 排序）的前 200 字 |

**只保留 `user_turns >= 1` 的 session。**

会用到的语法：`GROUP BY`、聚合函数、`FILTER` 或 `CASE WHEN`、`JOIN`、`COALESCE`，以及取"第一条"要用的窗口函数 `ROW_NUMBER()`。

### 标准答案（用 pandas 独立算出来的）

| platform | sessions | user_turns 总和 | user_turns 中位数 | ≤2 轮占比 | user_tokens | assistant_tokens | tool_tokens |
|---|---|---|---|---|---|---|---|
| chatgpt | 681 | 7500 | 4 | 39.5% | 160,604 | 3,653,107 | 0 |
| claude_code | 64 | 1056 | 2 | 51.6% | 68,949 | 815,701 | 15,821,646 |
| claude_web | 75 | 592 | 2 | 50.7% | 6,881 | 188,544 | 763,549 |
| codex | 64 | 402 | 2 | 54.7% | 20,521 | 235,222 | 14,812,155 |
| gemini | 170 | 2773 | 5 | 31.2% | 43,832 | 1,843,526 | 0 |
| **合计** | **1054** | | | | | | |

你的 SQL 结果和这张表逐格对上，`sessions` 表就是对的。

## 任务 3：分析查询（计划文档里的）

每个查询先写 SQL，再用 pandas 算一遍对照：

| 问题 | 主要语法 |
|---|---|
| 各平台的 session 数、token 总量 | `GROUP BY` |
| 按月的使用趋势（用 `ts_local`） | 日期截断 + `GROUP BY` |
| 一天中几点、星期几最常提问 | 从时间里取小时/星期 |
| 弃坑率：≤2 轮的 session 占比 | `CASE WHEN` 或 `FILTER` |
| 每个 session 第 N 次提问的平均长度 | `ROW_NUMBER() OVER (PARTITION BY ... ORDER BY ...)` |
| 各 activity / topic tag 在各平台的分布 | `JOIN` sessions 和 session_tags |
| 哪些 tag 最常一起出现 | session_tags **自连接** |
| 每月新出现的 tag 数 | 每个 tag 的首次出现时间 + `GROUP BY` |
| `duration_days > 1` 的长期 session 都是什么主题 | `JOIN` + 筛选 |

**注意**：跨平台比较时，默认要按 `source`（web / cli）分开看。
