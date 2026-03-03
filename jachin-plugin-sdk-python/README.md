# ⚡ Jachin Plugin Protocol (JPP) - Python SDK

**Write Once, Run Everywhere, Earn Crypto.**

不要让你的绝妙代码在硬盘里吃灰。使用 JPP Python SDK，**只需 5 分钟**，你就能将 Python 函数打包成绝对安全的 Wasm 神经元，上传至 Jachin 神经元商城，让全球的边缘智能体为你打工！

**每次你的技能被调用，你都将获得 `plugin.json` 中设定的版税分润。**

---

## 🛠️ 极速起步 (The 5-Minute Magic)

### Step 1: 编写你的业务逻辑 (`src/main.py`)

只需加上 `@jachin_plugin` 装饰器，所有 stdin/stdout (WASI) 我们替你搞定。

```python
from jachin_sdk import jachin_plugin

@jachin_plugin
def get_crypto_price(ticker: str) -> dict:
    # 你的核心逻辑
    return {"ticker": ticker, "price": 97500.0, "status": "bullish"}
```

### Step 2: 声明你的版税 (`plugin.json`)

```json
{
  "name": "crypto-oracle",
  "description": "Fetch real-time crypto prices",
  "royalty_fee": 0.01,
  "royalty_currency": "USDC"
}
```

### Step 3: 一键编译为 Wasm 物理装甲

```bash
pip install py2wasm   # 首次，需 Python 3.11
make build
```

拿到 `dist/plugin.wasm`，上传到 Jachin Nexus The Forge（铸造厂），**开始收租！**

### Step 4: 上传与发布

1. 打开 [Jachin Nexus Console](http://localhost:3000/console)
2. 进入 The Forge 或 神经元商城
3. 上传 `dist/plugin.wasm` + `dist/plugin.json`
4. 设置价格与分润
5. 发布！全球边缘智能体即刻可用

---

## 插件清单 (plugin.json)

```json
{
  "id": "com.example.crypto-price",
  "name": "Jachin Crypto Price",
  "version": "1.0.0",
  "royalty_fee": 0.01,
  "royalty_currency": "USDC",
  "entry_point": "dist/plugin.wasm",
  "schema": {
    "input": { "type": "object", "properties": { "ticker": { "type": "string" } } },
    "output": { "type": "object", "properties": { "price_usd": { "type": "number" } } }
  }
}
```

- `royalty_fee`：单次调用分润金额（USDC）
- `schema`：入参/出参 JSON Schema，供商城与 Agent 解析

---

## 标准协议：stdin/stdout JSON

Jachin Daemon 通过 **stdin** 传入 JSON 参数，插件将结果写入 **stdout**：

```
stdin:  {"ticker": "BTC"}
stdout: {"ticker": "BTC", "price_usd": 97500, "change_24h": 2.3}
```

本地测试：

```bash
make test
# 或
echo '{"ticker": "ETH"}' | python src/main.py
```

---

## 项目结构

```
jachin-plugin-sdk-python/
├── src/
│   ├── jachin_sdk.py   # @jachin_plugin 装饰器、stdin/stdout 协议
│   └── main.py         # 示例插件：fetch_crypto_price
├── plugin.json         # 清单与分润设置
├── Makefile            # make build / make test
└── README.md
```

---

## 环境要求

- **Python 3.11**（py2wasm 要求）
- **py2wasm**：`pip install py2wasm`
- **make**（或手动执行 `cd src && py2wasm main.py -o ../dist/plugin.wasm`）

---

## 运行时说明

Python 插件编译后的 Wasm 使用 **stdin/stdout** 协议。Jachin Layer 2 的 `core/wasm_runner.py` 已支持 **WASI 模式**：传入 `stdin_json` 即可通过 stdin 传递 JSON，并读取 stdout 结果。见 [wasm_runner.run_plugin_wasi](../core/wasm_runner.py)。

---

## 与 Rust SDK 对比

| 特性 | Python SDK | [Rust SDK](../jachin-plugin-sdk/) |
|------|------------|-----------------------------------|
| 上手速度 | ⭐⭐⭐ 极快 | ⭐⭐ 需学 Rust |
| Wasm 体积 | 较大（含 Python 运行时） | 极小（KB 级） |
| 性能 | 良好 | 极佳 |
| 适用场景 | 快速原型、数据类插件 | 生产级、IO 密集型 |

---

## 相关链接

- [Jachin Nexus](https://github.com/Samuel521199/jachin-system) - 主仓库
- [jachin-plugin-sdk](../jachin-plugin-sdk/) - Rust 版 SDK（轻量 Wasm）
- [JMP 规范](../docs/JMP_SPEC.md) - 完整协议

---

**现在就开始，用 Python 赚版税！** 🚀
