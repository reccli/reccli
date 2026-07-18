"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type {
  ActivityRecord,
  AgentRecord,
  RunConclusion,
  RunSnapshot,
  RunSummary,
} from "@/lib/types";

const POLL_RUNS_MS = 5_000;
const POLL_SNAPSHOT_MS = 2_000;

function compactId(value: string): string {
  const bits = value.split("_");
  return bits.length > 3 ? `${bits[0]}…${bits.at(-1)}` : value;
}

function titleCase(value?: string): string {
  return (value || "unknown")
    .replaceAll("-", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function timeLabel(value?: string): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "";
  return new Intl.DateTimeFormat(undefined, {
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}

function durationLabel(value?: number): string {
  if (!value) return "";
  if (value < 1_000) return `${value}ms`;
  const seconds = Math.round(value / 100) / 10;
  if (seconds < 60) return `${seconds}s`;
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

function providerLabel(provider?: string): string {
  if (provider === "claude") return "Claude";
  if (provider === "codex") return "Codex";
  return titleCase(provider);
}

function roundLabel(
  run?: Pick<
    RunSummary,
    "round" | "max_rounds" | "phase" | "closeout_round" | "max_closeout_rounds"
  > | null,
): string {
  if (!run) return "Round 0";
  if (run.phase === "conclusion") {
    return "Lead conclusion";
  }
  if (
    run.phase === "closeout" ||
    (run.max_rounds !== undefined && run.round > run.max_rounds)
  ) {
    return `Closeout ${run.closeout_round ?? Math.max(0, run.round - (run.max_rounds ?? 0))} of ${run.max_closeout_rounds ?? "?"}`;
  }
  return `Round ${run.round} of ${run.max_rounds ?? "?"}`;
}

function activityText(activity: ActivityRecord): string {
  if (activity.activity_type === "turn") {
    return (
      activity.reply?.summary ||
      activity.error ||
      `${titleCase(activity.status)} turn`
    );
  }
  if (activity.activity_type === "message") {
    return activity.content || `${activity.from} → ${activity.to}`;
  }
  return (
    activity.content ||
    activity.error ||
    titleCase(activity.type || "organization event")
  );
}

function activityBelongsToAgent(
  activity: ActivityRecord,
  agentId: string,
): boolean {
  return (
    activity.agent_id === agentId ||
    activity.from === agentId ||
    activity.to === agentId ||
    Boolean(activity.targets?.includes(agentId))
  );
}

async function apiRequest<T>(
  path: string,
  token: string,
  init?: RequestInit,
): Promise<T> {
  const response = await fetch(path, {
    ...init,
    cache: "no-store",
    headers: {
      "Content-Type": "application/json",
      "x-reccli-console-token": token,
      ...(init?.headers || {}),
    },
  });
  const payload = (await response.json()) as T & {
    error?: string;
    status?: string;
  };
  if (!response.ok) {
    throw new Error(payload.error || `Request failed (${response.status})`);
  }
  return payload;
}

function StatusDot({ status, live }: { status?: string; live?: boolean | null }) {
  const active =
    status === "running" || status === "starting" || status === "working";
  const paused = status === "paused";
  return (
    <span
      className={`status-dot ${active && live !== false ? "is-live" : ""} ${
        paused ? "is-paused" : ""
      }`}
      aria-label={`${status || "unknown"} status`}
    />
  );
}

function RunSidebar({
  runs,
  selectedRun,
  onSelect,
}: {
  runs: RunSummary[];
  selectedRun: string;
  onSelect: (runId: string) => void;
}) {
  return (
    <aside className="run-sidebar">
      <div className="sidebar-brand">
        <div className="brand-mark" aria-hidden="true">
          <span />
          <span />
          <span />
        </div>
        <div>
          <strong>RecCli</strong>
          <span>Org console</span>
        </div>
      </div>
      <div className="run-heading">
        <span>Organizations</span>
        <span className="count-chip">{runs.length}</span>
      </div>
      <div className="run-list">
        {runs.map((run) => (
          <button
            type="button"
            className={`run-item ${
              selectedRun === run.run_id ? "is-selected" : ""
            }`}
            key={run.run_id}
            onClick={() => onSelect(run.run_id)}
          >
            <div className="run-item-topline">
              <span className="run-name">{compactId(run.run_id)}</span>
              <StatusDot status={run.status} live={run.process_live} />
            </div>
            <div className="run-meta">
              <span>{titleCase(run.topology)}</span>
              <span>{roundLabel(run)}</span>
            </div>
            <div className="run-status">{titleCase(run.status)}</div>
          </button>
        ))}
        {!runs.length && (
          <div className="sidebar-empty">No organization runs found.</div>
        )}
      </div>
      <div className="sidebar-foot">
        <span className="local-dot" />
        Localhost only
      </div>
    </aside>
  );
}

function AgentChip({
  agent,
  selected,
  onSelect,
}: {
  agent: AgentRecord;
  selected: boolean;
  onSelect: () => void;
}) {
  const initials = agent.id
    .split("-")
    .map((word) => word[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();
  return (
    <button
      type="button"
      className={`agent-chip ${selected ? "is-selected" : ""} ${
        agent.is_lead ? "is-lead" : ""
      }`}
      onClick={onSelect}
    >
      <div className={`agent-avatar provider-${agent.provider || "unknown"}`}>
        {initials}
        <span className={`agent-state state-${agent.state}`} />
      </div>
      <div className="agent-copy">
        <strong>{agent.id}</strong>
        <span>{agent.role}</span>
      </div>
      <span className={`provider-pill provider-${agent.provider || "unknown"}`}>
        {providerLabel(agent.provider)}
      </span>
    </button>
  );
}

function ActivityCard({ activity }: { activity: ActivityRecord }) {
  const isMessage = activity.activity_type === "message";
  const isTurn = activity.activity_type === "turn";
  const isTelemetry = activity.activity_type === "telemetry";
  return (
    <article
      className={`activity-card activity-${activity.activity_type} ${
        activity.status === "failed" ? "is-error" : ""
      }`}
    >
      <div className="activity-kicker">
        <span>
          {isMessage
            ? `${activity.from || "system"} → ${activity.to || "team"}`
            : isTurn
              ? `Round ${activity.round ?? "?"} turn`
              : isTelemetry
                ? `${titleCase(activity.type || "activity")} · T${activity.turn ?? "?"}`
              : titleCase(activity.type || "event")}
        </span>
        <span>
          {durationLabel(activity.duration_ms) ||
            timeLabel(activity.deliveredAt || activity.ts)}
        </span>
      </div>
      <p>{activityText(activity)}</p>
      <div className="activity-tags">
        {activity.tag && <span>#{activity.tag}</span>}
        {activity.status && <span>{activity.status}</span>}
        {activity.operator_message && <span>human</span>}
        {isTelemetry && activity.provider && (
          <span>{providerLabel(activity.provider)}</span>
        )}
      </div>
    </article>
  );
}

function AgentStream({
  agent,
  activities,
  selected,
  onSelect,
}: {
  agent: AgentRecord;
  activities: ActivityRecord[];
  selected: boolean;
  onSelect: () => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const lastKey = activities
    .slice(-1)
    .map((item) => `${item.activity_type}-${item.round}-${item.ts}-${item.deliveredAt}`)
    .join("");
  useEffect(() => {
    const node = scrollRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [lastKey]);
  return (
    <section className={`agent-stream ${selected ? "is-selected" : ""}`}>
      <button type="button" className="stream-head" onClick={onSelect}>
        <div>
          <strong>{agent.id}</strong>
          <span>{agent.role}</span>
        </div>
        <div className="stream-head-state">
          <StatusDot status={agent.state} live={agent.state === "working"} />
          <span>{titleCase(agent.state)}</span>
        </div>
      </button>
      <div className="stream-body" ref={scrollRef}>
        {agent.assignment && (
          <article className="assignment-card">
            <div className="activity-kicker">
              <span>Assigned work</span>
              <span>R{agent.assignment.round ?? "?"}</span>
            </div>
            <strong>{agent.assignment.workItem}</strong>
            <p>{agent.assignment.content}</p>
          </article>
        )}
        {activities.slice(-12).map((activity, index) => (
          <ActivityCard
            activity={activity}
            key={`${activity.activity_type}-${activity.round}-${activity.ts}-${index}`}
          />
        ))}
        {!activities.length && (
          <div className="stream-empty">
            {agent.state === "awaiting_assignment"
              ? "Waiting for a specific primary-manager assignment."
              : "Waiting for this agent’s first durable turn."}
          </div>
        )}
      </div>
      <div className="stream-foot">
        <span>{providerLabel(agent.provider)}</span>
        <span>
          {agent.last_turn?.round
            ? `Last active R${agent.last_turn.round}`
            : "Not active yet"}
        </span>
      </div>
    </section>
  );
}

function ConclusionPanel({ conclusion }: { conclusion: RunConclusion }) {
  const evidenceCount = conclusion.evidence_and_tests?.length || 0;
  const blockerCount =
    (conclusion.scientific_or_product_blockers?.length || 0) +
    (conclusion.infrastructure_failures?.length || 0);
  return (
    <details className="conclusion-panel" open>
      <summary>
        <div>
          <span className="eyebrow">Lead after-action report</span>
          <strong>What this organization accomplished</strong>
        </div>
        <div className="conclusion-summary-facts">
          <span>{titleCase(conclusion.promotion_readiness)}</span>
          <span>{evidenceCount} evidence items</span>
          <span>{blockerCount} blockers</span>
        </div>
      </summary>
      <div className="conclusion-body">
        <div className="conclusion-outcome">
          <p>{conclusion.summary}</p>
          <div className="conclusion-meta">
            <span>{conclusion.lead_agent_id || "lead"}</span>
            <span>{providerLabel(conclusion.lead_provider)}</span>
            <span>{titleCase(conclusion.generated_by)}</span>
          </div>
        </div>
        <div className="conclusion-columns">
          <div>
            <h3>Accomplished</h3>
            <ul>
              {(conclusion.accomplishments || []).map((item, index) => (
                <li key={`accomplishment-${index}`}>{item}</li>
              ))}
              {!conclusion.accomplishments?.length && (
                <li>No completed deliverable was recorded.</li>
              )}
            </ul>
          </div>
          <div>
            <h3>Still blocking</h3>
            <ul>
              {[
                ...(conclusion.scientific_or_product_blockers || []),
                ...(conclusion.infrastructure_failures || []),
              ].map((item, index) => (
                <li key={`blocker-${index}`}>{item}</li>
              ))}
              {!blockerCount && <li>No terminal blocker was recorded.</li>}
            </ul>
          </div>
          <div>
            <h3>Next action</h3>
            <p>{conclusion.next_action}</p>
          </div>
        </div>
      </div>
    </details>
  );
}

function OperatorChat({
  snapshot,
  selectedAgent,
  token,
  onRefresh,
}: {
  snapshot: RunSnapshot;
  selectedAgent?: AgentRecord;
  token: string;
  onRefresh: () => Promise<void>;
}) {
  const [text, setText] = useState("");
  const [tag, setTag] = useState("plan");
  const [sending, setSending] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const agentId = selectedAgent?.id;
  const conversation = useMemo(
    () =>
      snapshot.messages
        .filter(
          (message) =>
            message.operator_message ||
            message.from === agentId ||
            message.to === agentId,
        )
        .slice(-80),
    [snapshot.messages, agentId],
  );
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [conversation.length, snapshot.controls.length]);

  const sendControl = async (
    action: "message" | "pause" | "resume" | "cancel",
  ) => {
    if (action === "message" && (!text.trim() || !agentId)) return;
    if (
      action === "cancel" &&
      !window.confirm(
        "Cancel this organization and terminate its active native agent processes?",
      )
    ) {
      return;
    }
    setSending(true);
    setNotice(null);
    try {
      const result = await apiRequest<{
        status?: string;
        detail?: string;
        id?: string;
      }>(
        `/api/runs/${encodeURIComponent(snapshot.run_id)}/control`,
        token,
        {
          method: "POST",
          body: JSON.stringify({
            action,
            target: action === "message" ? agentId : undefined,
            content: action === "message" ? text.trim() : undefined,
            tag,
            idempotency_key: crypto.randomUUID(),
          }),
        },
      );
      setNotice(
        result.detail ||
          (result.status === "queued"
            ? "Queued for the next safe round boundary."
            : titleCase(result.status)),
      );
      if (action === "message") setText("");
      await onRefresh();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error));
    } finally {
      setSending(false);
    }
  };

  const controlEnabled = snapshot.control_capabilities.message;
  return (
    <section className="operator-panel">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">Operator channel</span>
          <h2>{agentId ? `Steer ${agentId}` : "Select an agent"}</h2>
        </div>
        {selectedAgent && (
          <span className={`provider-pill provider-${selectedAgent.provider}`}>
            {providerLabel(selectedAgent.provider)}
          </span>
        )}
      </div>
      {!controlEnabled && (
        <div className="protocol-note">
          This run predates live inbox steering. Observation and cancellation
          remain available; new runs support messages and pause/resume.
        </div>
      )}
      <div className="operator-messages">
        {conversation.map((message, index) => {
          const human = Boolean(message.operator_message);
          return (
            <div
              className={`chat-row ${human ? "is-human" : "is-agent"}`}
              key={`${message.control_id || index}-${message.deliveredAt}`}
            >
              <div className="chat-bubble">
                <div className="chat-meta">
                  <span>{human ? "You" : message.from || "orchestrator"}</span>
                  <span>
                    {timeLabel(message.deliveredAt)} · R{message.round ?? 0}
                  </span>
                </div>
                <p>{message.content}</p>
              </div>
            </div>
          );
        })}
        {!conversation.length && (
          <div className="chat-empty">
            Select a person above. Their public organization traffic will
            collect here.
          </div>
        )}
        {snapshot.controls.slice(-5).map((control) => (
          <div className="control-receipt" key={control.id}>
            <span>{titleCase(control.action)}</span>
            <span className={`receipt-${control.queue_status}`}>
              {titleCase(control.queue_status)}
            </span>
            <p>
              {control.acknowledgement?.detail ||
                "Waiting for a safe organization boundary."}
            </p>
          </div>
        ))}
        <div ref={endRef} />
      </div>
      <div className="operator-compose">
        {notice && <div className="compose-notice">{notice}</div>}
        <textarea
          value={text}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void sendControl("message");
            }
          }}
          placeholder={
            controlEnabled
              ? `Give ${agentId || "the selected agent"} context or direction…`
              : "Steering is unavailable for this legacy run."
          }
          disabled={!controlEnabled || !agentId || sending}
          rows={3}
        />
        <div className="compose-actions">
          <select
            value={tag}
            onChange={(event) => setTag(event.target.value)}
            disabled={!controlEnabled}
            aria-label="Message tag"
          >
            <option value="plan">Plan</option>
            <option value="question">Question</option>
            <option value="answer">Answer</option>
            <option value="status">Status</option>
            <option value="blocker">Blocker</option>
            <option value="decision">Decision</option>
          </select>
          <button
            type="button"
            className="send-button"
            disabled={!controlEnabled || !text.trim() || !agentId || sending}
            onClick={() => void sendControl("message")}
          >
            Queue message
            <span aria-hidden="true">↗</span>
          </button>
        </div>
      </div>
      <div className="run-controls">
        {snapshot.control_capabilities.resume ? (
          <button
            type="button"
            disabled={sending}
            onClick={() => void sendControl("resume")}
          >
            Resume
          </button>
        ) : (
          <button
            type="button"
            disabled={!snapshot.control_capabilities.pause || sending}
            onClick={() => void sendControl("pause")}
          >
            Pause after round
          </button>
        )}
        <button
          type="button"
          className="danger-button"
          disabled={!snapshot.control_capabilities.cancel || sending}
          onClick={() => void sendControl("cancel")}
        >
          Cancel run
        </button>
      </div>
    </section>
  );
}

export default function OrganizationConsole() {
  const [token, setToken] = useState("");
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selectedRun, setSelectedRun] = useState("");
  const [snapshot, setSnapshot] = useState<RunSnapshot | null>(null);
  const [selectedAgent, setSelectedAgent] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const queryToken = params.get("token");
    if (queryToken) {
      window.localStorage.setItem("reccli-org-console-token", queryToken);
      params.delete("token");
      const query = params.toString();
      window.history.replaceState(
        {},
        "",
        `${window.location.pathname}${query ? `?${query}` : ""}`,
      );
      setToken(queryToken);
      return;
    }
    setToken(window.localStorage.getItem("reccli-org-console-token") || "");
  }, []);

  const loadRuns = useCallback(async () => {
    if (!token) return;
    try {
      const payload = await apiRequest<{
        status: string;
        runs: RunSummary[];
      }>("/api/runs", token);
      setRuns(payload.runs || []);
      setSelectedRun((current) => current || payload.runs?.[0]?.run_id || "");
      setError(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    }
  }, [token]);

  const loadSnapshot = useCallback(async () => {
    if (!token || !selectedRun) return;
    try {
      const payload = await apiRequest<RunSnapshot>(
        `/api/runs/${encodeURIComponent(selectedRun)}?recent=240`,
        token,
      );
      setSnapshot(payload);
      const agents = payload.topology_graph?.agents || [];
      setSelectedAgent((current) => {
        if (current && agents.some((agent) => agent.id === current)) return current;
        return payload.topology_graph?.leader_id || agents[0]?.id || "";
      });
      setError(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, [token, selectedRun]);

  useEffect(() => {
    void loadRuns();
    const timer = window.setInterval(() => void loadRuns(), POLL_RUNS_MS);
    return () => window.clearInterval(timer);
  }, [loadRuns]);

  useEffect(() => {
    setSnapshot(null);
    setLoading(true);
    void loadSnapshot();
    const timer = window.setInterval(
      () => void loadSnapshot(),
      POLL_SNAPSHOT_MS,
    );
    return () => window.clearInterval(timer);
  }, [loadSnapshot]);

  const agents = snapshot?.topology_graph?.agents || [];
  const lead = agents.find((agent) => agent.is_lead);
  const team = agents.filter((agent) => !agent.is_lead);
  const selected = agents.find((agent) => agent.id === selectedAgent);
  const progress = snapshot?.max_rounds
    ? Math.min(100, ((snapshot.round || 0) / snapshot.max_rounds) * 100)
    : 0;

  if (!token) {
    return (
      <main className="locked-screen">
        <div className="locked-card">
          <div className="brand-mark large" aria-hidden="true">
            <span />
            <span />
            <span />
          </div>
          <h1>Console token required</h1>
          <p>
            Launch this viewer through RecCli so it can open a localhost URL
            with a short-lived access token.
          </p>
          <code>reccli organization console --project-root /path/to/project</code>
        </div>
      </main>
    );
  }

  return (
    <main className="console-shell">
      <RunSidebar
        runs={runs}
        selectedRun={selectedRun}
        onSelect={setSelectedRun}
      />
      <div className="console-main">
        <header className="console-header">
          <div className="header-title">
            <div className="header-status">
              <StatusDot
                status={snapshot?.status}
                live={snapshot?.process?.live}
              />
              <span>{titleCase(snapshot?.status)}</span>
              <span className="header-separator">/</span>
              <span>{snapshot?.topology_graph?.name || "Organization"}</span>
            </div>
            <h1>{snapshot ? compactId(snapshot.run_id) : "Loading run…"}</h1>
          </div>
          <div className="round-meter">
            <div className="round-copy">
              <span>
                {roundLabel(snapshot)}
              </span>
              <span>{snapshot?.completed_turns ?? 0} turns complete</span>
            </div>
            <div className="progress-track">
              <span style={{ width: `${progress}%` }} />
            </div>
          </div>
          <div className="header-facts">
            <div>
              <span>Provider</span>
              <strong>{titleCase(snapshot?.provider)}</strong>
            </div>
            <div>
              <span>Process</span>
              <strong>{snapshot?.process?.live ? "Attached" : "Stopped"}</strong>
            </div>
            <div>
              <span>Promotion</span>
              <strong>
                {snapshot?.human_promotion_required ? "Human gate" : "Automatic"}
              </strong>
            </div>
          </div>
        </header>

        {error && <div className="error-banner">{error}</div>}

        <section className="team-rail">
          {lead && (
            <AgentChip
              agent={lead}
              selected={lead.id === selectedAgent}
              onSelect={() => setSelectedAgent(lead.id)}
            />
          )}
          <div className="team-divider">
            <span>Team</span>
          </div>
          <div className="team-scroller">
            {team.map((agent) => (
              <AgentChip
                agent={agent}
                selected={agent.id === selectedAgent}
                onSelect={() => setSelectedAgent(agent.id)}
                key={agent.id}
              />
            ))}
          </div>
        </section>

        {snapshot?.conclusion && (
          <ConclusionPanel conclusion={snapshot.conclusion} />
        )}

        <div className="workspace-grid">
          {snapshot ? (
            <OperatorChat
              snapshot={snapshot}
              selectedAgent={selected}
              token={token}
              onRefresh={loadSnapshot}
            />
          ) : (
            <section className="operator-panel loading-panel">
              <div className="loading-pulse" />
              <div className="loading-pulse short" />
            </section>
          )}
          <section className="workstream-panel">
            <div className="workstream-heading">
              <div>
                <span className="eyebrow">Live organization trace</span>
                <h2>Team work streams</h2>
              </div>
              <div className="stream-legend">
                <span>
                  <i className="legend-dot claude" /> Claude
                </span>
                <span>
                  <i className="legend-dot codex" /> Codex
                </span>
                <span className="refresh-copy">
                  Refreshing every {POLL_SNAPSHOT_MS / 1_000}s
                </span>
              </div>
            </div>
            <div className="stream-grid">
              {team.map((agent) => (
                <AgentStream
                  key={agent.id}
                  agent={agent}
                  activities={
                    snapshot?.activities.filter((activity) =>
                      activityBelongsToAgent(activity, agent.id),
                    ) || []
                  }
                  selected={agent.id === selectedAgent}
                  onSelect={() => setSelectedAgent(agent.id)}
                />
              ))}
              {loading && !team.length && (
                <>
                  {Array.from({ length: 8 }, (_, index) => (
                    <div className="stream-skeleton" key={index}>
                      <div className="loading-pulse" />
                      <div className="loading-pulse short" />
                    </div>
                  ))}
                </>
              )}
            </div>
          </section>
        </div>
      </div>
    </main>
  );
}
