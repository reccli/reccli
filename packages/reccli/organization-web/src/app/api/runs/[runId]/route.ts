import type { NextRequest } from "next/server";
import { authorized, unauthorized } from "@/lib/auth";
import { callBridge } from "@/lib/bridge";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ runId: string }> },
) {
  if (!authorized(request)) return unauthorized();
  const { runId } = await context.params;
  const recent = Number(request.nextUrl.searchParams.get("recent") || "180");
  try {
    return Response.json(
      await callBridge({
        command: "snapshot",
        run_id: runId,
        include_recent: Math.min(Math.max(recent, 1), 500),
      }),
    );
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
