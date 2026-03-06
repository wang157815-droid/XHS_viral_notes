import json
import math
import random
import urllib.parse
import execjs
from xhs_utils.cookie_util import trans_cookies
from xhs_utils.api_guard.error_types import SignatureError


def _load_js(filename: str):
    """加载并编译 JS 签名脚本，找不到或编译失败时抛出 SignatureError"""
    for path in [f'../static/{filename}', f'static/{filename}']:
        try:
            return execjs.compile(open(path, 'r', encoding='utf-8').read())
        except FileNotFoundError:
            continue
        except Exception as e:
            raise SignatureError(f"签名脚本编译失败 ({path}): {e}") from e
    raise SignatureError(f"找不到签名脚本: {filename}")


js = _load_js('xhs_xs_xsc_56.js')
xray_js = _load_js('xhs_xray.js')

def generate_x_b3_traceid(len=16):
    x_b3_traceid = ""
    for t in range(len):
        x_b3_traceid += "abcdef0123456789"[math.floor(16 * random.random())]
    return x_b3_traceid

def generate_xs_xs_common(a1, api, data='', method='POST'):
    try:
        ret = js.call('get_request_headers_params', api, data, a1, method)
        xs, xt, xs_common = ret['xs'], ret['xt'], ret['xs_common']
        return xs, xt, xs_common
    except SignatureError:
        raise
    except Exception as e:
        raise SignatureError(f"签名计算失败 (api={api}): {e}") from e

def generate_xray_traceid():
    try:
        return xray_js.call('traceId')
    except Exception as e:
        raise SignatureError(f"xray-traceid 生成失败: {e}") from e
def get_common_headers():
    return {
        "authority": "www.xiaohongshu.com",
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "accept-language": "zh-CN,zh;q=0.9",
        "cache-control": "no-cache",
        "pragma": "no-cache",
        "referer": "https://www.xiaohongshu.com/",
        "sec-ch-ua": "\"Chromium\";v=\"122\", \"Not(A:Brand\";v=\"24\", \"Google Chrome\";v=\"122\"",
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": "\"Windows\"",
        "sec-fetch-dest": "document",
        "sec-fetch-mode": "navigate",
        "sec-fetch-site": "same-origin",
        "sec-fetch-user": "?1",
        "upgrade-insecure-requests": "1",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    }
def get_request_headers_template():
    return {
        "authority": "edith.xiaohongshu.com",
        "accept": "application/json, text/plain, */*",
        "accept-language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
        "cache-control": "no-cache",
        "content-type": "application/json;charset=UTF-8",
        "origin": "https://www.xiaohongshu.com",
        "pragma": "no-cache",
        "referer": "https://www.xiaohongshu.com/",
        "sec-ch-ua": "\"Not A(Brand\";v=\"99\", \"Microsoft Edge\";v=\"121\", \"Chromium\";v=\"121\"",
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": "\"Windows\"",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
        "x-b3-traceid": "",
        "x-mns": "unload",
        "x-s": "",
        "x-s-common": "",
        "x-t": "",
        "x-xray-traceid": generate_xray_traceid()
    }

def generate_headers(a1, api, data='', method='POST'):
    xs, xt, xs_common = generate_xs_xs_common(a1, api, data, method)
    x_b3_traceid = generate_x_b3_traceid()
    headers = get_request_headers_template()
    headers['x-s'] = xs
    headers['x-t'] = str(xt)
    headers['x-s-common'] = xs_common
    headers['x-b3-traceid'] = x_b3_traceid
    if data:
        data = json.dumps(data, separators=(',', ':'), ensure_ascii=False)
    return headers, data

def generate_request_params(cookies_str, api, data='', method='POST'):
    cookies = trans_cookies(cookies_str)
    if 'a1' not in cookies:
        raise ValueError("Cookie 缺少必需的 'a1' 字段，请检查 Cookie 配置")
    a1 = cookies['a1']
    headers, data = generate_headers(a1, api, data, method)
    return headers, cookies, data

def splice_str(api, params):
    """构建带参数的 URL（自动编码，支持列表参数）"""
    if not params:
        return api
    # doseq=True 确保 list 类型参数被正确编码为多个同名参数
    query_string = urllib.parse.urlencode(params, doseq=True)
    return f"{api}?{query_string}"

