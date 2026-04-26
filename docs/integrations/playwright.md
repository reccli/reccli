# Playwright integration

For screenshot iteration on web UIs (e.g., the `reccli.com` landing page),
visual regression checks, and multi-step UX testing — let the agent use
Playwright. RecCli does not bundle, wrap, or proxy Playwright; the agent
installs and invokes it on demand via Bash, with the install cost paid
only the first time.

## When to use this

- Iterating on a UI design where the agent should *see* the rendered
  page, not just the JSX/CSS source.
- Before/after comparisons after a styling change.
- Visual regression in CI (run a script, compare PNGs).
- Multi-step user-flow testing (login, navigate, interact, screenshot).

If you don't have a web surface to render, you don't need this.

## Flavor 1 — Bash + Playwright CLI (recommended for most cases)

For single screenshots and iteration loops, the agent calls Playwright's
CLI directly through its existing Bash tool. No MCP server, no config
edits, no session restart.

```bash
# First-time setup (cached after first run)
npx playwright install chromium

# Take a screenshot
npx playwright screenshot \
  --browser=chromium \
  --viewport-size=1440,900 \
  http://localhost:3000 hero.png
```

The agent then uses its native `Read` / image-rendering capability to view
`hero.png`, edits the React/CSS, and re-runs the screenshot command.
Iterate until the visual matches your direction.

This works for ~80% of UI iteration use cases:
- Single-page screenshots at any viewport size.
- Sequential before/after comparisons.
- Visual checks of any framework (Next.js, Astro, plain HTML).

Limits of Flavor 1: each invocation is a fresh browser. No persistent
login, no cookies between calls, no multi-page state. If you need any of
those, use Flavor 2.

## Flavor 2 — `@playwright/mcp` (for persistent context)

[`@playwright/mcp`](https://github.com/microsoft/playwright-mcp) is the
official Playwright team's MCP server. It exposes Playwright actions
(`browser_navigate`, `browser_screenshot`, `browser_click`,
`browser_snapshot` for the accessibility tree, etc.) as MCP tools that
share a single browser context across calls.

Use this when:
- You need to log in once and then screenshot logged-in views.
- The flow spans multiple pages with cookie / session continuity.
- You want the accessibility tree (a11y inspection, automated testing).
- You're building a persistent visual regression suite.

### Setup

```bash
# One-time install of the MCP server (cached after first run)
npx -y @playwright/mcp@latest --help
npx playwright install chromium
```

Add to `~/.claude.json` (or your MCP client's equivalent):

```jsonc
{
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": ["@playwright/mcp@latest"]
    }
  }
}
```

For Codex (`~/.codex/config.toml`):

```toml
[mcp_servers.playwright]
command = "npx"
args = ["@playwright/mcp@latest"]
```

**Restart your CLI session.** MCP servers are connected at session
start; new tools don't hot-reload into a running session. After restart,
the agent sees the Playwright tools alongside RecCli's own.

## Choosing between the two

| Situation | Use |
|---|---|
| One-off screenshot of a page | Flavor 1 |
| "Iterate on this hero design" loop | Flavor 1 |
| Visual regression in a CI script | Flavor 1 |
| Login + screenshot the dashboard | Flavor 2 |
| Multi-page user-flow testing | Flavor 2 |
| Accessibility tree inspection | Flavor 2 |

When in doubt, start with Flavor 1. You can upgrade to Flavor 2 later
without throwing away the workflow — the agent already knows Playwright;
adding the MCP server just gives it a richer set of primitives.

## Why we don't bundle this

RecCli is a temporal memory engine for AI coding agents. Browser
automation is orthogonal to that mission. Bundling Playwright would:

- Force every RecCli user to download Chromium (~300MB) regardless of
  whether they ever take a screenshot.
- Make RecCli responsible for tracking Playwright's release cadence
  (multiple releases per month).
- Dilute the product's positioning ("memory engine" → "agentic dev
  meta-toolbelt").

The agent already has Bash. Playwright already publishes a stable CLI
and an official MCP server. The right answer is the agent invoking those
on demand, not RecCli wrapping them.

## Credit

[Playwright](https://github.com/microsoft/playwright) and
[`@playwright/mcp`](https://github.com/microsoft/playwright-mcp) are
built and maintained by Microsoft, licensed Apache 2.0. Both are used
unmodified — RecCli only documents the integration pattern.
