import { redirect } from "next/navigation";
import { desc } from "drizzle-orm";
import semver from "semver";
import { auth } from "@/auth";
import { describeDatabaseConnectError, getDb, isDatabaseConfigured } from "@/db";
import { desktopAppReleases } from "@/db/schema";
import {
  DownloadHall,
  type DownloadHallBanner,
  type ReleaseRow,
} from "@/components/download-hall";

export const dynamic = "force-dynamic";

export default async function DesktopDownloadsPage() {
  const session = await auth();
  if (!session?.user) {
    redirect("/login?callbackUrl=/desktop-downloads");
  }

  let latest: ReleaseRow | null = null;
  let history: ReleaseRow[] = [];
  let emptyBanner: DownloadHallBanner | null = null;

  if (!isDatabaseConfigured()) {
    emptyBanner = {
      tone: "warning",
      text:
        "未检测到 DATABASE_URL：当前 Nexus 进程没有连接 PostgreSQL，本页无法读取 desktop_app_releases——" +
        "这通常会被误认为「发行记录没了」。请在 cloud/nexus/.env.local 配置 DATABASE_URL，先启动 Postgres 再启动 Nexus；" +
        "修改 .env 后请完整停止并重新执行 npm run dev（否则仍可能沿用旧环境变量）。",
    };
  } else {
    const db = getDb();
    if (!db) {
      emptyBanner = {
        tone: "danger",
        text: "无法初始化数据库连接（getDb 返回空）。请检查 DATABASE_URL 是否有效。",
      };
    } else {
      try {
        const rows = await db
          .select({
            version: desktopAppReleases.version,
            notes: desktopAppReleases.notes,
            pubDate: desktopAppReleases.pubDate,
            artifacts: desktopAppReleases.artifacts,
          })
          .from(desktopAppReleases)
          .orderBy(desc(desktopAppReleases.pubDate));

        const mapped: ReleaseRow[] = rows.map((r) => ({
          version: r.version,
          notes: r.notes ?? "",
          pub_date: r.pubDate.toISOString(),
          platforms: Object.keys(r.artifacts ?? {}),
        }));

        const valid = mapped.filter((m) => semver.valid(semver.coerce(m.version) ?? m.version));
        valid.sort((a, b) =>
          semver.rcompare(
            semver.clean(a.version) ?? a.version,
            semver.clean(b.version) ?? b.version
          )
        );
        if (valid.length) {
          latest = valid[0]!;
          history = valid.slice(1);
        } else if (mapped.length > 0) {
          emptyBanner = {
            tone: "warning",
            text: `数据库中有 ${mapped.length} 条桌面发行记录，但 version 字段均不符合 semver，已全部被过滤，页面因而为空。请将版本号登记为三段式数字（如 0.8.105），避免使用无法解析的占位串。`,
          };
        }
        // rows.length === 0：真实无数据，emptyBanner 留空，由 DownloadHall 显示「请登记」提示
      } catch (e) {
        const hint = describeDatabaseConnectError(e);
        const tail = e instanceof Error ? e.message : String(e);
        emptyBanner = {
          tone: "danger",
          text:
            (hint ?? `查询 desktop_app_releases 失败：${tail}`) +
            " 若你确认曾发布过，多为 Postgres 未启动或 DATABASE_URL 指向了另一套空库；请勿使用 docker compose down -v 以免删掉持久卷数据。",
        };
      }
    }
  }

  return (
    <div className="min-h-screen bg-[radial-gradient(ellipse_80%_50%_at_50%_-20%,rgba(34,211,238,0.12),transparent_50%),#050508]">
      <DownloadHall
        latest={latest}
        history={history}
        userEmail={session.user.email}
        emptyBanner={emptyBanner}
      />
    </div>
  );
}
