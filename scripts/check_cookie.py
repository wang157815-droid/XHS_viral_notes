"""Cookie 完整性和有效性检查脚本"""
import sys
sys.path.insert(0, '.')

from viral_agent.services.user_data_service import get_user_data_service
from apis.xhs_pc_apis import XHS_Apis

def check_cookie():
    print("=" * 50)
    print("Cookie 检查工具")
    print("=" * 50)
    
    # 1. 读取保存的 Cookie
    user_data = get_user_data_service('admin')
    cookie = user_data.get_cookie()
    
    if not cookie:
        print("\n❌ 未找到保存的 Cookie")
        return
    
    print(f"\n📋 Cookie 长度: {len(cookie)} 字符")
    
    # 2. 检查关键字段
    print("\n🔍 关键字段检查:")
    required_fields = {
        'a1': '签名必需',
        'web_session': '登录状态（最重要）',
        'webId': '设备标识',
    }
    optional_fields = {
        'gid': '会话ID',
        'xsecappid': '应用ID', 
        'websectiga': '安全令牌',
        'sec_poison_id': '安全ID',
    }
    
    missing_required = []
    for field, desc in required_fields.items():
        has = f'{field}=' in cookie
        status = "✅" if has else "❌ 缺失"
        print(f"  [{status}] {field} - {desc}")
        if not has:
            missing_required.append(field)
    
    print("\n  可选字段:")
    for field, desc in optional_fields.items():
        has = f'{field}=' in cookie
        status = "✅" if has else "⚪"
        print(f"  [{status}] {field} - {desc}")
    
    # 3. 判断完整性
    print("\n" + "-" * 50)
    if missing_required:
        print(f"⚠️  Cookie 不完整，缺少: {', '.join(missing_required)}")
        if 'web_session' in missing_required:
            print("   → web_session 缺失说明登录流程未完成（可能需要短信验证）")
        return
    
    print("✅ Cookie 字段完整")
    
    # 4. 测试有效性
    print("\n🧪 测试 API 调用...")
    xhs = XHS_Apis()
    
    # 测试1: 获取频道列表
    success, msg, data = xhs.get_homefeed_all_channel(cookie)
    if success:
        print(f"  ✅ 获取频道列表: 成功 ({len(data)} 个频道)")
    else:
        print(f"  ❌ 获取频道列表: {msg}")
    
    # 测试2: 搜索功能（增强检查：同时验证数据内容）
    success, msg, data = xhs.search_some_note("测试", 1, cookie)
    if success and data and len(data) > 0:
        print(f"  ✅ 搜索功能: 成功 ({len(data)} 条结果)")
    elif success and (not data or len(data) == 0):
        print(f"  ⚠️  搜索功能: 请求成功但无数据返回（Cookie 可能未完成登录）")
        success = False  # 标记为无效，影响最终结论
    else:
        print(f"  ❌ 搜索功能: {msg}")
    
    # 5. 最终结论
    print("\n" + "=" * 50)
    if success:
        print("🎉 结论: Cookie 有效，可以正常使用！")
    else:
        print("❌ 结论: Cookie 无效")
        print("   可能原因:")
        print("   1. Cookie 已过期")
        print("   2. 扫码登录时未完成短信验证")
        print("   3. 账号被临时限制")
        print("\n   建议: 手动从浏览器复制 Cookie")

if __name__ == '__main__':
    check_cookie()
