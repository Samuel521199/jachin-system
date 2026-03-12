# Jachin System Pilot - 全链路测试 Skill
# 用法: make build

TARGET = wasm32-unknown-unknown
CRATE = jachin_system_pilot
OUT = main.wasm

.PHONY: build clean check

build:
	@echo ">>> 编译 jachin-system-pilot..."
	@rustup target add $(TARGET) 2>/dev/null || true
	cargo build --target $(TARGET) --release
	@cp target/$(TARGET)/release/$(CRATE).wasm $(OUT)
	@echo ">>> 完成: $(OUT)"

clean:
	cargo clean
	rm -f $(OUT)

check:
	cargo check --target $(TARGET)
