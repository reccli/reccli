import type { NextRequest } from "next/server";
import { authorized, unauthorized } from "@/lib/auth";
import { callBridge } from "@/lib/bridge";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(request: NextRequest) {
  if (!authorized(request)) return unauthorized();
  try {
    return Response.json(await callBridge({ command: "list", limit: 100 }));
  } catch (error) {
    return Response.json(
      {
        status: "bridge_error",
        error: error instanceof Error ? error.message : String(error),
      },
      { status: 500 },
    );
  }
}
