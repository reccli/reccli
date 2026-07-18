"""Launcher for the localhost-only RecCli organization console."""

from __future__ import annotations

import argparse
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Optional

from .project.devproject import discover_project_root


def web_root() -> Path:
    return Path(__file__).resolve().parent / "organization-web"


def _newest_source_mtime(root: Path) -> float:
    candidates = [
        root / "package.json",
        root / "package-lock.json",
        root / "next.config.ts",
        root / "tsconfig.json",
    ]
    candidates.extend((root / "src").rglob("*") if (root / "src").is_dir() else [])
    return max(
        (path.stat().st_mtime for path in candidates if path.is_file()),
        default=0.0,
    )


def _run_setup(command: list[str], cwd: Path) -> None:
    completed = subprocess.run(command, cwd=cwd, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"{' '.join(command)} failed with exit {completed.returncode}")


def ensure_console_ready(root: Path, *, development: bool = False) -> None:
    if shutil.which("node") is None or shutil.which("npm") is None:
        raise RuntimeError("Node.js and npm are required for the organization console")
    if not (root / "package.json").is_file():
        raise RuntimeError(f"organization console source is missing: {root}")
    if not (root / "node_modules" / ".bin" / "next").exists():
        print("Installing RecCli organization console dependencies…", flush=True)
        _run_setup(["npm", "install"], root)
    if development:
        return
    build_id = root / ".next" / "BUILD_ID"
    if (
        not build_id.is_file()
        or build_id.stat().st_mtime < _newest_source_mtime(root)
    ):
        print("Building RecCli organization console…", flush=True)
        _run_setup(["npm", "run", "build"], root)


def _wait_for_port(host: str, port: int, process: subprocess.Popen, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"organization console exited early with code {process.returncode}",
            )
        try:
            with socket.create_connection((host, port), timeout=0.25):
                return
        except OSError:
            time.sleep(0.15)
    raise RuntimeError(f"organization console did not become ready on {host}:{port}")


def serve_console(
    project: Path,
    *,
    port: int = 8777,
    open_browser: bool = True,
    development: bool = False,
    token: Optional[str] = None,
) -> int:
    project_root = discover_project_root(project.expanduser().resolve())
    if project_root is None:
        raise RuntimeError(f"No RecCli/Git project found from {project}")
    root = web_root()
    ensure_console_ready(root, development=development)
    token = token or secrets.token_urlsafe(24)
    host = "127.0.0.1"
    url = f"http://{host}:{int(port)}/?token={token}"
    env = os.environ.copy()
    package_parent = str(Path(__file__).resolve().parent.parent)
    inherited_pythonpath = env.get("PYTHONPATH", "")
    env.update({
        "RECCLI_PROJECT_ROOT": str(project_root),
        "RECCLI_CONSOLE_TOKEN": token,
        "RECCLI_PYTHON": sys.executable,
        "PYTHONPATH": os.pathsep.join(
            value for value in (package_parent, inherited_pythonpath) if value
        ),
        "HOSTNAME": host,
        "PORT": str(int(port)),
    })
    script = "dev" if development else "start"
    process = subprocess.Popen(
        [
            "npm",
            "run",
            script,
            "--",
            "--hostname",
            host,
            "--port",
            str(int(port)),
        ],
        cwd=root,
        env=env,
    )
    try:
        _wait_for_port(host, int(port), process)
        display_url = url if not open_browser else f"http://{host}:{int(port)}/"
        print(f"RecCli organization console: {display_url}", flush=True)
        print(f"Project: {project_root}", flush=True)
        if open_browser:
            webbrowser.open(url)
        return process.wait()
    except KeyboardInterrupt:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        return 130
    finally:
        if process.poll() is None:
            process.terminate()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the RecCli organization console")
    parser.add_argument("project", nargs="?", default=str(Path.cwd()))
    parser.add_argument("--port", type=int, default=8777)
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--dev", action="store_true")
    parser.add_argument("--token")
    args = parser.parse_args()
    try:
        return serve_console(
            Path(args.project),
            port=args.port,
            open_browser=not args.no_open,
            development=args.dev,
            token=args.token,
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
