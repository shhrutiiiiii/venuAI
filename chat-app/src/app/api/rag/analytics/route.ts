import { NextResponse } from "next/server";

const RAG_SERVER = process.env.RAG_SERVER_URL || "http://127.0.0.1:8000";

/**
 * Proxy to the VenuAI analytics dashboard endpoint.
 * Returns aggregated query stats, grounding trends, hallucination rates,
 * top topics, and recent queries for the admin dashboard.
 */
export async function GET() {
  try {
    const response = await fetch(`${RAG_SERVER}/analytics/dashboard`, {
      cache: "no-store",
    });
    if (!response.ok) {
      return NextResponse.json(
        { error: "Analytics endpoint returned error", status: response.status },
        { status: response.status }
      );
    }
    const data = await response.json();
    return NextResponse.json(data);
  } catch (err) {
    console.error("[Analytics Dashboard] Error:", err);
    return NextResponse.json(
      { error: "Analytics service unavailable." },
      { status: 503 }
    );
  }
}
