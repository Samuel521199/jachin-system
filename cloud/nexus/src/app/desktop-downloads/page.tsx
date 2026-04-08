import { redirect } from "next/navigation";
import { desc } from "drizzle-orm";
import semver from "semver";
import { auth } from "@/auth";
import { getDb, isDatabaseConfigured } from "@/db";
import { desktopAppReleases } from "@/db/schema";
import { DownloadHall, type ReleaseRow } from "@/components/download-hall";

export const dynamic = "force-dynamic";

export default async function DesktopDownloadsPage() {
  const session = await auth();
  if (!session?.user) {
    redirect("/login?callbackUrl=/desktop-downloads");
  }

  let latest: ReleaseRow | null = null;
  let history: ReleaseRow[] = [];

  if (isDatabaseConfigured()) {
    const db = getDb();
    if (db) {
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
      }
    }
  }

  return (
    <div className="min-h-screen bg-[radial-gradient(ellipse_80%_50%_at_50%_-20%,rgba(34,211,238,0.12),transparent_50%),#050508]">
      <DownloadHall
        latest={latest}
        history={history}
        userEmail={session.user.email}
      />
    </div>
  );
}
