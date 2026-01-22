#!/usr/bin/env python3
"""分析 logo 图片的背景颜色"""

from PIL import Image
from collections import Counter


def analyze_colors(input_path: str):
    """分析图片的颜色分布"""
    img = Image.open(input_path)
    if img.mode != 'RGBA':
        img = img.convert('RGBA')

    pixels = list(img.getdata())
    width, height = img.size

    # 统计四个角落的颜色（背景色）
    corner_positions = [
        (0, 0), (width-1, 0),  # 左上、右上
        (0, height-1), (width-1, height-1)  # 左下、右下
    ]

    print("=== 四角背景颜色 ===")
    px = img.load()
    for pos in corner_positions:
        color = px[pos[0], pos[1]]
        print(f"  位置 {pos}: RGB{color[:3]} -> #{color[0]:02x}{color[1]:02x}{color[2]:02x}")

    # 统计最常见的颜色
    print("\n=== 最常见的 10 种颜色 ===")
    color_counter = Counter(pixels)
    for color, count in color_counter.most_common(10):
        percentage = count / len(pixels) * 100
        print(f"  RGB{color[:3]} #{color[0]:02x}{color[1]:02x}{color[2]:02x} - {percentage:.1f}%")

    # 采样左上角区域（明显是背景的地方）
    print("\n=== 左上角 50x50 区域采样 ===")
    bg_colors = []
    for y in range(min(50, height)):
        for x in range(min(50, width)):
            bg_colors.append(px[x, y][:3])

    bg_counter = Counter(bg_colors)
    for color, count in bg_counter.most_common(5):
        print(f"  RGB{color} -> #{color[0]:02x}{color[1]:02x}{color[2]:02x}")


if __name__ == "__main__":
    input_path = "/mnt/c/Users/ASUS/Desktop/1bced8313dc96f53f7028d111222c0e4.png"
    analyze_colors(input_path)
