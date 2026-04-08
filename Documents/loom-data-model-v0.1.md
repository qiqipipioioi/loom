# Loom 数据模型设计文档 v0.1

## 1. 文档信息

- 项目名称：Loom
- 文档版本：v0.1
- 文档类型：数据模型设计文档
- 目标阶段：MVP / 第一阶段落地
- 关联文档：`loom-architecture-v0.1.md`

## 2. 文档目标

本文档用于定义 Loom 在 MVP 阶段的核心数据模型、实体关系、字段语义、生命周期规则和索引建议，为后续数据库设计、接口设计、聚合逻辑和前端展示提供统一的数据基础。

Loom 当前是一个本地运行的实时聚合信息流系统，因此数据模型设计遵循以下原则：

- 本地优先，结构清晰
- 围绕消息流与聚合卡片建模
- 保留原始来源，不因聚合而丢失明细
- 支持英文中译、分类、评分、热度、展开来源
- 控制生命周期，默认仅保留 7 天消息

## 3. 设计原则

### 3.1 建模原则

- 原始消息与聚合结果分层建模
- 配置、运行状态、业务数据分开存储
- 尽量保留可追溯字段
- AI 结果作为增强字段，而非唯一真相来源
- 便于本地查询、筛选、聚合和过期清理

### 3.2 核心抽象

Loom MVP 的核心抽象分为五类：

- Source：外部数据源配置
- Message：原始消息标准化结果
- Cluster：聚合后的主展示对象
- Mapping：聚合组与消息的关联关系
- SystemState：系统运行状态

## 4. 实体关系总览

```text
Source 1 ---- N Message
Message N ---- 1 Cluster
Cluster 1 ---- N Message
Cluster 1 ---- N ClusterMessageMap
Source 1 ---- N ClusterMessageMap
```

补充说明：

- 一个 Source 可以产生多条 Message
- 一条 Message 在任意时刻最多属于一个 Cluster
- 一个 Cluster 聚合多条 Message
- ClusterMessageMap 用于保留聚合展开视图所需的明细映射

## 5. 核心实体定义

## 5.1 Source

### 5.1.1 用途

表示一个可配置的数据源。Source 是采集调度的输入，也是来源健康状态与默认分类的承载对象。

### 5.1.2 字段定义

| 字段名 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| source_id | string | 是 | 来源唯一标识 |
| source_name | string | 是 | 来源名称 |
| source_type | enum | 是 | 来源类型，`api` 或 `feed` |
| endpoint | string | 是 | API 地址或 Feed URL |
| method | string | 否 | 请求方法，Feed 通常可为空 |
| headers_json | json/text | 否 | 请求头配置 |
| auth_config_json | json/text | 否 | 鉴权配置 |
| polling_interval_seconds | integer | 是 | 轮询周期，单位秒 |
| timeout_seconds | integer | 否 | 请求超时时间 |
| default_category | string | 否 | 默认分类 |
| enabled | boolean | 是 | 是否启用 |
| health_status | enum | 是 | 来源健康状态 |
| last_success_at | datetime | 否 | 最近成功抓取时间 |
| last_error_at | datetime | 否 | 最近失败时间 |
| last_error_message | text | 否 | 最近错误信息 |
| created_at | datetime | 是 | 创建时间 |
| updated_at | datetime | 是 | 更新时间 |

### 5.1.3 枚举建议

#### source_type

- api
- feed

#### health_status

- unknown
- healthy
- degraded
- failed
- disabled

### 5.1.4 设计说明

- `default_category` 用于规则分类的初始值
- `enabled` 控制调度器是否生成任务
- `health_status` 由调度器和抓取结果动态更新
- 鉴权配置不应在 UI 中明文展示

## 5.2 Message

### 5.2.1 用途

表示来自外部来源的一条原始消息，在经过标准化、语言识别、AI 增强和基础去重后写入存储。Message 是最核心的明细实体，也是聚合、展开、搜索与追溯的基础。

### 5.2.2 字段定义

| 字段名 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| message_id | string | 是 | 消息唯一标识 |
| source_id | string | 是 | 来源 ID |
| source_name | string | 是 | 来源名称快照 |
| source_type | enum | 是 | 来源类型快照 |
| external_id | string | 否 | 来源侧消息 ID |
| title | text | 是 | 原始标题 |
| content | text | 否 | 标准化后的原始正文 |
| url | text | 是 | 原始链接 |
| published_at | datetime | 否 | 来源发布时间 |
| fetched_at | datetime | 是 | 本地抓取时间 |
| language | enum | 是 | 语言标识 |
| translated_title_zh | text | 否 | 中文标题 |
| ai_summary | text | 否 | AI 生成摘要，原文语言 |
| translated_summary_zh | text | 否 | 中文摘要 |
| primary_category | string | 否 | 主分类 |
| tags_json | json/text | 否 | 标签数组 |
| rule_importance_score | real | 否 | 规则重要性分 |
| ai_importance_score | real | 否 | AI 辅助重要性分 |
| final_importance_score | real | 否 | 最终重要性分 |
| dedup_key | string | 否 | 基础去重键 |
| cluster_id | string | 否 | 所属聚合组 |
| ai_confidence | real | 否 | AI 输出置信度 |
| is_deleted | boolean | 是 | 逻辑删除标记 |
| created_at | datetime | 是 | 入库时间 |
| expires_at | datetime | 是 | 过期时间 |

### 5.2.3 枚举建议

#### language

- zh
- en
- other
- unknown

### 5.2.4 设计说明

- `translated_title_zh` 和 `translated_summary_zh` 仅对英文消息强制关注
- `source_name`、`source_type` 为快照字段，避免来源配置变更影响历史展示
- `final_importance_score` 是前端优先使用的单值
- `cluster_id` 允许为空，表示尚未完成聚合或被单独成组
- `expires_at` 用于 7 天 TTL 清理

## 5.3 Cluster

### 5.3.1 用途

表示多个相似 Message 聚合后的主展示对象。前端主时间线默认展示 Cluster，而不是单条 Message。

### 5.3.2 字段定义

| 字段名 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| cluster_id | string | 是 | 聚合组唯一标识 |
| canonical_title | text | 是 | 聚合主标题 |
| canonical_title_zh | text | 否 | 中文聚合标题 |
| canonical_summary | text | 否 | 主摘要 |
| canonical_summary_zh | text | 否 | 中文主摘要 |
| primary_category | string | 否 | 主分类 |
| tags_json | json/text | 否 | 聚合组标签 |
| importance_score | real | 否 | 聚合组重要性 |
| heat_score | real | 否 | 聚合组热度 |
| message_count | integer | 是 | 聚合内消息数 |
| source_count | integer | 是 | 聚合内来源数 |
| first_published_at | datetime | 否 | 最早消息发布时间 |
| latest_published_at | datetime | 否 | 最新消息发布时间 |
| latest_message_at | datetime | 否 | 最近入组时间 |
| representative_message_id | string | 否 | 代表消息 ID |
| created_at | datetime | 是 | 创建时间 |
| updated_at | datetime | 是 | 更新时间 |
| expires_at | datetime | 是 | 过期时间 |

### 5.3.3 设计说明

- `canonical_title` 可来自代表消息，也可由聚合逻辑或 AI 生成
- `canonical_summary_zh` 是主页面首选摘要字段
- `message_count` 和 `source_count` 用于热度和 UI 展示
- `representative_message_id` 用于打开默认原文链接
- `expires_at` 通常取组内最新有效消息对应的生命周期上界

## 5.4 ClusterMessageMap

### 5.4.1 用途

用于维护 Cluster 与 Message 的关联关系，支撑“展开查看全部来源”的前端能力，同时避免仅依赖 Message 表反查时的查询混乱。

### 5.4.2 字段定义

| 字段名 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| id | string | 是 | 映射唯一标识 |
| cluster_id | string | 是 | 聚合组 ID |
| message_id | string | 是 | 消息 ID |
| source_id | string | 是 | 来源 ID |
| source_name | string | 是 | 来源名称快照 |
| published_at | datetime | 否 | 该消息发布时间 |
| created_at | datetime | 是 | 建立映射时间 |

### 5.4.3 设计说明

- 该表可视为展开视图的物化辅助层
- 可支持按 `published_at` 或 `created_at` 排序展示来源

## 5.5 SystemState

### 5.5.1 用途

记录系统运行状态和关键统计指标，供系统状态页展示与后台任务监控使用。

### 5.5.2 字段定义

| 字段名 | 类型 | 必填 | 说明 |
|---|---|---:|---|
| state_key | string | 是 | 状态键 |
| state_value | text | 是 | 状态值 |
| updated_at | datetime | 是 | 更新时间 |

### 5.5.3 推荐状态项

- network_status
- lmstudio_status
- last_cleanup_at
- total_messages_7d
- total_clusters_7d
- total_sources_enabled
- last_scheduler_run_at

### 5.5.4 设计说明

- MVP 阶段使用 key-value 即可
- 后续若状态复杂，可拆成独立结构化表

## 6. 字段语义与约束

## 6.1 唯一性建议

### Source

- `source_id` 唯一
- `source_name` 建议唯一，但不强依赖

### Message

- `message_id` 唯一
- 若 `external_id` 存在，建议与 `source_id` 形成联合唯一约束
- `url` 不必全局唯一，但建议建立索引

### Cluster

- `cluster_id` 唯一

### ClusterMessageMap

- `id` 唯一
- `cluster_id + message_id` 应唯一

## 6.2 时间字段约束

- 所有时间统一使用 UTC 存储
- 前端展示按本地时区转换
- `fetched_at` 和 `created_at` 必须存在
- `published_at` 允许为空，因某些来源可能不提供

## 6.3 删除策略

- 默认优先物理删除过期 Message
- 若采用逻辑删除，`is_deleted` 仅作为中间态，不应长期积累
- 清理后同步删除 ClusterMessageMap
- 无有效 Message 的 Cluster 一并删除

## 7. 数据生命周期设计

## 7.1 生命周期规则

- Message 生命周期默认 7 天
- Cluster 生命周期由关联 Message 决定
- Source 为长期配置数据，不参与 TTL
- SystemState 为状态数据，可长期保留或覆盖更新

## 7.2 生命周期流程

```text
消息入库
→ 计算 expires_at = created_at + 7天
→ 正常参与聚合、评分、展示
→ 到期后由清理任务删除
→ 清理映射关系
→ 若 Cluster 已无有效消息，则删除 Cluster
```

## 7.3 边界情况

若 Cluster 很早创建，但最近仍有新消息入组：

- 删除过期 Message
- 保留 Cluster
- 重算 `message_count`
- 重算 `source_count`
- 重算 `heat_score`
- 更新 `expires_at`

## 8. 聚合建模说明

## 8.1 为什么 Message 与 Cluster 要分层

如果只保留单层消息：

- 前端无法优雅展示多源聚合
- 热度难以从群组层面表达
- 无法自然支持“展开全部来源”

分层后可实现：

- 明细完整保留
- 主页面简洁
- 评分按聚合组表达
- 展开视图可回溯到每条来源

## 8.2 代表消息策略

每个 Cluster 需要一个 `representative_message_id`，推荐选择规则如下：

- 高可信来源优先
- 标题最清晰优先
- 发布时间较早优先
- 若有官方源，优先官方源

## 8.3 聚合组字段更新策略

当新 Message 加入 Cluster 时，应同步更新：

- `message_count`
- `source_count`
- `latest_published_at`
- `latest_message_at`
- `importance_score`
- `heat_score`
- `canonical_summary_zh`

## 9. 中英对照建模说明

## 9.1 Message 层

英文消息应保留：

- 原始标题：`title`
- 中文标题：`translated_title_zh`
- 原文摘要：`ai_summary`
- 中文摘要：`translated_summary_zh`

## 9.2 Cluster 层

聚合卡片建议保留：

- 主标题：`canonical_title`
- 中文主标题：`canonical_title_zh`
- 主摘要：`canonical_summary`
- 中文主摘要：`canonical_summary_zh`

## 9.3 设计取舍

- 中文消息不强制生成英译
- 保留原文有助于后续核对和多语展示
- 聚合卡片优先展示中文，有需要再切换原文

## 10. 评分建模说明

## 10.1 Message 层评分

Message 层保留以下分数：

- `rule_importance_score`
- `ai_importance_score`
- `final_importance_score`

用途：

- 为 Cluster 评分提供输入
- 用于调试和分析来源质量

## 10.2 Cluster 层评分

Cluster 层保留：

- `importance_score`
- `heat_score`

用途：

- 前端主卡片展示
- 时间线排序与筛选

## 10.3 推荐约束

- 所有分数统一归一化到 0–100
- 空值表示尚未计算完成
- 前端应允许显示“计算中”状态

## 11. 索引建议

## 11.1 Source

- `idx_source_enabled`
- `idx_source_health_status`

## 11.2 Message

- `idx_message_source_id`
- `idx_message_cluster_id`
- `idx_message_published_at`
- `idx_message_created_at`
- `idx_message_expires_at`
- `idx_message_language`
- `idx_message_primary_category`
- `idx_message_external_id`
- `idx_message_url`
- `idx_message_dedup_key`

## 11.3 Cluster

- `idx_cluster_latest_message_at`
- `idx_cluster_primary_category`
- `idx_cluster_importance_score`
- `idx_cluster_heat_score`
- `idx_cluster_expires_at`

## 11.4 ClusterMessageMap

- `idx_cmm_cluster_id`
- `idx_cmm_message_id`
- `idx_cmm_source_id`
- `idx_cmm_published_at`

## 11.5 全文搜索建议

若启用 SQLite FTS，可考虑对以下字段建立搜索索引：

- `Message.title`
- `Message.content`
- `Message.translated_title_zh`
- `Message.translated_summary_zh`
- `Cluster.canonical_title`
- `Cluster.canonical_summary_zh`

## 12. 推荐表结构拆分

MVP 阶段建议至少包含以下表：

- `sources`
- `messages`
- `clusters`
- `cluster_message_map`
- `system_state`

可选辅助表：

- `source_fetch_logs`
- `ai_task_logs`
- `cleanup_logs`

其中辅助表不属于 MVP 必需项，但对调试很有帮助。

## 13. 示例对象

## 13.1 Source 示例

```json
{
  "source_id": "src_openai_blog",
  "source_name": "OpenAI Blog",
  "source_type": "feed",
  "endpoint": "https://openai.com/blog/rss.xml",
  "polling_interval_seconds": 300,
  "default_category": "AI",
  "enabled": true,
  "health_status": "healthy"
}
```

## 13.2 Message 示例

```json
{
  "message_id": "msg_20260401_001",
  "source_id": "src_openai_blog",
  "source_name": "OpenAI Blog",
  "source_type": "feed",
  "external_id": "openai-rss-abc123",
  "title": "Introducing GPT-X",
  "content": "Today we are launching...",
  "url": "https://openai.com/index/gpt-x",
  "published_at": "2026-04-01T08:00:00Z",
  "fetched_at": "2026-04-01T08:01:10Z",
  "language": "en",
  "translated_title_zh": "推出 GPT-X",
  "ai_summary": "OpenAI announced a new model...",
  "translated_summary_zh": "OpenAI 发布了一款新模型，重点在于...",
  "primary_category": "AI",
  "tags_json": ["模型发布", "OpenAI"],
  "final_importance_score": 88,
  "cluster_id": "clu_ai_20260401_001",
  "created_at": "2026-04-01T08:01:12Z",
  "expires_at": "2026-04-08T08:01:12Z"
}
```

## 13.3 Cluster 示例

```json
{
  "cluster_id": "clu_ai_20260401_001",
  "canonical_title": "Introducing GPT-X",
  "canonical_title_zh": "GPT-X 发布",
  "canonical_summary_zh": "多家来源报道 OpenAI 发布新模型 GPT-X。",
  "primary_category": "AI",
  "importance_score": 91,
  "heat_score": 76,
  "message_count": 5,
  "source_count": 4,
  "representative_message_id": "msg_20260401_001",
  "created_at": "2026-04-01T08:01:13Z",
  "updated_at": "2026-04-01T08:05:20Z",
  "expires_at": "2026-04-08T08:05:20Z"
}
```

## 14. 后续演进建议

未来若进入 v0.2 及以上，可考虑新增以下模型：

- Watchlist：重点关注对象
- CategoryRule：分类规则
- SourceTrustProfile：来源可信度配置
- NotificationRecord：通知记录
- UserFeedback：用户反馈，用于排序优化
- Entity：实体抽取结果

当前阶段这些不是必需项，可在业务稳定后逐步引入。

## 15. 结论

Loom v0.1 的数据模型以 `Source`、`Message`、`Cluster` 为核心，兼顾了以下能力：

- 支持本地可配置来源接入
- 支持消息标准化与多源聚合
- 支持英文消息中英对照
- 支持重要性和热度评分
- 支持时间线主卡片与来源展开视图
- 支持 7 天生命周期控制

这套模型能够为后续数据库表设计、接口定义、前端渲染和聚合逻辑实现提供稳定基础。
