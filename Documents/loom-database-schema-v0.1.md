# Loom 数据库表结构草案 v0.1

## 1. 文档信息

- 项目名称：Loom
- 文档版本：v0.1
- 文档类型：数据库表结构草案
- 目标阶段：MVP / 第一阶段落地
- 关联文档：
  - `loom-architecture-v0.1.md`
  - `loom-data-model-v0.1.md`
  - `loom-tech-stack-and-structure-v0.1.md`

## 2. 文档目标

本文档用于将 Loom 的数据模型进一步落地为可执行的数据库表结构草案，重点覆盖：

- 核心表结构
- 字段类型建议
- 主键与索引建议
- 生命周期与清理规则
- 典型查询场景

当前目标数据库为 SQLite，因此本设计会尽量采用适合 SQLite 的建模与约束方式。

## 3. 设计原则

- 以单机本地运行场景为前提
- 结构清晰，便于快速开发
- 能支撑消息流、聚合、评分、翻译和实时展示
- 字段尽量面向查询场景设计
- 允许部分灵活字段使用 JSON 文本存储
- 优先保障最近 7 天的数据访问效率

## 4. 表结构总览

Loom v0.1 建议至少包含以下核心表：

- `sources`
- `messages`
- `clusters`
- `cluster_message_map`
- `system_state`

建议补充以下辅助表：

- `source_fetch_logs`
- `ai_task_logs`
- `cleanup_logs`

其中辅助表不是 MVP 主链路必需项，但非常有助于排查问题。

## 5. 核心表定义

## 5.1 `sources`

### 用途

存储 API 与 Feed 来源配置。

### 建议字段

| 字段名 | 类型 | 约束 | 说明 |
|---|---|---|---|
| source_id | TEXT | PK | 来源唯一标识 |
| source_name | TEXT | NOT NULL | 来源名称 |
| source_type | TEXT | NOT NULL | `api` 或 `feed` |
| endpoint | TEXT | NOT NULL | API 地址或 Feed URL |
| method | TEXT | NULL | HTTP 方法 |
| headers_json | TEXT | NULL | headers JSON 字符串 |
| auth_config_json | TEXT | NULL | 鉴权配置 JSON |
| polling_interval_seconds | INTEGER | NOT NULL | 轮询频率 |
| timeout_seconds | INTEGER | NULL | 请求超时 |
| default_category | TEXT | NULL | 默认分类 |
| enabled | INTEGER | NOT NULL DEFAULT 1 | 是否启用 |
| health_status | TEXT | NOT NULL DEFAULT 'unknown' | 健康状态 |
| last_success_at | TEXT | NULL | 最近成功时间 |
| last_error_at | TEXT | NULL | 最近失败时间 |
| last_error_message | TEXT | NULL | 最近错误信息 |
| created_at | TEXT | NOT NULL | 创建时间 |
| updated_at | TEXT | NOT NULL | 更新时间 |

### 索引建议

- `idx_sources_enabled`
- `idx_sources_health_status`

### 备注

- SQLite 中布尔值可用 INTEGER 0/1 表示
- 时间字段建议统一存 ISO 8601 UTC 字符串

## 5.2 `messages`

### 用途

存储标准化后的原始消息明细，是 Loom 的核心事实表。

### 建议字段

| 字段名 | 类型 | 约束 | 说明 |
|---|---|---|---|
| message_id | TEXT | PK | 消息唯一标识 |
| source_id | TEXT | NOT NULL | 来源 ID |
| source_name | TEXT | NOT NULL | 来源名称快照 |
| source_type | TEXT | NOT NULL | 来源类型快照 |
| external_id | TEXT | NULL | 来源侧消息 ID |
| title | TEXT | NOT NULL | 原始标题 |
| content | TEXT | NULL | 原始正文或标准化正文 |
| url | TEXT | NOT NULL | 原始链接 |
| published_at | TEXT | NULL | 来源发布时间 |
| fetched_at | TEXT | NOT NULL | 本地抓取时间 |
| language | TEXT | NOT NULL DEFAULT 'unknown' | 语言 |
| translated_title_zh | TEXT | NULL | 中文标题 |
| ai_summary | TEXT | NULL | AI 摘要 |
| translated_summary_zh | TEXT | NULL | 中文摘要 |
| primary_category | TEXT | NULL | 主分类 |
| tags_json | TEXT | NULL | 标签数组 JSON |
| rule_importance_score | REAL | NULL | 规则重要性分 |
| ai_importance_score | REAL | NULL | AI 重要性分 |
| final_importance_score | REAL | NULL | 最终重要性分 |
| dedup_key | TEXT | NULL | 去重键 |
| cluster_id | TEXT | NULL | 所属聚合组 |
| ai_confidence | REAL | NULL | AI 置信度 |
| is_deleted | INTEGER | NOT NULL DEFAULT 0 | 删除标记 |
| created_at | TEXT | NOT NULL | 入库时间 |
| expires_at | TEXT | NOT NULL | 过期时间 |

### 外键建议

- `source_id` → `sources.source_id`
- `cluster_id` → `clusters.cluster_id`

SQLite 可开启外键支持，但考虑到清理和批量更新的可控性，也可由应用层维护引用完整性。

### 唯一性建议

- `message_id` 唯一
- 建议增加联合唯一索引：`(source_id, external_id)`，前提是 `external_id` 可稳定获取

### 索引建议

- `idx_messages_source_id`
- `idx_messages_cluster_id`
- `idx_messages_published_at`
- `idx_messages_created_at`
- `idx_messages_expires_at`
- `idx_messages_language`
- `idx_messages_primary_category`
- `idx_messages_external_id`
- `idx_messages_url`
- `idx_messages_dedup_key`

### 查询用途

- 最近消息列表
- 某来源消息列表
- 某聚合组下消息列表
- 分类筛选
- 过期清理扫描
- AI 增强字段更新

## 5.3 `clusters`

### 用途

存储聚合后的主展示卡片。

### 建议字段

| 字段名 | 类型 | 约束 | 说明 |
|---|---|---|---|
| cluster_id | TEXT | PK | 聚合组唯一标识 |
| canonical_title | TEXT | NOT NULL | 主标题 |
| canonical_title_zh | TEXT | NULL | 中文主标题 |
| canonical_summary | TEXT | NULL | 主摘要 |
| canonical_summary_zh | TEXT | NULL | 中文主摘要 |
| primary_category | TEXT | NULL | 主分类 |
| tags_json | TEXT | NULL | 标签 JSON |
| importance_score | REAL | NULL | 聚合重要性 |
| heat_score | REAL | NULL | 聚合热度 |
| message_count | INTEGER | NOT NULL DEFAULT 1 | 聚合消息数 |
| source_count | INTEGER | NOT NULL DEFAULT 1 | 聚合来源数 |
| first_published_at | TEXT | NULL | 最早发布时间 |
| latest_published_at | TEXT | NULL | 最近发布时间 |
| latest_message_at | TEXT | NULL | 最近入组时间 |
| representative_message_id | TEXT | NULL | 代表消息 ID |
| created_at | TEXT | NOT NULL | 创建时间 |
| updated_at | TEXT | NOT NULL | 更新时间 |
| expires_at | TEXT | NOT NULL | 过期时间 |

### 索引建议

- `idx_clusters_latest_message_at`
- `idx_clusters_primary_category`
- `idx_clusters_importance_score`
- `idx_clusters_heat_score`
- `idx_clusters_expires_at`

### 查询用途

- 主时间线列表
- 分类筛选
- 按热度或重要性排序
- TTL 清理

## 5.4 `cluster_message_map`

### 用途

维护 Cluster 与 Message 的映射关系，支撑展开查看全部来源。

### 建议字段

| 字段名 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | TEXT | PK | 映射唯一标识 |
| cluster_id | TEXT | NOT NULL | 聚合组 ID |
| message_id | TEXT | NOT NULL | 消息 ID |
| source_id | TEXT | NOT NULL | 来源 ID |
| source_name | TEXT | NOT NULL | 来源名称快照 |
| published_at | TEXT | NULL | 消息发布时间 |
| created_at | TEXT | NOT NULL | 映射建立时间 |

### 唯一性建议

- 联合唯一：`(cluster_id, message_id)`

### 索引建议

- `idx_cluster_message_map_cluster_id`
- `idx_cluster_message_map_message_id`
- `idx_cluster_message_map_source_id`
- `idx_cluster_message_map_published_at`

### 查询用途

- 聚合卡片展开来源列表
- 聚合组来源数统计
- 来源追溯

## 5.5 `system_state`

### 用途

存储系统运行状态和关键指标。

### 建议字段

| 字段名 | 类型 | 约束 | 说明 |
|---|---|---|---|
| state_key | TEXT | PK | 状态键 |
| state_value | TEXT | NOT NULL | 状态值 |
| updated_at | TEXT | NOT NULL | 更新时间 |

### 推荐状态项

- `network_status`
- `lmstudio_status`
- `last_cleanup_at`
- `total_messages_7d`
- `total_clusters_7d`
- `total_sources_enabled`
- `last_scheduler_run_at`

## 6. 辅助表建议

## 6.1 `source_fetch_logs`

### 用途

记录来源抓取历史，便于排查问题和在状态页展示最近错误。

### 建议字段

| 字段名 | 类型 | 说明 |
|---|---|---|
| log_id | TEXT | 主键 |
| source_id | TEXT | 来源 ID |
| started_at | TEXT | 开始时间 |
| ended_at | TEXT | 结束时间 |
| status | TEXT | success / failed |
| fetched_count | INTEGER | 拉取条数 |
| new_count | INTEGER | 新增条数 |
| error_message | TEXT | 错误信息 |

## 6.2 `ai_task_logs`

### 用途

记录 AI 增强任务结果，用于观察 LM Studio 调用状况。

### 建议字段

| 字段名 | 类型 | 说明 |
|---|---|---|
| task_id | TEXT | 主键 |
| message_id | TEXT | 关联消息 |
| task_type | TEXT | translate / summary / category / importance |
| status | TEXT | success / failed |
| started_at | TEXT | 开始时间 |
| ended_at | TEXT | 结束时间 |
| error_message | TEXT | 错误信息 |

## 6.3 `cleanup_logs`

### 用途

记录过期清理任务运行情况。

### 建议字段

| 字段名 | 类型 | 说明 |
|---|---|---|
| cleanup_id | TEXT | 主键 |
| started_at | TEXT | 开始时间 |
| ended_at | TEXT | 结束时间 |
| deleted_messages | INTEGER | 删除消息数 |
| deleted_clusters | INTEGER | 删除聚合组数 |
| status | TEXT | success / failed |
| error_message | TEXT | 错误信息 |

## 7. 推荐 DDL 草案

以下 SQL 为面向 SQLite 的草案，不代表最终可直接上线版本，但足够作为开发起点。

## 7.1 创建 `sources`

```sql
CREATE TABLE IF NOT EXISTS sources (
  source_id TEXT PRIMARY KEY,
  source_name TEXT NOT NULL,
  source_type TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  method TEXT,
  headers_json TEXT,
  auth_config_json TEXT,
  polling_interval_seconds INTEGER NOT NULL,
  timeout_seconds INTEGER,
  default_category TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  health_status TEXT NOT NULL DEFAULT 'unknown',
  last_success_at TEXT,
  last_error_at TEXT,
  last_error_message TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

## 7.2 创建 `messages`

```sql
CREATE TABLE IF NOT EXISTS messages (
  message_id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  source_name TEXT NOT NULL,
  source_type TEXT NOT NULL,
  external_id TEXT,
  title TEXT NOT NULL,
  content TEXT,
  url TEXT NOT NULL,
  published_at TEXT,
  fetched_at TEXT NOT NULL,
  language TEXT NOT NULL DEFAULT 'unknown',
  translated_title_zh TEXT,
  ai_summary TEXT,
  translated_summary_zh TEXT,
  primary_category TEXT,
  tags_json TEXT,
  rule_importance_score REAL,
  ai_importance_score REAL,
  final_importance_score REAL,
  dedup_key TEXT,
  cluster_id TEXT,
  ai_confidence REAL,
  is_deleted INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);
```

## 7.3 创建 `clusters`

```sql
CREATE TABLE IF NOT EXISTS clusters (
  cluster_id TEXT PRIMARY KEY,
  canonical_title TEXT NOT NULL,
  canonical_title_zh TEXT,
  canonical_summary TEXT,
  canonical_summary_zh TEXT,
  primary_category TEXT,
  tags_json TEXT,
  importance_score REAL,
  heat_score REAL,
  message_count INTEGER NOT NULL DEFAULT 1,
  source_count INTEGER NOT NULL DEFAULT 1,
  first_published_at TEXT,
  latest_published_at TEXT,
  latest_message_at TEXT,
  representative_message_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);
```

## 7.4 创建 `cluster_message_map`

```sql
CREATE TABLE IF NOT EXISTS cluster_message_map (
  id TEXT PRIMARY KEY,
  cluster_id TEXT NOT NULL,
  message_id TEXT NOT NULL,
  source_id TEXT NOT NULL,
  source_name TEXT NOT NULL,
  published_at TEXT,
  created_at TEXT NOT NULL
);
```

## 7.5 创建 `system_state`

```sql
CREATE TABLE IF NOT EXISTS system_state (
  state_key TEXT PRIMARY KEY,
  state_value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

## 8. 推荐索引 SQL

```sql
CREATE INDEX IF NOT EXISTS idx_sources_enabled
ON sources(enabled);

CREATE INDEX IF NOT EXISTS idx_sources_health_status
ON sources(health_status);

CREATE INDEX IF NOT EXISTS idx_messages_source_id
ON messages(source_id);

CREATE INDEX IF NOT EXISTS idx_messages_cluster_id
ON messages(cluster_id);

CREATE INDEX IF NOT EXISTS idx_messages_published_at
ON messages(published_at);

CREATE INDEX IF NOT EXISTS idx_messages_created_at
ON messages(created_at);

CREATE INDEX IF NOT EXISTS idx_messages_expires_at
ON messages(expires_at);

CREATE INDEX IF NOT EXISTS idx_messages_language
ON messages(language);

CREATE INDEX IF NOT EXISTS idx_messages_primary_category
ON messages(primary_category);

CREATE INDEX IF NOT EXISTS idx_messages_external_id
ON messages(external_id);

CREATE INDEX IF NOT EXISTS idx_messages_url
ON messages(url);

CREATE INDEX IF NOT EXISTS idx_messages_dedup_key
ON messages(dedup_key);

CREATE INDEX IF NOT EXISTS idx_clusters_latest_message_at
ON clusters(latest_message_at);

CREATE INDEX IF NOT EXISTS idx_clusters_primary_category
ON clusters(primary_category);

CREATE INDEX IF NOT EXISTS idx_clusters_importance_score
ON clusters(importance_score DESC);

CREATE INDEX IF NOT EXISTS idx_clusters_heat_score
ON clusters(heat_score DESC);

CREATE INDEX IF NOT EXISTS idx_clusters_expires_at
ON clusters(expires_at);

CREATE UNIQUE INDEX IF NOT EXISTS idx_cluster_message_map_unique
ON cluster_message_map(cluster_id, message_id);

CREATE INDEX IF NOT EXISTS idx_cluster_message_map_cluster_id
ON cluster_message_map(cluster_id);

CREATE INDEX IF NOT EXISTS idx_cluster_message_map_source_id
ON cluster_message_map(source_id);
```

## 9. 典型查询场景

## 9.1 主时间线查询

目标：

- 按时间倒序展示聚合卡片
- 支持分类筛选
- 支持按重要性或热度排序

示例思路：

```sql
SELECT *
FROM clusters
WHERE expires_at > ?
ORDER BY latest_message_at DESC
LIMIT ? OFFSET ?;
```

## 9.2 聚合卡片展开来源

目标：

- 展示某个 Cluster 下所有消息来源

示例思路：

```sql
SELECT m.message_id, m.title, m.translated_title_zh, m.url, m.language, m.published_at, m.source_name
FROM cluster_message_map cmm
JOIN messages m ON m.message_id = cmm.message_id
WHERE cmm.cluster_id = ?
ORDER BY COALESCE(m.published_at, m.created_at) DESC;
```

## 9.3 最近新消息查询

目标：

- 用于实时推送或调试消息流

```sql
SELECT *
FROM messages
WHERE created_at > ?
ORDER BY created_at DESC;
```

## 9.4 清理过期消息

```sql
DELETE FROM messages
WHERE expires_at <= ?;
```

后续需要继续：

- 删除孤立映射
- 删除无消息 Cluster

## 10. FTS 搜索建议

如果要支持最近 7 天消息全文搜索，建议使用 SQLite FTS。

推荐索引字段：

- `messages.title`
- `messages.content`
- `messages.translated_title_zh`
- `messages.translated_summary_zh`
- `clusters.canonical_title`
- `clusters.canonical_summary_zh`

第一版若不想引入 FTS，也可以先用 `LIKE` 做最小搜索能力，但中长期建议上 FTS。

## 11. 清理策略落地建议

## 11.1 清理顺序

建议按以下顺序清理：

1. 删除过期 `messages`
2. 删除无效 `cluster_message_map`
3. 重算受影响 `clusters`
4. 删除已无有效消息的 `clusters`
5. 记录 `cleanup_logs`

## 11.2 为什么不能只删 messages

因为 Loom 的主展示单位是聚合卡片：

- 只删 `messages` 会留下脏 `cluster_message_map`
- 只删 `messages` 也可能导致 `clusters.message_count` 不准确

因此必须做级联修复。

## 12. 迁移建议

在正式开发中，建议把建表和索引管理放入迁移体系。

推荐最少包含：

- 初始建表迁移
- 索引补充迁移
- 字段调整迁移

即使是本地 SQLite，也应使用迁移机制，而不是靠手工修改数据库。

## 13. 结论

Loom v0.1 的数据库草案以 `sources`、`messages`、`clusters`、`cluster_message_map`、`system_state` 为核心，已经足以支撑以下能力：

- 来源配置与调度
- 原始消息入库
- 英文中译与 AI 增强字段存储
- 聚合卡片展示与来源展开
- 热度与重要性查询
- 7 天生命周期管理

在此基础上，项目已经具备进入真实建库和接口开发阶段的条件。
