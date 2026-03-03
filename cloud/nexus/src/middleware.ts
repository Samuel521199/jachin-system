import { NextResponse, type NextRequest } from "next/server";
import { updateSession } from "@/lib/supabase-auth/middleware";

const PROTECTED_PATHS = ["/console", "/plaza", "/forge", "/market"];

function isProtected(pathname: string): boolean {
  return PROTECTED_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`));
}

export async function middleware(request: NextRequest) {
  const skipAuth = process.env.SKIP_AUTH === "true";
  const hasSupabase =
    process.env.NEXT_PUBLIC_SUPABASE_URL && process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

  if (skipAuth || !hasSupabase) {
    return NextResponse.next();
  }

  const { response, user } = await updateSession(request);

  const url = request.nextUrl.clone();
  if (isProtected(url.pathname) && !user) {
    url.pathname = "/login";
    url.searchParams.set("redirect", request.nextUrl.pathname);
    return NextResponse.redirect(url);
  }

  if (url.pathname === "/login" && user) {
    const redirect = url.searchParams.get("redirect") || "/console";
    url.pathname = redirect;
    url.searchParams.delete("redirect");
    return NextResponse.redirect(url);
  }

  return response;
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|api/).*)",
  ],
};
