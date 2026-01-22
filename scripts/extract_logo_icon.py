#!/usr/bin/env python3
"""
从 RED MUSE logo 图片中提取图标部分
保存为透明背景的 PNG 文件
"""

from PIL import Image
import os


def extract_icon(
    input_path: str,
    output_path: str,
    icon_crop_box: tuple[int, int, int, int] | None = None,
    bg_color_range: tuple[tuple[int, int, int], tuple[int, int, int]] | None = None,
):
    """
    从 logo 图片中提取图标

    Args:
        input_path: 输入图片路径
        output_path: 输出图片路径
        icon_crop_box: 裁剪区域 (left, top, right, bottom)
        bg_color_range: 背景颜色范围 ((r_min, g_min, b_min), (r_max, g_max, b_max))
    """
    # 打开图片
    img = Image.open(input_path)
    print(f"原始图片尺寸: {img.size}")
    print(f"图片模式: {img.mode}")

    # 转换为 RGBA 模式（支持透明度）
    if img.mode != 'RGBA':
        img = img.convert('RGBA')

    width, height = img.size

    # 默认裁剪区域：图片左侧 40% 区域（图标通常在左边）
    if icon_crop_box is None:
        # 根据图片3的布局，图标大约占左侧 30%
        icon_crop_box = (0, 0, int(width * 0.32), height)

    # 裁剪图标区域
    icon_img = img.crop(icon_crop_box)
    print(f"裁剪后尺寸: {icon_img.size}")

    # 默认背景颜色范围（深蓝色背景）
    if bg_color_range is None:
        # 深蓝色背景的 RGB 范围
        bg_color_range = ((0, 10, 20), (30, 50, 80))

    # 去除背景，转为透明
    icon_img = remove_background(icon_img, bg_color_range)

    # 裁剪掉多余的透明区域
    icon_img = trim_transparent(icon_img)
    print(f"去除边缘后尺寸: {icon_img.size}")

    # 保存
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    icon_img.save(output_path, 'PNG')
    print(f"✅ 图标已保存到: {output_path}")

    return icon_img


def remove_background(
    img: Image.Image,
    bg_color_range: tuple[tuple[int, int, int], tuple[int, int, int]],
    tolerance: int = 30,
) -> Image.Image:
    """
    将指定颜色范围的背景转为透明

    根据分析，背景色精确范围：
    - R: 5-20, G: 15-30, B: 45-65 (深蓝色 #091434 附近)
    """
    img = img.copy()
    pixels = img.load()
    width, height = img.size

    # 精确的背景颜色范围（基于实际采样）
    bg_r_range = (0, 25)   # 红色分量很低
    bg_g_range = (10, 35)  # 绿色分量较低
    bg_b_range = (40, 70)  # 蓝色分量中等偏低

    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]

            # 检查是否是背景色
            is_background = (
                bg_r_range[0] <= r <= bg_r_range[1] and
                bg_g_range[0] <= g <= bg_g_range[1] and
                bg_b_range[0] <= b <= bg_b_range[1]
            )

            if is_background:
                pixels[x, y] = (r, g, b, 0)

    return img


def trim_transparent(img: Image.Image, padding: int = 10) -> Image.Image:
    """
    裁剪掉图片周围的透明区域，保留一定边距

    Args:
        img: 输入图片
        padding: 保留的边距像素
    """
    # 获取非透明区域的边界框
    bbox = img.getbbox()
    if bbox is None:
        return img

    left, top, right, bottom = bbox

    # 添加边距
    width, height = img.size
    left = max(0, left - padding)
    top = max(0, top - padding)
    right = min(width, right + padding)
    bottom = min(height, bottom + padding)

    return img.crop((left, top, right, bottom))


def main():
    """主函数"""
    # 项目根目录
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # 输入文件：图片3（清晰度最佳）- 使用 WSL 路径格式
    input_path = "/mnt/c/Users/ASUS/Desktop/1bced8313dc96f53f7028d111222c0e4.png"

    # 输出路径
    output_dir = os.path.join(project_root, "web", "static", "images")
    output_path = os.path.join(output_dir, "logo_icon.png")

    # 检查输入文件
    if not os.path.exists(input_path):
        print(f"❌ 输入文件不存在: {input_path}")
        return

    print("=" * 50)
    print("开始提取 RED MUSE 图标...")
    print("=" * 50)

    # 提取图标
    icon = extract_icon(input_path, output_path)

    # 额外生成一个 favicon 尺寸的版本
    favicon_path = os.path.join(output_dir, "favicon.png")
    icon_resized = icon.resize((64, 64), Image.Resampling.LANCZOS)
    icon_resized.save(favicon_path, 'PNG')
    print(f"✅ Favicon 已保存到: {favicon_path}")

    print("=" * 50)
    print("图标提取完成！")
    print("=" * 50)


if __name__ == "__main__":
    main()
