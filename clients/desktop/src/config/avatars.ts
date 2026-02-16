/**
 * Avatar 形象配置 - 支持商店购买与替换
 *
 * 资产目录规范：
 *   public/assets/avatars/{avatarId}/
 *     manifest.json       # 本文件定义的元数据
 *     color-atlas.json   # 颜色图集（或 manifest 中指定的文件名）
 *     color-atlas-*.png
 *     normal-atlas.json  # 法线图集（可选）
 *     normal-atlas-*.png
 *
 * 命名规则：
 *   - avatarId: 小写+下划线，如 core, premium_neko, premium_cat
 *   - 图集 JSON: 与 TexturePacker 输出兼容，meta.images 为相对路径
 *   - 新形象：复制模板目录，修改 manifest.json 的 id/name
 */

/** 形象清单（manifest.json 结构） */
export interface AvatarManifest {
  /** 唯一标识，与目录名一致 */
  id: string;
  /** 显示名称 */
  name: string;
  /** 版本，用于缓存与更新 */
  version: string;
  /** 颜色图集 JSON 路径（相对本目录或绝对路径） */
  colorAtlas: string;
  /** 法线图集 JSON 路径（可选） */
  normalAtlas?: string;
  /** 是否商店商品（需购买） */
  premium?: boolean;
  /** 缩略图路径（商店展示用） */
  thumbnail?: string;
}

/** 解析后的形象配置（用于加载） */
export interface AvatarConfig {
  avatarId: string;
  name: string;
  /** 颜色图集完整 URL */
  colorAtlasUrl: string;
  /** 法线图集完整 URL（可选） */
  normalAtlasUrl?: string;
  premium?: boolean;
}

/** 资产根路径（相对于 public） */
const AVATARS_BASE = "/assets/avatars";

/** 内置形象注册表（可扩展为从 API 拉取） */
const AVATAR_REGISTRY: Record<string, Omit<AvatarManifest, "id">> = {
  core: {
    name: "默认形象",
    version: "1.0",
    // 兼容旧路径：可指向 atlases/ 或 avatars/core/
    colorAtlas: "color-atlas.json",
    normalAtlas: "normal-atlas.json",
    premium: false,
  },
  // 后续商店形象示例：
  // premium_neko: {
  //   name: "猫咪",
  //   version: "1.0",
  //   colorAtlas: "color-atlas.json",
  //   normalAtlas: "normal-atlas.json",
  //   premium: true,
  // },
};

/** 旧版 atlases 路径兼容（core 未迁移时） */
const LEGACY_ATLAS_PATHS: Record<string, { color: string; normal: string }> = {
  core: {
    color: "/assets/atlases/core-atlas.json",
    normal: "/assets/atlases/core-normal-atlas.json",
  },
};

/**
 * 获取形象配置
 * @param avatarId 形象 ID
 * @param useLegacy 若为 true 且存在旧路径，优先使用旧路径（兼容未迁移的 core）
 */
export function getAvatarConfig(
  avatarId: string,
  useLegacy = true
): AvatarConfig | null {
  const legacy = useLegacy && LEGACY_ATLAS_PATHS[avatarId];
  if (legacy) {
    return {
      avatarId,
      name: AVATAR_REGISTRY[avatarId]?.name ?? avatarId,
      colorAtlasUrl: legacy.color,
      normalAtlasUrl: legacy.normal,
      premium: AVATAR_REGISTRY[avatarId]?.premium ?? false,
    };
  }

  const manifest = AVATAR_REGISTRY[avatarId];
  if (!manifest) return null;

  const base = `${AVATARS_BASE}/${avatarId}`;
  const resolvePath = (p: string) =>
    p.startsWith("/") ? p : `${base}/${p}`;

  return {
    avatarId,
    name: manifest.name,
    colorAtlasUrl: resolvePath(manifest.colorAtlas),
    normalAtlasUrl: manifest.normalAtlas
      ? resolvePath(manifest.normalAtlas)
      : undefined,
    premium: manifest.premium ?? false,
  };
}

/** 获取所有可用形象（含用户已拥有的） */
export function getAvailableAvatars(
  ownedIds: string[] = []
): { id: string; name: string; premium: boolean; owned: boolean }[] {
  const ids = Object.keys(AVATAR_REGISTRY);
  const ownedSet = new Set(ownedIds);
  return ids.map((id) => {
    const m = AVATAR_REGISTRY[id];
    return {
      id,
      name: m?.name ?? id,
      premium: m?.premium ?? false,
      owned: !m?.premium || ownedSet.has(id),
    };
  });
}

/** 默认 Pixi 形象 ID（新用户） */
export const DEFAULT_PIXI_AVATAR_ID = "core";
