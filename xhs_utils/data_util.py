import json
import os
import re
import time
import openpyxl
import requests
from loguru import logger
from retry import retry


def norm_str(input_str):
    """清理字符串中的非法文件名字符"""
    new_str = re.sub(r'[\\/:*?"<>| ]+', "", input_str).replace('\n', '').replace('\r', '')
    return new_str

def norm_text(text):
    ILLEGAL_CHARACTERS_RE = re.compile(r'[\000-\010]|[\013-\014]|[\016-\037]')
    text = ILLEGAL_CHARACTERS_RE.sub(r'', text)
    return text

def sanitize_excel_value(value):
    """防止 Excel 公式注入"""
    if isinstance(value, str) and value and value[0] in ('=', '+', '-', '@', '\t', '\r'):
        return "'" + value  # 添加单引号前缀转义公式
    return value


def timestamp_to_str(timestamp):
    """将小红书时间戳转为本地时间字符串。

    兼容: 毫秒 int、秒 int、数字字符串;已是 `YYYY-MM-DD` 形态则原样截断返回。
    """
    if timestamp is None or timestamp == "":
        return ""
    if isinstance(timestamp, str):
        s = timestamp.strip()
        if not s:
            return ""
        if not s.isdigit():
            return s[:19]
        try:
            ts = int(s)
        except ValueError:
            return s[:19]
    else:
        try:
            ts = int(float(timestamp))
        except (TypeError, ValueError):
            return str(timestamp).strip()[:19]
    if ts <= 0:
        return ""
    if ts > 1_000_000_000_000:
        ts = ts // 1000
    try:
        time_local = time.localtime(ts)
        return time.strftime("%Y-%m-%d %H:%M:%S", time_local)
    except (OverflowError, OSError, ValueError):
        return ""

def handle_user_info(data, user_id):
    home_url = f'https://www.xiaohongshu.com/user/profile/{user_id}'
    nickname = data['basic_info']['nickname']
    avatar = data['basic_info']['imageb']
    red_id = data['basic_info']['red_id']
    gender = data['basic_info']['gender']
    if gender == 0:
        gender = '男'
    elif gender == 1:
        gender = '女'
    else:
        gender = '未知'
    ip_location = data['basic_info']['ip_location']
    desc = data['basic_info']['desc']
    follows = data['interactions'][0]['count']
    fans = data['interactions'][1]['count']
    interaction = data['interactions'][2]['count']
    tags_temp = data['tags']
    tags = []
    for tag in tags_temp:
        try:
            tags.append(tag['name'])
        except:
            pass
    return {
        'user_id': user_id,
        'home_url': home_url,
        'nickname': nickname,
        'avatar': avatar,
        'red_id': red_id,
        'gender': gender,
        'ip_location': ip_location,
        'desc': desc,
        'follows': follows,
        'fans': fans,
        'interaction': interaction,
        'tags': tags,
    }

def handle_note_info(data):
    note_id = data['id']
    note_url = data['url']
    note_type = data['note_card']['type']
    if note_type == 'normal':
        note_type = '图集'
    else:
        note_type = '视频'
    user_id = data['note_card']['user']['user_id']
    home_url = f'https://www.xiaohongshu.com/user/profile/{user_id}'
    nickname = data['note_card']['user']['nickname']
    avatar = data['note_card']['user']['avatar']
    title = data['note_card']['title']
    if title.strip() == '':
        title = f'无标题'
    desc = data['note_card']['desc']
    liked_count = data['note_card']['interact_info']['liked_count']
    collected_count = data['note_card']['interact_info']['collected_count']
    comment_count = data['note_card']['interact_info']['comment_count']
    share_count = data['note_card']['interact_info']['share_count']
    image_list_temp = data['note_card']['image_list']
    image_list = []
    for image in image_list_temp:
        try:
            image_list.append(image['info_list'][1]['url'])
            # success, msg, img_url = XHS_Apis.get_note_no_water_img(image['info_list'][1]['url'])
            # image_list.append(img_url)
        except:
            pass
    if note_type == '视频':
        video_cover = image_list[0] if image_list else None

        # 提取所有可用的视频URL（多源备选）
        video_urls = []

        try:
            video_data = data['note_card']['video']

            # 方法1：origin_video_key（主要方法）
            origin_key = video_data.get('consumer', {}).get('origin_video_key')
            if origin_key:
                video_urls.append({
                    'url': f'https://sns-video-bd.xhscdn.com/{origin_key}',
                    'priority': 1,
                    'source': 'origin_key',
                    'codec': 'unknown',
                    'quality': 'original'
                })

            # 方法2：从stream中提取所有URL
            stream_data = video_data.get('media', {}).get('stream', {})

            # H.265 超清（1080p）- 优先级最高
            h265_list = stream_data.get('h265', [])
            if len(h265_list) > 1:
                h265_hd = h265_list[1]
                if h265_hd.get('master_url'):
                    video_urls.append({
                        'url': h265_hd['master_url'],
                        'priority': 2,
                        'source': 'stream',
                        'codec': 'H.265',
                        'quality': f"{h265_hd.get('width', 1080)}x{h265_hd.get('height', 1920)}"
                    })
                # 添加备用URL
                for backup_url in h265_hd.get('backup_urls', []):
                    video_urls.append({
                        'url': backup_url,
                        'priority': 3,
                        'source': 'stream_backup',
                        'codec': 'H.265',
                        'quality': f"{h265_hd.get('width', 1080)}x{h265_hd.get('height', 1920)}"
                    })

            # H.265 标准（720p）
            if len(h265_list) > 0:
                h265_std = h265_list[0]
                if h265_std.get('master_url'):
                    video_urls.append({
                        'url': h265_std['master_url'],
                        'priority': 4,
                        'source': 'stream',
                        'codec': 'H.265',
                        'quality': f"{h265_std.get('width', 720)}x{h265_std.get('height', 1280)}"
                    })

            # H.264（最高兼容性）
            h264_list = stream_data.get('h264', [])
            if h264_list:
                h264_video = h264_list[0]
                if h264_video.get('master_url'):
                    video_urls.append({
                        'url': h264_video['master_url'],
                        'priority': 5,
                        'source': 'stream',
                        'codec': 'H.264',
                        'quality': f"{h264_video.get('width', 720)}x{h264_video.get('height', 1280)}"
                    })
                # 添加备用URL
                for backup_url in h264_video.get('backup_urls', []):
                    video_urls.append({
                        'url': backup_url,
                        'priority': 6,
                        'source': 'stream_backup',
                        'codec': 'H.264',
                        'quality': f"{h264_video.get('width', 720)}x{h264_video.get('height', 1280)}"
                    })

        except (KeyError, TypeError, IndexError) as e:
            pass  # 数据提取失败，使用已有的URL

        # video_addr保持向后兼容（使用第一个可用URL）
        video_addr = video_urls[0]['url'] if video_urls else None

        # 按优先级排序
        video_urls.sort(key=lambda x: x['priority'])

    else:
        video_cover = None
        video_addr = None
        video_urls = []
    tags_temp = data['note_card']['tag_list']
    tags = []
    for tag in tags_temp:
        try:
            tags.append(tag['name'])
        except:
            pass
    nc = data.get("note_card") or {}
    raw_time = nc.get("time")
    if raw_time in (None, "", 0):
        raw_time = data.get("create_time") or nc.get("create_time")
    upload_time = timestamp_to_str(raw_time) if raw_time not in (None, "", 0) else ""
    if 'ip_location' in data['note_card']:
        ip_location = data['note_card']['ip_location']
    else:
        ip_location = '未知'
    return {
        'note_id': note_id,
        'note_url': note_url,
        'note_type': note_type,
        'user_id': user_id,
        'home_url': home_url,
        'nickname': nickname,
        'avatar': avatar,
        'title': title,
        'desc': desc,
        'liked_count': liked_count,
        'collected_count': collected_count,
        'comment_count': comment_count,
        'share_count': share_count,
        'video_cover': video_cover,
        'video_addr': video_addr,
        'video_urls': video_urls if note_type == '视频' else [],  # 新增：多源视频URL列表
        'image_list': image_list,
        'tags': tags,
        'upload_time': upload_time,
        'ip_location': ip_location,
    }

def handle_comment_info(data):
    note_id = data['note_id']
    note_url = data['note_url']
    comment_id = data['id']
    user_id = data['user_info']['user_id']
    home_url = f'https://www.xiaohongshu.com/user/profile/{user_id}'
    nickname = data['user_info']['nickname']
    avatar = data['user_info']['image']
    content = data['content']
    show_tags = data['show_tags']
    like_count = data['like_count']
    upload_time = timestamp_to_str(data['create_time'])
    try:
        ip_location = data['ip_location']
    except:
        ip_location = '未知'
    pictures = []
    try:
        pictures_temp = data['pictures']
        for picture in pictures_temp:
            try:
                pictures.append(picture['info_list'][1]['url'])
                # success, msg, img_url = XHS_Apis.get_note_no_water_img(picture['info_list'][1]['url'])
                # pictures.append(img_url)
            except:
                pass
    except:
        pass
    return {
        'note_id': note_id,
        'note_url': note_url,
        'comment_id': comment_id,
        'user_id': user_id,
        'home_url': home_url,
        'nickname': nickname,
        'avatar': avatar,
        'content': content,
        'show_tags': show_tags,
        'like_count': like_count,
        'upload_time': upload_time,
        'ip_location': ip_location,
        'pictures': pictures,
    }
def save_to_xlsx(datas, file_path, type='note'):
    wb = openpyxl.Workbook()
    ws = wb.active
    if type == 'note':
        headers = ['笔记id', '笔记url', '笔记类型', '用户id', '用户主页url', '昵称', '头像url', '标题', '描述', '点赞数量', '收藏数量', '评论数量', '分享数量', '视频封面url', '视频地址url', '图片地址url列表', '标签', '上传时间', 'ip归属地']
    elif type == 'user':
        headers = ['用户id', '用户主页url', '用户名', '头像url', '小红书号', '性别', 'ip地址', '介绍', '关注数量', '粉丝数量', '作品被赞和收藏数量', '标签']
    else:
        headers = ['笔记id', '笔记url', '评论id', '用户id', '用户主页url', '昵称', '头像url', '评论内容', '评论标签', '点赞数量', '上传时间', 'ip归属地', '图片地址url列表']
    ws.append(headers)
    for data in datas:
        # 先清理非法字符，再进行公式注入防护
        data = {k: sanitize_excel_value(norm_text(str(v))) for k, v in data.items()}
        ws.append(list(data.values()))
    wb.save(file_path)
    logger.info(f'数据保存至 {file_path}')

def download_media(path, name, url, type):
    # timeout=(连接超时, 读取超时)，媒体文件可能较大需要更长读取时间
    if type == 'image':
        content = requests.get(url, timeout=(5, 30)).content
        with open(path + '/' + name + '.jpg', mode="wb") as f:
            f.write(content)
    elif type == 'video':
        res = requests.get(url, stream=True, timeout=(5, 60))
        size = 0
        chunk_size = 1024 * 1024
        with open(path + '/' + name + '.mp4', mode="wb") as f:
            for data in res.iter_content(chunk_size=chunk_size):
                f.write(data)
                size += len(data)

def save_user_detail(user, path):
    with open(f'{path}/detail.txt', mode="w", encoding="utf-8") as f:
        # 逐行输出到txt里
        f.write(f"用户id: {user['user_id']}\n")
        f.write(f"用户主页url: {user['home_url']}\n")
        f.write(f"用户名: {user['nickname']}\n")
        f.write(f"头像url: {user['avatar']}\n")
        f.write(f"小红书号: {user['red_id']}\n")
        f.write(f"性别: {user['gender']}\n")
        f.write(f"ip地址: {user['ip_location']}\n")
        f.write(f"介绍: {user['desc']}\n")
        f.write(f"关注数量: {user['follows']}\n")
        f.write(f"粉丝数量: {user['fans']}\n")
        f.write(f"作品被赞和收藏数量: {user['interaction']}\n")
        f.write(f"标签: {user['tags']}\n")

def save_note_detail(note, path):
    with open(f'{path}/detail.txt', mode="w", encoding="utf-8") as f:
        # 逐行输出到txt里
        f.write(f"笔记id: {note['note_id']}\n")
        f.write(f"笔记url: {note['note_url']}\n")
        f.write(f"笔记类型: {note['note_type']}\n")
        f.write(f"用户id: {note['user_id']}\n")
        f.write(f"用户主页url: {note['home_url']}\n")
        f.write(f"昵称: {note['nickname']}\n")
        f.write(f"头像url: {note['avatar']}\n")
        f.write(f"标题: {note['title']}\n")
        f.write(f"描述: {note['desc']}\n")
        f.write(f"点赞数量: {note['liked_count']}\n")
        f.write(f"收藏数量: {note['collected_count']}\n")
        f.write(f"评论数量: {note['comment_count']}\n")
        f.write(f"分享数量: {note['share_count']}\n")
        f.write(f"视频封面url: {note['video_cover']}\n")
        f.write(f"视频地址url: {note['video_addr']}\n")
        f.write(f"图片地址url列表: {note['image_list']}\n")
        f.write(f"标签: {note['tags']}\n")
        f.write(f"上传时间: {note['upload_time']}\n")
        f.write(f"ip归属地: {note['ip_location']}\n")



@retry(tries=3, delay=1)
def download_note(note_info, path, save_choice):
    note_id = note_info['note_id']
    user_id = note_info['user_id']
    title = note_info['title']
    title = norm_str(title)[:40]
    nickname = note_info['nickname']
    nickname = norm_str(nickname)[:20]
    if title.strip() == '':
        title = f'无标题'
    save_path = f'{path}/{nickname}_{user_id}/{title}_{note_id}'
    check_and_create_path(save_path)
    with open(f'{save_path}/info.json', mode='w', encoding='utf-8') as f:
        f.write(json.dumps(note_info) + '\n')
    note_type = note_info['note_type']
    save_note_detail(note_info, save_path)
    if note_type == '图集' and save_choice in ['media', 'media-image', 'all']:
        for img_index, img_url in enumerate(note_info['image_list']):
            download_media(save_path, f'image_{img_index}', img_url, 'image')
    elif note_type == '视频' and save_choice in ['media', 'media-video', 'all']:
        download_media(save_path, 'cover', note_info['video_cover'], 'image')
        download_media(save_path, 'video', note_info['video_addr'], 'video')
    return save_path


def check_and_create_path(path):
    if not os.path.exists(path):
        os.makedirs(path)
