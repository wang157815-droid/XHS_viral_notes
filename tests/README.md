# 测试文件说明

本目录包含Spider_XHS项目的核心测试文件。

## 测试文件概览

### 1. test_deepseek.py
**用途**: 测试国内大模型API集成

**测试内容**:
- DeepSeek API连接测试
- 验证环境变量配置（API Key、API Base、Model Name）
- 测试简单的AI对话功能
- 验证国内大模型API是否正常工作

**运行方法**:
```bash
python tests/test_deepseek.py
```

**前置条件**:
- 需要在`.env`文件中配置：
  - `OPENAI_API_KEY`: DeepSeek API密钥
  - `OPENAI_API_BASE`: API基础URL（默认：https://api.deepseek.com/v1）
  - `MODEL_NAME`: 模型名称（默认：deepseek-chat）

**预期结果**:
- API连接成功
- 能够正常调用AI模型并返回响应

---

### 2. test_knowledge_base.py
**用途**: 测试动态知识库系统

**测试内容**:
- 领域检测功能（detect_domain）
- 不同领域视频标题的准确识别
- 动态提示词生成功能
- 知识库是否正确加载和应用

**运行方法**:
```bash
python tests/test_knowledge_base.py
```

**测试用例**:
- 眼部护理领域检测
- 面部护理领域检测
- 唇部护理领域检测
- 彩妆领域检测
- 身体护理领域检测

**预期结果**:
- 各领域关键词能正确匹配
- 返回对应的专业知识库内容

---

### 3. test_viral_image.py
**用途**: 测试爆文分析系统（图片笔记专用）

**测试内容**:
- 图片笔记收集功能（ViralNoteCollector）
- 图片笔记特征提取
- AI深度分析功能
- Excel报告导出功能

**运行方法**:
```bash
python tests/test_viral_image.py
```

**测试流程**:
1. 收集指定关键词的爆款图片笔记
2. 对每篇笔记进行特征提取（封面OCR、产品植入分析等）
3. 调用AI进行深度分析
4. 导出Excel分析报告

**前置条件**:
- 需要配置有效的小红书Cookie
- 需要配置AI API（DeepSeek/GLM等）
- 需要安装OCR依赖（EasyOCR或PaddleOCR）

**预期结果**:
- 成功收集爆款笔记数据
- 正确提取笔记特征
- 生成完整的分析报告

---

### 4. test_ocr.py
**用途**: 测试OCR引擎功能

**测试内容**:
- EasyOCR引擎初始化测试
- PaddleOCR引擎初始化测试（降级方案）
- OCR文字识别准确性测试

**运行方法**:
```bash
python tests/test_ocr.py
```

**测试流程**:
1. 优先测试EasyOCR（推荐方案）
2. 如果EasyOCR不可用，测试PaddleOCR
3. 使用测试图片验证识别效果

**依赖安装**:
```bash
# 方案A：EasyOCR（推荐）
pip install easyocr

# 方案B：PaddleOCR（备选）
pip install paddlepaddle paddleocr
```

**预期结果**:
- OCR引擎成功初始化
- 能够识别中英文文字（首次运行会下载模型）

---

### 5. test_viral_app.py
**用途**: 测试爆文Agent Web应用功能

**测试内容**:
- Web服务健康检查
- Cookie管理API测试
- Excel导出功能测试

**运行方法**:
```bash
# 1. 先启动Web应用
python viral_app.py

# 2. 在另一个终端运行测试
python tests/test_viral_app.py
```

**测试端点**:
- `GET /health` - 服务健康检查
- `GET /api/viral/cookie/status` - Cookie状态查询
- `POST /api/viral/cookie` - Cookie保存
- `GET /api/viral/export/{task_id}` - Excel报告导出

**预期结果**:
- 所有API端点正常响应
- Cookie管理功能正常工作
- 能够访问Web界面（http://localhost:8000）

---

## 测试最佳实践

### 运行测试前
1. 确保已安装所有依赖：`pip install -r requirements.txt`
2. 配置`.env`文件（参考`.env.example`）
3. 如需测试爬虫功能，配置有效的小红书Cookie

### 测试顺序建议
1. **test_ocr.py** - 验证OCR基础依赖
2. **test_deepseek.py** - 验证AI API配置
3. **test_knowledge_base.py** - 验证知识库系统
4. **test_viral_app.py** - 验证Web服务
5. **test_viral_image.py** - 完整集成测试

### 常见问题

**问题1**: ModuleNotFoundError
- 解决：`pip install -r requirements.txt`

**问题2**: OCR初始化失败
- 解决：安装EasyOCR或PaddleOCR，首次运行会下载模型

**问题3**: API调用失败
- 解决：检查`.env`文件配置，确认API Key有效

**问题4**: Cookie无效
- 解决：从浏览器获取最新Cookie并更新

---

## 已删除的测试文件

以下临时测试文件已被删除（2024-11-10）：
- `test_glm45v.py` - GLM模型临时测试
- `test_video_analysis.py` - 视频分析临时测试
- `test_video_reality_check.py` - 视频时间戳验证测试
- `test_xhs_video.py` - 小红书视频采集测试
- `test_multimodal_analysis.py` - 多模态分析临时测试
- `test_all_images_ocr.py` - 批量OCR临时测试
- `test_text_analysis.py` - 文本分析临时测试
- `test_api_response.py` - API响应检查测试

这些文件的功能已整合到核心测试文件中，或不再需要。

---

## 维护说明

### 添加新测试
1. 在`tests/`目录下创建`test_*.py`文件
2. 遵循现有测试文件的命名和结构规范
3. 更新本README文档

### 测试文件规范
- 文件名：`test_<功能名>.py`
- 包含详细的docstring说明测试用途
- 使用loguru记录测试过程和结果
- 提供清晰的错误提示和解决方案

### 测试覆盖目标
- [ ] 数据采集功能测试
- [x] AI分析功能测试
- [x] OCR功能测试
- [x] Web API测试
- [x] 知识库系统测试
- [ ] Excel导出功能完整测试
- [ ] 性能和压力测试
