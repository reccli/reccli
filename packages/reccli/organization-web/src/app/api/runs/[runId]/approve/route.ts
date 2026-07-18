import type { NextRequest } from "next/server";
import { authorized, unauthorized } from "@/lib/auth";
import { callBridge } from "@/lib/bridge";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(
  request: NextRequest,
  context: { params: Promise<{ runId: string }> },
) {
  if (!authorized(request)) return unauthorized();
  const { runId } = await context.params;
  try {
    const body = (await request.json()) as Record<string, unknown>;
    const result = await callBridge(
      {
        command: "approve",
        run_id: runId,
        request_sha256: body.request_sha256,
        idempotency_key: body.idempotency_key,
      },
      60_000,
    );
    return Response.json(
      result,
      { status: result.status === "bridge_error" ? 500 : 200 },
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
