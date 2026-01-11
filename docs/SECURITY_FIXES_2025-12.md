# Spider_XHS 安全修复总结

**修复日期**: 2025年12月
**修复版本**: v2.0 安全增强版
**修复人员**: Claude AI 辅助开发

---

## 📋 修复概览

本次安全修复共处理 **13 个安全问题**，按优先级分为 P0（紧急）、P1（高）、P2（中）、P3（低）四个等级。

| 优先级 | 问题数量 | 状态 |
|--------|----------|------|
| P0 紧急 | 3 | ✅ 已修复 |
| P1 高 | 3 | ✅ 已修复 |
| P2 中 | 5 | ✅ 已修复 |
| P3 低 | 2 | ✅ 已修复 |

---

## 🚨 P0 紧急问题

### 1. JWT 认证系统实现

**问题描述**: 原系统无任何认证机制，Cookie 等敏感配置完全裸露。

**修复方案**:
- 新建 `viral_agent/auth/` 认证模块
- 实现 JWT Token 认证（PyJWT + bcrypt）
- 首次启动自动创建 admin 账户并生成随机密码
- 强制首次登录后修改密码

**涉及文件**:
```
viral_agent/auth/
├── __init__.py          # 模块导出
└── auth_service.py      # 认证服务实现
```

**关键代码**:
```python
# auth_service.py
def verify_token_and_password_changed(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> str:
    """验证 JWT Token + 检查是否已修改密码"""
    username = verify_token(credentials)
    if check_must_change_password(username):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="必须先修改密码才能访问其他功能",
            headers={"X-Must-Change-Password": "true"}
        )
    return username
```

### 2. 路径遍历攻击防护

**问题描述**: `viral_app.py` 中的下载接口直接拼接用户输入的文件名，可能导致任意文件读取。

**修复方案**:
- 添加文件名正则校验（仅允许字母数字下划线连字符）
- 使用 `Path.resolve()` 验证最终路径在预期目录内

**关键代码**:
```python
import re
from pathlib import Path

# 路径遍历防护
if not re.match(r'^[\w\-]+$', task_id):
    raise HTTPException(status_code=400, detail="无效的任务ID格式")

file_path = Path(DOWNLOAD_DIR) / f"{task_id}.xlsx"
if not file_path.resolve().is_relative_to(Path(DOWNLOAD_DIR).resolve()):
    raise HTTPException(status_code=400, detail="非法的文件路径")
```

### 3. TLS 证书验证

**问题描述**: `xhs_pc_apis.py` 中存在 `verify=False`，禁用了 HTTPS 证书验证。

**修复方案**: 移除所有 `verify=False` 参数，恢复默认的 TLS 验证。

---

## 🔴 P1 高优先级问题

### 4. Cookie 敏感信息保护

**问题描述**: Cookie 信息在日志和 API 响应中明文展示。

**修复方案**:
- Cookie 状态 API 仅返回长度和是否存在，不返回内容
- 日志中对 Cookie 进行脱敏处理

### 5. 强制密码修改服务端校验

**问题描述**: 强制修改密码逻辑仅在前端生效，服务端未拦截。

**修复方案**:
- 新增 `verify_token_and_password_changed` 函数
- 所有受保护端点使用新验证函数
- 返回 HTTP 403 状态码表示需要修改密码

**涉及文件**: `viral_app.py`

```python
# 所有需要认证的端点都使用新的验证函数
@app.get("/api/viral/cookie/status")
async def get_cookie_status(
    username: str = Depends(verify_token_and_password_changed)
):
    ...
```

### 6. 前端 403 状态码处理

**问题描述**: 前端仅处理 401 未授权，未处理 403 需要修改密码。

**修复方案**:
```javascript
// authFetch 函数中添加 403 处理
if (response.status === 403) {
    const data = await response.clone().json().catch(() => ({}));
    if (data.detail && data.detail.includes('修改密码')) {
        showChangePasswordModal();
        throw new Error('请先修改密码');
    }
}
```

---

## 🟡 P2 中优先级问题

### 7. URL 编码双重编码问题

**问题描述**: `get_search_keyword` 先用 `quote()` 编码，再经 `splice_str` 的 `urlencode()` 导致双重编码。

**修复方案**:
```python
# 修复前
params = {"keyword": urllib.parse.quote(word)}

# 修复后 - 让 splice_str 统一处理编码
params = {"keyword": word}
```

### 8. URL 参数安全解析

**问题描述**: 使用字符串分割方式解析 URL 参数，可能被恶意构造的 URL 绕过。

**修复方案**:
```python
from urllib.parse import urlparse, parse_qs

# 安全解析 URL 参数
parsed = urlparse(url)
params = parse_qs(parsed.query)
xsec_token = params.get('xsec_token', [''])[0]
```

### 9. 请求超时设置

**问题描述**: 所有 `requests` 调用均未设置超时，可能导致线程永久阻塞。

**修复方案**:
```python
DEFAULT_TIMEOUT = 10  # 秒

# 所有请求添加超时参数
response = requests.get(url, headers=headers, timeout=DEFAULT_TIMEOUT)
response = requests.post(url, json=data, timeout=DEFAULT_TIMEOUT)
```

**涉及文件**: `apis/xhs_pc_apis.py`（约 20 处修改）

### 10. Excel 公式注入防护

**问题描述**: 用户控制的内容直接写入 Excel，可能导致公式注入攻击。

**修复方案**:
```python
def sanitize_excel_value(value):
    """防止 Excel 公式注入"""
    if isinstance(value, str) and value and value[0] in ('=', '+', '-', '@', '\t', '\r'):
        return "'" + value
    return value
```

**涉及文件**: `xhs_utils/data_util.py`

### 11. Cookie 'a1' 字段验证

**问题描述**: 签名生成依赖 Cookie 中的 'a1' 字段，但未验证其存在。

**修复方案**:
```python
def generate_request_params(cookies_str, api, data=''):
    cookies = trans_cookies(cookies_str)
    if 'a1' not in cookies:
        raise ValueError("Cookie 缺少必需的 'a1' 字段，请检查 Cookie 配置")
    a1 = cookies['a1']
    ...
```

---

## 🟢 P3 低优先级问题

### 12. 配置默认值安全

**问题描述**: `config.py` 中 `EXCEL_NAME` 默认包含路径分隔符。

**修复方案**: 修改为安全的默认文件名格式。

### 13. 启动脚本 JWT_SECRET 检查

**问题描述**: 脚本未能正确检测引号包裹的占位符值。

**修复方案**:
```bash
# 提取并去除引号
jwt_value=$(grep "^JWT_SECRET=" .env | sed 's/^JWT_SECRET=//' | tr -d '"' | tr -d "'")

# 检查多种占位符模式
if [ -z "$jwt_value" ] || \
   [[ "$jwt_value" == *"your-jwt-secret"* ]] || \
   [[ "$jwt_value" == *"replace-with"* ]] || \
   [[ "$jwt_value" == *"here"* ]]; then
    echo "❌ 错误: JWT_SECRET 未配置或使用了默认占位符"
    exit 1
fi
```

---

## 📁 修改文件清单

| 文件路径 | 修改类型 | 说明 |
|----------|----------|------|
| `viral_agent/auth/__init__.py` | 新增 | 认证模块导出 |
| `viral_agent/auth/auth_service.py` | 新增 | JWT 认证服务实现 |
| `viral_app.py` | 修改 | 集成认证、路径遍历防护 |
| `apis/xhs_pc_apis.py` | 修改 | 超时设置、TLS验证、URL解析 |
| `xhs_utils/xhs_util.py` | 修改 | URL编码、Cookie验证 |
| `xhs_utils/xhs_creator_util.py` | 修改 | URL编码修复 |
| `xhs_utils/data_util.py` | 修改 | Excel注入防护、下载超时 |
| `config.py` | 修改 | 安全默认值 |
| `run_spider.py` | 修改 | 移除未使用参数 |
| `scripts/start_viral_app.sh` | 修改 | JWT_SECRET 检查增强 |
| `web/templates/index.html` | 修改 | 403处理、移除死代码 |
| `tests/test_viral_app.py` | 修改 | 添加认证支持 |
| `.env.example` | 修改 | 添加 JWT_SECRET 配置项 |
| `README.md` | 修改 | 添加安全特性文档 |

---

## 🔧 部署检查清单

部署前请确认以下事项：

- [ ] 已复制 `.env.example` 为 `.env`
- [ ] 已配置强随机 `JWT_SECRET`（32字节十六进制）
- [ ] 已安装新增依赖：`pip install PyJWT 'passlib[bcrypt]'`
- [ ] 首次启动后记录日志中的 admin 初始密码
- [ ] 已通过 Web 界面修改 admin 初始密码
- [ ] 已配置有效的小红书 Cookie

---

## 📝 测试验证

### 认证测试

```bash
# 设置测试密码环境变量
export TEST_PASSWORD="your-new-password"

# 运行测试
python tests/test_viral_app.py
```

### 手动验证

1. **未登录访问**: 访问 `/api/viral/cookie/status` 应返回 401
2. **初始密码登录**: 登录后访问受保护 API 应返回 403
3. **修改密码后**: 正常访问所有 API

---

## 🔐 安全建议

1. **定期轮换 JWT_SECRET**: 建议每 90 天更换一次
2. **监控登录日志**: 关注异常登录尝试
3. **限制访问来源**: 生产环境建议配置 IP 白名单
4. **启用 HTTPS**: 部署时务必使用 HTTPS
5. **定期更新依赖**: 关注 PyJWT、passlib 等安全更新

---

*本文档由 Claude AI 自动生成，记录了 2025年12月 的安全修复工作。*
