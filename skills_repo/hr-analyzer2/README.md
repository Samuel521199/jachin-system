# HR 简历透析镜 2

基于 hr-analyzer 重新打包，支持：
- **动态配置修改**：通过 Skill Matrix 设置按钮修改 JD_template 等配置
- **卸载时配置自动清除**：删除技能时 skill_registry 中的配置会自动清理

## 构建

```bash
cargo build --target wasm32-unknown-unknown --release
```

输出：`target/wasm32-unknown-unknown/release/hr_analyzer2.wasm` → 重命名为 `main.wasm`

## 安装方式

### 方式一：内置（已就绪）

技能已放入 `l3_node/skills/wasm_plugins/hr-analyzer2/`，重启 L3 后即可在 Skill Matrix 中看到。

### 方式二：L2 侧载

1. 将 `plugin.json` 和 `main.wasm` 放入 `~/.jachin/inventory/skills/hr-analyzer2/`
2. 或解压 `hr-analyzer2-package.zip` 到该目录
3. 调用 L2 热重载：`POST /api/v2/inventory/reload`

### 方式三：上传包

`skills_repo/hr-analyzer2-package.zip` 可用于 L2 管理面板上传或分发给其他节点。
