#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
灵活的小红书爬虫运行脚本
支持命令行参数、配置文件和交互式模式
"""

import argparse
import json
import sys
import time
import random
from pathlib import Path

# 检查依赖并给出友好提示
try:
    from loguru import logger
except ImportError:
    print("错误：缺少必要的依赖库 loguru")
    print("请先安装依赖：pip install -r requirements.txt")
    sys.exit(1)

try:
    from apis.xhs_pc_apis import XHS_Apis
    from xhs_utils.common_util import init
    from xhs_utils.data_util import handle_note_info, download_note, save_to_xlsx
    from main import Data_Spider
    import config
except ImportError as e:
    print(f"错误：无法导入项目模块 - {e}")
    print("请确保在项目根目录下运行此脚本")
    sys.exit(1)

def setup_logger(log_level='INFO', log_file=None):
    """配置日志"""
    logger.remove()  # 移除默认处理器

    # 控制台输出
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level=log_level
    )

    # 文件输出
    if log_file:
        logger.add(
            log_file,
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
            rotation="10 MB",
            retention="7 days",
            level=log_level
        )

def parse_arguments():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description='小红书数据爬虫 - 灵活配置版',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例用法:
  # 搜索模式
  python run_spider.py --mode search --query "美食" --num 20 --save all

  # 用户模式
  python run_spider.py --mode user --user-url "https://..." --save media

  # 笔记模式
  python run_spider.py --mode notes --notes "url1" "url2" --save excel

  # 交互式模式
  python run_spider.py --mode interactive

  # 使用配置文件
  python run_spider.py --config my_config.json
        """
    )

    # 基础参数
    parser.add_argument('--mode', choices=['search', 'user', 'notes', 'interactive', 'config'],
                       default='config', help='运行模式（默认使用config.py配置）')
    parser.add_argument('--save', choices=['all', 'media', 'media-video', 'media-image', 'excel'],
                       help='保存选项')
    parser.add_argument('--config', type=str, help='自定义配置文件路径（JSON格式）')
    parser.add_argument('--proxy', type=str, help='代理服务器地址')

    # 搜索模式参数
    search_group = parser.add_argument_group('搜索模式参数')
    search_group.add_argument('--query', type=str, help='搜索关键词')
    search_group.add_argument('--num', type=int, help='获取数量')
    search_group.add_argument('--sort', type=int, choices=[0,1,2,3,4],
                            help='排序: 0综合 1最新 2最多点赞 3最多评论 4最多收藏')
    search_group.add_argument('--type', type=int, choices=[0,1,2],
                            help='类型: 0不限 1视频 2图文')
    search_group.add_argument('--time', type=int, choices=[0,1,2,3],
                            help='时间: 0不限 1一天内 2一周内 3半年内')
    search_group.add_argument('--range', type=int, choices=[0,1,2,3],
                            help='范围: 0不限 1已看过 2未看过 3已关注')

    # 用户模式参数
    user_group = parser.add_argument_group('用户模式参数')
    user_group.add_argument('--user-url', type=str, help='用户主页链接')
    user_group.add_argument('--liked', action='store_true', help='下载用户点赞的笔记')
    user_group.add_argument('--collected', action='store_true', help='下载用户收藏的笔记')

    # 笔记模式参数
    notes_group = parser.add_argument_group('笔记模式参数')
    notes_group.add_argument('--notes', nargs='+', help='笔记链接列表')
    notes_group.add_argument('--excel-name', type=str, help='Excel文件名')

    # 高级参数
    advanced_group = parser.add_argument_group('高级参数')
    advanced_group.add_argument('--log-level', choices=['DEBUG', 'INFO', 'WARNING', 'ERROR'],
                               default='INFO', help='日志级别')

    return parser.parse_args()

def load_config_file(config_path):
    """加载JSON配置文件"""
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def interactive_mode():
    """交互式模式"""
    print("\n" + "="*50)
    print("欢迎使用小红书爬虫 - 交互式模式")
    print("="*50)

    # 选择模式
    print("\n请选择爬取模式:")
    print("1. 搜索笔记")
    print("2. 爬取用户")
    print("3. 爬取指定笔记")
    print("0. 退出")

    mode_choice = input("\n请输入选项 [0-3]: ").strip()

    if mode_choice == '0':
        print("感谢使用，再见！")
        return None

    # 通用设置
    print("\n请选择保存方式:")
    print("1. 保存全部（media + excel）")
    print("2. 只保存媒体文件")
    print("3. 只保存视频")
    print("4. 只保存图片")
    print("5. 只保存Excel")

    save_choice_map = {'1': 'all', '2': 'media', '3': 'media-video',
                      '4': 'media-image', '5': 'excel'}
    save_input = input("请输入选项 [1-5]: ").strip()
    save_choice = save_choice_map.get(save_input, 'all')

    params = {'save_choice': save_choice}

    if mode_choice == '1':  # 搜索模式
        params['mode'] = 'search'
        params['query'] = input("\n请输入搜索关键词: ").strip()
        params['num'] = int(input("请输入获取数量 [默认10]: ").strip() or "10")

        print("\n排序方式: 0综合 1最新 2最多点赞 3最多评论 4最多收藏")
        params['sort'] = int(input("请选择 [默认0]: ").strip() or "0")

        print("\n笔记类型: 0不限 1视频 2图文")
        params['type'] = int(input("请选择 [默认0]: ").strip() or "0")

        print("\n时间范围: 0不限 1一天内 2一周内 3半年内")
        params['time'] = int(input("请选择 [默认0]: ").strip() or "0")

    elif mode_choice == '2':  # 用户模式
        params['mode'] = 'user'
        params['user_url'] = input("\n请输入用户主页链接: ").strip()
        params['liked'] = input("是否下载点赞笔记? [y/N]: ").strip().lower() == 'y'
        params['collected'] = input("是否下载收藏笔记? [y/N]: ").strip().lower() == 'y'

    elif mode_choice == '3':  # 笔记模式
        params['mode'] = 'notes'
        notes_input = input("\n请输入笔记链接（多个用空格分隔）: ").strip()
        params['notes'] = notes_input.split()
        params['excel_name'] = input("Excel文件名 [默认自动生成]: ").strip()

    return params

def run_spider_with_params(params):
    """根据参数运行爬虫"""
    cookies_str, base_path = init()
    data_spider = Data_Spider()

    # 添加延时函数
    def random_delay(min_delay=1, max_delay=3):
        delay = random.uniform(min_delay, max_delay)
        logger.info(f"等待 {delay:.1f} 秒...")
        time.sleep(delay)

    mode = params.get('mode', 'search')
    save_choice = params.get('save_choice', 'all')
    proxies = params.get('proxies')

    logger.info(f"运行模式: {mode}")
    logger.info(f"保存选项: {save_choice}")

    try:
        if mode == 'search':
            # 搜索模式
            result = data_spider.spider_some_search_note(
                query=params.get('query', ''),
                require_num=params.get('num', 10),
                cookies_str=cookies_str,
                base_path=base_path,
                save_choice=save_choice,
                sort_type_choice=params.get('sort', 0),
                note_type=params.get('type', 0),
                note_time=params.get('time', 0),
                note_range=params.get('range', 0),
                pos_distance=params.get('pos_distance', 0),
                geo=params.get('geo'),
                excel_name=params.get('excel_name', ''),
                proxies=proxies
            )
            logger.success(f"搜索完成！共获取 {len(result[0])} 条笔记")

        elif mode == 'user':
            # 用户模式
            user_url = params.get('user_url', '')
            if not user_url:
                logger.error("用户链接不能为空！")
                return

            result = data_spider.spider_user_all_note(
                user_url=user_url,
                cookies_str=cookies_str,
                base_path=base_path,
                save_choice=save_choice,
                excel_name=params.get('excel_name', ''),
                proxies=proxies
            )
            logger.success(f"用户爬取完成！共获取 {len(result[0])} 条笔记")

            # 如果需要爬取点赞或收藏
            if params.get('liked') or params.get('collected'):
                logger.info("点赞和收藏功能需要额外开发...")

        elif mode == 'notes':
            # 笔记列表模式
            notes = params.get('notes', [])
            if not notes:
                logger.error("笔记列表不能为空！")
                return

            excel_name = params.get('excel_name', 'notes_collection')
            data_spider.spider_some_note(
                notes=notes,
                cookies_str=cookies_str,
                base_path=base_path,
                save_choice=save_choice,
                excel_name=excel_name,
                proxies=proxies
            )
            logger.success(f"笔记爬取完成！共处理 {len(notes)} 条笔记")

    except Exception as e:
        logger.error(f"爬虫运行失败: {str(e)}")
        raise

def main():
    """主函数"""
    args = parse_arguments()

    # 设置日志
    setup_logger(args.log_level)

    # 准备参数
    params = {}

    # 根据不同模式准备参数
    if args.mode == 'interactive':
        # 交互式模式
        params = interactive_mode()
        if params is None:
            return

    elif args.mode == 'config':
        # 使用config.py配置
        logger.info("使用 config.py 配置文件")
        params = {
            'mode': config.SPIDER_MODE,
            'save_choice': config.SAVE_CHOICE,
            'proxies': config.PROXIES,
            'excel_name': config.EXCEL_NAME,
        }

        if config.SPIDER_MODE == 'search':
            params.update(config.SEARCH_CONFIG)
            params['num'] = params.pop('query_num', 10)
            params['sort'] = params.pop('sort_type', 0)
        elif config.SPIDER_MODE == 'user':
            params.update(config.USER_CONFIG)
        elif config.SPIDER_MODE == 'notes':
            params.update(config.NOTES_CONFIG)

    elif args.config:
        # 使用自定义JSON配置文件
        logger.info(f"加载配置文件: {args.config}")
        params = load_config_file(args.config)

    else:
        # 使用命令行参数
        params['mode'] = args.mode
        params['save_choice'] = args.save or 'all'

        if args.proxy:
            params['proxies'] = {
                'http': args.proxy,
                'https': args.proxy
            }

        if args.mode == 'search':
            params.update({
                'query': args.query,
                'num': args.num or 10,
                'sort': args.sort or 0,
                'type': args.type or 0,
                'time': args.time or 0,
                'range': args.range or 0,
            })
        elif args.mode == 'user':
            params.update({
                'user_url': args.user_url,
                'liked': args.liked,
                'collected': args.collected,
            })
        elif args.mode == 'notes':
            params.update({
                'notes': args.notes or [],
                'excel_name': args.excel_name or 'notes',
            })

    # 运行爬虫
    logger.info("开始运行小红书爬虫...")
    logger.info(f"参数: {json.dumps(params, ensure_ascii=False, indent=2)}")

    run_spider_with_params(params)

    logger.success("爬虫运行完成！")
    logger.info(f"结果保存在 {config.OUTPUT_PATH['base']} 目录")

if __name__ == '__main__':
    main()