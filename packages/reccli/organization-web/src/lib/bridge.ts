import { spawn } from "node:child_process";

const MAX_OUTPUT_BYTES = 32 * 1024 * 1024;

export async function callBridge(
  payload: Record<string, unknown>,
  timeoutMs = 15_000,
): Promise<Record<string, unknown>> {
  const python = process.env.RECCLI_PYTHON || "python3";
  const projectRoot = process.env.RECCLI_PROJECT_ROOT;
  if (!projectRoot) {
    throw new Error("RECCLI_PROJECT_ROOT is not configured");
  }
  const input = JSON.stringify({
    ...payload,
    working_directory: projectRoot,
  });
  const bridgeEnvironment: NodeJS.ProcessEnv = {
    NODE_ENV: process.env.NODE_ENV || "production",
  };
  for (const name of [
    "PATH",
    "PYTHONPATH",
    "PYTHONHOME",
    "HOME",
    "XDG_CONFIG_HOME",
    "CODEX_HOME",
    "CLAUDE_CONFIG_DIR",
    "RECCLI_HOST",
    "LANG",
    "LC_ALL",
    "SYSTEMROOT",
  ]) {
    const value = process.env[name];
    if (value) bridgeEnvironment[name] = value;
  }
  return await new Promise((resolve, reject) => {
    const child = spawn(
      python,
      ["-m", "reccli.organization_console_bridge"],
      {
        env: bridgeEnvironment,
        stdio: ["pipe", "pipe", "pipe"],
      },
    );
    const stdout: Buffer[] = [];
    const stderr: Buffer[] = [];
    let size = 0;
    const timeout = setTimeout(() => {
      child.kill("SIGTERM");
      reject(new Error("RecCli bridge timed out"));
    }, timeoutMs);
    child.stdout.on("data", (chunk: Buffer) => {
      size += chunk.length;
      if (size > MAX_OUTPUT_BYTES) {
        child.kill("SIGTERM");
        reject(new Error("RecCli bridge output exceeded its safety limit"));
        return;
      }
      stdout.push(chunk);
    });
    child.stderr.on("data", (chunk: Buffer) => stderr.push(chunk));
    child.on("error", (error) => {
      clearTimeout(timeout);
      reject(error);
    });
    child.on("close", (code) => {
      clearTimeout(timeout);
      const raw = Buffer.concat(stdout).toString("utf8");
      try {
        const value = JSON.parse(raw || "{}") as Record<string, unknown>;
        if (code && value.status !== "bridge_error") {
          reject(
            new Error(
              Buffer.concat(stderr).toString("utf8") ||
                `RecCli bridge exited with code ${code}`,
            ),
          );
          return;
        }
        resolve(value);
      } catch {
        reject(
          new Error(
            Buffer.concat(stderr).toString("utf8") ||
              "RecCli bridge returned invalid JSON",
          ),
        );
      }
    });
    child.stdin.end(input);
  });
}
