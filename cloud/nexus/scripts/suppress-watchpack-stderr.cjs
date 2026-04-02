/**
 * 在 `node --require` 下尽早加载，过滤 Webpack Watchpack 在 Windows 盘符根目录
 * 对受保护路径 lstat 产生的已知噪声（EINVAL / System Volume Information 等）。
 * 不影响其它 stderr 输出。
 *
 * 需要看原始 Watchpack 日志时，在 shell 中设置后启动：
 *   set NEXUS_DEV_SHOW_WATCHPACK=1   （PowerShell: $env:NEXUS_DEV_SHOW_WATCHPACK=1）
 * 并改用：npx next dev（不经本 preload）。
 */
"use strict";

if (process.env.NEXUS_DEV_SHOW_WATCHPACK !== "1") {
  shouldDropLineImpl();
}

function shouldDropLineImpl() {
  function shouldDropLine(s) {
    if (!s.includes("Watchpack Error (initial scan)")) return false;
    if (s.includes("System Volume Information")) return true;
    if (s.includes("$Recycle.Bin") || s.includes("$RECYCLE.BIN")) return true;
    return false;
  }

  const origWrite = process.stderr.write.bind(process.stderr);

  process.stderr.write = function stderrWriteFiltered(chunk, encoding, cb) {
    if (chunk == null || chunk === "") {
      return origWrite(chunk, encoding, cb);
    }
    let s;
    if (typeof chunk === "string") {
      s = chunk;
    } else if (Buffer.isBuffer(chunk)) {
      s =
        typeof encoding === "string" && encoding !== "buffer"
          ? chunk.toString(encoding)
          : chunk.toString("utf8");
    } else {
      s = String(chunk);
    }

    if (shouldDropLine(s)) {
      if (typeof encoding === "function") {
        encoding();
      } else if (typeof cb === "function") {
        cb();
      }
      return true;
    }

    return origWrite(chunk, encoding, cb);
  };

  const origErr = console.error;
  console.error = function consoleErrorFiltered(...args) {
    const msg = args
      .map((a) =>
        a instanceof Error ? `${a.message}\n${a.stack ?? ""}` : String(a)
      )
      .join(" ");
    if (shouldDropLine(msg)) return;
    origErr.apply(console, args);
  };
}
