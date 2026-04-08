import { NextResponse } from "next/server";
import { auth } from "@/auth.edge";

export default auth((req) => {
  return NextResponse.next();
});

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)",
  ],
};
