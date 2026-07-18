"""Local web UI for browsing the .devsession corpus.

Read-only HTML browser served over localhost. Zero new dependencies — uses
stdlib http.server + Tailwind via CDN.

Three views:
  /                    Sessions list (chronological, newest first)
  /session/<name>      Session detail (raw conversation + summary panel)
  /search?q=...        Substring search across conversation + summaries

Run via:  reccli browse [path] [--port 8765]
"""

import json
import urllib.parse
import webbrowser
from datetime import datetime
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..project.devproject import default_devsession_dir


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def _list_sessions(devsession_dir: Path) -> List[Dict[str, Any]]:
    items = []
    for p in devsession_dir.glob("*.devsession"):
        try:
            stat = p.stat()
            with open(p) as f:
                d = json.load(f)
        except Exception:
            continue
        conv = d.get("conversation", [])
        summary = d.get("summary") or {}
        items.append({
            "name": p.name,
            "size_kb": stat.st_size // 1024,
            "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "msg_count": len(conv),
            "overview": (summary.get("overview") or "")[:300],
            "feature_id": d.get("feature_id"),
            "decision_count": len(summary.get("decisions") or []),
            "open_count": len(summary.get("open_issues") or []),
        })
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items


def _load_session(devsession_dir: Path, name: str) -> Optional[Dict[str, Any]]:
    p = devsession_dir / name
    if not p.exists() or not p.is_file() or not name.endswith(".devsession"):
        return None
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return None


def _search_sessions(devsession_dir: Path, query: str, max_hits: int = 80) -> List[Dict[str, Any]]:
    if not query.strip():
        return []
    q = query.lower()
    hits: List[Dict[str, Any]] = []
    for p in sorted(devsession_dir.glob("*.devsession"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            with open(p) as f:
                d = json.load(f)
        except Exception:
            continue
        conv = d.get("conversation", [])
        summary = d.get("summary") or {}

        # Search raw messages
        for i, m in enumerate(conv):
            content = (m.get("content") or "")
            if q in content.lower():
                idx = content.lower().find(q)
                start = max(0, idx - 80)
                end = min(len(content), idx + len(q) + 80)
                hits.append({
                    "session": p.name,
                    "msg_index": i,
                    "role": m.get("role", "?"),
                    "kind": "message",
                    "snippet": content[start:end].replace("\n", " "),
                })
                if len(hits) >= max_hits:
                    return hits

        # Search summary fields (lighter — just dump and check)
        if summary:
            for cat in ("decisions", "code_changes", "problems_solved", "open_issues", "next_steps"):
                for it in (summary.get(cat) or []):
                    text = json.dumps(it, ensure_ascii=False).lower()
                    if q in text:
                        title = it.get("title") or it.get("decision") or it.get("description") \
                                or it.get("issue") or it.get("step") or ""
                        hits.append({
                            "session": p.name,
                            "msg_index": -1,
                            "role": "summary",
                            "kind": cat,
                            "snippet": title[:200],
                        })
                        if len(hits) >= max_hits:
                            return hits
    return hits


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

_BASE_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>{title}</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    body {{ font-family: ui-sans-serif, system-ui, sans-serif; }}
    pre {{ white-space: pre-wrap; word-wrap: break-word; font-family: ui-monospace, monospace; }}
    .role-user {{ background: #eff6ff; border-color: #93c5fd; }}
    .role-assistant {{ background: #f9fafb; border-color: #d1d5db; }}
    .role-tool {{ background: #fef3c7; border-color: #fcd34d; }}
  </style>
</head>
<body class="bg-slate-50 text-slate-900">
  <div class="max-w-7xl mx-auto p-6">
    <header class="mb-6 flex items-center justify-between gap-4 flex-wrap">
      <h1 class="text-xl font-semibold">
        <a href="/" class="hover:text-blue-600">RecCli Corpus Browser</a>
      </h1>
      <form action="/search" method="get" class="flex gap-2">
        <input name="q" value="{query}" placeholder="substring search across sessions"
               class="px-3 py-1.5 border border-slate-300 rounded w-96 text-sm" />
        <button class="px-4 py-1.5 bg-blue-600 text-white rounded text-sm hover:bg-blue-700">Search</button>
      </form>
    </header>
    <div class="text-xs text-slate-500 mb-4 font-mono">{project_root}</div>
    {body}
  </div>
</body>
</html>
"""


def _shell(title: str, body: str, project_root: Path, query: str = "") -> str:
    return _BASE_HTML.format(
        title=escape(title), body=body, query=escape(query), project_root=escape(str(project_root))
    )


def _render_index(items: List[Dict[str, Any]], project_root: Path) -> str:
    if not items:
        body = "<div class='text-slate-600'>No .devsession files in this project.</div>"
        return _shell("Sessions", body, project_root)

    rows = []
    for item in items:
        meta_bits = [
            f"{item['msg_count']} msgs",
            f"{item['size_kb']} KB",
            escape(item['mtime'][:16].replace('T', ' ')),
        ]
        if item['decision_count']:
            meta_bits.append(f"{item['decision_count']} decisions")
        if item['open_count']:
            meta_bits.append(f"{item['open_count']} open")
        meta = " · ".join(meta_bits)
        rows.append(f"""
        <a href="/session/{escape(item['name'])}" class="block bg-white border border-slate-200 rounded p-4 hover:border-blue-400 hover:shadow-sm transition">
          <div class="flex items-baseline justify-between mb-2 gap-4">
            <div class="font-mono text-sm font-medium">{escape(item['name'])}</div>
            <div class="text-xs text-slate-500 whitespace-nowrap">{meta}</div>
          </div>
          <div class="text-sm text-slate-700 line-clamp-2">{escape(item['overview'] or '(no summary generated)')}</div>
        </a>
        """)
    body = f"""
    <div class="mb-3 text-sm text-slate-600">{len(items)} session(s) — newest first</div>
    <div class="space-y-2">{''.join(rows)}</div>
    """
    return _shell("Sessions", body, project_root)


def _render_summary_section(category: str, items: List[Dict[str, Any]]) -> str:
    if not items:
        return ""
    rows = []
    for it in items:
        title = it.get("title") or it.get("decision") or it.get("description") \
                or it.get("issue") or it.get("step") or ""
        detail = it.get("detail") or it.get("rationale") or it.get("solution") or ""
        files_field = it.get("files") or []
        file_html = ""
        if files_field:
            if isinstance(files_field[0], str):
                file_list = files_field
            else:
                file_list = [f.get("path", "") if isinstance(f, dict) else str(f) for f in files_field]
            file_html = (
                f"<div class='text-xs text-slate-500 mt-1 font-mono'>"
                f"{escape(', '.join(file_list[:6]))}"
                f"{f' (+{len(file_list)-6} more)' if len(file_list) > 6 else ''}"
                f"</div>"
            )
        rows.append(f"""
        <div class="border-l-2 border-slate-300 pl-3 py-1 mb-2">
          <div class="text-sm font-medium">{escape(title)}</div>
          {f'<div class="text-xs text-slate-600 mt-1">{escape(detail)}</div>' if detail else ''}
          {file_html}
        </div>
        """)
    return f"""
    <div class="mb-4">
      <h3 class="text-xs uppercase tracking-wide text-slate-500 font-semibold mb-2">{escape(category)} ({len(items)})</h3>
      {''.join(rows)}
    </div>
    """


def _render_session(d: Dict[str, Any], name: str, project_root: Path) -> str:
    conv = d.get("conversation", [])
    summary = d.get("summary") or {}
    feature_id = d.get("feature_id") or ""
    metadata = d.get("metadata") or {}

    # Conversation messages
    msg_html = []
    for i, m in enumerate(conv):
        role = m.get("role", "?")
        content = m.get("content") or ""
        ts = m.get("timestamp", "")
        # Truncate huge messages
        truncated_note = ""
        if len(content) > 8000:
            content = content[:8000]
            truncated_note = f"<div class='text-xs text-amber-700 mt-1'>(truncated to 8000 chars)</div>"
        role_class = role if role in ("user", "assistant", "tool") else "tool"
        msg_html.append(f"""
        <div class="role-{escape(role_class)} border rounded p-3 mb-2">
          <div class="flex items-baseline justify-between mb-1 gap-4">
            <div class="font-medium text-xs">[{i}] {escape(role)}</div>
            <div class="text-xs text-slate-500 font-mono">{escape(ts)}</div>
          </div>
          <pre class="text-sm">{escape(content)}</pre>
          {truncated_note}
        </div>
        """)

    # Summary panel
    if summary:
        overview = summary.get("overview", "")
        summary_panel = f"""
        <div class="bg-white border border-slate-200 rounded p-4 mb-4">
          <div class="text-xs uppercase tracking-wide text-slate-500 font-semibold mb-2">Overview</div>
          <div class="text-sm text-slate-700">{escape(overview)}</div>
        </div>
        {_render_summary_section('Decisions', summary.get('decisions', []))}
        {_render_summary_section('Code changes', summary.get('code_changes', []))}
        {_render_summary_section('Problems solved', summary.get('problems_solved', []))}
        {_render_summary_section('Open issues', summary.get('open_issues', []))}
        {_render_summary_section('Next steps', summary.get('next_steps', []))}
        """
    else:
        summary_panel = "<div class='text-sm text-slate-500 italic'>(no summary generated)</div>"

    feature_html = ""
    if feature_id:
        feature_html = f"<div class='text-xs text-slate-500'>feature: <code>{escape(feature_id)}</code></div>"

    metadata_html = ""
    if metadata:
        items_list = ", ".join(f"<code>{escape(k)}={escape(str(v))[:40]}</code>" for k, v in metadata.items())
        metadata_html = f"<div class='text-xs text-slate-500 mt-1'>metadata: {items_list}</div>"

    body = f"""
    <div class="mb-4">
      <a href="/" class="text-sm text-blue-600 hover:underline">&larr; All sessions</a>
      <h2 class="font-mono text-lg font-medium mt-2">{escape(name)}</h2>
      {feature_html}
      {metadata_html}
    </div>
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
      <div class="lg:col-span-2 min-w-0">
        <h3 class="text-xs uppercase tracking-wide text-slate-500 font-semibold mb-2">
          Conversation ({len(conv)} msgs)
        </h3>
        {''.join(msg_html)}
      </div>
      <div class="lg:col-span-1 min-w-0">
        <h3 class="text-xs uppercase tracking-wide text-slate-500 font-semibold mb-2">Summary</h3>
        {summary_panel}
      </div>
    </div>
    """
    return _shell(name, body, project_root)


def _render_search(hits: List[Dict[str, Any]], query: str, project_root: Path) -> str:
    if not query:
        body = "<div class='text-slate-600'>Enter a search query.</div>"
        return _shell("Search", body, project_root, query=query)
    if not hits:
        body = f"<div class='text-slate-600'>No results for <code>{escape(query)}</code></div>"
        return _shell(f"Search: {query}", body, project_root, query=query)

    rows = []
    for h in hits:
        link = f"/session/{escape(h['session'])}"
        kind_label = h['kind']
        msg_label = f"[{h['msg_index']}] {escape(h['role'])}" if h['msg_index'] >= 0 else escape(h['kind'])
        rows.append(f"""
        <div class="bg-white border border-slate-200 rounded p-3 mb-2">
          <div class="flex items-baseline justify-between mb-2 gap-4">
            <a href="{link}" class="font-mono text-sm text-blue-600 hover:underline">{escape(h['session'])}</a>
            <div class="text-xs text-slate-500">{msg_label}</div>
          </div>
          <pre class="text-xs text-slate-700">…{escape(h['snippet'])}…</pre>
        </div>
        """)
    body = f"""
    <div class="mb-3 text-sm text-slate-600">
      {len(hits)} hit(s) for <code>{escape(query)}</code>
    </div>
    {''.join(rows)}
    """
    return _shell(f"Search: {query}", body, project_root, query=query)


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    devsession_dir: Path = None  # type: ignore
    project_root: Path = None  # type: ignore

    def log_message(self, format, *args):
        return  # quiet

    def _serve(self, html: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        body = html.encode("utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)
        try:
            if path == "/":
                items = _list_sessions(self.devsession_dir)
                self._serve(_render_index(items, self.project_root))
            elif path.startswith("/session/"):
                name = urllib.parse.unquote(path[len("/session/"):])
                # Path-traversal guard
                if "/" in name or ".." in name or "\\" in name:
                    self._serve("<h1>400</h1><p>Bad name.</p>", status=400)
                    return
                d = _load_session(self.devsession_dir, name)
                if d is None:
                    self._serve(f"<h1>404</h1><p>Session not found: {escape(name)}</p>", status=404)
                else:
                    self._serve(_render_session(d, name, self.project_root))
            elif path == "/search":
                q = (qs.get("q") or [""])[0]
                hits = _search_sessions(self.devsession_dir, q)
                self._serve(_render_search(hits, q, self.project_root))
            else:
                self._serve(f"<h1>404</h1><p>Not found: {escape(path)}</p>", status=404)
        except Exception as e:
            self._serve(f"<h1>500</h1><pre>{escape(repr(e))}</pre>", status=500)


def serve(project_root: Path, port: int = 8765, open_browser: bool = True) -> None:
    """Start the corpus browser on localhost:<port>."""
    devsession_dir = default_devsession_dir(project_root) if project_root else Path.cwd()
    if not devsession_dir.exists():
        raise RuntimeError(
            f"No devsession directory at {devsession_dir}. Activate a project first."
        )

    _Handler.devsession_dir = devsession_dir
    _Handler.project_root = project_root or devsession_dir.parent

    httpd = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    url = f"http://127.0.0.1:{port}/"
    n_sessions = len(list(devsession_dir.glob("*.devsession")))
    print(f"RecCli corpus browser serving on {url}")
    print(f"  project:  {_Handler.project_root}")
    print(f"  corpus:   {devsession_dir}")
    print(f"  sessions: {n_sessions}")
    print("Ctrl+C to stop.")

    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        httpd.shutdown()
