import json
import os
from loguru import logger
from apis.xhs_pc_apis import XHS_Apis
from xhs_utils.common_util import init
from xhs_utils.data_util import handle_note_info, save_to_xlsx
from xhs_utils.download_util import download_notes_parallel


class Data_Spider():
    def __init__(self):
        self.xhs_apis = XHS_Apis()
        self._search_context = None  # 保存搜索上下文，用于 token 刷新

    def spider_note(self, note_url: str, cookies_str: str, proxies=None):
        """
        爬取一个笔记的信息
        :param note_url:
        :param cookies_str:
        :return:
        """
        note_info = None
        try:
            success, msg, note_info = self.xhs_apis.get_note_info(note_url, cookies_str, proxies)
            if success:
                note_info = note_info['data']['items'][0]
                note_info['url'] = note_url
                note_info = handle_note_info(note_info)
        except Exception as e:
            success = False
            msg = e
        logger.info(f'爬取笔记信息 {note_url}: {success}, msg: {msg}')
        return success, msg, note_info

    def spider_some_note(self, notes: list, cookies_str: str, base_path: dict, save_choice: str, excel_name: str = '', proxies=None):
        """
        爬取一些笔记的信息（支持 token 过期自动刷新）
        :param notes:
        :param cookies_str:
        :param base_path:
        :return:
        """
        if (save_choice == 'all' or save_choice == 'excel') and excel_name == '':
            raise ValueError('excel_name 不能为空')
        note_list = []
        consecutive_empty = 0  # 连续空结果计数
        remaining_urls = list(notes)  # 可变副本，用于 token 刷新后替换

        for i in range(len(remaining_urls)):
            note_url = remaining_urls[i]  # 每次从列表读取（刷新后可能已更新）
            success, msg, note_info = self.spider_note(note_url, cookies_str, proxies)
            if note_info is not None and success:
                note_list.append(note_info)
                consecutive_empty = 0
            else:
                consecutive_empty += 1
                if consecutive_empty >= 3 and self._search_context:
                    logger.warning(f"连续 {consecutive_empty} 次获取笔记详情为空，尝试批量刷新 xsec_token...")
                    refreshed = self._batch_refresh_tokens(remaining_urls, i + 1, cookies_str, proxies)
                    if refreshed > 0:
                        logger.info(f"成功刷新 {refreshed} 个笔记的 xsec_token")
                        consecutive_empty = 0
                    else:
                        logger.warning("token 刷新未找到匹配，可能 token 已全面过期")
                elif consecutive_empty >= 3 and not self._search_context:
                    logger.warning(
                        f"连续 {consecutive_empty} 次获取笔记详情为空，"
                        "当前为用户流模式，无法自动刷新 token，请重新获取用户页 URL"
                    )

        if save_choice == 'all' or 'media' in save_choice:
            download_notes_parallel(note_list, base_path['media'], save_choice)
        if save_choice == 'all' or save_choice == 'excel':
            file_path = os.path.abspath(os.path.join(base_path['excel'], f'{excel_name}.xlsx'))
            save_to_xlsx(note_list, file_path)


    def _batch_refresh_tokens(self, url_list: list, start_index: int, cookies_str: str, proxies=None) -> int:
        """
        批量刷新 xsec_token（复用搜索上下文重搜获取新 token）

        直接修改 url_list 中 start_index 之后的元素，调用方迭代时会读到更新后的值。

        Args:
            url_list: 完整的 URL 列表（原地修改）
            start_index: 从此索引开始刷新
            cookies_str: Cookie 字符串
            proxies: 代理配置

        Returns:
            成功刷新的 URL 数量
        """
        import urllib.parse
        ctx = self._search_context
        if not ctx:
            return 0

        try:
            # 用原始搜索条件重搜一页，获取新 token
            success, msg, res_json = self.xhs_apis.search_note(
                ctx['query'], cookies_str, page=1,
                sort_type_choice=ctx['sort_type_choice'],
                note_type=ctx['note_type'],
                note_time=ctx['note_time'],
                note_range=ctx['note_range'],
                pos_distance=ctx['pos_distance'],
                geo=ctx['geo'],
                proxies=proxies
            )
            if not success or not res_json:
                return 0

            items = res_json.get('data', {}).get('items', [])
            # 构建 {note_id: new_xsec_token} 映射
            token_map = {}
            for item in items:
                nid = item.get('id')
                token = item.get('xsec_token')
                if nid and token:
                    token_map[nid] = token

            if not token_map:
                return 0

            # 原地替换 url_list 中 start_index 之后的 URL
            refreshed = 0
            for i in range(start_index, len(url_list)):
                parsed = urllib.parse.urlparse(url_list[i])
                note_id = parsed.path.split('/')[-1]
                if note_id in token_map:
                    new_token = token_map[note_id]
                    url_list[i] = f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token={new_token}"
                    refreshed += 1

            return refreshed

        except Exception as e:
            logger.warning(f"批量刷新 token 失败: {e}")
            return 0

    def spider_user_all_note(self, user_url: str, cookies_str: str, base_path: dict, save_choice: str, excel_name: str = '', proxies=None):
        """
        爬取一个用户的所有笔记
        :param user_url:
        :param cookies_str:
        :param base_path:
        :return:
        """
        # 用户流不支持自动 token 刷新
        self._search_context = None
        note_list = []
        try:
            success, msg, all_note_info = self.xhs_apis.get_user_all_notes(user_url, cookies_str, proxies)
            if not success or not all_note_info:
                logger.warning(
                    f"获取用户笔记列表失败或为空（success={success}, 数量={len(all_note_info) if all_note_info else 0}），"
                    "用户页 xsec_token 可能已过期，请重新获取用户页 URL"
                )
            if success:
                logger.info(f'用户 {user_url} 作品数量: {len(all_note_info)}')
                for simple_note_info in all_note_info:
                    note_url = f"https://www.xiaohongshu.com/explore/{simple_note_info['note_id']}?xsec_token={simple_note_info['xsec_token']}"
                    note_list.append(note_url)
            if save_choice == 'all' or save_choice == 'excel':
                excel_name = user_url.split('/')[-1].split('?')[0]
            self.spider_some_note(note_list, cookies_str, base_path, save_choice, excel_name, proxies)
        except Exception as e:
            success = False
            msg = e
        logger.info(f'爬取用户所有视频 {user_url}: {success}, msg: {msg}')
        return note_list, success, msg

    def spider_some_search_note(self, query: str, require_num: int, cookies_str: str, base_path: dict, save_choice: str, sort_type_choice=0, note_type=0, note_time=0, note_range=0, pos_distance=0, geo: dict = None,  excel_name: str = '', proxies=None):
        """
            指定数量搜索笔记，设置排序方式和笔记类型和笔记数量
            :param query 搜索的关键词
            :param require_num 搜索的数量
            :param cookies_str 你的cookies
            :param base_path 保存路径
            :param sort_type_choice 排序方式 0 综合排序, 1 最新, 2 最多点赞, 3 最多评论, 4 最多收藏
            :param note_type 笔记类型 0 不限, 1 视频笔记, 2 普通笔记
            :param note_time 笔记时间 0 不限, 1 一天内, 2 一周内天, 3 半年内
            :param note_range 笔记范围 0 不限, 1 已看过, 2 未看过, 3 已关注
            :param pos_distance 位置距离 0 不限, 1 同城, 2 附近 指定这个必须要指定 geo
            返回搜索的结果
        """
        note_list = []
        try:
            # 保存搜索上下文，用于后续 token 刷新
            self._search_context = {
                'query': query,
                'sort_type_choice': sort_type_choice,
                'note_type': note_type,
                'note_time': note_time,
                'note_range': note_range,
                'pos_distance': pos_distance,
                'geo': geo,
            }
            success, msg, notes = self.xhs_apis.search_some_note(query, require_num, cookies_str, sort_type_choice, note_type, note_time, note_range, pos_distance, geo, proxies)
            if success:
                notes = list(filter(lambda x: x['model_type'] == "note", notes))
                logger.info(f'搜索关键词 {query} 笔记数量: {len(notes)}')
                for note in notes:
                    note_url = f"https://www.xiaohongshu.com/explore/{note['id']}?xsec_token={note['xsec_token']}"
                    note_list.append(note_url)
            if save_choice == 'all' or save_choice == 'excel':
                excel_name = query
            self.spider_some_note(note_list, cookies_str, base_path, save_choice, excel_name, proxies)
        except Exception as e:
            success = False
            msg = e
        logger.info(f'搜索关键词 {query} 笔记: {success}, msg: {msg}')
        return note_list, success, msg

if __name__ == '__main__':
    """
        此文件为爬虫的入口文件，可以直接运行
        apis/xhs_pc_apis.py 为爬虫的api文件，包含小红书的全部数据接口，可以继续封装
        apis/xhs_creator_apis.py 为小红书创作者中心的api文件
        感谢star和follow
    """

    cookies_str, base_path = init()
    data_spider = Data_Spider()
    """
        save_choice: all: 保存所有的信息, media: 保存视频和图片（media-video只下载视频, media-image只下载图片，media都下载）, excel: 保存到excel
        save_choice 为 excel 或者 all 时，excel_name 不能为空
    """


    # # 1 爬取列表的所有笔记信息 笔记链接 如下所示 注意此url会过期！
    # notes = [
    #     r'https://www.xiaohongshu.com/explore/683fe17f0000000023017c6a?xsec_token=ABBr_cMzallQeLyKSRdPk9fwzA0torkbT_ubuQP1ayvKA=&xsec_source=pc_user',
    # ]
    # data_spider.spider_some_note(notes, cookies_str, base_path, 'all', 'test')

    # # 2 爬取用户的所有笔记信息 用户链接 如下所示 注意此url会过期！
    # user_url = 'https://www.xiaohongshu.com/user/profile/64c3f392000000002b009e45?xsec_token=AB-GhAToFu07JwNk_AMICHnp7bSTjVz2beVIDBwSyPwvM=&xsec_source=pc_feed'
    # data_spider.spider_user_all_note(user_url, cookies_str, base_path, 'all')

    # 3 搜索指定关键词的笔记
    query = "榴莲"
    query_num = 10
    sort_type_choice = 0  # 0 综合排序, 1 最新, 2 最多点赞, 3 最多评论, 4 最多收藏
    note_type = 0 # 0 不限, 1 视频笔记, 2 普通笔记
    note_time = 2 # 0 不限, 1 一天内, 2 一周内天, 3 半年内
    note_range = 0  # 0 不限, 1 已看过, 2 未看过, 3 已关注
    pos_distance = 0  # 0 不限, 1 同城, 2 附近 指定这个1或2必须要指定 geo
    # geo = {
    #     # 经纬度
    #     "latitude": 39.9725,
    #     "longitude": 116.4207
    # }
    data_spider.spider_some_search_note(query, query_num, cookies_str, base_path, 'all', sort_type_choice, note_type, note_time, note_range, pos_distance, geo=None)
