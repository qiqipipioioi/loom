# Loom 技术选型与工程目录设计 v0.1

## 1. 文档信息

- 项目名称：Loom
- 文档版本：v0.1
- 文档类型：技术选型与工程目录设计
- 目标阶段：MVP / 第一阶段落地
- 关联文档：
  - `loom-architecture-v0.1.md`
  - `loom-data-model-v0.1.md`
  - `loom-mvp-breakdown-v0.1.md`

## 2. 文档目标

本文档用于回答 Loom 在实际开始开发时的两个关键问题：

- 第一版采用什么技术栈最合适
- 工程目录应该如何组织，才能支持后续迭代

本文档的目标不是给出唯一正确答案，而是给出一套适合 Loom 当前阶段的务实方案：本地运行、开发效率高、便于集成 LM Studio、便于快速搭建可用的 Web UI。

## 3. 选型原则

Loom 当前的产品边界非常明确，因此技术选型应围绕以下原则展开：

- 本地运行优先
- 实现成本可控
- 开发链路尽量简单
- 数据库和部署形态轻量
- 前后端通信方便
- 对 LM Studio 集成友好
- 适合实时 UI 和后台调度并存

不建议在第一版引入：

- 微服务架构
- 复杂消息队列
- 分布式任务系统
- 云原生依赖
- 过重的 ORM 或平台基础设施

## 4. 推荐技术方案总览

## 4.1 总体推荐

建议 Loom v0.1 采用以下技术组合：

- 前端：React + TypeScript + Vite
- UI：Tailwind CSS
- 后端：Node.js + TypeScript
- Web 服务：Fastify
- 数据库：SQLite
- 数据访问：Drizzle ORM 或原生 SQL + 轻量封装
- 实时通信：WebSocket
- 定时调度：Node 内部 Scheduler
- Feed 解析：RSS/Atom 解析库
- HTTP 客户端：undici 或 axios
- AI 接口：LM Studio OpenAI 兼容接口
- 桌面形态：先 Web 本地应用，后续可再包 Electron 或 Tauri

## 4.2 为什么是这套组合

这套选型的优点：

- TypeScript 一套语言覆盖前后端
- Vite 启动快，适合本地开发
- React 足够成熟，适合做实时信息流 UI
- Fastify 比 Express 更现代，也更适合结构化接口
- SQLite 非常适合单机本地应用
- WebSocket 适合实时推送新消息
- Node 与 LM Studio 的 HTTP 集成非常直接

## 5. 前端选型

## 5.1 推荐：React + TypeScript + Vite

### 选择理由

- 开发体验成熟
- 生态丰富
- 组件化适合复杂列表和配置页
- 与本地 Web UI 形态匹配
- Vite 热更新快，适合快速迭代

### 适合 Loom 的场景

- 实时时间线
- 来源配置页
- 系统状态页
- 展开式聚合消息卡片
- 筛选、排序、搜索、分页或虚拟滚动

## 5.2 UI 层推荐：Tailwind CSS

### 选择理由

- 能快速做出信息密度高的后台式界面
- 对时间线、卡片、状态标签、表单页都很适合
- 与 React/Vite 搭配顺畅

### 第一版不建议

- 一开始引入过重的设计系统
- 为了“美观”过度复杂化 UI 基础设施

## 5.3 状态管理建议

MVP 阶段可采用：

- React Query 或 TanStack Query：管理接口数据
- Zustand：管理局部界面状态

这样可以避免一开始引入过重的全局状态方案。

## 6. 后端选型

## 6.1 推荐：Node.js + TypeScript + Fastify

### 选择理由

- 与前端语言统一
- Fastify 结构清晰，性能足够
- 中间件和插件体系适合本地 API 服务
- WebSocket 支持方便
- 集成定时任务和外部 HTTP 拉取很直接

### Loom 中的职责

- 提供本地 API
- 管理来源配置
- 执行调度和抓取
- 处理标准化、AI 调用、聚合、评分
- 推送实时事件给前端

## 6.2 为什么不推荐第一版直接用 Python

Python 当然也能做，但对于 Loom 当前的整体形态，Node 方案更统一：

- 前后端同语言
- 本地 UI 与服务协同更顺手
- WebSocket 和本地 API 服务开发链路更短

如果后续某些 AI 或文本处理逻辑确实更适合 Python，可以在 v0.2 以后独立拆出处理模块。

## 7. 数据库选型

## 7.1 推荐：SQLite

### 选择理由

- 本地单机运行最合适
- 无需部署数据库服务
- 足够支持 Loom 的 7 天数据窗口
- 查询简单直接
- 便于备份和迁移

### Loom 中适合存储的内容

- Source 配置
- Message 明细
- Cluster 聚合结果
- ClusterMessageMap 映射
- SystemState 状态数据

## 7.2 ORM 建议

推荐优先级：

- 首选：Drizzle ORM
- 备选：原生 SQL + 查询封装

### 选择理由

- Drizzle 更轻量，接近 SQL 思维
- 类型支持较好
- 适合本地 SQLite

如果第一版更注重可控和透明，也可以直接写 SQL，并通过 repository 层封装。

## 8. 实时通信选型

## 8.1 推荐：WebSocket

### 选择理由

- 主页面需要实时插入新消息
- 需要在聚合更新时同步刷新 UI
- 可以复用一条长连接

### 典型事件

- new_message
- cluster_created
- cluster_updated
- source_health_changed
- ai_status_changed

## 8.2 SSE 是否可行

可以，但第一版更推荐 WebSocket，因为 Loom 不只是单向推送，后续很可能还会扩展：

- 客户端主动请求刷新特定卡片
- 手动重试来源
- 触发某类后台同步动作

## 9. 调度与抓取选型

## 9.1 调度器

推荐先使用 Node 内部调度即可：

- 轻量 scheduler
- setInterval / cron 风格库

第一版不需要引入 BullMQ、Temporal、Airflow 等复杂调度系统。

## 9.2 HTTP 请求

推荐：

- `undici` 或 `axios`

要求：

- 支持超时
- 支持 headers
- 支持错误处理
- 支持认证请求

## 9.3 Feed 解析

推荐：

- RSS/Atom 标准解析库

要求：

- 能稳定解析 title、link、published date、content
- 容忍部分源格式不标准

## 10. AI 接口选型

## 10.1 推荐：LM Studio OpenAI 兼容接口

### 选择理由

- 已符合你的本地运行要求
- 集成方式与标准 OpenAI 风格接口一致
- Node 侧容易封装统一 client

### Loom 中的调用类型

- 文本分类
- 英文中译
- 摘要
- 标签提取
- 重要性辅助判断
- 聚合辅助判断

## 10.2 AI 调用策略

不建议同步阻塞主抓取链路，推荐：

- 消息先入库
- AI 异步补齐增强字段
- 前端先展示基础内容，增强字段完成后刷新卡片

## 11. 本地运行形态建议

## 11.1 第一阶段推荐

先采用：

- 本地 Node 服务
- 本地前端 Web 应用
- 浏览器访问本地地址

这是最简单、最灵活的开发和调试方式。

## 11.2 第二阶段可选

后续若要更像正式桌面软件，可考虑再封装：

- Electron
- Tauri

但不建议在 MVP 阶段立即引入。

原因是：

- 会增加打包与跨平台复杂度
- 会分散第一版核心精力

## 12. 工程目录设计目标

目录设计需要满足以下要求：

- 前后端分层清晰
- 便于本地运行
- 数据模型、服务、调度、AI 解耦
- 后续能扩展测试、脚本和数据库迁移

## 13. 推荐工程结构

```text
loom/
├── apps/
│   ├── web/
│   │   ├── src/
│   │   │   ├── app/
│   │   │   ├── pages/
│   │   │   ├── components/
│   │   │   ├── features/
│   │   │   ├── hooks/
│   │   │   ├── lib/
│   │   │   ├── services/
│   │   │   ├── store/
│   │   │   └── styles/
│   │   ├── public/
│   │   └── package.json
│   └── server/
│       ├── src/
│       │   ├── api/
│       │   ├── config/
│       │   ├── db/
│       │   ├── models/
│       │   ├── repositories/
│       │   ├── services/
│       │   ├── scheduler/
│       │   ├── ingestion/
│       │   ├── normalization/
│       │   ├── ai/
│       │   ├── clustering/
│       │   ├── scoring/
│       │   ├── realtime/
│       │   ├── cleanup/
│       │   └── utils/
│       └── package.json
├── packages/
│   ├── shared/
│   │   ├── src/
│   │   │   ├── types/
│   │   │   ├── schemas/
│   │   │   ├── constants/
│   │   │   └── utils/
│   │   └── package.json
│   └── config/
│       └── package.json
├── docs/
├── scripts/
├── data/
├── package.json
└── pnpm-workspace.yaml
```

## 14. 目录说明

## 14.1 `apps/web`

负责本地前端界面。

建议页面包括：

- 实时时间线
- 来源配置
- 系统状态

建议模块组织方式：

- `pages/`：页面入口
- `features/`：按业务拆分，例如 timeline、sources、system-status
- `components/`：通用组件
- `services/`：调用后端 API
- `store/`：前端局部状态

## 14.2 `apps/server`

负责本地 API、调度、抓取、AI、聚合和清理。

建议职责如下：

- `api/`：路由与请求处理
- `db/`：数据库连接、迁移、SQL
- `repositories/`：数据访问层
- `services/`：业务服务编排
- `scheduler/`：定时任务调度
- `ingestion/`：API 与 Feed 拉取
- `normalization/`：标准化逻辑
- `ai/`：LM Studio client 与任务处理
- `clustering/`：聚合逻辑
- `scoring/`：评分逻辑
- `realtime/`：WebSocket 推送
- `cleanup/`：TTL 清理

## 14.3 `packages/shared`

用于存放前后端共享内容：

- TypeScript 类型定义
- DTO
- 分类常量
- 评分范围常量
- 公共校验 schema

这有助于降低前后端模型漂移。

## 14.4 `data`

用于本地运行时数据，例如：

- SQLite 数据文件
- 本地日志
- 调试输出

建议：

- 不将运行时数据提交到版本库

## 14.5 `docs`

用于集中存放 Loom 设计与实现文档。

目前已包含：

- 架构设计
- 数据模型设计
- MVP 模块拆解

后续还可以继续补：

- API 文档
- SQL 草案
- AI 工作流文档

## 15. Monorepo 建议

推荐采用轻量 monorepo：

- 根目录统一依赖管理
- web 和 server 分开
- shared 复用类型与常量

推荐工具：

- pnpm workspace

### 选择理由

- 比多仓库更容易同步模型与接口
- 比单仓大杂烩更清晰

## 16. 配置管理建议

建议分三层配置：

- 环境配置
- 数据源业务配置
- UI 本地偏好配置

### 环境配置

用于：

- 本地服务端口
- 数据库路径
- LM Studio 地址

### 数据源业务配置

存于数据库中，用于：

- 来源地址
- headers
- 频率
- 分类

### UI 本地偏好

用于：

- 默认筛选条件
- 排序方式
- 时间线展示偏好

## 17. 测试策略建议

虽然当前还未进入开发，但目录上最好提前预留测试结构。

### 服务端

- 单元测试：标准化、评分、聚合
- 集成测试：来源抓取到入库

### 前端

- 组件测试：卡片、筛选栏、来源配置表单
- 页面测试：时间线渲染、实时更新

### AI 相关

- prompt 输出结构检查
- 分类和翻译结果的基础断言

## 18. 日志与可观测性建议

MVP 阶段不需要复杂 observability 平台，但至少应具备：

- 服务启动日志
- 来源抓取日志
- AI 调用日志
- 清理任务日志
- WebSocket 推送日志

日志建议按模块分层输出，便于排查本地问题。

## 19. 推荐结论

如果要为 Loom v0.1 给出一套最务实的技术方案，推荐如下：

- 前端：React + TypeScript + Vite + Tailwind CSS
- 后端：Node.js + TypeScript + Fastify
- 数据库：SQLite
- 数据访问：Drizzle ORM 或原生 SQL 封装
- 实时通信：WebSocket
- AI：LM Studio OpenAI 兼容接口
- 目录结构：pnpm workspace monorepo

这套方案能兼顾以下几点：

- 足够轻量
- 易于本地运行
- 前后端协同顺畅
- 适合实时信息流
- 易于后续扩展

## 20. 后续建议

在本文件基础上，最适合继续落地的两个文档是：

- 数据库表结构 SQL 草案
- API 与页面交互设计文档

这两个文档将直接把当前方案推进到可以开始搭建项目骨架的程度。
