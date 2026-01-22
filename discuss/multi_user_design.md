# 多用户并发系统设计方案

## 一、当前架构分析

### 现状
```
┌─────────────────────────────────────────────────────────┐
│                    当前单用户架构                         │
├─────────────────────────────────────────────────────────┤
│  用户认证: admin 单账户                                   │
│  Cookie:   全局共享（.env 中的 COOKIE）                   │
│  数据存储: 全局目录（datas/viral_analysis/）              │
│  任务执行: 同步阻塞，无队列                               │
└─────────────────────────────────────────────────────────┘
```

### 问题
1. **Cookie 冲突**：所有用户共享一个小红书账号，容易触发风控
2. **数据混杂**：不同用户的分析历史混在一起
3. **资源竞争**：多人同时操作会相互阻塞
4. **无法追溯**：不知道哪个任务是谁发起的

---

## 二、目标架构

```
┌─────────────────────────────────────────────────────────────────┐
│                       多用户并发架构                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                       │
│  │  用户A   │  │  用户B   │  │  用户C   │  ...                   │
│  │ Cookie_A │  │ Cookie_B │  │ Cookie_C │                       │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘                       │
│       │             │             │                              │
│       ▼             ▼             ▼                              │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                    任务队列（Redis/内存）                 │    │
│  │  [Task1: user_a, search] [Task2: user_b, analyze] ...   │    │
│  └─────────────────────────────────────────────────────────┘    │
│                              │                                   │
│                              ▼                                   │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │                     Worker Pool                          │    │
│  │  Worker1 ──► 执行 Task1 ──► 存入 datas/users/user_a/    │    │
│  │  Worker2 ──► 执行 Task2 ──► 存入 datas/users/user_b/    │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 三、核心改造模块

### 3.1 用户管理增强

**新增文件**: `viral_agent/auth/user_service.py`

```python
# 用户数据模型
class User:
    username: str
    password_hash: str
    role: str  # admin | user
    cookie: Optional[str]  # 用户自己的小红书 Cookie
    quota: UserQuota  # 配额限制
    created_at: datetime

class UserQuota:
    daily_search_limit: int = 500      # 每日搜索笔记数
    daily_analyze_limit: int = 100     # 每日分析任务数
    storage_limit_mb: int = 500        # 存储空间上限
    concurrent_tasks: int = 2          # 同时运行任务数
```

**API 变更**:
| 端点 | 当前 | 改造后 |
|------|------|--------|
| `POST /api/auth/register` | 无 | 新增：用户自助注册 |
| `POST /api/viral/cookie` | 全局 Cookie | 改为用户专属 Cookie |
| `GET /api/user/quota` | 无 | 新增：查询配额使用情况 |
| `POST /api/admin/users` | 无 | 新增：管理员创建用户 |

---

### 3.2 数据目录隔离

**目录结构变更**:
```
datas/
├── auth/
│   └── users.json              # 用户账户信息
├── users/                      # 【新增】用户数据根目录
│   ├── user_a/
│   │   ├── cookies.json        # 用户的小红书 Cookie
│   │   ├── viral_analysis/     # 分析结果
│   │   ├── excel_datas/        # 导出的 Excel
│   │   ├── cover_cache/        # 封面缓存
│   │   └── documents/          # 上传的知识库文档
│   ├── user_b/
│   │   └── ...
│   └── user_c/
│       └── ...
└── shared/                     # 【新增】共享资源
    ├── knowledge_base/         # 公共知识库
    └── chromadb/               # 向量数据库
```

**新增服务**: `viral_agent/services/user_data_service.py`
```python
class UserDataService:
    """用户数据隔离服务"""

    def get_user_data_dir(self, username: str) -> Path:
        """获取用户数据目录"""
        return Path(f"datas/users/{username}")

    def get_user_analysis_dir(self, username: str) -> Path:
        """获取用户分析结果目录"""
        return self.get_user_data_dir(username) / "viral_analysis"

    def get_user_storage_usage(self, username: str) -> int:
        """计算用户已用存储空间（MB）"""
        ...
```

---

### 3.3 任务队列系统

**方案选择**:
| 方案 | 优点 | 缺点 | 推荐场景 |
|------|------|------|---------|
| 内存队列 | 简单，无额外依赖 | 重启丢失，单机限制 | 小规模（<10用户） |
| Redis + RQ | 持久化，可横向扩展 | 需要 Redis 服务 | 中规模（10-100用户） |
| Celery | 功能强大，监控完善 | 配置复杂 | 大规模（>100用户） |

**推荐方案**: 先用**内存队列 + asyncio**，后续可平滑迁移到 Redis。

**新增文件**: `viral_agent/services/task_queue.py`
```python
from dataclasses import dataclass
from enum import Enum
from asyncio import Queue, Semaphore

class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"

@dataclass
class Task:
    task_id: str
    username: str
    task_type: str  # search | analyze | export
    params: dict
    status: TaskStatus
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    result: Optional[dict]
    error: Optional[str]

class TaskQueue:
    """任务队列管理器"""

    def __init__(self, max_workers: int = 4):
        self.queue = Queue()
        self.tasks: Dict[str, Task] = {}
        self.user_semaphores: Dict[str, Semaphore] = {}  # 每用户并发限制
        self.global_semaphore = Semaphore(max_workers)

    async def submit(self, username: str, task_type: str, params: dict) -> str:
        """提交任务，返回 task_id"""
        ...

    async def get_status(self, task_id: str) -> Task:
        """获取任务状态"""
        ...

    async def get_user_tasks(self, username: str) -> List[Task]:
        """获取用户的所有任务"""
        ...
```

---

### 3.4 API 端点改造

**任务提交改为异步**:
```python
# 当前（同步阻塞）
@app.post("/api/viral/search")
async def search_viral_notes(request: SearchRequest, username: str = Depends(...)):
    # 直接执行，阻塞等待结果
    result = await collector.search_and_collect(...)
    return result

# 改造后（异步任务）
@app.post("/api/viral/search")
async def search_viral_notes(request: SearchRequest, username: str = Depends(...)):
    # 提交到队列，立即返回 task_id
    task_id = await task_queue.submit(
        username=username,
        task_type="search",
        params=request.dict()
    )
    return {"task_id": task_id, "status": "pending"}

# 新增：查询任务状态
@app.get("/api/tasks/{task_id}")
async def get_task_status(task_id: str, username: str = Depends(...)):
    task = await task_queue.get_status(task_id)
    if task.username != username:
        raise HTTPException(403, "无权访问此任务")
    return task

# 新增：获取任务列表
@app.get("/api/tasks")
async def list_tasks(username: str = Depends(...)):
    return await task_queue.get_user_tasks(username)
```

---

### 3.5 前端改造

**任务状态轮询**:
```javascript
// 提交任务
async function submitSearch(params) {
    const res = await fetch('/api/viral/search', {
        method: 'POST',
        body: JSON.stringify(params)
    });
    const { task_id } = await res.json();

    // 开始轮询
    pollTaskStatus(task_id);
}

// 轮询任务状态
async function pollTaskStatus(taskId) {
    const poll = setInterval(async () => {
        const res = await fetch(`/api/tasks/${taskId}`);
        const task = await res.json();

        updateUI(task);  // 更新进度条/状态显示

        if (task.status === 'completed' || task.status === 'failed') {
            clearInterval(poll);
            if (task.status === 'completed') {
                showResult(task.result);
            } else {
                showError(task.error);
            }
        }
    }, 2000);  // 每 2 秒轮询
}
```

**或使用 WebSocket 实时推送**（更优雅但复杂度更高）

---

## 四、改造文件清单

### 新增文件
| 文件 | 说明 |
|------|------|
| `viral_agent/auth/user_service.py` | 用户管理服务（注册、配额） |
| `viral_agent/services/user_data_service.py` | 用户数据隔离服务 |
| `viral_agent/services/task_queue.py` | 任务队列服务 |
| `viral_agent/models/task.py` | 任务数据模型 |
| `web/templates/tasks.html` | 任务列表页面（可选） |

### 修改文件
| 文件 | 改动 |
|------|------|
| `viral_app.py` | API 端点改造（异步任务提交） |
| `viral_agent/auth/auth_service.py` | 支持多用户、角色权限 |
| `viral_agent/services/core/viral_collector.py` | 接收 user_cookie 参数 |
| `viral_agent/services/core/viral_analyzer.py` | 接收 user_data_dir 参数 |
| `web/templates/index.html` | 前端任务轮询逻辑 |

---

## 五、实施步骤（建议分阶段）

### 阶段一：用户数据隔离（1-2天）
1. 创建 `user_data_service.py`，实现目录隔离
2. 修改现有服务，支持传入 `username` 参数
3. 每个用户独立存储分析结果

### 阶段二：用户 Cookie 管理（1天）
1. 修改 Cookie 存储逻辑，改为用户专属
2. 修改 `viral_collector`，使用用户 Cookie
3. 前端 Cookie 设置界面适配

### 阶段三：任务队列（2-3天）
1. 实现内存任务队列
2. 改造 API 为异步提交
3. 前端轮询/WebSocket 支持

### 阶段四：配额与限流（1天）
1. 实现配额检查逻辑
2. 超限时返回友好提示
3. 管理员配额管理界面

### 阶段五：多用户注册（可选）
1. 用户自助注册 API
2. 邀请码机制（可选）
3. 管理员用户管理界面

---

## 六、数据库选型建议

### 当前：JSON 文件
- 优点：简单，无依赖
- 缺点：并发写入风险，查询效率低

### 推荐升级路径
```
JSON 文件 ──► SQLite ──► PostgreSQL
   │              │            │
   └─ 1-5用户     └─ 5-50用户   └─ >50用户
```

**SQLite 方案**（推荐下一步）:
```python
# 使用 SQLAlchemy + aiosqlite
from sqlalchemy.ext.asyncio import create_async_engine

engine = create_async_engine("sqlite+aiosqlite:///datas/app.db")
```

---

## 七、安全考虑

1. **Cookie 加密存储**：用户 Cookie 应加密后存储
2. **任务结果清理**：定期清理过期任务和数据
3. **API 限流**：防止单用户频繁请求
4. **日志审计**：记录用户操作日志

---

## 八、估算工作量

| 阶段 | 工作量 | 优先级 |
|------|--------|--------|
| 用户数据隔离 | 1-2天 | P0 |
| 用户 Cookie 管理 | 1天 | P0 |
| 任务队列 | 2-3天 | P1 |
| 配额与限流 | 1天 | P2 |
| 多用户注册 | 1天 | P2 |
| **总计** | **6-8天** | - |

---

## 九、是否需要执行？

如果确认要实施，建议从 **阶段一（用户数据隔离）** 开始，这是最核心的改动，其他功能可以逐步叠加。

请告诉我：
1. 是否需要先执行某个阶段？
2. 是否需要调整方案细节？
3. 用户规模预期是多少？（影响技术选型）
