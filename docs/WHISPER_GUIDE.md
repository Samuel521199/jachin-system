# Whisper 模型完整指南

## 目录

1. [重要说明](#重要说明)
2. [模型存储位置](#模型存储位置)
3. [预下载模型](#预下载模型)
4. [自定义路径配置](#自定义路径配置)
5. [验证和故障排除](#验证和故障排除)

---

## 重要说明

**安装 `openai-whisper` 包后，模型文件不会自动下载。**

Whisper 使用**延迟加载**机制：
- 首次调用语音识别时，会自动下载模型
- 模型文件存储在本地缓存目录
- 下载后，后续使用无需重新下载

---

## 模型存储位置

### 默认位置

**Windows:**
```
C:\Users\<你的用户名>\.cache\whisper\
```

**Linux/Mac:**
```
~/.cache/whisper/
```

### 自定义存储位置（推荐，节省C盘空间）

可以通过以下方式自定义模型存储路径：

#### 方法1：在 `.env` 文件中配置（推荐）

编辑 `e:\jachin-system\.env` 文件，添加：

```env
# 示例：将模型存储到 E 盘
WHISPER_MODEL_PATH=E:\models\whisper

# 或存储到 D 盘
# WHISPER_MODEL_PATH=D:\AI\Models\whisper
```

#### 方法2：设置环境变量

**Windows PowerShell:**
```powershell
# 临时设置（当前会话有效）
$env:WHISPER_MODEL_PATH = "E:\models\whisper"

# 永久设置（需要管理员权限）
[System.Environment]::SetEnvironmentVariable("WHISPER_MODEL_PATH", "E:\models\whisper", "User")
```

**Windows CMD:**
```cmd
setx WHISPER_MODEL_PATH "E:\models\whisper"
```

**Linux/Mac:**
```bash
export WHISPER_MODEL_PATH=/home/user/models/whisper
# 或添加到 ~/.bashrc 或 ~/.zshrc
echo 'export WHISPER_MODEL_PATH=/home/user/models/whisper' >> ~/.bashrc
```

#### 方法3：使用 XDG_CACHE_HOME（Linux/Mac）

```bash
export XDG_CACHE_HOME=/home/user/.cache
# Whisper 会在 $XDG_CACHE_HOME/whisper 存储模型
```

**优先级：** `.env` 文件 > `WHISPER_MODEL_PATH` 环境变量 > `XDG_CACHE_HOME` 环境变量 > 默认路径

---

## 可用模型

| 模型 | 大小 | 速度 | 准确度 | 推荐场景 |
|------|------|------|--------|----------|
| `tiny` | ~39 MB | 最快 | 较低 | 快速测试 |
| `base` | ~74 MB | 快 | 良好 | **推荐，默认** |
| `small` | ~244 MB | 中等 | 更好 | 高质量识别 |
| `medium` | ~769 MB | 较慢 | 高 | 专业场景 |
| `large` | ~1550 MB | 最慢 | 最高 | 最高质量需求 |

---

## 预下载模型

为了避免首次使用时等待，可以预先下载模型：

### 方法1：使用预下载脚本（推荐）

```bash
# Windows - 使用默认路径
scripts\download_whisper_model.bat

# Windows - 指定模型大小
scripts\download_whisper_model.bat base    # 默认，推荐
scripts\download_whisper_model.bat small   # 更高质量
scripts\download_whisper_model.bat tiny    # 最快

# Windows - 指定自定义路径
scripts\download_whisper_model.bat --path E:\models\whisper
scripts\download_whisper_model.bat base --path E:\models\whisper

# Linux/Mac
python scripts/download_whisper_model.py --path /home/user/models/whisper
```

### 方法2：手动下载（Python）

```python
import whisper

# 下载 base 模型（推荐）
model = whisper.load_model("base")

# 或下载其他模型
# model = whisper.load_model("small")
# model = whisper.load_model("medium")
```

### 方法3：首次使用时自动下载

如果未预下载，首次使用语音识别功能时会自动下载，但可能需要等待几分钟。

---

## 自定义路径配置

### 快速配置指南

#### 方法1：在 `.env` 文件中配置（推荐，最简单）

1. 打开 `e:\jachin-system\.env` 文件

2. 找到 `WHISPER_MODEL_PATH` 配置项，设置为你想要的路径：

```env
# 示例：存储到 E 盘
WHISPER_MODEL_PATH=E:\models\whisper

# 或存储到 D 盘
# WHISPER_MODEL_PATH=D:\AI\Models\whisper
```

3. 保存文件

4. 重启后端服务

5. 使用预下载脚本下载模型到指定位置：

```bash
# Windows
scripts\download_whisper_model.bat --path E:\models\whisper
```

#### 方法2：设置环境变量

**Windows PowerShell（临时，当前会话有效）：**
```powershell
$env:WHISPER_MODEL_PATH = "E:\models\whisper"
```

**Windows PowerShell（永久，需要重启终端）：**
```powershell
[System.Environment]::SetEnvironmentVariable("WHISPER_MODEL_PATH", "E:\models\whisper", "User")
```

**Windows CMD（永久）：**
```cmd
setx WHISPER_MODEL_PATH "E:\models\whisper"
```

设置后需要重启后端服务。

#### 方法3：在预下载脚本中指定路径

```bash
# Windows
scripts\download_whisper_model.bat base --path E:\models\whisper

# Linux/Mac
python scripts/download_whisper_model.py base --path /home/user/models/whisper
```

### 验证配置

启动后端服务后，查看日志，应该看到：

```
INFO: Whisper STT provider initialized with model: base
INFO: Model storage path: E:\models\whisper
```

### 注意事项

1. **路径格式**：
   - Windows: 使用反斜杠或正斜杠都可以，例如 `E:\models\whisper` 或 `E:/models/whisper`
   - Linux/Mac: 使用正斜杠，例如 `/home/user/models/whisper`

2. **目录权限**：确保应用有权限在指定路径创建目录和写入文件

3. **路径不存在**：如果路径不存在，系统会自动创建

4. **优先级**：
   - `.env` 文件中的 `WHISPER_MODEL_PATH`（最高优先级）
   - 环境变量 `WHISPER_MODEL_PATH`
   - 环境变量 `XDG_CACHE_HOME`
   - 默认路径 `~/.cache/whisper`（最低优先级）

### 迁移现有模型

如果之前已经下载了模型到默认位置，可以手动移动：

**Windows:**
```powershell
# 1. 创建新目录
New-Item -ItemType Directory -Path "E:\models\whisper" -Force

# 2. 移动模型文件
Move-Item "$env:USERPROFILE\.cache\whisper\*" "E:\models\whisper\" -Force

# 3. 验证
Test-Path "E:\models\whisper\base.pt"
```

**Linux/Mac:**
```bash
# 1. 创建新目录
mkdir -p /home/user/models/whisper

# 2. 移动模型文件
mv ~/.cache/whisper/* /home/user/models/whisper/

# 3. 验证
ls /home/user/models/whisper/
```

---

## 当前配置

系统默认使用 **`base`** 模型，在以下文件中配置：

- `core/core/voice/stt.py`: `OpenAIWhisperSTTProvider.__init__(model: str = "base")`

如需更改模型大小，可以：
1. 修改 `core/core/voice/stt.py` 中的默认值
2. 或通过环境变量配置（如果后续添加支持）

---

## 验证和故障排除

### 验证模型是否已下载

检查模型文件是否存在：

**Windows PowerShell（默认路径）:**
```powershell
Test-Path "$env:USERPROFILE\.cache\whisper\base.pt"
```

**Windows PowerShell（自定义路径）:**
```powershell
# 如果设置了 WHISPER_MODEL_PATH
$modelPath = $env:WHISPER_MODEL_PATH
if (-not $modelPath) { $modelPath = "$env:USERPROFILE\.cache\whisper" }
Test-Path "$modelPath\base.pt"
```

**Linux/Mac（默认路径）:**
```bash
ls ~/.cache/whisper/
```

**Linux/Mac（自定义路径）:**
```bash
# 如果设置了 WHISPER_MODEL_PATH
echo $WHISPER_MODEL_PATH
ls $WHISPER_MODEL_PATH
```

如果看到 `base.pt` 文件（约74MB），说明模型已下载。

### 常见问题

#### Q: 设置了路径但模型还是下载到默认位置？

A: 检查：
1. `.env` 文件是否正确保存
2. 环境变量是否正确设置
3. 是否重启了后端服务
4. 查看后端日志确认使用的路径

#### Q: 权限错误？

A: 确保：
1. 应用有权限在指定路径创建目录
2. 路径不存在时，父目录有写入权限
3. Windows 可能需要以管理员身份运行

#### Q: 路径包含空格怎么办？

A: 使用引号：
```env
WHISPER_MODEL_PATH="E:\My Models\whisper"
```

或使用短路径名（Windows）：
```env
WHISPER_MODEL_PATH=E:\MYMOD~1\whisper
```

#### Q: 模型下载失败？

**解决方案：**
1. 检查网络连接
2. 检查磁盘空间
3. 尝试手动下载：
   ```python
   import whisper
   whisper.load_model("base")
   ```

#### Q: 找不到模型文件？

**解决方案：**
1. 检查缓存目录权限
2. 手动创建缓存目录：
   ```bash
   # 默认路径
   mkdir -p ~/.cache/whisper  # Linux/Mac
   mkdir %USERPROFILE%\.cache\whisper  # Windows
   
   # 自定义路径
   mkdir -p /home/user/models/whisper  # Linux/Mac
   mkdir E:\models\whisper  # Windows
   ```
3. 检查 `.env` 文件中的 `WHISPER_MODEL_PATH` 配置是否正确
4. 检查环境变量是否设置正确

#### Q: 模型加载很慢？

**解决方案：**
- 使用更小的模型（如 `tiny`）
- 或确保模型文件在本地（已下载）

### 注意事项

1. **首次下载需要网络连接**：模型从 GitHub 下载
2. **磁盘空间**：确保有足够空间（base 模型约74MB）
3. **下载速度**：取决于网络速度，可能需要几分钟
4. **ffmpeg 必需**：Whisper 需要 ffmpeg 来处理音频文件
