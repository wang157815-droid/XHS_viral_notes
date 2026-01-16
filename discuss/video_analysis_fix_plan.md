# 视频分析系统修复方案

## 修复进度

| 阶段 | 状态 | 完成时间 |
|------|------|---------|
| P0: ASR Bug | ✅ 已完成 | 2026-01-15 |
| P0-补充: Windows file:// URI | ⚠️ 需二次修复 | 2026-01-15 |
| P0-补充: file:// → Base64 | ✅ 已完成 | 2026-01-16 |
| P0-补充: 音频时长检查 | ✅ 已完成 | 2026-01-15 |
| P0-补充: URL ASR 超时优化 | ✅ 已完成 | 2026-01-15 |
| P1-a: 视频大小限制 | ✅ 已完成 | 2026-01-15 |
| P1-b: 压缩版URL优先选择 | ✅ 已完成（含自动转换） | 2026-01-16 |
| P1-c: 帧+ASR分离架构 | ✅ 已实现（默认关闭） | 已存在 |
| P2: 架构重构 | ⏳ 待定 | - |

---

## 问题诊断总结

### P0: ASR 全部失败（致命 Bug）✅ 已修复

**根本原因**：API 调用方式错误
- 原代码使用 `Transcription.async_call(file_urls=[local_path])`
- `Transcription` 异步接口的 `file_urls` **只接受 URL**，不支持本地路径
- 正确做法：本地文件应使用 `MultiModalConversation.call` + `file://` 协议

**修复内容**（`viral_agent/services/av_sync/qwen_asr.py`）：
```python
# 本地文件使用 MultiModalConversation.call
messages = [
    {"role": "system", "content": [{"text": ""}]},
    {"role": "user", "content": [{"audio": f"file://{absolute_path}"}]}
]
response = dashscope.MultiModalConversation.call(
    model="qwen3-asr-flash",
    messages=messages,
    asr_options={"enable_timestamps": True, "enable_itn": True}
)
```

**验证方式**：
```bash
uv run python scripts/test_asr_fix.py
```

---

### P0-补充: Windows file:// URI 兼容性 ⚠️ 需二次修复

**问题**：原代码使用 `f"file://{absolute_path}"` 拼接 URI，在 Windows 上会生成 `file://C:\Users\...` 格式，不符合 RFC 8089 标准。

**第一次修复**（2026-01-15）：使用 `pathlib.Path.as_uri()` 生成 `file:///C:/Users/...` 格式。

**但仍然失败**：日志显示 dashscope SDK 把 `file:///C:/` 解析成了 `/C:/`，导致路径无效：
```
本地 ASR: file:///C:/Users/.../audio.wav
本地 ASR 失败: The file: /C:/Users/... is not exists!
```

---

### P0-补充: file:// → Base64 编码 ✅ 已修复（2026-01-16）

**根本原因**：dashscope SDK 对 `file://` URI 的解析在某些版本/平台上存在问题。

**最终解决方案**：改用 Base64 编码上传音频，这是[阿里云官方文档](https://help.aliyun.com/zh/model-studio/audio-language-model)推荐的方式之一。

**修复代码**（`qwen_asr.py:191-207`）：
```python
# 读取文件并转为 Base64（避免 file:// URI 解析问题）
with open(abs_path, 'rb') as f:
    audio_data = f.read()
audio_base64 = base64.b64encode(audio_data).decode('utf-8')
# 格式：data:audio/wav;base64,{base64_data}
# 显式指定 MIME 类型，确保 SDK 正确解析
audio_uri = f"data:audio/wav;base64,{audio_base64}"

messages = [
    {"role": "system", "content": [{"text": ""}]},
    {"role": "user", "content": [{"audio": audio_uri}]}
]
```

**优点**：
- 跨平台兼容（Windows/Linux/macOS）
- 不依赖 SDK 对文件路径的解析
- 显式 MIME 类型确保 SDK 正确解析
- 阿里云官方推荐方式

---

### P1-b: 压缩版URL优先选择 + 自动转换 ✅ 已完成（2026-01-16）

**问题**：
1. 小红书视频有多个清晰度版本，原代码选择第一个可访问的URL（通常是高清版 ~25MB），导致超过 AI 的 7MB 限制
2. 小红书API返回的 `stream` 对象不包含 `_130.mp4` 压缩版，但CDN实际存在

**URL后缀含义**：
| 后缀 | 编码 | 预估大小 | API返回 |
|------|------|----------|---------|
| `_130.mp4` | H.264 压缩版 | ~4MB | ❌ 不返回 |
| `_259.mp4` | H.264 720p | ~8MB | ✅ 返回 |
| `_114.mp4` | H.265 720p | ~25MB | ✅ 返回 |
| `_115.mp4` | H.265 1080p | ~25MB | ✅ 返回 |

**修复方案**：

1. 新增 `try_get_compressed_url()` 函数（`xhs_utils/url_validator.py:64-107`）：
   - 自动转换 `_259.mp4` → `_130.mp4` URL（同时替换路径和文件名）
   - HEAD 请求验证压缩版是否可用
   - 结果缓存避免重复请求

2. 修改 `get_best_video_url_for_ai()` 函数（`xhs_utils/url_validator.py:142-279`）：
   - 新增 `try_compressed` 参数（默认 True）
   - 第一步：尝试获取压缩版URL
   - 如失败则按预估大小排序选择

**URL转换规则**：
```
原版: http://sns-video-hw.xhscdn.com/stream/79/110/259/xxx_259.mp4
压缩: http://sns-video-hw.xhscdn.com/stream/79/110/130/xxx_130.mp4
                                          ^^^             ^^^
                                         路径替换        后缀替换
```

**调用位置**：`video_ai_analyzer.py:472-476`

**测试脚本**：`scripts/test_url_selection.py`（测试6/7验证新功能）

---

### P0-补充: 音频时长检查 ✅ 已修复

**问题**：`qwen3-asr-flash` 模型（本地文件模式）最长只支持 **3 分钟** 音频，超长音频会导致 API 错误。

**修复方案**：
1. 新增 `_get_audio_duration()` 方法，使用 `ffprobe` 获取音频时长
2. 新增 `AudioTooLongError` 异常类
3. 在 `transcribe()` 方法中检查本地文件时长，超过 180 秒时抛出明确异常

```python
duration = self._get_audio_duration(audio_path)
if duration is not None and duration > self.LOCAL_MAX_DURATION:
    raise AudioTooLongError(
        f"音频时长 {duration:.1f}s 超过本地模式限制 ({self.LOCAL_MAX_DURATION}s)"
    )
```

**用户影响**：超过 3 分钟的音频会收到明确的错误提示，建议使用 URL 模式处理。

---

### P0-补充: URL ASR 超时优化 ✅ 已修复

**问题**：原代码 URL 模式轮询超时固定为 5 分钟，对于长音频（10分钟以上）会超时失败。

**修复方案**：动态计算超时时间
- 基础超时：5 分钟
- 每分钟音频额外增加：30 秒
- 未知时长默认：30 分钟

```python
URL_BASE_TIMEOUT = 300     # 基础超时 5 分钟
URL_TIMEOUT_PER_MIN = 30   # 每分钟音频额外增加的超时

if estimated_duration:
    extra_timeout = (estimated_duration / 60) * URL_TIMEOUT_PER_MIN
    max_wait = int(URL_BASE_TIMEOUT + extra_timeout)
else:
    max_wait = 1800  # 未知时长，使用 30 分钟默认超时
```

**示例**：
- 10 分钟音频 → 超时 = 300 + 10×30 = 600 秒（10分钟）
- 30 分钟音频 → 超时 = 300 + 30×30 = 1200 秒（20分钟）
- 未知时长 → 超时 = 1800 秒（30分钟）

---

### P1-a: 视频大小限制过严 ✅ 已修复

**原问题**：AVSync 和 AI 分析共用 15MB 限制，导致约 50% 视频无法处理

**修复方案**：分离两种场景的限制
- **AVSync 场景**：提升到 200MB（本地 ffmpeg 处理，不受 API 限制）
- **AI 分析场景**：保持 7MB（base64 后约 10MB，符合 API 限制），超过自动降级到 URL 模式

**修改文件**：
1. `viral_agent/services/video/video_enhanced_analyzer.py:85-99`
   - AVSync 下载限制提升到 `min(配置值, 200MB)`
   - 下载超时从 60s 提升到 120s
2. `.env.example:91-96`
   - 更新配置说明，默认值改为 100MB

**配置说明**：
```bash
# .env 中配置
VIDEO_MAX_SIZE_MB="100"  # AVSync 可处理最大 200MB，AI 自动限制在 7MB
```

---

### P1-b: 时间戳不准确 ✅ 已实现（默认关闭）

**问题**：URL 模式下，AI 模型稀疏抽帧导致时间戳不可靠

**发现**：帧+ASR 分离架构**已经在代码中实现**！

**代码位置**：`viral_agent/services/video/frame_asr_joint_analyzer.py`

**工作流程**（与推荐方案一致）：
```
AVSync 输出
  ├─ 帧（ffmpeg 抽取，带精确时间戳）
  └─ ASR（句粒度时间戳）
       ↓
FrameASRJointAnalyzer
  ├─ _select_frames() — 选择关键帧
  └─ _match_speech_to_frame() — 为每帧匹配语音
       ↓
构建 JSON: [{帧base64, t=12.4s, 语音="xxx"}, ...]
       ↓
VL 模型分析 → 事件+精确时间段
```

**启用方法**：
```bash
# .env 配置（需同时启用两项）
ENABLE_AV_SYNC=true
ENABLE_FRAME_ASR_ANALYSIS=true
VIDEO_SOURCE_MODE=proxy
```

**配置参数**：
```bash
FRAME_ASR_SAMPLE_STRATEGY="key"  # 采样策略：key/all/interval
FRAME_ASR_MAX_FRAMES="5"         # 单次API调用最多帧数
FRAME_ASR_API_DELAY="2.0"        # API调用间隔（秒）
```

**状态**：✅ 已实现，默认关闭，需手动启用

---

### P2: 架构重构

**问题**：`services/` 目录下有 58 个 Python 文件，违反 CLAUDE.md 规定的"每层文件夹不超过 8 个文件"

**待清理**：
- 根目录和子目录存在同名文件（冗余）
- 部分模块可以合并

**状态**：待定，不影响功能

---

## 验证清单

### 基础验证
- [ ] 运行 `uv run python scripts/test_asr_fix.py` 验证 ASR 修复

### 完整视频分析验证
运行完整视频分析，观察日志中：
- [ ] AVSync 下载限制应显示 100MB（或配置值）
- [ ] ASR 应使用 `MultiModalConversation.call` 而非 `Transcription.async_call`
- [ ] 大视频（>7MB）AI 分析应降级到 URL 模式
- [ ] ASR 应返回有效文本而非 "url error"

### 补充修复验证
- [ ] Windows 路径测试：file:// URI 应为 `file:///C:/...` 格式（三斜杠）
- [ ] 音频时长检查：超过 3 分钟的本地音频应抛出 `AudioTooLongError`
- [ ] URL 超时：长音频（>10分钟）ASR 识别应有足够的轮询超时时间
- [ ] 日志应显示：`音频时长: xxx.xs` 和 `URL ASR 超时设置: xxxs`

---

## 参考资料

- [阿里云 DashScope 录音文件识别文档](https://help.aliyun.com/zh/model-studio/qwen-speech-recognition)
- qwen3-asr-flash: 支持本地文件（通过 MultiModalConversation），最长 3 分钟
- qwen3-asr-flash-filetrans: 仅支持 URL，最长 12 小时
