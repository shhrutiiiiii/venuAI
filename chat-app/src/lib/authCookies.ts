import { NextResponse } from "next/server";

export const ACCESS_COOKIE = "venuai_access_token";
export const REFRESH_COOKIE = "venuai_refresh_token";
/** The refresh cookie is only ever sent to the auth proxy routes. */
export const REFRESH_PATH = "/api/auth";

const secure = () => process.env.NODE_ENV === "production";

function expire(res: NextResponse, name: string, path: string): void {
  // NextResponse.cookies is keyed by name, so a second cookie with the same name
  // but a different path must be appended as a raw Set-Cookie header.
  res.headers.append("Set-Cookie", `${name}=; Path=${path}; Max-Age=0; HttpOnly; SameSite=Lax`);
}

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
  expire(res, REFRESH_COOKIE, "/");
}

export function clearSessionCookies(res: NextResponse): void {
  res.cookies.set(ACCESS_COOKIE, "", { httpOnly: true, path: "/", maxAge: 0 });
  res.cookies.set(REFRESH_COOKIE, "", { httpOnly: true, path: REFRESH_PATH, maxAge: 0 });
  expire(res, REFRESH_COOKIE, "/");
}
