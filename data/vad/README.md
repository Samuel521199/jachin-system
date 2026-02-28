# VAD 模型目录

本目录用于放置 **Silero VAD** 的 ONNX 模型文件：`silero_vad.onnx`（约 2MB）。

## 模型未随仓库提供

`silerio_vad.onnx` **需要自行下载或导出**，仓库中不包含该文件。

## 获取方式

### 方式一：Python 包自动下载

安装 [silero-vad](https://github.com/snakers4/silero-vad) 后，用 `load_silero_vad(onnx=True)` 会从官方源下载 ONNX 模型到缓存。可从缓存目录中复制出 `silero_vad.onnx` 到本目录，或参考官方 Wiki 的导出说明。

```bash
pip install silero-vad
python -c "from silero_vad import load_silero_vad; load_silero_vad(onnx=True)"
# 然后在 Python 缓存或 silero-vad 示例目录中查找 .onnx 文件，复制到 data/vad/silero_vad.onnx
```

### 方式二：从 Hugging Face 下载

- 可到 [Hugging Face silero-vad 相关页面](https://huggingface.co/snakers4/silero-vad-models) 或 [deepghs/silero-vad-onnx](https://huggingface.co/deepghs/silero-vad-onnx) 查找并下载与 16kHz、512 样本/块兼容的 ONNX 文件。
- 将下载到的 ONNX 文件重命名为 `silero_vad.onnx` 并放入本目录：`data/vad/silero_vad.onnx`。

## 环境变量（可选）

若希望**固定使用本仓库下的 data 目录**（例如开发环境），可在运行桌面端前设置：

- **Windows (PowerShell)**：`$env:JACHIN_VAD_DEBUG_PATH = "E:\jachin-system\data"`
- **Windows (CMD)**：`set JACHIN_VAD_DEBUG_PATH=E:\jachin-system\data`
- **或在项目根目录 `.env` 中**（若桌面端会读取）：`JACHIN_VAD_DEBUG_PATH=E:\jachin-system\data`

此时程序会使用：**`E:\jachin-system\data\vad\silero_vad.onnx`**。

不设置时，程序会按“便携目录 → 系统数据目录”自动解析路径。只要 `silero_vad.onnx` 已放在本目录，不设置环境变量也可（程序会回退到 `data/vad` 或系统目录）。
