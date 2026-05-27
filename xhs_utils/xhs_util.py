import json
import math
import os
import random
import urllib.parse
from urllib.parse import urlparse

import execjs
from xhs_utils.cookie_util import trans_cookies

try:
    js = execjs.compile(open(r'../static/xhs_xs_xsc_56.js', 'r', encoding='utf-8').read())
except:
    js = execjs.compile(open(r'static/xhs_xs_xsc_56.js', 'r', encoding='utf-8').read())

try:
    xray_js = execjs.compile(open(r'../static/xhs_xray.js', 'r', encoding='utf-8').read())
except:
    xray_js = execjs.compile(open(r'static/xhs_xray.js', 'r', encoding='utf-8').read())

def generate_x_b3_traceid(len=16):
    x_b3_traceid = ""
    for t in range(len):
        x_b3_traceid += "abcdef0123456789"[math.floor(16 * random.random())]
    return x_b3_traceid

def generate_xs_xs_common(a1, api, data=''):
    ret = js.call('get_request_headers_params', api, data, a1)
    xs, xt, xs_common = ret['xs'], ret['xt'], ret['xs_common']
    return xs, xt, xs_common

def generate_xs(a1, api, data=''):
    ret = js.call('get_xs', api, data, a1)
    xs, xt = ret['X-s'], ret['X-t']
    return xs, xt

def generate_xray_traceid():
    return xray_js.call('traceId')
# 浏览器版本参数集中管理,确保 Playwright 启动参数、签名 headers、sec-ch-ua 三者一致
# 对齐到 MediaCrawler 同版本（Chrome 137),避免 a1 指纹和 UA 版本不匹配被反爬识别
BROWSER_CHROME_MAJOR = "137"
BROWSER_CHROMIUM_MAJOR = "137"
BROWSER_UA = (
    f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    f"(KHTML, like Gecko) Chrome/{BROWSER_CHROME_MAJOR}.0.0.0 Safari/537.36"
)
BROWSER_SEC_CH_UA = (
    f'"Chromium";v="{BROWSER_CHROMIUM_MAJOR}", '
    f'"Google Chrome";v="{BROWSER_CHROME_MAJOR}", '
    f'"Not.A/Brand";v="99"'
)


def xhs_is_international() -> bool:
    """是否走国际站 RedNote（与 MediaCrawler 的 XHS_INTERNATIONAL 对齐）。

    未显式设置时，若 XHS_LOGIN_START_URL 含 rednote.com 则自动视为国际站，
    便于扫码登录与后续 API 校验使用同一套 webapi.rednote.com。
    """
    v = (os.environ.get("XHS_INTERNATIONAL") or "").strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    login_url = (os.environ.get("XHS_LOGIN_START_URL") or "").lower()
    return "rednote.com" in login_url


def xhs_web_origin() -> str:
    """站点 Origin（无末尾斜杠），用于 Origin / Referer。"""
    if xhs_is_international():
        raw = (os.environ.get("XHS_WEB_ORIGIN") or "https://www.rednote.com").strip().rstrip("/")
        return raw
    raw = (os.environ.get("XHS_WEB_ORIGIN") or "https://www.xiaohongshu.com").strip().rstrip("/")
    return raw


def xhs_api_base_url() -> str:
    """JSON API 根地址（无末尾斜杠）。国内 edith；国际 webapi.rednote.com。"""
    if xhs_is_international():
        raw = (os.environ.get("XHS_API_BASE_URL") or "https://webapi.rednote.com").strip().rstrip("/")
        return raw
    raw = (os.environ.get("XHS_API_BASE_URL") or "https://edith.xiaohongshu.com").strip().rstrip("/")
    return raw


def xhs_api_authority() -> str:
    return urlparse(xhs_api_base_url()).netloc or "edith.xiaohongshu.com"


def get_common_headers():
    origin = xhs_web_origin()
    host = urlparse(origin).netloc or origin.replace("https://", "").replace("http://", "").split("/")[0]
    return {
        "authority": host,
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "accept-language": "zh-CN,zh;q=0.9",
        "cache-control": "no-cache",
        "pragma": "no-cache",
        "priority": "u=0, i",
        "referer": f"{origin}/",
        "sec-ch-ua": BROWSER_SEC_CH_UA,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": "\"Windows\"",
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "same-origin",
        "sec-fetch-user": "?1",
        "upgrade-insecure-requests": "1",
        "user-agent": BROWSER_UA,
    }


def get_request_headers_template():
    origin = xhs_web_origin()
    return {
        "authority": xhs_api_authority(),
        "accept": "application/json, text/plain, */*",
        "accept-language": "zh-CN,zh;q=0.9",
        "cache-control": "no-cache",
        "content-type": "application/json;charset=UTF-8",
        "origin": origin,
        "pragma": "no-cache",
        "priority": "u=1, i",
        "referer": f"{origin}/",
        "sec-ch-ua": BROWSER_SEC_CH_UA,
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": "\"Windows\"",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        "user-agent": BROWSER_UA,
        "x-b3-traceid": "",
        "x-mns": "unload",
        "x-s": "",
        "x-s-common": "",
        "x-t": "",
        "x-xray-traceid": generate_xray_traceid(),
    }

def _sign_via_xhshow(cookie_str, api, data, method):
    """委托 xhshow 生成签名 headers。返回 (xs, xt, xs_common, traceid)。"""
    from xhs_utils.xhshow_adapter import sign_with_xhshow

    signs = sign_with_xhshow(uri=api, data=data, cookie_str=cookie_str, method=method)
    return (
        signs.get("x-s", ""),
        signs.get("x-t", ""),
        signs.get("x-s-common", ""),
        signs.get("x-b3-traceid", ""),
    )


def generate_headers(a1, api, data='', cookie_str='', method='POST'):
    """生成请求 headers。

    从阶段 4.1 起支持 xhshow 签名后端（默认）。通过 env XHS_SIGN_BACKEND 切换:
    - xhshow (默认): 用 xhshow 纯 Python 库, 跟进小红书最新算法
    - execjs:        用老 static/xhs_xs_xsc_56.js (兼容老流程)

    为保持对调用方的向后兼容, cookie_str 和 method 为可选参数;
    xhshow 后端需要这两个参数才能工作, 缺失时降级回 execjs。
    """
    from xhs_utils.xhshow_adapter import get_sign_backend

    backend = get_sign_backend()
    # xhshow 需要完整 cookie_str, 缺失则降级 execjs
    if backend == "xhshow" and cookie_str:
        try:
            xs, xt, xs_common, traceid = _sign_via_xhshow(
                cookie_str, api, data, method
            )
        except Exception as _sign_exc:
            # 降级到 execjs 老算法——老算法会导致 XHS 返回 data:{}，务必排查根因
            import logging as _logging
            _logging.getLogger(__name__).warning(
                f"[xhs_sign] xhshow 签名失败，已降级到 execjs 老算法（会导致 data:{{}}）: {_sign_exc}"
            )
            xs, xt, xs_common = generate_xs_xs_common(a1, api, data)
            traceid = generate_x_b3_traceid()
    else:
        xs, xt, xs_common = generate_xs_xs_common(a1, api, data)
        traceid = generate_x_b3_traceid()

    headers = get_request_headers_template()
    headers['x-s'] = xs
    headers['x-t'] = str(xt)
    headers['x-s-common'] = xs_common
    headers['x-b3-traceid'] = traceid
    if data:
        data = json.dumps(data, separators=(',', ':'), ensure_ascii=False)
    return headers, data


def generate_request_params(cookies_str, api, data='', method='POST'):
    cookies = trans_cookies(cookies_str)
    if 'a1' not in cookies:
        raise ValueError("Cookie 缺少必需的 'a1' 字段，请检查 Cookie 配置")
    a1 = cookies['a1']
    headers, data = generate_headers(a1, api, data, cookie_str=cookies_str, method=method)
    return headers, cookies, data

def splice_str(api, params):
    """构建带参数的 URL（自动编码，支持列表参数）"""
    if not params:
        return api
    # doseq=True 确保 list 类型参数被正确编码为多个同名参数
    query_string = urllib.parse.urlencode(params, doseq=True)
    return f"{api}?{query_string}"


def splice_str_get_xhs(api: str, params: dict) -> str:
    """小红书 Web GET 接口查询串（与 MediaCrawler XiaoHongShuClient._build_query_string 对齐）。

    `urllib.parse.urlencode` 会把 `image_formats=jpg,webp,avif` 里的逗号编成 %2C，
    与浏览器 / xhshow GET 签名字符串不一致，易导致评论等接口 success=False。
    """
    if not params:
        return api
    parts: list[str] = []
    for key, value in params.items():
        value_str = str(value) if value is not None else ""
        value_str = urllib.parse.quote(value_str, safe=",")
        parts.append(f"{key}={value_str}")
    return f"{api}?{'&'.join(parts)}"

