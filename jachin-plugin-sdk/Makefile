# Jachin Plugin SDK - 一键编译为 .wasm
# 用法: make build

TARGET = wasm32-unknown-unknown
OUT = dist/main.wasm

.PHONY: build clean check

build:
	@echo ">>> 编译 JPP 插件..."
	@rustup target add $(TARGET) 2>/dev/null || true
	cargo build --target $(TARGET) --release
	@mkdir -p dist
	@cp target/$(TARGET)/release/jachin_plugin_example.wasm dist/main.wasm
	@echo ">>> 完成: $(OUT)"

clean:
	cargo clean
	rm -rf dist

check:
	cargo check --target $(TARGET)
