import { NextRequest } from "next/server";
import { proxyToBackend } from "@/lib/proxy";

/** POST /export/report {format,title,markdown,citations} -> binary download (docx/pdf/md). */
export async function POST(req: NextRequest) {
  return proxyToBackend(req, "/export/report", { withBody: true, timeoutMs: 60_000, unavailable: "Export service unavailable." });
}
