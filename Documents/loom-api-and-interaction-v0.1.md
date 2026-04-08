# Loom API 与页面交互设计 v0.1

## 1. 文档信息

- 项目名称：Loom
- 文档版本：v0.1
- 文档类型：API 与页面交互设计
- 目标阶段：MVP / 第一阶段落地
- 关联文档：
  - `loom-architecture-v0.1.md`
  - `loom-data-model-v0.1.md`
  - `loom-database-schema-v0.1.md`
  - `loom-mvp-breakdown-v0.1.md`

## 2. 文档目标

本文档用于明确 Loom MVP 阶段前后端交互边界，定义：

- 本地 API 的职责和接口结构
- 页面与接口之间的交互方式
- WebSocket 实时事件模型
- MVP 阶段推荐的请求与响应格式

由于 Loom 是本地运行系统，这里的 API 主要服务于本地 Web UI，而不是公共开放接口。

## 3. 设计原则

- 接口围绕页面需求设计
- 命名清晰、结构稳定
- 响应格式统一
- 支持本地 UI 的实时性和渐进增强
- 允许 AI 字段异步补齐

## 4. API 分层

Loom 的本地接口建议分为四类：

- Source API：来源配置与测试
- Timeline API：时间线和聚合卡片查询
- System API：系统状态与运维动作
- Realtime API：WebSocket 实时推送

## 5. 通用响应结构

建议所有 HTTP 响应采用统一包装：

```json
{
  "success": true,
  "data": {},
  "error": null,
  "meta": {}
}
```

错误结构建议：

```json
{
  "success": false,
  "data": null,
  "error": {
    "code": "SOURCE_TEST_FAILED",
    "message": "Failed to fetch source"
  },
  "meta": {}
}
```

## 6. Source API

## 6.1 获取来源列表

### 接口

- `GET /api/sources`

### 用途

- 来源配置页展示所有来源

### 请求参数

- `enabled` 可选
- `sourceType` 可选

### 响应示例

```json
{
  "success": true,
  "data": [
    {
      "sourceId": "src_openai_blog",
      "sourceName": "OpenAI Blog",
      "sourceType": "feed",
      "endpoint": "https://openai.com/blog/rss.xml",
      "pollingIntervalSeconds": 300,
      "defaultCategory": "AI",
      "enabled": true,
      "healthStatus": "healthy",
      "lastSuccessAt": "2026-04-01T08:00:00Z"
    }
  ],
  "error": null,
  "meta": {}
}
```

## 6.2 创建来源

### 接口

- `POST /api/sources`

### 用途

- 新建 API 或 Feed 来源

### 请求体示例

```json
{
  "sourceName": "OpenAI Blog",
  "sourceType": "feed",
  "endpoint": "https://openai.com/blog/rss.xml",
  "pollingIntervalSeconds": 300,
  "defaultCategory": "AI",
  "enabled": true
}
```

## 6.3 更新来源

### 接口

- `PUT /api/sources/:sourceId`

### 用途

- 修改来源配置

## 6.4 删除来源

### 接口

- `DELETE /api/sources/:sourceId`

### 用途

- 删除一个来源配置

## 6.5 启用或停用来源

### 接口

- `PATCH /api/sources/:sourceId/status`

### 请求体示例

```json
{
  "enabled": false
}
```

## 6.6 测试来源连接

### 接口

- `POST /api/sources/:sourceId/test`

### 用途

- 在来源配置页点击“测试连接”

### 响应示例

```json
{
  "success": true,
  "data": {
    "status": "ok",
    "previewCount": 3,
    "message": "Source connection successful"
  },
  "error": null,
  "meta": {}
}
```

## 7. Timeline API

## 7.1 获取聚合时间线

### 接口

- `GET /api/timeline/clusters`

### 用途

- 主页面拉取聚合卡片列表

### 请求参数

- `category` 可选
- `sourceId` 可选
- `sortBy` 可选，支持：
  - `latest`
  - `importance`
  - `heat`
- `limit` 可选
- `offset` 可选
- `keyword` 可选
- `highImportanceOnly` 可选

### 响应示例

```json
{
  "success": true,
  "data": [
    {
      "clusterId": "clu_ai_001",
      "canonicalTitle": "Introducing GPT-X",
      "canonicalTitleZh": "GPT-X 发布",
      "canonicalSummaryZh": "多家来源报道 OpenAI 发布新模型 GPT-X。",
      "primaryCategory": "AI",
      "tags": ["模型发布", "OpenAI"],
      "importanceScore": 91,
      "heatScore": 76,
      "messageCount": 5,
      "sourceCount": 4,
      "latestMessageAt": "2026-04-01T08:05:20Z",
      "representativeMessageId": "msg_001"
    }
  ],
  "error": null,
  "meta": {
    "limit": 20,
    "offset": 0,
    "total": 1
  }
}
```

## 7.2 获取聚合卡片详情

### 接口

- `GET /api/clusters/:clusterId`

### 用途

- 打开详情页或侧边面板

### 响应内容

- 聚合主字段
- 展示字段
- 代表消息
- 展开来源数量

## 7.3 获取聚合组下全部来源

### 接口

- `GET /api/clusters/:clusterId/messages`

### 用途

- 点击“展开全部来源”

### 请求参数

- `sortBy` 可选，默认 `publishedAtDesc`

### 响应示例

```json
{
  "success": true,
  "data": [
    {
      "messageId": "msg_001",
      "sourceId": "src_openai_blog",
      "sourceName": "OpenAI Blog",
      "title": "Introducing GPT-X",
      "translatedTitleZh": "推出 GPT-X",
      "url": "https://openai.com/index/gpt-x",
      "language": "en",
      "publishedAt": "2026-04-01T08:00:00Z",
      "finalImportanceScore": 88
    }
  ],
  "error": null,
  "meta": {}
}
```

## 7.4 获取最近原始消息

### 接口

- `GET /api/messages/recent`

### 用途

- 调试页面或开发模式观察原始流

### 说明

- 不是主页面核心接口，但开发阶段很有价值

## 8. System API

## 8.1 获取系统状态

### 接口

- `GET /api/system/status`

### 用途

- 系统状态页展示网络、AI、调度与统计信息

### 响应示例

```json
{
  "success": true,
  "data": {
    "networkStatus": "online",
    "lmstudioStatus": "healthy",
    "lastSchedulerRunAt": "2026-04-01T08:05:00Z",
    "lastCleanupAt": "2026-04-01T00:00:00Z",
    "totalMessages7d": 1280,
    "totalClusters7d": 460,
    "totalSourcesEnabled": 7
  },
  "error": null,
  "meta": {}
}
```

## 8.2 手动触发来源同步

### 接口

- `POST /api/system/sync`

### 用途

- 用户手动触发一次抓取

### 请求体示例

```json
{
  "sourceId": "src_openai_blog"
}
```

### 说明

- `sourceId` 可选
- 不传则同步所有启用来源

## 8.3 手动触发清理

### 接口

- `POST /api/system/cleanup`

### 用途

- 手动执行一次 7 天 TTL 清理

## 8.4 获取来源健康状态

### 接口

- `GET /api/system/source-health`

### 用途

- 系统状态页单独展示来源健康状况

## 9. AI 相关 API

尽管 AI 处理建议以后台异步任务为主，但仍建议提供可观察接口。

## 9.1 获取 AI 服务状态

### 接口

- `GET /api/ai/status`

### 用途

- 检查 LM Studio 连接是否正常

## 9.2 重试某条消息的 AI 增强

### 接口

- `POST /api/ai/messages/:messageId/retry`

### 用途

- 调试或人工补救某条消息 AI 失败的情况

## 10. WebSocket 事件设计

## 10.1 连接入口

- `ws://localhost:<port>/ws`

## 10.2 事件命名建议

- `timeline.cluster.created`
- `timeline.cluster.updated`
- `message.ai.updated`
- `source.health.changed`
- `system.status.updated`

## 10.3 事件载荷示例

### 新聚合卡片创建

```json
{
  "type": "timeline.cluster.created",
  "payload": {
    "clusterId": "clu_ai_001",
    "latestMessageAt": "2026-04-01T08:05:20Z"
  }
}
```

### 聚合卡片更新

```json
{
  "type": "timeline.cluster.updated",
  "payload": {
    "clusterId": "clu_ai_001",
    "messageCount": 6,
    "sourceCount": 5,
    "heatScore": 82
  }
}
```

### AI 字段补齐

```json
{
  "type": "message.ai.updated",
  "payload": {
    "messageId": "msg_001",
    "clusterId": "clu_ai_001"
  }
}
```

## 11. 页面交互设计

## 11.1 主时间线页面

### 页面目标

- 作为 Loom 的主入口
- 用最小认知负担展示值得关注的聚合信息

### 页面结构

- 顶部筛选栏
- 时间线列表区
- 右侧详情或弹层

### 页面初始化流程

```text
页面加载
→ 请求 /api/timeline/clusters
→ 渲染列表
→ 建立 WebSocket 连接
→ 接收增量事件更新列表
```

### 核心交互

- 切换分类筛选
- 按时间 / 重要性 / 热度排序
- 仅查看高重要性
- 展开查看全部来源
- 点击原始链接跳转

## 11.2 来源配置页面

### 页面目标

- 提供来源管理能力

### 页面初始化流程

```text
页面加载
→ 请求 /api/sources
→ 渲染来源列表
```

### 核心交互

- 新增来源
- 编辑来源
- 启用或停用来源
- 测试连接
- 删除来源

### 关键表单字段

- 名称
- 类型
- 地址
- 请求头
- 认证信息
- 轮询频率
- 默认分类

## 11.3 系统状态页面

### 页面目标

- 让用户了解 Loom 当前是否工作正常

### 页面初始化流程

```text
页面加载
→ 请求 /api/system/status
→ 请求 /api/system/source-health
→ 渲染状态面板
```

### 核心展示信息

- 网络状态
- LM Studio 状态
- 来源健康状态
- 最近同步时间
- 7 天消息量
- 聚合组数量

## 12. 页面与接口映射

| 页面/区域 | 主要接口 |
|---|---|
| 时间线首页 | `GET /api/timeline/clusters` |
| 聚合卡片展开 | `GET /api/clusters/:clusterId/messages` |
| 来源配置页 | `GET /api/sources` |
| 新增来源 | `POST /api/sources` |
| 编辑来源 | `PUT /api/sources/:sourceId` |
| 测试来源 | `POST /api/sources/:sourceId/test` |
| 系统状态页 | `GET /api/system/status` |
| 手动同步 | `POST /api/system/sync` |
| AI 状态查看 | `GET /api/ai/status` |

## 13. 前端缓存策略建议

## 13.1 时间线列表

- 首次通过 HTTP 拉取完整数据
- 后续通过 WebSocket 做增量刷新
- 用户切换筛选条件时重新请求接口

## 13.2 来源配置页

- 页面级缓存即可
- 保存成功后直接局部刷新列表

## 13.3 系统状态页

- 定时拉取或由 WebSocket 推送更新

## 14. 错误处理建议

## 14.1 HTTP 层

- 表单错误直接显示字段级提示
- 来源测试错误显示详细失败原因
- 系统错误显示简短提示并保留日志

## 14.2 WebSocket 层

- 连接断开时自动重连
- 重连成功后重新拉取一次时间线，避免漏消息

## 14.3 AI 异常场景

- AI 未返回时卡片仍可展示基础信息
- 中英对照未完成时显示原文并标记处理中

## 15. 推荐接口实现顺序

建议按以下顺序开发 API：

1. `GET /api/sources`
2. `POST /api/sources`
3. `PUT /api/sources/:sourceId`
4. `POST /api/sources/:sourceId/test`
5. `GET /api/timeline/clusters`
6. `GET /api/clusters/:clusterId/messages`
7. `GET /api/system/status`
8. `GET /api/ai/status`
9. WebSocket 推送
10. 手动同步与清理接口

这个顺序能优先满足配置、展示和实时性主链路。

## 16. 结论

Loom v0.1 的 API 设计应以本地 Web UI 为中心，重点支撑三类页面：

- 来源配置页
- 实时时间线页
- 系统状态页

同时通过 WebSocket 提供增量实时能力，通过本地 HTTP API 提供查询、配置和操作入口。按照本文档的接口边界，项目已经可以开始进入页面和服务端的并行开发阶段。
