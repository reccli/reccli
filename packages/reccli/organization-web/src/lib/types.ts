export interface RunSummary {
  run_id: string;
  run_dir: string;
  status: string;
  round: number;
  max_rounds?: number;
  phase?: string;
  closeout_round?: number;
  max_closeout_rounds?: number;
  detail?: string;
  updated_at?: string;
  created_at?: string;
  topology?: string;
  provider?: string;
  host_provider?: string;
  human_promotion_required?: boolean;
  process_live?: boolean | null;
  control_protocol?: string | null;
}

export interface AgentRecord {
  id: string;
  role: string;
  provider?: string;
  state: string;
  logical_state?: string;
  write_scope: string;
  is_lead: boolean;
  is_finalizer: boolean;
  is_integrator: boolean;
  assignment?: {
    from?: string;
    content?: string;
    workItem?: string;
    risk?: string;
    round?: number;
  } | null;
  last_turn?: {
    round?: number;
    status?: string;
    duration_ms?: number;
    summary?: string;
    usage?: Record<string, number>;
  };
}

export interface ActivityRecord {
  activity_type: "message" | "turn" | "event";
  source?: string;
  round?: number;
  ts?: string;
  deliveredAt?: string;
  type?: string;
  from?: string;
  to?: string;
  tag?: string;
  content?: string;
  status?: string;
  agent_id?: string;
  duration_ms?: number;
  error?: string;
  targets?: string[];
  reply?: {
    summary?: string;
    state?: string;
    messages?: Array<Record<string, unknown>>;
  };
  usage?: Record<string, number>;
  operator_message?: boolean;
  control_id?: string;
}

export interface ControlRecord {
  id: string;
  action: string;
  target?: string;
  content?: string;
  tag?: string;
  requested_at?: string;
  queue_status: string;
  acknowledgement?: {
    status?: string;
    detail?: string;
    acknowledged_at?: string;
    targets?: string[];
  };
}

export interface RunSnapshot {
  run_id: string;
  run_dir: string;
  status: string;
  detail?: string;
  mission?: string;
  round: number;
  max_rounds: number;
  rounds_remaining?: number;
  phase?: string;
  closeout_round?: number;
  max_closeout_rounds?: number;
  closeout_rounds_remaining?: number;
  scheduled_turns?: number;
  completed_turns?: number;
  attempted_turns?: number;
  failed_turns?: number;
  provider?: string;
  host_provider?: string;
  updated_at?: string;
  process?: {
    pid?: number;
    live?: boolean | null;
    active_agents?: string[];
  };
  topology_graph: {
    id?: string;
    name?: string;
    description?: string;
    leader_id?: string;
    finalizer_id?: string;
    manager_ids?: string[];
    worker_ids?: string[];
    agents: AgentRecord[];
    routes?: Array<{
      from: string;
      to: string;
      tags?: string[] | null;
    }>;
  };
  activities: ActivityRecord[];
  messages: ActivityRecord[];
  controls: ControlRecord[];
  control_capabilities: {
    protocol?: string | null;
    message: boolean;
    pause: boolean;
    resume: boolean;
    cancel: boolean;
  };
  usage?: Record<string, number>;
  usage_by_provider?: Record<string, Record<string, number>>;
  delivered_messages?: number;
  dropped_messages?: number;
  human_promotion_required?: boolean;
  promotion_request?: Record<string, unknown> | null;
  artifact_manifest?: Record<string, unknown> | null;
}
