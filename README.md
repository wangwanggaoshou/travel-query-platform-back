# 旅途智览 - 后端 API

基于 Python FastAPI + SQLAlchemy 的旅游信息查询系统后端。

## 技术栈

- Python 3.11+
- FastAPI - Web 框架
- SQLAlchemy - ORM
- SQLite - 数据库
- uv - 包管理器

## 快速开始

### 安装依赖

```bash
cd D:\gcsj_4\back-end
uv sync
```

### 初始化数据

国内热门城市景点（约 40 条，不含境外），数据来自维基媒体与高德 POI；若配置高德 **Web 服务** Key 会补充坐标与配图。

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
│   ├── main.py          # FastAPI 入口
│   ├── config.py        # 配置管理
│   ├── database.py      # 数据库连接
│   ├── models/          # SQLAlchemy 模型
│   ├── schemas/         # Pydantic 模式
│   ├── api/             # API 路由
│   ├── services/        # 业务逻辑层
│   └── utils/           # 工具函数
├── crawler/             # 爬虫模块
├── data/
│   ├── travel.db        # SQLite 数据库
│   ├── seed.py          # 种子数据脚本
│   └── seeds/           # 种子数据 JSON
└── pyproject.toml       # 项目配置
```

## API 接口

| 模块 | 路径 | 说明 |
|------|------|------|
| 用户 | `/user/*` | 登录、注册、信息管理、偏好、签证 |
| 景点 | `/scenic/*` | 列表、详情、搜索、分类、收藏 |
| 攻略 | `/guide/*` | 列表、详情、搜索、分类、**AI Agent 生成** |

### 攻略 Agent

在 `back-end/.env` 中配置（留空则前端显示「待配置」，无法生成）：

- `GUIDE_AGENT_LLM_API_KEY` / `GUIDE_AGENT_LLM_BASE_URL` / `GUIDE_AGENT_LLM_MODEL` — OpenAI 兼容大模型
- `GUIDE_AGENT_WEB_SEARCH_API_KEY` / `GUIDE_AGENT_WEB_SEARCH_PROVIDER` — 联网搜索（`tavily` 或 `serper`）
## 测试账号

- 用户名: `test`
- 密码: `123456`
