"""
预下载 Whisper 模型脚本

Whisper 模型会在首次使用时自动下载，但首次使用可能较慢。
此脚本可以预先下载模型，避免首次使用时等待。

模型大小参考：
- tiny:   ~39 MB  (最快，准确度较低)
- base:   ~74 MB  (推荐，平衡速度和准确度)
- small:  ~244 MB (更准确)
- medium: ~769 MB (高准确度)
- large:  ~1550 MB (最高准确度，但较慢)

自定义路径：
可通过 .env 或环境变量 WHISPER_MODEL_PATH、XDG_CACHE_HOME 指定，或使用 settings
"""

# -*- coding: utf-8 -*-
import sys
import os

# 项目根目录（用于加载 settings）
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

# 设置标准输出编码为 UTF-8（Windows 兼容）
if sys.platform == 'win32':
    try:
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    except:
        pass

def download_whisper_model(model_name: str = "base", model_path: str = None):
    """下载指定的 Whisper 模型"""
    try:
        import whisper
        
        # 确定模型存储路径（优先使用 settings）
        try:
            from core.config import settings
            _whisper_path = settings.WHISPER_MODEL_PATH
            _xdg_cache = settings.XDG_CACHE_HOME
        except Exception:
            _whisper_path = _xdg_cache = None

        if model_path:
            final_path = model_path
            os.environ["XDG_CACHE_HOME"] = os.path.dirname(model_path)
            print(f"使用自定义路径: {final_path}")
        elif _whisper_path:
            final_path = _whisper_path
            cache_dir = os.path.dirname(final_path)
            os.environ["XDG_CACHE_HOME"] = cache_dir
            print(f"使用配置 WHISPER_MODEL_PATH: {final_path}")
        elif _xdg_cache:
            final_path = os.path.join(_xdg_cache, "whisper")
            print(f"使用配置 XDG_CACHE_HOME: {final_path}")
        elif os.getenv("WHISPER_MODEL_PATH"):
            final_path = os.getenv("WHISPER_MODEL_PATH")
            cache_dir = os.path.dirname(final_path)
            os.environ["XDG_CACHE_HOME"] = cache_dir
            print(f"使用环境变量 WHISPER_MODEL_PATH: {final_path}")
        elif os.getenv("XDG_CACHE_HOME"):
            cache_dir = os.getenv("XDG_CACHE_HOME")
            final_path = os.path.join(cache_dir, "whisper")
            print(f"使用环境变量 XDG_CACHE_HOME: {final_path}")
        else:
            # 默认路径
            default_cache = os.path.join(os.path.expanduser("~"), ".cache")
            final_path = os.path.join(default_cache, "whisper")
            print(f"使用默认路径: {final_path}")
        
        # 确保目录存在
        os.makedirs(final_path, exist_ok=True)
        
        print(f"正在下载 Whisper 模型: {model_name}")
        print(f"模型将保存到: {final_path}")
        print("首次下载可能需要几分钟，请耐心等待...")
        print()
        
        # 加载模型会自动下载
        model = whisper.load_model(model_name)
        
        # 检查模型文件是否存在
        model_file = os.path.join(final_path, f"{model_name}.pt")
        if os.path.exists(model_file):
            print(f"[成功] 模型 {model_name} 下载完成！")
            print(f"模型文件: {model_file}")
            file_size = os.path.getsize(model_file) / (1024 * 1024)  # MB
            print(f"文件大小: {file_size:.2f} MB")
        else:
            print(f"[成功] 模型 {model_name} 下载完成！")
            print(f"（模型文件应位于: {model_file}）")
        
        return True
    except ImportError:
        print("[错误] 未安装 openai-whisper")
        print("请先安装: pip install openai-whisper")
        return False
    except Exception as e:
        print(f"[错误] 下载失败: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    # 解析命令行参数
    model = "base"  # 默认模型
    model_path = None
    
    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--model" or arg == "-m":
            if i + 1 < len(sys.argv):
                model = sys.argv[i + 1]
                i += 2
            else:
                print("错误: --model 需要指定模型名称")
                sys.exit(1)
        elif arg == "--path" or arg == "-p":
            if i + 1 < len(sys.argv):
                model_path = sys.argv[i + 1]
                i += 2
            else:
                print("错误: --path 需要指定路径")
                sys.exit(1)
        elif not arg.startswith("-"):
            # 位置参数：第一个是模型名
            model = arg
            i += 1
        else:
            print(f"未知参数: {arg}")
            print("用法: python download_whisper_model.py [--model MODEL] [--path PATH]")
            sys.exit(1)
    
    print("=" * 60)
    print("Whisper 模型预下载工具")
    print("=" * 60)
    print(f"目标模型: {model}")
    if model_path:
        print(f"存储路径: {model_path}")
    print()
    
    if download_whisper_model(model, model_path):
        print()
        print("=" * 60)
        print("[成功] 下载完成！现在可以使用语音识别功能了。")
        print("=" * 60)
    else:
        print()
        print("=" * 60)
        print("[错误] 下载失败，请检查错误信息。")
        print("=" * 60)
        sys.exit(1)
