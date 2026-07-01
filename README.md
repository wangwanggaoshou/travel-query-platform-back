# 旅途智览 - 后端 API

基于 Python FastAPI + SQLAlchemy 的旅游信息查询系统后端。

## 技术栈

| 技术 | 版本 | 说明 |
|------|------|------|
| Python | 3.11+ | 运行环境 |
| FastAPI | 0.115+ | Web 框架 |
| SQLAlchemy | 2.0+ | ORM |
| SQLite | - | 数据库 |
| uv | - | 包管理器 |

## 快速开始

### 安装依赖

```bash
cd D:\gcsj_4\back-end
uv sync
```

### 初始化数据

国内热门城市景点（约 40 条，不含境外），数据来自百度百科、维基导游与高德 POI；若配置高德 **Web 服务** Key 会补充坐标与配图。

```bash
# 删除旧库并重新爬取（推荐首次或换数据源后）
uv run python -m data.seed --reset

# 仅追加/更新（不删库）
uv run python -m data.seed
```

**高德 Key（后端搜索与种子）**：在 [高德控制台](https://console.amap.com/dev/key/app) 为应用勾选 **Web 服务** 平台并创建 Key，写入 `back-end/.env` 的 `AMAP_KEY=`。  
前端 `VITE_AMAP_KEY`（JS API）与后端 Web 服务 Key **不能混用**，否则会报 `USERKEY_PLAT_NOMATCH`。

### 启动开发服务器

```bash
uv run uvicorn app.main:app --reload --port 8080
```

### 访问 API 文档

- Swagger UI: http://localhost:8080/docs
- ReDoc: http://localhost:8080/redoc

## 项目结构

```
back-end/
├── app/
│   ├── main.py              # FastAPI 入口
│   ├── config.py            # 配置管理（环境变量）
│   ├── database.py          # 数据库连接
│   │
│   ├── api/                 # API 路由
│   │   ├── scenic.py        # 景点模块（列表/详情/搜索/分类/热门/推荐/AI Agent）
│   │   ├── guide.py         # 攻略模块（AI Agent 状态/生成）
│   │   ├── globe.py         # 3D地球模块（国家/地标/坐标解析/图片）
│   │   └── user.py          # 用户模块（登录/注册/信息/偏好/签证）
│   │
│   ├── models/              # SQLAlchemy 模型
│   │   ├── scenic.py        # 景点模型
│   │   ├── guide.py         # 攻略模型
│   │   ├── user.py          # 用户模型
│   │   ├── favorite.py      # 收藏模型
│   │   └── visa.py          # 签证模型
│   │
│   ├── schemas/             # Pydantic 模式
│   │   ├── scenic.py        # 景点请求/响应
│   │   ├── guide.py         # 攻略请求/响应
│   │   ├── recommend.py     # AI 推荐请求
│   │   └── user.py          # 用户请求/响应
│   │
│   ├── services/            # 业务逻辑层
│   │   ├── scenic_service.py    # 景点服务
│   │   ├── scenic_discover.py   # 爬虫聚合服务
│   │   ├── guide_service.py     # 攻略服务
│   │   ├── recommend_service.py # 推荐服务
│   │   ├── globe_service.py     # 3D地球服务
│   │   ├── landmark_images.py   # 地标图片服务
│   │   └── auth_service.py      # 认证服务
│   │
│   ├── agents/              # AI Agent 模块
│   │   ├── llm.py           # LLM 客户端
│   │   ├── guide_agent.py   # 攻略生成 Agent
│   │   ├── recommend_agent.py   # 推荐Agent
│   │   ├── config.py        # Agent 配置
│   │   └── tools/           # Agent 工具
│   │       ├── web_search.py    # 联网搜索
│   │       └── image_search.py  # 图片搜索
│   │
│   ├── data/                # 内置数据
│   │   └── world_landmarks.py   # 全球地标数据
│   │
│   └── utils/               # 工具函数
│       ├── response.py      # 统一响应格式
│       └── security.py      # JWT / 密码加密
│
├── crawler/                 # 爬虫模块
│   ├── baike.py              # 百度百科爬虫
│   ├── mediawiki.py           # 维基导游爬虫
│   └── amap_client.py         # 高德地图 POI 爬虫
├── data/
│   ├── travel.db            # SQLite 数据库
│   ├── seed.py              # 种子数据脚本
│   └── seeds/               # 种子数据 JSON
│
└── pyproject.toml           # 项目配置
```

## API 模块

| 模块 | 路径前缀 | 说明 |
|------|----------|------|
| 景点 | `/scenic` | 列表、详情、搜索、分类、热门、推荐、AI Agent 推荐 |
| 攻略 | `/guide` | AI Agent 状态、AI 生成攻略 |
| 3D地球 | `/globe` | 国家列表、坐标解析、地标列表、地标图片 |
| 用户 | `/user` | 登录、注册、信息管理、偏好、签证（已预留，前端未对接） |

### 主要接口

#### 景点模块

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/scenic/list` | 景点列表（分页、筛选、排序） |
| GET | `/scenic/detail/{id}` | 景点详情 |
| GET | `/scenic/search` | 搜索景点（支持爬虫聚合扩展） |
| GET | `/scenic/categories` | 景点分类列表 |
| GET | `/scenic/hot` | 热门景点 |
| GET | `/scenic/recommend` | 基础推荐 |
| GET | `/scenic/recommend/agent/status` | AI Agent 配置状态 |
| POST | `/scenic/recommend/agent` | AI Agent 智能推荐 |

#### 攻略模块

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/guide/agent/status` | AI Agent 配置状态 |
| POST | `/guide/generate` | AI 生成攻略（支持联网搜索） |

#### 3D地球模块

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/globe/countries` | 国家列表 |
| GET | `/globe/resolve` | 坐标解析国家 |
| GET | `/globe/landmarks/{country_key}` | 国家地标列表 |
| GET | `/globe/landmarks/images` | 地标图片搜索 |

## AI Agent 配置

在 `back-end/.env` 中配置（留空则前端显示「待配置」，无法生成）：

### 攻略 Agent

```env
# 大模型配置（OpenAI 兼容接口）
GUIDE_AGENT_LLM_API_KEY=your-api-key
GUIDE_AGENT_LLM_BASE_URL=https://api.deepseek.com
GUIDE_AGENT_LLM_MODEL=deepseek-chat

# 联网搜索（tavily | serper）
GUIDE_AGENT_WEB_SEARCH_API_KEY=your-search-api-key
GUIDE_AGENT_WEB_SEARCH_PROVIDER=tavily
```

### 推荐 Agent

与攻略 Agent 共用 LLM 配置，无需额外配置。

## 环境变量

完整的环境变量配置示例：

```env
# 应用配置
DEBUG=true
SECRET_KEY=your-secret-key-change-in-production

# 数据库
DATABASE_URL=sqlite:///./data/travel.db

# 高德 Web 服务 Key
AMAP_KEY=your-amap-web-service-key

# AI Agent - 大模型
GUIDE_AGENT_LLM_API_KEY=your-llm-api-key
GUIDE_AGENT_LLM_BASE_URL=https://api.deepseek.com
GUIDE_AGENT_LLM_MODEL=deepseek-chat

# AI Agent - 联网搜索
GUIDE_AGENT_WEB_SEARCH_API_KEY=your-search-api-key
GUIDE_AGENT_WEB_SEARCH_PROVIDER=tavily

# CORS（前端地址）
CORS_ORIGINS=["http://localhost:3000","http://127.0.0.1:3000"]
```

## 测试账号

- 用户名: `test`
- 密码: `123456`

## 开发说明

### 超时配置

- AI 接口（攻略生成、智能推荐）：120 秒
- 3D 地球接口（地标列表、坐标解析）：60 秒
- 地标图片搜索：45 秒
- 常规接口：15 秒

### 爬虫聚合

搜索景点时，若本地数据库无结果，可启用 `discover=true` 参数，系统将并行请求高德 POI、百度百科和维基导游，聚合结果后自动入库。

> **性能说明：** 百度百科替代了 Wikipedia 作为国内景点介绍的主要来源（国内 ~1s vs ~5s），Wikipedia 保留用于境外景点的英文页面。景点发现已实现多目的地并行、百科源并行，单次搜索发现耗时约 5 秒。
