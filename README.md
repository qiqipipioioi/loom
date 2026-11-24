# Loom 操作指南

## 项目简介

Loom 是一个本地优先的新闻聚合工具，用来抓取多个信息源并在本地进行聚合展示。

当前版本的核心行为如下：

- 只处理最近 `24` 小时内的新消息
- 数据库只保留最近 `24` 小时的消息
- 新消息在抓取后立即执行 AI 翻译和分类
- 不做翻译/分类回填，不扫描历史消息补跑 AI
- 如果 AI 处理失败，消息仍会保留，但界面会显示失败原因

## 环境要求

- Python `3.11+`
- macOS 或其他可运行 Python/FastAPI 的环境
- 可选：LM Studio

项目依赖定义见 [pyproject.toml](file:///Users/tuxun/Documents/work/loom/pyproject.toml)。

## 安装

在项目根目录执行：

```bash
python3 -m pip install -e .
```

安装完成后会提供命令行工具 `loomctl`。

## 启动与停止

### 启动服务

```bash
loomctl start
```

启动后默认访问地址：

```text
http://127.0.0.1:8000/
```

说明：

- 服务固定使用 `8000` 端口
- 启动时会自动清理 `8001`、`8002` 的占用进程
- 如果 `8000` 已被占用，会尝试自动释放

### 停止服务

```bash
loomctl stop
```

### 日志与运行文件

运行相关文件位于项目根目录下的 `data/`：

- 日志：`data/loom.log`
- PID：`data/loom.pid`
- 数据库：`data/loom.db`

## 页面说明

启动后打开浏览器访问：

```text
http://127.0.0.1:8000/
```

主要功能区：

- 时间线：查看聚合后的新闻卡片
- 详情面板：查看每条消息的标题、中文摘要、原文正文和原始链接
- 设置面板：管理来源、AI 配置、手动同步

## 基本使用流程

推荐按下面顺序操作：

1. 启动服务
2. 打开网页
3. 进入右上角“设置”
4. 添加或编辑来源
5. 配置 LM Studio
6. 点击“手动同步”
7. 查看时间线与详情

## 来源管理

在“设置”面板中可以管理来源。

支持的来源类型：

- `feed`：RSS / Atom
- `api`：返回 XML 或 JSON 的接口
- `x`：X/Twitter 账号

启动时会一次性导入 19 个市场公告与宏观数据订阅，轮询间隔为 1–3 分钟。
已有的同地址订阅会保留原配置，后续在设置中停用或删除这些订阅也会保留。

可进行的操作：

- 新增来源
- 编辑来源
- 删除来源
- 启用 / 停用来源
- 测试来源连通性
- 批量导入 X 账号

### 来源字段说明

- `name`：来源名称
- `source_type`：来源类型
- `endpoint`：来源地址
- `method`：HTTP 方法
- `category`：默认分类
- `poll_interval_minutes`：轮询间隔
- `enabled`：是否启用

## AI 配置

在“设置”面板中可以配置 LM Studio。

可配置项：

- `AI Base URL`
- `AI 模型`
- `API Key`
- 是否开启翻译
- 是否开启分类
- 分类候选列表

推荐流程：

1. 启动 LM Studio
2. 在 LM Studio 中加载模型
3. 在 Loom 设置中填写 Base URL 和模型名
4. 点击“测试 LM Studio”
5. 测试通过后保存配置

### AI 处理规则

- 仅对新抓取消息执行一次 AI 翻译和分类
- 中文消息默认不重复翻译
- 如果 AI 失败，不会回填重试
- 失败原因会显示在：
  - 系统状态中的“AI 最近失败”
  - 来源卡片的错误信息
  - 手动同步反馈

## 同步规则

### 手动同步

在“设置”面板点击“手动同步”即可。

同步结果会显示：

- 成功同步了多少个来源
- 抓取失败了多少个来源
- AI 处理失败了多少个来源
- 首个失败原因

### 定时同步

系统会按来源配置中的 `poll_interval_minutes` 周期自动同步。

### 时间窗口规则

当前版本固定规则：

- 每次启动或同步，处理最近 `24` 小时的消息
- 处理窗口与数据库保留窗口一致，服务中断后恢复时不会遗漏当天内容

这部分逻辑位于 [main.py](file:///Users/tuxun/Documents/work/loom/loom_app/main.py)：

- `SYNC_LOOKBACK_HOURS = 24`
- `persist_messages()`

## 数据保留规则

当前数据库仅保留最近 `24` 小时消息。

行为说明：

- 应用启动时会自动清理 `24` 小时前的旧消息
- 每轮同步结束后会再次清理 `24` 小时前的旧消息
- 清理后会自动重建聚合结果

抓取处理窗口和数据库保留窗口当前均为 `24` 小时。

## 状态说明

### 来源状态

来源可能出现以下状态：

- `healthy`：抓取成功，AI 处理正常
- `warning`：抓取成功，但 AI 翻译/分类失败
- `error`：抓取本身失败

### 系统状态

系统状态面板会显示：

- 网络状态
- LM Studio 状态
- AI Base URL
- AI 模型
- 翻译 / 分类开关
- AI 最近失败
- 最近同步时间
- 最近清理时间
- 已启用来源数
- 消息数 / 聚合组数

## 常用接口

如果需要用脚本或调试工具访问，可以使用以下接口：

- `GET /api/health`：健康检查
- `GET /api/sources`：来源列表
- `POST /api/sources`：新增来源
- `POST /api/sources/test`：测试来源
- `POST /api/sources/bulk-x`：批量导入 X 账号
- `GET /api/ai/config`：读取 AI 配置
- `PUT /api/ai/config`：保存 AI 配置
- `POST /api/ai/test`：测试 AI 连接
- `GET /api/timeline/clusters`：聚合时间线
- `GET /api/clusters/{cluster_id}/messages`：聚合详情
- `GET /api/system/status`：系统状态
- `POST /api/system/sync`：执行手动同步
- `WS /ws/stream`：实时事件流

## 常见问题

### 1. 页面能打开，但没有新消息

先检查：

- 来源是否启用
- 来源地址是否有效
- 来源测试是否通过
- 消息发布时间是否在最近 `2` 小时内

### 2. 消息抓到了，但没有翻译或分类

可能原因：

- LM Studio 未启动
- Base URL 或模型名错误
- 模型只输出思考过程，没有输出最终 JSON
- 连接被拒绝或超时

处理方法：

1. 打开设置
2. 点击“测试 LM Studio”
3. 检查“AI 最近失败”
4. 查看来源卡片上的失败原因

### 3. 为什么旧消息消失了

这是当前设计行为：

- 超过 `24` 小时的消息会被自动清理

### 4. 为什么某些来源返回了很多内容，但最终只看到少量消息

系统只处理最近 `24` 小时内的消息；更早的内容不会进入数据库。

### 5. X 来源提示网络不可达或访客令牌失败

优先在来源的“自定义请求头”中配置 X Developer Console 生成的官方 Bearer Token：

```json
{"Authorization": "Bearer YOUR_X_API_BEARER_TOKEN"}
```

配置后 Loom 会使用 X API v2 获取账号和帖子。未配置 Token 时，Loom 会先尝试 X 公开网页，再回退到 FxEmbed 的公开时间线 API。回退服务只接收公开账号名，不会收到来源中配置的 Cookie、Authorization 或其他自定义请求头。

公共时间线 API 可以通过环境变量替换为自托管实例；设置为空字符串可禁用该回退：

```bash
LOOM_X_PUBLIC_API_BASE_URL=https://your-fxembed.example/2 loomctl start
```

如果本机不能直接访问 `x.com` 或 `api.x.com`，需要在启动 Loom 时提供可用代理，例如：

```bash
HTTPS_PROXY=http://127.0.0.1:7890 loomctl start
```

端口请按本机代理软件的实际配置调整。连续连接失败后，Loom 会短暂暂停其他 X 来源请求，避免一次同步长时间卡住。

## 开发校验

开发时可执行以下命令：

```bash
python3 -m ruff check .
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

## 目录说明

```text
loom/
├── loom_app/
│   ├── main.py
│   ├── database.py
│   ├── schemas.py
│   ├── cli.py
│   └── static/
├── data/
├── tests/
├── pyproject.toml
└── README.md
```

## 当前版本要点

- 新闻抓取窗口：最近 `2` 小时
- 数据库存储窗口：最近 `24` 小时
- AI 仅对新消息执行一次
- 不做历史回填
- AI 失败原因会显示在界面中
