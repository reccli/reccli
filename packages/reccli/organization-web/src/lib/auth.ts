import type { NextRequest } from "next/server";

export function authorized(request: NextRequest): boolean {
  const expected = process.env.RECCLI_CONSOLE_TOKEN;
  if (!expected) return false;
  return request.headers.get("x-reccli-console-token") === expected;
}

export function unauthorized(): Response {
  return Response.json(
    { status: "unauthorized", error: "Valid console token required." },
    { status: 401 },
  );
}
