# Jachin Plugin SDK (Python) - 编译脚本
# 使用 py2wasm 将 Python 插件编译为 Wasm，上传至 Jachin Nexus 商城

.PHONY: build test run install

DIST_DIR := dist
PLUGIN_WASM := $(DIST_DIR)/plugin.wasm
ENTRY := src/main.py

# 默认目标
all: build

# 编译：Python -> Wasm（在 src/ 下执行以便解析 jachin_sdk 导入）
build:
	@echo "🔧 Jachin Plugin SDK - 编译 Python 插件为 Wasm..."
	@mkdir -p $(DIST_DIR)
	@which py2wasm >/dev/null 2>&1 || (echo "❌ 请先安装 py2wasm: pip install py2wasm"; exit 1)
	cd src && py2wasm main.py -o ../$(PLUGIN_WASM)
	@cp plugin.json $(DIST_DIR)/
	@echo "✅ 编译完成: $(PLUGIN_WASM)"
	@echo "📤 上传至 Jachin Nexus 控制台: dist/plugin.wasm + dist/plugin.json"

# 本地测试（不编译，直接运行 Python）
test:
	@echo "🧪 本地测试 stdin/stdout 协议..."
	@echo '{"ticker": "BTC"}' | python $(ENTRY)
	@echo ""
	@echo '{"ticker": "ETH"}' | python $(ENTRY)

# 直接运行（开发调试）
run:
	python $(ENTRY)

# 安装 py2wasm 依赖
install:
	pip install py2wasm
	@echo "✅ py2wasm 已安装（需 Python 3.11）"
