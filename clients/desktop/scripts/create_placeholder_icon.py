"""
创建占位图标文件（临时解决方案）

这个脚本创建一个最小的 ICO 文件用于开发
"""

from PIL import Image, ImageDraw

# 创建一个简单的 32x32 图标
size = 32
img = Image.new('RGBA', (size, size), (102, 126, 234, 255))  # 紫色背景
draw = ImageDraw.Draw(img)

# 绘制一个简单的 "J" 字母
draw.ellipse([4, 4, 28, 28], fill=(255, 255, 255, 255))
draw.text((10, 6), "J", fill=(102, 126, 234, 255))

# 保存为 ICO 文件
ico_path = "src-tauri/icons/icon.ico"
img.save(ico_path, format='ICO', sizes=[(32, 32)])
print(f"Created placeholder icon: {ico_path}")
