"""
小红书爬虫配置文件
可以在这里修改所有爬虫参数，无需修改主程序
"""

# ==================== 基础配置 ====================
# 爬虫模式选择：'search' | 'user' | 'notes' | 'interactive'
SPIDER_MODE = 'search'  # 默认使用搜索模式

# 保存选项
# 'all' - 保存所有内容（图片、视频、Excel）
# 'media' - 保存所有媒体文件
# 'media-video' - 只保存视频
# 'media-image' - 只保存图片
# 'excel' - 只保存Excel文件
SAVE_CHOICE = 'excel'

# Excel文件名（留空则自动生成）
EXCEL_NAME = ''

# 代理设置（留空则不使用代理）
PROXIES = None
# PROXIES = {
#     'http': 'http://127.0.0.1:7890',
#     'https': 'http://127.0.0.1:7890'
# }

# ==================== 搜索模式配置 ====================
SEARCH_CONFIG = {
    'query': '耙耙柑耙耙柑',           # 搜索关键词
    'query_num': 10,           # 获取数量
    'sort_type': 0,            # 0:综合, 1:最新, 2:最多点赞, 3:最多评论, 4:最多收藏
    'note_type': 0,            # 0:不限, 1:视频笔记, 2:普通笔记
    'note_time': 3,            # 0:不限, 1:一天内, 2:一周内, 3:半年内
    'note_range': 0,           # 0:不限, 1:已看过, 2:未看过, 3:已关注
    'pos_distance': 0,         # 0:不限, 1:同城, 2:附近
    'geo': None                # 地理位置，使用时取消注释
    # 'geo': {
    #     'latitude': 39.9725,   # 纬度
    #     'longitude': 116.4207  # 经度
    # }
}

# ==================== 用户模式配置 ====================
USER_CONFIG = {
    # 用户主页链接（注意URL会过期）
    'user_url': 'https://www.xiaohongshu.com/user/profile/64c3f392000000002b009e45',
    'download_liked': False,    # 是否下载用户点赞的笔记
    'download_collected': False  # 是否下载用户收藏的笔记
}

# ==================== 笔记列表模式配置 ====================
NOTES_CONFIG = {
    # 笔记链接列表（注意URL会过期）
    'notes': [
        'https://www.xiaohongshu.com/explore/683fe17f0000000023017c6a',
        # 可以添加更多笔记链接
    ],
    'excel_name': 'my_notes'  # Excel文件名
}

# ==================== 高级设置 ====================
ADVANCED = {
    'retry_times': 3,          # 失败重试次数
    'timeout': 30,             # 请求超时时间（秒）
    'delay_min': 1,            # 最小延时（秒）
    'delay_max': 3,            # 最大延时（秒）
    'max_workers': 5,          # 最大并发数
    'download_media': True,    # 是否下载媒体文件
    'skip_existing': True,     # 跳过已存在的文件
    'log_level': 'INFO',       # 日志级别：DEBUG, INFO, WARNING, ERROR
}

# ==================== 输出路径配置 ====================
OUTPUT_PATH = {
    'base': 'download',           # 基础下载目录
    'excel': 'download/excel',    # Excel保存路径
    'media': 'download/media',    # 媒体文件保存路径
    'log': 'download/logs'        # 日志文件路径
}