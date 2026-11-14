import { NextResponse } from "next/server";

export const ACCESS_COOKIE = "venuai_access_token";
export const REFRESH_COOKIE = "venuai_refresh_token";
/** The refresh cookie is only ever sent to the auth proxy routes. */
export const REFRESH_PATH = "/api/auth";

const secure = () => process.env.NODE_ENV === "production";

export function setSessionCookies(res: NextResponse, access: string, refresh: string): void {
  res.cookies.set(ACCESS_COOKIE, access, {
    httpOnly: true,
    sameSite: "lax",
    secure: secure(),
    path: "/",
    maxAge: 60 * 30, // matches backend ACCESS_TOKEN_TTL_SECONDS default
  });
  res.cookies.set(REFRESH_COOKIE, refresh, {
    httpOnly: true,
    sameSite: "strict",
    secure: secure(),
    path: REFRESH_PATH,
    maxAge: 60 * 60 * 24 * 14, // matches backend REFRESH_TOKEN_TTL_SECONDS default
  });
  // Sessions created before path-scoping left a refresh cookie at "/": drop it.
  res.cookies.set(REFRESH_COOKIE, "", { path: "/", maxAge: 0 });
}

export function clearSessionCookies(res: NextResponse): void {
  res.cookies.set(ACCESS_COOKIE, "", { httpOnly: true, path: "/", maxAge: 0 });
  res.cookies.set(REFRESH_COOKIE, "", { httpOnly: true, path: REFRESH_PATH, maxAge: 0 });
  res.cookies.set(REFRESH_COOKIE, "", { httpOnly: true, path: "/", maxAge: 0 });
}
