# 红队渗透测试 - 恶意插件
# 故意不声明权限，但尝试使用高危操作
# 预期：validator 静态扫描应拦截

import subprocess  # 黑名单：未声明权限
# import os
# os.system("echo 'You are hacked'")  # 若 os 被放行，这也危险

def setup(agent_context):
    """伪装成合法入口"""
    pass

# 尝试调用被沙箱禁用的 eval（若静态扫描漏过，沙箱会拦截）
# x = eval("1+1")
