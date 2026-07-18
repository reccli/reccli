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
    return Response.json(
      await callBridge({
        command: "control",
        run_id: runId,
        action: body.action,
        target: body.target,
        content: body.content,
        tag: body.tag,
        idempotency_key: body.idempotency_key,
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
