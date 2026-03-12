.PHONY: build test run

DIST_DIR := dist
PLUGIN_WASM := $(DIST_DIR)/plugin.wasm

build:
	@echo "🔧 编译 HR Swarm Engine 为 Wasm..."
	@mkdir -p $(DIST_DIR)
	@which py2wasm >/dev/null 2>&1 || (echo "❌ 需 Python 3.11: pip install py2wasm"; exit 1)
	cd src && py2wasm main.py -o ../$(PLUGIN_WASM)
	@cp plugin.json $(DIST_DIR)/
	@echo "✅ 完成: $(PLUGIN_WASM)"

test:
	@echo "🧪 本地测试 stdin/stdout..."
	@echo '{"resume_text":"张三\nJava 3年\nSpringCloud","hr_criteria":"要求本科"}' | python src/main.py

run:
	python src/main.py
