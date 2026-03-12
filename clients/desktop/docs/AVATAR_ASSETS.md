# Avatar 资产规范

为支持后期商店购买与形象替换，资产目录与命名需遵循以下规范。

## 目录结构

```
public/assets/avatars/
├── {avatarId}/              # 形象唯一 ID，如 core, premium_neko
│   ├── manifest.json        # 元数据（必选）
│   ├── color-atlas.json    # 颜色图集
│   ├── color-atlas-1.png
│   ├── color-atlas-2.png
│   ├── ...
│   ├── normal-atlas.json   # 法线图集（可选）
│   ├── normal-atlas-1.png
│   └── ...
├── core/                   # 默认形象
├── premium_neko/           # 商店形象示例
└── ...
```

## 命名规则

| 项目 | 规则 | 示例 |
|------|------|------|
| avatarId | 小写字母 + 下划线，与目录名一致 | `core`, `premium_neko` |
| 颜色图集 JSON | `color-atlas.json` 或自定义 | `color-atlas.json` |
| 颜色图集 PNG | 与 JSON 中 `meta.images` 一致 | `color-atlas-1.png` |
| 法线图集 JSON | `normal-atlas.json` 或自定义 | `normal-atlas.json` |
| 法线图集 PNG | 与 JSON 中 `meta.images` 一致 | `normal-atlas-1.png` |

## manifest.json

```json
{
  "id": "core",
  "name": "默认形象",
  "version": "1.0",
  "colorAtlas": "color-atlas.json",
  "normalAtlas": "normal-atlas.json",
  "premium": false,
  "thumbnail": "thumbnail.png"
}
```

| 字段 | 类型 | 必选 | 说明 |
|------|------|------|------|
| id | string | ✓ | 与目录名一致 |
| name | string | ✓ | 显示名称 |
| version | string | ✓ | 用于缓存与更新 |
| colorAtlas | string | ✓ | 颜色图集 JSON，相对本目录 |
| normalAtlas | string | | 法线图集 JSON |
| premium | boolean | | 是否商店商品 |
| thumbnail | string | | 缩略图路径 |

## 图集 JSON 规范

与 TexturePacker 输出兼容：

- `meta.images`: 图片文件名数组，如 `["color-atlas-1.png", "color-atlas-2.png"]`
- `animations`: 动画定义，key 为大写（IDLE, SLEEP, SMILE 等）
- 支持内嵌帧格式（`frames` 为对象）或分离格式（顶层 `frames` + 动画内 `frames` 数组）

## 兼容旧路径

`core` 形象在迁移前可继续使用 `public/assets/atlases/` 下的文件：

- `core-atlas.json` → 颜色图集
- `core-normal-atlas.json` → 法线图集

迁移时可将文件移至 `public/assets/avatars/core/` 并重命名为 `color-atlas.json`、`normal-atlas.json`，同时更新 `meta.images` 中的文件名。

## 新增形象流程

1. 在 `public/assets/avatars/` 下创建 `{avatarId}/` 目录
2. 编写 `manifest.json`
3. 放入图集 JSON 与 PNG
4. 在 `src/config/avatars.ts` 的 `AVATAR_REGISTRY` 中注册（或后续改为 API 拉取）
