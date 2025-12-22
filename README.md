<p align="center">
  <a href="https://github.com/cv-cat/Spider_XHS" target="_blank" align="center" alt="Go to XHS_Spider Website">
    <picture>
      <img width="220" src="https://github.com/user-attachments/assets/b817a5d2-4ca6-49e9-b7b1-efb07a4fb325" alt="Spider_XHS logo">
    </picture>
  </a>
</p>


<div align="center">
    <a href="https://www.python.org/">
        <img src="https://img.shields.io/badge/python-3.7%2B-blue" alt="Python 3.7+">
    </a>
    <a href="https://nodejs.org/zh-cn/">
        <img src="https://img.shields.io/badge/nodejs-18%2B-blue" alt="NodeJS 18+">
    </a>
</div>



# Spider_XHS

**✨ 专业的小红书数据采集解决方案，支持笔记爬取，保存格式为excel或者media**

**✨ 小红书全域运营解决方法，AI一键改写笔记（图文，视频）直接上传**

**🚀 新增：智能爆文分析系统，自动提取爆款笔记特征，生成创作模型**

## ⭐功能列表

**⚠️ 任何涉及数据注入的操作都是不被允许的，本项目仅供学习交流使用，如有违反，后果自负**

| 模块           | 已实现                                                                             |
|---------------|---------------------------------------------------------------------------------|
| 小红书创作者平台 | ✅ 二维码登录<br/>✅ 手机验证码登录<br/>✅ 上传（图集、视频）作品<br/>✅查看自己上传的作品      |
|    小红书PC    | ✅ 二维码登录<br/> ✅ 手机验证码登录<br/> ✅ 获取无水印图片<br/> ✅ 获取无水印视频<br/> ✅ 获取主页的所有频道<br/>✅ 获取主页推荐笔记<br/>✅ 获取某个用户的信息<br/>✅ 用户自己的信息<br/>✅ 获取某个用户上传的笔记<br/>✅ 获取某个用户所有的喜欢笔记<br/>✅ 获取某个用户所有的收藏笔记<br/>✅ 获取某个笔记的详细内容<br/>✅ 搜索笔记内容<br/>✅ 搜索用户内容<br/>✅ 获取某个笔记的评论<br/>✅ 获取未读消息信息<br/>✅ 获取收到的评论和@提醒信息<br/>✅ 获取收到的点赞和收藏信息<br/>✅ 获取新增关注信息|
| 爆文分析系统 | ✅ 自动搜索爆款笔记<br/>✅ 多维度特征提取<br/>✅ 封面OCR文字识别<br/>✅ 产品植入时机分析<br/>✅ 营销场景识别<br/>✅ AI深度分析（支持国内大模型）<br/>✅ 生成爆文创作模型<br/>✅ Excel分析报告导出<br/>✅ Web可视化界面<br/>✅ 前端Cookie管理<br/>✅ 导出功能增强<br/>✅ **提示词模块化**（新增）<br/>✅ **RAG文档知识库**（新增）<br/>✅ **视频笔记深度分析**（新增） |


## 🌟 功能特性

### 数据采集功能
- ✅ **多维度数据采集**
  - 用户主页信息
  - 笔记详细内容
  - 智能搜索结果抓取
- 🚀 **高性能架构**
  - 自动重试机制
- 🔒 **安全稳定**
  - 小红书最新API适配
  - 异常处理机制
  - proxy代理
- 🎨 **便捷管理**
  - 结构化目录存储
  - 格式化输出（JSON/EXCEL/MEDIA）
- 🎯 **全新Web界面**（2025年12月重构）
  - 亮色主题设计，小红书红强调色
  - Plus Jakarta Sans + Noto Sans SC 专业字体
  - 卡片悬浮动画、数据更新动效
  - 告别紫蓝渐变"AI味"设计

### 🤖 爆文分析功能（新增）
- 📊 **智能特征提取**
  - 标题模式识别
  - 内容结构分析
  - 封面OCR文字识别
  - 营销元素提取
  - **所有图片OCR分析**（新增）
- 🎯 **深度分析能力**
  - 产品植入时机分析
  - 7种营销场景识别
  - 6种推荐方法分析
  - **AI文本深度语义分析**（升级）
  - **多模态AI图文联合分析**（新增）
- 📈 **创作模型生成**
  - 爆款标题公式
  - 内容框架模板
  - 图文配合策略
  - 最佳实践总结
  - Excel报告导出

### 🗂️ 知识库管理系统（最新）

#### 📋 结构化知识库（JSON配置）
- 🔧 **动态知识库配置**
  - JSON配置文件存储
  - 支持多领域知识管理（眼部护理、面部护理、唇部护理、彩妆、身体护理等）
  - 关键词自动匹配领域
  - 优先级和权重控制
- 🌐 **Web可视化管理**
  - 领域CRUD操作（创建、查看、更新、删除）
  - 关键词动态管理
  - 知识库测试工具
  - 配置导入导出
  - 自动备份机制（保留最近10个备份）
- 🎯 **智能领域检测**
  - 基于标题和描述的关键词匹配
  - 动态加载相关领域知识到AI提示词
  - 避免"提示词污染"问题

#### 📚 RAG文档知识库（新增）
- 🤖 **智能文档检索**
  - 支持PDF、Word、Markdown、TXT文档上传
  - 自动解析并向量化文档内容
  - 基于ChromaDB的语义检索
  - 与JSON配置融合，双轨制知识体系
- 📄 **文档管理功能**
  - Web界面一键上传文档
  - 多领域标签关联
  - 文档搜索和删除
  - 支持批量文档处理
- 🔍 **语义检索能力**
  - 向量相似度匹配
  - 元数据过滤（领域、格式、时间）
  - 自动整合到AI分析提示词
  - 提升分析精准度

### 🧩 提示词模块化系统（新增）
- 📝 **模块化架构**
  - 所有AI提示词集中在 `prompts/` 目录管理
  - 提示词模板与业务逻辑分离
  - 支持模板继承和组合
- 🔧 **核心组件**
  - `viral_analysis_prompts.py`：爆文深度分析提示词
  - `video_cover_prompts.py`：封面分类提示词
  - `video_title_prompts.py`：标题分类提示词
  - `video_timeline_prompts.py`：时间轴分析提示词
- 🎯 **构建函数**
  - `build_viral_analysis_prompt()`：构建爆文分析提示词
  - `parse_viral_analysis_response()`：解析AI响应
  - `build_video_metadata_prompt()`：构建视频元数据提示词

## 🎨效果图
### 处理后的所有用户
![image](https://github.com/cv-cat/Spider_XHS/assets/94289429/00902dbd-4da1-45bc-90bb-19f5856a04ad)
### 某个用户所有的笔记
![image](https://github.com/cv-cat/Spider_XHS/assets/94289429/880884e8-4a1d-4dc1-a4dc-e168dd0e9896)
### 某个笔记具体的内容
![image](https://github.com/cv-cat/Spider_XHS/assets/94289429/d17f3f4e-cd44-4d3a-b9f6-d880da626cc8)
### 保存的excel
![image](https://github.com/user-attachments/assets/707f20ed-be27-4482-89b3-a5863bc360e7)

## 🛠️ 快速开始
### ⛳运行环境
- Python 3.8+
- Node.js 18+

### 🎯安装依赖

#### 自动安装（推荐）
```bash
chmod +x scripts/install_deps.sh
./scripts/install_deps.sh
```

#### 手动安装
```bash
# 升级pip
pip install --upgrade pip

# 安装基础依赖
pip install -r requirements.txt

# 安装OCR依赖（可选，用于封面文字识别）
# 推荐方案：EasyOCR（与RAG无冲突）
pip install easyocr

# 备选方案：PaddleOCR（与ChromaDB冲突，二选一）
# pip install paddlepaddle paddleocr

# 安装RAG文档知识库依赖（可选）
pip install chromadb pypdf python-docx markdown

# 安装Node.js依赖
npm install
```

#### 依赖冲突解决

**OCR与RAG依赖冲突说明**：
- PaddleOCR 与 ChromaDB 存在 protobuf 版本冲突
- 推荐使用 EasyOCR + ChromaDB 组合，无冲突

```bash
# 方案1：同时使用OCR和RAG（推荐）
pip install easyocr chromadb pypdf python-docx markdown

# 方案2：仅使用RAG（不需要OCR）
pip uninstall paddlepaddle paddleocr -y
pip install chromadb pypdf python-docx markdown

# 方案3：仅使用OCR（不需要RAG）
# 保持PaddleOCR安装，不安装chromadb

# 其他常见问题
# numpy版本冲突
pip install numpy==1.26.0

# 使用国内镜像
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 🎨配置Cookie（重要！）

#### 获取Cookie步骤
1. **打开Chrome浏览器**，访问 https://www.xiaohongshu.com
2. **登录你的小红书账号**
3. **按F12打开开发者工具**，切换到"Network"（网络）标签
4. **刷新页面**（F5）
5. **找到任意请求**，点击查看"Request Headers"
6. **复制Cookie值**（很长的一串字符）
7. **创建.env文件**：复制`.env.example`为`.env`
8. **粘贴Cookie**：将Cookie值粘贴到`COOKIES='这里'`

![image](https://github.com/user-attachments/assets/6a7e4ecb-0432-4581-890a-577e0eae463d)

`.env`文件示例：
```bash
# 小红书Cookie（必填）
COOKIES='你的cookie字符串'

# AI分析配置（选填，用于爆文深度分析）
# 方案1：DeepSeek（推荐，性价比高）
OPENAI_API_BASE="https://api.deepseek.com/v1"
OPENAI_API_KEY="sk-xxx"
MODEL_NAME="deepseek-chat"

# 方案2：免费方案（智谱GLM）
# OPENAI_API_BASE="https://open.bigmodel.cn/api/paas/v4"
# OPENAI_API_KEY="xxx"
# MODEL_NAME="glm-4-flash"
```

![image](https://github.com/user-attachments/assets/5e62bc35-d758-463e-817c-7dcaacbee13c)

### 🚀运行项目

#### 方法一：使用配置文件（推荐）
1. 编辑 `config.py` 文件，设置你需要的参数
2. 运行简化脚本：
```bash
python easy_run.py
```

#### 方法二：命令行参数
```bash
# 搜索模式
python run_spider.py --mode search --query "美食" --num 20 --save excel

# 用户模式
python run_spider.py --mode user --user-url "用户链接" --save all

# 交互式模式（最友好）
python run_spider.py --mode interactive
```

#### 方法三：直接修改代码（传统方式）
修改 `main.py` 底部的参数后运行：
```bash
python main.py
```

#### 🎯 爆文分析系统使用指南（新功能）

**第一步：启动服务**
```bash
python viral_app.py
```

**第二步：打开浏览器**
访问 http://localhost:8000

**第三步：使用界面操作**

**Tab 1: 爆文分析**
1. **配置Cookie**（新功能！）：
   - 在页面顶部的"Cookie配置"卡片中直接输入Cookie
   - 点击"保存Cookie"按钮
   - 点击"检查Cookie状态"确认配置成功
   - 不再需要修改.env文件
2. **输入搜索关键词**：如"防脱精华"、"美食探店"、"护肤"等
3. **设置分析参数**：
   - 笔记数量：5-20篇（建议先少量测试）
   - 最小点赞数：1000（筛选爆款的阈值）
   - 启用视频分析：是否分析视频笔记（可选）
4. **点击"开始分析"**：系统会自动采集和分析
5. **查看分析结果**：
   - 标题特征：高频词汇、句式模式
   - 内容结构：开头、中间、结尾模式
   - 封面分析：OCR文字识别（支持EasyOCR）
   - 营销洞察：产品植入时机、推荐方式
   - AI深度分析：爆款原因、创作建议
6. **导出报告**：
   - 支持导出原始数据Excel（新增）
   - 支持导出分析结果Excel
   - 自动处理各种数据格式

**Tab 2: 知识库管理**（新功能！）
1. **查看领域列表**：左侧显示所有已配置的领域（眼部护理、面部护理等）
2. **编辑领域**：点击领域卡片，右侧显示详细配置表单
   - 基本信息：ID、名称、启用状态、优先级
   - 关键词管理：添加/删除关键词，用于自动匹配领域
   - 知识库内容：问题、引出方式、植入方式、示例
3. **测试领域检测**：
   - 输入标题和描述
   - 点击"测试检测"查看系统会匹配到哪些领域
   - 验证关键词配置是否合理
4. **导入导出配置**：
   - 导出：下载JSON配置文件备份
   - 导入：上传JSON文件批量更新配置
5. **自动备份**：每次保存都会自动备份，保留最近10个历史版本

**测试脚本**（可选）
```bash
python test_viral_image.py  # 测试图文笔记分析
python test_deepseek.py     # 测试国内大模型API
```

### 📝配置说明

#### config.py 主要参数
```python
# 爬虫模式：'search' | 'user' | 'notes'
SPIDER_MODE = 'search'

# 保存选项
SAVE_CHOICE = 'all'        # 全部保存
# SAVE_CHOICE = 'excel'     # 只保存Excel
# SAVE_CHOICE = 'media'     # 只保存媒体
# SAVE_CHOICE = 'media-video'  # 只保存视频
# SAVE_CHOICE = 'media-image'  # 只保存图片

# 搜索配置
SEARCH_CONFIG = {
    'query': '美食',        # 搜索关键词
    'query_num': 10,       # 获取数量
    'sort_type': 0,        # 0综合 1最新 2点赞 3评论 4收藏
    'note_type': 0,        # 0不限 1视频 2图文
    ...
}
```

#### 🤖 AI分析配置（可选）

##### 基础配置：文本深度分析
系统支持多种国内大模型API，成本比GPT-4降低95%：

```bash
# 方案1：DeepSeek（推荐，性价比最高）
OPENAI_API_BASE="https://api.deepseek.com/v1"
OPENAI_API_KEY="sk-deepseek-xxx"
MODEL_NAME="deepseek-chat"

# 方案2：智谱GLM（完全免费）
OPENAI_API_BASE="https://open.bigmodel.cn/api/paas/v4"
OPENAI_API_KEY="xxx"
MODEL_NAME="glm-4-flash"

# 方案3：通义千问（中文最强）
OPENAI_API_BASE="https://dashscope.aliyuncs.com/compatible-mode/v1"
OPENAI_API_KEY="sk-xxx"
MODEL_NAME="qwen-max"
```

##### 高级配置：多模态分析（图文联合理解）
如需分析图片与文字的配合关系，可配置支持视觉的AI模型：

```bash
# 多模态方案1：通义千问VL（推荐，中文多模态最强）
MULTIMODAL_API_BASE="https://dashscope.aliyuncs.com/compatible-mode/v1"
MULTIMODAL_API_KEY="sk-your-qwen-api-key"
MULTIMODAL_MODEL_NAME="qwen-vl-max"

# 多模态方案2：智谱GLM-4V（完全免费）
MULTIMODAL_API_BASE="https://open.bigmodel.cn/api/paas/v4"
MULTIMODAL_API_KEY="your-glm-api-key"
MULTIMODAL_MODEL_NAME="glm-4v"
```

💡 **配置建议**：
- **只做文本分析**：配置 DeepSeek 即可（约0.16元/100篇）
- **需要图文联合分析**：额外配置多模态模型（智谱GLM-4V完全免费）
- **需要RAG文档检索**：额外配置Embedding模型（见下方说明）

##### RAG Embedding配置（可选）
如需使用文档知识库功能，需配置Embedding模型。支持单独配置Embedding API：

```bash
# 方案1：阿里云text-embedding-v4（推荐，性价比最高）
EMBEDDING_API_BASE="https://dashscope.aliyuncs.com/compatible-mode/v1"
EMBEDDING_API_KEY="sk-your-dashscope-api-key"
EMBEDDING_MODEL="text-embedding-v4"
# 成本：约0.0007元/1000次调用（比OpenAI便宜70%）
# 申请地址：https://dashscope.console.aliyun.com/

# 方案2：共用主模型API（如果主模型也支持Embedding）
# 不配置EMBEDDING_API_*，系统会自动使用OPENAI_API_*
EMBEDDING_MODEL="text-embedding-v4"

# 方案3：OpenAI Embedding（需要OpenAI API或镜像）
EMBEDDING_API_BASE="https://api.openai.com/v1"
EMBEDDING_API_KEY="sk-your-openai-api-key"
EMBEDDING_MODEL="text-embedding-3-small"

# 方案4：本地Embedding（完全免费，离线可用）
USE_LOCAL_EMBEDDING=true
LOCAL_EMBEDDING_MODEL="paraphrase-multilingual-MiniLM-L12-v2"
# 需安装：pip install sentence-transformers
```

**配置优先级**：
1. 如果配置了 `EMBEDDING_API_KEY`，使用单独的Embedding API
2. 否则使用主模型的 `OPENAI_API_KEY`
3. 如果都没配置，使用ChromaDB默认embedding

**推荐方案**：
- **文本分析用DeepSeek + Embedding用阿里云**（方案1，成本最优）
- **完全免费方案**：本地Embedding模型（方案4）

### 🔍常见问题解答

#### Q1：爆文分析系统怎么用？
**A**：运行`python viral_app.py`后，打开浏览器访问 http://localhost:8000 即可使用可视化界面。

#### Q2：Cookie失效了怎么办？
**A**：重新登录小红书网页版，按照上面的步骤重新获取Cookie。现在可以直接在Web界面的Cookie配置卡片中输入新Cookie，无需修改.env文件。

#### Q3：提示"签名错误"？
**A**：
1. 检查Cookie是否正确复制完整
2. Cookie可能已过期，需要重新获取
3. 检查.env文件格式是否正确

#### Q4：爆文分析没有结果？
**A**：
1. 检查关键词是否太冷门
2. 降低"最小点赞数"阈值（比如从1000降到100）
3. 确保Cookie有效

#### Q5：OCR安装失败？
**A**：OCR功能是可选的。推荐使用EasyOCR（兼容性更好）：
```bash
# 方案A（推荐）：安装EasyOCR
pip install easyocr

# 方案B：如果EasyOCR不可用，使用PaddleOCR
pip install paddlepaddle==2.6.2 paddleocr -i https://pypi.tuna.tsinghua.edu.cn/simple
```

#### Q6：如何避免被限制？
**A**：
1. 控制请求频率，避免过快
2. 每次分析控制在10-20篇
3. 可以使用代理IP

#### Q7：多模态分析是什么？怎么配置？
**A**：多模态分析可以理解图片内容，分析图文配合关系。配置方法：
1. 推荐使用智谱GLM-4V（完全免费）
2. 在.env文件中添加：
   ```bash
   MULTIMODAL_API_BASE="https://open.bigmodel.cn/api/paas/v4"
   MULTIMODAL_API_KEY="your-api-key"
   MULTIMODAL_MODEL_NAME="glm-4v"
   ```
3. 申请地址：https://open.bigmodel.cn/

#### Q8：如何使用GLM-4V Plus进行视频分析？
**A**：GLM-4V Plus (GLM-4.5V) 支持直接分析视频URL，适合深度视频分析：
1. **配置环境变量**：
   ```bash
   # 视频分析配置
   VIDEO_MODEL_NAME="glm-4v-plus"  # GLM-4.5V
   VIDEO_ANALYSIS_MODE="full"      # 完整视频分析
   MULTIMODAL_API_BASE="https://open.bigmodel.cn/api/paas/v4"
   MULTIMODAL_API_KEY="your-api-key"
   ```
2. **分析模式**：
   - `full` 模式：直接分析视频，精确识别时间节点（需付费额度）
   - `metadata` 模式：基于标题描述推断，成本低速度快
3. **成本优化建议**：
   - 大批量分析使用 metadata 模式
   - 重点视频使用 full 模式深度分析
   - 利用自动缓存机制避免重复调用

#### Q9：为什么"热门标签"或"高产作者"数据为空？
**A**：可能的原因和解决方案：
1. **标签数据缺失**：
   - 检查Cookie是否有效（重新获取Cookie）
   - 尝试不同的热门关键词（如"护肤"、"美妆"、"穿搭"）
   - 有些笔记确实没有标签，这是正常的
2. **用户信息缺失**：
   - 验证Cookie配置正确
   - 运行检查脚本：`python quick_check_api.py`
3. **采集量太少**：
   - 增加采集数量（建议至少20篇）
   - 降低最小点赞数阈值
4. **数据处理问题**：
   - 查看生成的 `debug_*.json` 文件检查原始API响应
   - 确认 `note_card.tag_list` 和 `note_card.user` 字段存在

#### Q10：如何验证API是否能正常返回数据？
**A**：使用快速检查脚本：
```bash
python quick_check_api.py
```
脚本会自动检查：
- ✅ 搜索API是否能获取标签和用户信息
- ✅ 详情API是否能获取标签和用户信息
- ✅ 互动数据是否完整

成功输出示例：
```
✅ 结论: API能够正常获取标签和用户信息
   您的爬虫可以采集到这些数据，用于生成分析报告
```

#### Q11：视频笔记没有视频URL怎么办？
**A**：这是已知问题并已修复。如果你的历史数据中视频URL为空：

**问题表现**：
- JSON文件中 `video_addr` 和 `video_cover` 为 `None`
- 视频AI分析提示"所有视频笔记都没有视频地址"
- Excel报告中视频分析为空

**解决方案**：
1. **历史数据无法修复**，必须重新爬取视频笔记
2. **最新代码已修复**，新爬取的数据会正确包含视频URL
3. 验证修复效果：
   ```bash
   # 测试视频URL提取
   python test_video_fix.py

   # 或调试原始API数据
   python debug_video_api.py
   ```

**技术原因**：
- 原代码直接返回API原始数据，未调用 `handle_note_info` 处理
- 视频URL需要从原始数据手动拼接：`https://sns-video-bd.xhscdn.com/{origin_video_key}`
- 已在 `viral_collector.py:321` 修复

#### Q12：Excel报告中的数据显示有问题？
**A**：以下问题已在最新版本修复：

| 问题 | 修复说明 |
|-----|---------|
| 平均收藏率/点赞率为0 | 已添加计算逻辑，动态计算点赞率和收藏率 |
| 内容结构特征含义不明 | 已优化中文描述（如"包含列表/编号"而非"has_list"） |
| 热门标签TOP15为空 | 已添加数据校验和"暂无数据"提示 |
| 高产作者TOP5只显示数字 | 已修复数据类型检查，正确显示用户昵称 |
| 用户特征含义不清 | 已添加解释性说明文字 |
| 产品分析难以理解 | 已添加说明：基于AI智能分析的结果 |
| AI深度分析为空 | 已优化数据处理，支持多层嵌套中文键名 |

**如何更新**：
1. 拉取最新代码：`git pull`
2. 重新导出Excel：在Web界面点击"导出Excel"
3. 历史分析结果需重新导出才能看到优化效果

#### Q13：视频AI分析出现并发错误或格式错误？
**A**：这两个问题已在最新版本修复：

**问题1：并发过高（429错误）**
```
{"error":{"code":"1302","message":"您当前使用该API的并发数过高，请降低并发，或联系客服增加限额。"}}
```

**解决方案**：
- 已优化批量处理逻辑，**每次最多5个视频并发**
- 批次间自动暂停2秒，避免触发API限流
- 修改文件：`viral_agent/services/video_ai_analyzer.py:479-544`

**问题2：视频格式错误（400错误）**
```
{"error":{"code":"1210","message":"视频输入格式/解析错误"}}
```

**可能原因**：
1. 小红书视频URL可能过期或有访问限制
2. 部分视频格式可能不被API支持
3. 视频文件损坏或无法访问

**解决方案**：
- 已添加**智能回退机制**：格式错误时自动切换到基于标题和描述的分析
- 添加URL验证，过滤无效视频链接
- 所有异常都有兜底处理，确保分析流程继续
- 修改文件：`viral_agent/services/video_ai_analyzer.py:212-260`

**如何验证修复**：
```bash
# 拉取最新代码
git pull

# 重新运行视频分析
python viral_app.py
```

#### Q14：出现"Cannot run the event loop while another loop is running"错误？
**A**：这是异步事件循环冲突，已修复。

**错误详情**：
```python
RuntimeError: Cannot run the event loop while another loop is running
位置: viral_agent/services/viral_analyzer.py:901
```

**根本原因**：
- 在FastAPI等异步环境中，代码尝试嵌套运行事件循环
- Python的asyncio默认不允许嵌套

**解决方案**：
1. 已添加 `nest-asyncio` 依赖
2. 在 `viral_analyzer.py` 中自动应用补丁

**需要的操作**：
```bash
# 安装新依赖
pip install nest-asyncio>=1.5.8

# 或者
pip install -r requirements.txt
```

**修改文件**：
- `requirements.txt` (line 8)
- `viral_agent/services/viral_analyzer.py` (line 12-15)

#### Q15：如何使用RAG文档知识库？
**A**：RAG文档知识库可以上传成功案例文档，系统会自动检索相关内容辅助AI分析。

**使用步骤**：
1. **配置Embedding模型**（见上方"RAG Embedding配置"）
2. **启动Web界面**：`python viral_app.py`
3. **上传文档**：
   - 访问 http://localhost:8000
   - 切换到"知识库管理" Tab
   - 支持PDF、Word、Markdown、TXT格式
   - 填写标题、描述，选择关联领域
4. **自动集成**：
   - 分析爆款笔记时，系统自动检索相关文档
   - 整合到AI提示词中，提升分析精准度

**测试RAG功能**：
```bash
python test_rag_system.py
```

**常见问题**：
- **Embedding失败404错误**：国内大模型API不支持OpenAI Embedding，使用本地模型
- **ChromaDB安装冲突**：卸载PaddleOCR，改用EasyOCR
- **检索无结果**：检查Embedding配置，确认文档已上传

#### Q16：RAG与JSON知识库的区别？
**A**：系统采用双轨制知识库设计：

| 特性 | JSON配置 | RAG文档库 |
|------|----------|-----------|
| **数据类型** | 结构化规则 | 非结构化文档 |
| **适用场景** | 固定模板、产品植入技巧 | 成功案例、经验总结 |
| **更新方式** | Web表单编辑 | 上传文档 |
| **检索方式** | 关键词匹配 | 语义向量检索 |
| **管理成本** | 手动维护 | 自动解析 |

**推荐使用**：
- 结构化知识（如植入方式）→ JSON配置
- 案例文档（如爆款笔记分析）→ RAG文档库
- 两者自动融合，互不影响

#### Q17：代码修复验证
**A**：使用以下脚本验证所有修复是否正确应用：

```bash
# 测试RAG系统
python test_rag_system.py

# 测试数字格式转换（中文数字解析）
python test_number_parser.py
```

**验证检查项**：
- ✅ 文档解析（PDF/Word/MD/TXT）
- ✅ RAG向量检索
- ✅ 知识库统一接口
- ✅ 中文数字格式转换（"2.3万" → 23000）

## 📊 图文分析系统升级说明

### 🚀 最新升级（专注图文笔记深度分析）

我们对图文笔记分析系统进行了全面升级，按照以下顺序完成三项核心改进：

#### 1️⃣ AI文本深度分析 ✅
**改进内容**：
- ✅ 发送**完整标题+正文**（旧版只发送前200字）
- ✅ 增加到**6大深度分析维度**：
  - 语义理解：标题策略分析
  - 内容结构：正文框架提取
  - 情感共鸣：表达方式洞察
  - 价值传递：核心卖点识别
  - 差异化策略：创新角度建议
  - 实操建议：可落地的创作指南

**测试命令**：
```bash
python test_text_analysis.py
```

#### 2️⃣ 多模态AI分析（图文联合理解）✅
**新增功能**：
- ✅ 分析笔记的**所有图片**（不只是封面）
- ✅ **图片+文字联合分析**，理解图文配合策略
- ✅ 支持3种多模态模型：
  - 通义千问VL (qwen-vl-max) - 中文最强
  - 智谱GLM-4V (glm-4v) - 完全免费
  - GPT-4V (gpt-4-vision-preview)

**分析维度**：
- 图片内容：视觉焦点、叙事顺序
- 图文配合：呼应关系、配合逻辑
- 视觉呈现：封面吸引力、排版设计
- 成功要素：可复用的创作技巧

**测试命令**：
```bash
python test_multimodal_analysis.py
```

#### 3️⃣ 所有图片OCR文字提取 ✅
**扩展功能**：
- ✅ 分析笔记的**全部图片**（1-9张）
- ✅ 对比**封面 vs 其他图片**的文字策略
- ✅ 统计**文字覆盖率**、高频关键词
- ✅ 分析图片数量分布规律

**测试命令**：
```bash
python test_all_images_ocr.py
```

### 📈 分析能力升级对比

| 分析项目 | 升级前 | 升级后 |
|---------|--------|--------|
| **文本分析** | 统计（词频、长度） | 统计 + **AI语义深度理解** |
| **正文范围** | 前200字 | **完整正文** |
| **图片数量** | 1张（封面） | **1-9张（全部）** |
| **图文关联** | ❌ 无 | ✅ **多模态AI联合分析** |
| **OCR范围** | 仅封面 | **所有图片** |
| **分析维度** | 5个基础维度 | **6个深度维度** |

### 💰 成本说明

| 功能模块 | 推荐方案 | 成本 |
|---------|---------|------|
| **文本深度分析** | DeepSeek | 约0.16元/100篇 |
| **多模态分析** | 智谱GLM-4V | **完全免费** |
| **OCR文字提取** | EasyOCR/PaddleOCR | 本地免费 |

### 🎯 快速配置多模态分析

**步骤1：申请API密钥**
访问 https://open.bigmodel.cn/ 注册并申请免费的GLM-4V API

**步骤2：配置环境变量**
在 `.env` 文件中添加：
```bash
MULTIMODAL_API_BASE="https://open.bigmodel.cn/api/paas/v4"
MULTIMODAL_API_KEY="你的API密钥"
MULTIMODAL_MODEL_NAME="glm-4v"
```

**步骤3：运行测试**
```bash
python test_multimodal_analysis.py
```

## 🗂️ 知识库管理系统详解

### 系统架构

动态知识库系统采用**三层架构**，避免"提示词污染"问题：

```
1. 关键词检测层（快速）
   └─> 根据标题/描述关键词判断领域

2. 知识库加载层（动态）
   └─> 只加载相关领域的专业知识

3. AI分析层（精准）
   └─> 基础提示词 + 领域知识 → 精准分析
```

**核心优势**：
- ✅ **避免污染**：面部护理视频不会加载眼部知识
- ✅ **精准匹配**：自动选择最相关的领域知识
- ✅ **易于扩展**：Web界面或代码添加新领域
- ✅ **性能优化**：按需加载，减少token消耗

### 添加新领域

#### 方法1：通过Web界面（推荐）

1. 访问 http://localhost:8000
2. 切换到"知识库管理"Tab
3. 点击"添加"按钮
4. 按提示输入：
   - 领域ID（英文+下划线，如 `hair_care`）
   - 领域名称（中文，如 `头发护理`）
   - 关键词（逗号分隔，如 `头发,洗发水,护发`）
5. 创建后在右侧编辑详细内容

#### 方法2：通过代码（高级）

编辑 `viral_agent/config/knowledge_base.json`：

```json
{
  "domains": [
    {
      "id": "hair_care",
      "name": "头发护理",
      "enabled": true,
      "priority": 10,
      "keywords": ["头发", "洗发", "护发", "发膜"],
      "knowledge": {
        "problems": ["头发干燥", "头皮油腻", "脱发"],
        "intro_ways": ["发质问题引出", "护发经验分享"],
        "embed_ways": ["洗护流程植入", "护发手法展示"],
        "examples": []
      }
    }
  ]
}
```

### 知识库配置管理

**导出配置**：
- 点击"导出配置"下载JSON文件
- 用途：备份、迁移、分享配置

**导入配置**：
- 点击"导入配置"上传JSON文件
- 用途：恢复备份、批量修改、团队协作

**测试检测**：
- 输入标题和描述
- 点击"测试检测"查看系统会匹配哪些领域
- 验证关键词配置是否合理

### 最佳实践

1. **关键词设置**：每个领域添加5-10个核心关键词
2. **优先级控制**：重要领域设置更高优先级（0-100）
3. **定期备份**：系统自动保留最近10个备份版本
4. **测试验证**：添加关键词后立即测试检测功能

### 故障排查

**Q: 检测不到预期的领域？**
- A: 检查标题中是否包含关键词，在知识库管理中添加新关键词

**Q: 同时检测到多个领域？**
- A: 正常现象，系统会按优先级排序，可调整优先级值

**Q: 如何禁用某个领域？**
- A: 在知识库管理中将领域的"启用"状态设为否

## 🎬 视频分析功能详解

### 🚀 视频笔记深度分析升级（2025年12月）

本次升级将视频笔记分析能力**完全对齐图文笔记**，实现全面深度分析：

#### 升级概览

| 分析维度 | 升级前 | 升级后 |
|---------|--------|--------|
| **封面分析** | 基础分类（4大类12子类） | **40+字段深度分析**（文字/颜色/布局/视觉风格） |
| **内容分析** | ❌ 无 | ✅ **4维分析**（开场钩子/结构/情感/收尾） |
| **产品分析** | 7个时间节点 | **12数据点**（含营销场景/CTA类型等） |
| **知识库** | 通用配置 | **领域专属视频知识**（8领域） |
| **综合推理** | ❌ 无 | ✅ **视频爆文模型生成** |

#### 新增分析能力

**1. 视频封面深度分析（40+字段）**

| 维度 | 分析内容 |
|-----|---------|
| **文字分析** | 是否有文字、文字数量、主标题、副标题、位置分布 |
| **颜色分析** | 主色调、配色方案、颜色数量、是否高对比 |
| **布局分析** | 布局类型、是否有人物、是否有产品、人物位置 |
| **视觉风格** | 滤镜风格、光线效果、整体氛围、设计感评分 |

**2. 视频内容质量分析（4维度）**

| 维度 | 分析要点 | 评分指标 |
|-----|---------|---------|
| **开场钩子** | 钩子类型、吸引力、前3秒策略 | 0-100分 |
| **内容结构** | 叙事结构、节奏把控、信息密度 | 结构类型 |
| **情感曲线** | 情感变化、高潮点、共鸣设计 | 曲线类型 |
| **收尾策略** | CTA类型、引导效果、收尾质量 | 0-100分 |

**3. 视频产品深度分析（12数据点）**

| 编号 | 数据点 | 说明 |
|-----|-------|------|
| A | 产品出现时间 | 首次出现的时间点（秒） |
| B | 产品使用时间 | 实际演示的时间点（秒） |
| C | 干货开始时间 | 实用内容开始点（秒） |
| D | 内容类型 | 大类-子类（如：干货教程-手法干货） |
| E | 切入点 | 如何引入主题 |
| F | 引出方式 | 如何引入产品 |
| G | 植入方式 | 产品融入方式 |
| **H** | **营销场景** | 7种场景识别（新增） |
| **I** | **推荐方法** | 6种方法识别（新增） |
| **J** | **产品分类** | 主类-子类识别（新增） |
| **K** | **CTA类型** | 行动号召类型（新增） |
| **L** | **CTA时间** | CTA出现时间点（新增） |

**4. 领域专属视频知识库**

每个领域现在包含 `video_knowledge` 配置：

```json
{
  "video_knowledge": {
    "hook_types": ["熬夜垮脸/眼周问题展示", "对比效果前置"],
    "content_structures": ["眼部手法教程", "护理流程展示"],
    "optimal_timing": {
      "product_appear": "15-30秒",
      "product_use": "30-60秒",
      "cta": "视频末尾5秒内"
    },
    "cover_styles": ["眼部特写+效果对比", "产品手持展示"],
    "video_examples": [...]
  }
}
```

**5. 视频爆文模型综合推理**

系统整合所有分析结果，生成可执行的视频创作策略：

```
输入数据：
├─ 封面统计（40+字段汇总）
├─ 内容质量分析（4维评分）
├─ 产品分析（12数据点）
├─ 时间轴统计（7节点分布）
└─ 领域知识库

    ↓ AI综合推理

输出：
├─ 最佳封面策略
├─ 内容结构建议
├─ 产品植入时机
├─ CTA优化建议
└─ 领域专属技巧
```

#### 配置方式

**启用视频深度分析**（需要多模态模型）：

```bash
# .env 配置
MULTIMODAL_API_KEY=your-key
MULTIMODAL_API_BASE=https://open.bigmodel.cn/api/paas/v4
MULTIMODAL_MODEL_NAME=glm-4v-plus
```

启动日志会显示：
```
✓ 多模态分析已启用: glm-4v-plus
✓ 视频内容质量分析器已初始化（4维分析）
✓ 视频产品深度分析器已初始化（12数据点）
```

---

### 分析维度

#### 1. 封面分类（4大类12子类）

**个人形象类**
- 颜值图：展示博主面部或全身
- 素颜图：无妆或轻妆真实状态
- 状态对比图：使用前后效果对比

**产品展示类**
- 纯产品图：产品单独或多产品展示
- 人物与产品互动：手持或使用产品
- 博主+产品组合：拼图形式

**内容呈现类**
- 流程图/教程：步骤指导图解
- 干货截图：APP界面或数据图表
- 科普拼图：知识点解释

**专业聚焦类**
- 眼部状态展示：眼部问题改善
- 护肤/化妆过程：美容步骤展示
- 专业人设/明星相关：专业形象

#### 2. 标题分类（9大类）

1. **问题解决类** - 痛点+解决方案
2. **干货指导类** - 方法/技巧分享
3. **效果展示类** - 效果描述
4. **数字营销类** - 数字化标题
5. **年龄相关类** - 年龄标签定位
6. **热点引流类** - 热点话题
7. **产品推广类** - 产品推荐
8. **场景应用类** - 场景化内容
9. **人设相关类** - 博主人设

#### 3. 时间轴分析（7个数据点）

- **A: 产品出现时间** - 首次展示时间点（秒）
- **B: 产品使用时间** - 实际演示时间点（秒）
- **C: 干货开始时间** - 实用内容开始点（秒）
- **D: 内容类型** - 大类-子类（如：干货教程-手法干货）
- **E: 切入点** - 如何引入主题（眼部问题/熬夜场景等）
- **F: 引出方式** - 如何引入产品（自用分享/问题引出等）
- **G: 植入方式** - 产品融入方式（流程植入/手持口播等）

### 视频分析模式

**full模式**（完整分析）：
- 直接分析视频URL
- 精确识别时间节点
- 需要付费额度（GLM-4V Plus）

**metadata模式**（元数据分析）：
- 基于标题描述推断
- 成本低速度快
- 适合批量分析

### 输出格式示例

**封面分类**：
```
产品展示类-人物与产品互动-单图
```

**标题分类**：
```
问题解决类-眼部问题+干货手法
```

**时间轴分析**：
```
Result_30s,60s,45s,干货教程-手法干货,眼部问题,自用分享,干货手法中植入
```

### 优化建议生成

系统自动生成三类优化建议：

1. **封面设计建议**
   - 最受欢迎的封面类型
   - 图片形式选择（单图/拼图）
   - 视觉元素建议

2. **标题写作建议**
   - 流行标题类型
   - 最佳长度范围
   - 高频关键词使用

3. **视频节奏建议**
   - 产品最佳出现时机
   - 干货内容开始时间
   - 产品植入策略

### 🗝️注意事项
- Cookie会过期，需要定期更新（一般1-7天）
- 笔记URL中的xsec_token有时效性
- 建议添加适当延时，避免请求过于频繁
- 结果保存在 `download/` 目录下
- 仅供学习研究使用，请遵守相关法律法规

## 🏗️ 系统架构优化（2025年12月）

### 优化概览

本次架构升级包含7个阶段，全面提升代码质量和可维护性：

| 阶段 | 优化内容 | 涉及文件 |
|------|---------|---------|
| **阶段1** | 数据模型清晰化 | `viral_agent/models/` |
| **阶段2** | 服务层解耦 | `viral_agent/services/` |
| **阶段3** | 视频分析能力增强 | `video_*.py` 系列 |
| **阶段4** | Excel导出优化 | `export_service.py` |
| **阶段5** | 提示词模块化重构 | `viral_agent/prompts/` |
| **阶段6** | RAG知识库系统 | `rag_service.py`, `knowledge_retriever.py` |
| **阶段7** | **视频分析对齐优化**（新增） | `video_content_analyzer.py`, `video_product_analyzer.py`, `synthesis_service.py` |

### 阶段1-2：数据模型与服务层

**数据模型清晰化**：
- 所有数据结构采用强类型定义（`TypedDict`、`dataclass`）
- 统一的数据模型位于 `viral_agent/models/` 目录
- 核心模型包括：`ViralNote`、`VideoAnalysisResult`、`Document`

**服务层解耦**：
- 每个服务模块职责单一
- 服务间通过接口通信，降低耦合度
- 支持依赖注入，便于测试

### 阶段3-4：视频分析与导出

**视频分析能力增强**：
- 封面分类器：4大类12子类智能分类
- 标题分类器：9大类40+子分类
- 时间轴分析：7个关键时间点提取
- 支持批量分析和统计汇总

**Excel导出优化**：
- 分析结果自动格式化
- 支持多Sheet结构化导出
- 图表和统计数据自动生成

### 阶段5：提示词模块化重构

将所有硬编码的AI提示词抽取为模块化组件：

```
viral_agent/prompts/
├── __init__.py                        # 模块导出
├── video_cover_prompts.py             # 封面分析提示词
├── video_title_prompts.py             # 标题分析提示词
├── video_timeline_prompts.py          # 时间轴分析提示词
├── viral_analysis_prompts.py          # 爆文分析提示词
├── video_content_prompts.py           # 内容质量分析提示词（新增）
├── video_content_prompts_parser.py    # 内容分析解析器（新增）
├── video_product_prompts.py           # 产品分析提示词（新增）
├── video_product_prompts_parser.py    # 产品分析解析器（新增）
├── video_synthesis_prompts.py         # 综合推理提示词（新增）
└── video_synthesis_prompts_parser.py  # 综合推理解析器（新增）
```

**viral_analysis_prompts.py 核心组件**：
```python
# 系统角色提示词
VIRAL_ANALYZER_SYSTEM_PROMPT = "..."

# 深度分析模板
VIRAL_DEEP_ANALYSIS_TEMPLATE = """..."""

# JSON响应规范
VIRAL_ANALYSIS_JSON_SCHEMA = {...}

# 提示词构建函数
def build_viral_analysis_prompt(keyword, samples, features, knowledge_section=""):
    """构建爆文深度分析提示词"""
    ...

def parse_viral_analysis_response(response):
    """解析AI分析响应"""
    ...

def build_video_metadata_prompt(base_prompt, video_url, title, description):
    """构建视频元数据分析提示词"""
    ...
```

**模块化优势**：
- **集中管理**：所有提示词在 `prompts/` 目录下统一维护
- **可复用性**：提示词模板可在不同服务间共享
- **易于维护**：修改提示词无需改动业务逻辑代码
- **类型安全**：构建函数提供参数校验
- **版本控制**：提示词变更可追踪

### 阶段6：RAG知识库系统

**双轨制知识库架构**：

```
┌─────────────────────────────────────────────────────────┐
│              知识库系统（双轨制）                          │
├──────────────────────┬──────────────────────────────────┤
│  轨道1: 结构化知识     │  轨道2: 文档知识库 + RAG          │
├──────────────────────┼──────────────────────────────────┤
│ • JSON配置文件        │ • 文档上传（Word/PDF/MD/TXT）    │
│ • 手动填写表单        │ • 自动解析向量化                  │
│ • 固定规则和模板      │ • ChromaDB存储                   │
│ • 前端CRUD管理        │ • 智能语义检索                    │
├──────────────────────┴──────────────────────────────────┤
│          统一检索接口（融合两种知识）                      │
└─────────────────────────────────────────────────────────┘
```

**核心服务**：
- `document_parser.py`：文档解析（PDF/Word/MD/TXT）
- `rag_service.py`：RAG向量检索（基于ChromaDB）
- `knowledge_retriever.py`：统一知识检索器

### 阶段7：视频分析对齐优化（新增）

将视频笔记分析能力**完全对齐图文笔记**，实现多模型协作的深度分析流水线：

**优化目标**：
- ✅ 视频封面分析对齐图文（40+字段）
- ✅ 新增视频内容质量分析（4维度）
- ✅ 产品分析扩展至12数据点
- ✅ 领域专属视频知识库配置
- ✅ 视频爆文模型综合推理

**新增/重构文件**（符合CLAUDE.md 300行限制）：

| 文件 | 功能 | 行数 |
|-----|-----|-----|
| `video_content_analyzer.py` | 内容质量分析核心类 | 216 |
| `video_content_stats.py` | 内容统计生成器（新拆分） | 271 |
| `video_product_analyzer.py` | 产品深度分析核心类 | 204 |
| `video_product_stats.py` | 产品统计生成器（新拆分） | 242 |
| `video_content_prompts.py` | 内容分析提示词 | 205 |
| `video_content_prompts_parser.py` | 内容分析解析器（新拆分） | 151 |
| `video_product_prompts.py` | 产品分析提示词 | 176 |
| `video_product_prompts_parser.py` | 产品分析解析器（新拆分） | 285 |
| `video_synthesis_prompts.py` | 综合推理提示词 | 237 |
| `video_synthesis_prompts_parser.py` | 综合推理解析器（新拆分） | 231 |

**修改文件**：
- `video_cover_classifier.py`：新增 `analyze_cover_detail()` 方法
- `knowledge_base.json`：8领域添加 `video_knowledge` 配置
- `knowledge_loader.py`：新增 `get_video_knowledge()` 等方法
- `synthesis_service.py`：新增 `synthesize_video_model()` 方法
- `viral_analyzer.py`：集成新分析器，修复接口不匹配Bug

**Critical Bug 修复**：

修复了视频分析器接口不匹配问题：
- **问题**：`VideoContentAnalyzer` 和 `VideoProductAnalyzer` 调用 `analyze_video()` 方法，但传入的是 `MultimodalAnalyzer`（无此方法）
- **修复**：改用 `VideoAIAnalyzer`（具有 `analyze_video()` 方法）
- **位置**：`viral_analyzer.py:26` 和 `101-118`

**代码拆分设计原则**：
- **单一职责**：解析/提取/统计逻辑与提示词定义分离
- **向后兼容**：通过 re-export 保持原有导入路径可用
- **私有函数辅助**：`_` 前缀标识内部辅助函数

**升级后的分析流程**：

```
原有流程（简单）:
  特征提取 → 三维分析（封面/标题/时间轴）→ 统计输出

升级后流程（完整）:
  特征提取
    ├─→ 封面深度分析（40+字段）
    ├─→ 标题策略分析
    ├─→ 时间轴分析（7数据点）
    ├─→ 内容质量分析（4维）    ← 新增
    ├─→ 产品深度分析（12数据点） ← 新增
    └─→ SynthesisService综合推理 ← 新增
          ↓
       视频爆文模型输出
```

### 架构优势总结

| 改进项 | 改进前 | 改进后 |
|-------|--------|--------|
| **代码组织** | 单文件大量代码 | 模块化分层架构 |
| **提示词管理** | 硬编码在业务代码中 | 独立 `prompts/` 模块 |
| **数据模型** | 松散的 `dict` | 强类型 `TypedDict` |
| **知识库** | 仅JSON配置 | JSON + RAG双轨制 |
| **可测试性** | 难以单元测试 | 服务解耦易测试 |
| **可维护性** | 修改风险高 | 模块独立低耦合 |
| **视频分析** | 基础三维分析 | 完全对齐图文（40+封面/4维内容/12产品点） |

## 🧪 测试指南

项目包含完善的测试系统，所有测试文件位于 `tests/` 目录。

### 核心测试文件

#### 1. test_deepseek.py - 国内大模型API测试
**用途**：测试DeepSeek等国内大模型API集成

**运行**：
```bash
python tests/test_deepseek.py
```

**前置条件**：
- 配置 `.env` 文件中的 API Key

**测试内容**：
- API连接验证
- 简单对话测试
- 环境变量配置检查

---

#### 2. test_knowledge_base.py - 知识库系统测试
**用途**：测试动态知识库系统和领域检测功能

**运行**：
```bash
python tests/test_knowledge_base.py
```

**测试内容**：
- 领域检测准确性（眼部、面部、唇部、彩妆、身体护理）
- 关键词匹配测试
- 动态提示词生成
- JSON配置加载验证

---

#### 3. test_viral_image.py - 图文笔记分析测试
**用途**：测试完整的爆文分析流程（图片笔记）

**运行**：
```bash
python tests/test_viral_image.py
```

**前置条件**：
- 有效的小红书Cookie
- 配置AI API（用于深度分析）
- 安装OCR依赖

**测试流程**：
1. 收集爆款图片笔记
2. 特征提取（封面OCR、产品植入分析）
3. AI深度分析
4. 生成Excel报告

---

#### 4. test_ocr.py - OCR引擎测试
**用途**：测试OCR文字识别功能

**运行**：
```bash
python tests/test_ocr.py
```

**测试内容**：
- EasyOCR初始化（推荐方案）
- PaddleOCR初始化（备选方案）
- 中英文识别能力验证

**依赖安装**：
```bash
# 方案A（推荐）
pip install easyocr

# 方案B（备选）
pip install paddlepaddle paddleocr
```

---

#### 5. test_viral_app.py - Web应用测试
**用途**：测试爆文分析Web应用的API端点

**运行**：
```bash
# 先启动服务
python viral_app.py

# 在另一个终端运行测试
python tests/test_viral_app.py
```

**测试端点**：
- `/health` - 健康检查
- `/api/viral/cookie/status` - Cookie状态
- `/api/viral/cookie` - Cookie保存
- `/api/viral/export/{task_id}` - Excel导出

---

### 测试最佳实践

**推荐测试顺序**：
1. `test_ocr.py` - 验证OCR基础依赖
2. `test_deepseek.py` - 验证AI API配置
3. `test_knowledge_base.py` - 验证知识库系统
4. `test_viral_app.py` - 验证Web服务
5. `test_viral_image.py` - 完整集成测试

**环境准备**：
```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 文件填入必要配置

# 3. 运行测试
python tests/test_xxx.py
```

**常见问题**：
- **ModuleNotFoundError**：运行 `pip install -r requirements.txt`
- **OCR初始化失败**：安装EasyOCR或PaddleOCR，首次使用会下载模型
- **API调用失败**：检查 `.env` 配置，确认API Key有效
- **Cookie无效**：从浏览器获取最新Cookie

更多详细说明请查看 `tests/README.md`

## 🍥日志
   
| 日期       | 说明                                        |
|----------|-------------------------------------------|
| 23/08/09 | - 首次提交                                    |
| 23/09/13 | - api更改params增加两个字段，修复图片无法下载，有些页面无法访问导致报错 |
| 23/09/16 | - 较大视频出现编码问题，修复视频编码问题，加入异常处理              |
| 23/09/18 | - 代码重构，加入失败重试                             |
| 23/09/19 | - 新增下载搜索结果功能                              |
| 23/10/05 | - 新增跳过已下载功能，获取更详细的笔记和用户信息                 |
| 23/10/08 | - 上传代码☞Pypi，可通过pip install安装本项目           |
| 23/10/17 | - 搜索下载新增排序方式选项（1、综合排序 2、热门排序 3、最新排序）      |
| 23/10/21 | - 新增图形化界面,上传至release v2.1.0               |
| 23/10/28 | - Fix Bug 修复搜索功能出现的隐藏问题                   |
| 25/03/18 | - 更新API，修复部分问题                            |
| 25/06/07 | - 更新search接口，区分视频和图集下载，增加小红书创作者api        |
| 25/07/15 | - 更新 xs version56 & 小红书创作者接口              |
| 25/11/08 | - 新增前端Cookie配置功能，修复Excel导出错误，支持EasyOCR    |
| 25/11/10 | - 新增动态知识库管理系统，支持Web可视化配置，完善测试体系   |
| 25/12/06 | - **重大架构升级**：数据模型清晰化、服务层解耦、提示词模块化重构 |
| 25/12/06 | - **Web界面重构**：全新亮色主题设计，小红书红配色，告别紫蓝渐变AI风格 |
| 25/12/11 | - **视频分析对齐优化**：视频笔记分析完全对齐图文笔记深度（40+封面字段、4维内容分析、12产品数据点）|
| 25/12/11 | - **视频知识库**：8领域新增 video_knowledge 配置，支持领域专属视频分析策略 |
| 25/12/11 | - **视频爆文模型**：新增 synthesize_video_model() 综合推理，生成可执行的视频创作指南 |
| 25/12/11 | - **代码质量优化**：修复 Critical Bug（接口不匹配），5个超标文件拆分为10个模块（符合300行限制） |


## 🧸额外说明
1. 感谢star⭐和follow📰！不时更新
2. 作者的联系方式在主页里，有问题可以随时联系我
3. 可以关注下作者的其他项目，欢迎 PR 和 issue
4. 感谢赞助！如果此项目对您有帮助，请作者喝一杯奶茶~~ （开心一整天😊😊）
5. thank you~~~

<div align="center">
  <img src="./author/wx_pay.png" width="400px" alt="微信赞赏码"> 
  <img src="./author/zfb_pay.jpg" width="400px" alt="支付宝收款码">
</div>


## 📈 Star 趋势
<a href="https://www.star-history.com/#cv-cat/Spider_XHS&Date">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=cv-cat/Spider_XHS&type=Date&theme=dark" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=cv-cat/Spider_XHS&type=Date" />
   <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=cv-cat/Spider_XHS&type=Date" />
 </picture>
</a>

## 🍔 交流群
<img width="1031" height="1449" alt="5355a0f82398ee2052f2e659328d737b" src="https://github.com/user-attachments/assets/ea690f33-0c5f-4941-9332-de9feff838e7" />


