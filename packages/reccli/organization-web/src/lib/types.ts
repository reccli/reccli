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
  approval_pending?: boolean;
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
  goal?: WorkerGoal | null;
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

export interface WorkerGoal {
  worker_id: string;
  manager_id?: string;
  work_item: string;
  objective: string;
  risk?: string;
  source?: "manager" | "human" | string;
  status: string;
  created_round?: number;
  updated_round?: number;
  completed_round?: number;
  candidate?: string | null;
  goal_sha256?: string;
  progress_contract_sha256?: string | null;
  progress_evaluator_id?: string | null;
  progress_success_rule?: string | null;
}

export interface OffGoalFlag {
  flag_id: string;
  worker_id: string;
  manager_id?: string;
  work_item: string;
  content: string;
  risk?: string;
  status: string;
  created_round?: number;
  consulted_manager_id?: string | null;
  consult_round?: number | null;
  validation?: string | null;
  validation_round?: number | null;
}

export interface ActivityRecord {
  activity_type: "message" | "turn" | "event" | "telemetry";
  source?: string;
  round?: number;
  ts?: string;
  deliveredAt?: string;
  type?: string;
  from?: string;
  to?: string;
  tag?: string;
  content?: string;
  paths?: string[];
  provider?: string;
  turn?: number;
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

export interface RunConclusion {
  schema?: string;
  run_id?: string;
  terminal_status?: string;
  generated_at?: string;
  generated_by?: "lead" | "host-fallback" | string;
  lead_agent_id?: string;
  lead_provider?: string;
  summary: string;
  accomplishments: string[];
  conclusive_findings: string[];
  evidence_and_tests: string[];
  scientific_or_product_blockers: string[];
  infrastructure_failures: string[];
  unresolved: string[];
  promotion_readiness: string;
  next_action: string;
  limitations: string[];
  candidates?: Array<{
    candidate?: string;
    kind?: string;
    agent_id?: string;
    round?: number;
    paths?: string[];
  }>;
  artifacts?: string[];
  turn_counts?: {
    attempted?: number;
    completed?: number;
    failed?: number;
  };
  round_counts?: {
    total?: number;
    working?: number;
    closeout?: number;
  };
}

export interface ResearchCellRecord {
  work_item?: string;
  question?: string;
  risk?: string;
  specialists?: string[];
  assigned_specialist?: string;
  specialist_id?: string;
  sha256?: string;
  candidate?: string;
  persisted_path?: string;
  implementation_readiness?: string;
  authorized_work_items?: string[];
  payload?: {
    disposition?: string;
    decision_to_unlock?: string;
    recommendation?: string;
    unresolved?: string[];
    sources?: Array<{
      title?: string;
      locator?: string;
      supported_claim?: string;
      url_or_doi?: string;
    }>;
  };
}

export interface ExperimentLoopContract {
  sha256?: string;
  work_item?: string;
  manager_id?: string;
  worker_id?: string;
  mutable_file?: string;
  evaluator_id?: string;
  max_trials?: number;
  max_consecutive_non_improving?: number;
  max_wall_seconds?: number;
  status?: string;
  halt_reason?: string;
  action?: string;
  activation_baseline_candidate?: string;
  goal_sha256?: string | null;
  goal_success_rule?: string;
}

export interface ExperimentLoopTrial {
  contract_sha256?: string;
  work_item?: string;
  manager_id?: string;
  worker_id?: string;
  round?: number;
  trial_number?: number;
  verdict?: "baseline" | "keep" | "discard" | "inconclusive" | "crash" | string;
  challenger_candidate?: string;
  resulting_head?: string;
  budget_slot?: number | null;
  previous_record_sha256?: string | null;
  record_sha256?: string;
  ts?: string;
  intent?: {
    hypothesis?: string;
    single_change?: string;
    expected_result?: string;
  } | null;
  outcome?: {
    evaluator_id?: string;
    commands_pass?: boolean;
    timed_out?: boolean;
    hard_gates?: Record<string, boolean>;
    metrics?: Record<string, number>;
    notes?: string[];
    result_error?: string | null;
    duration_ms?: number;
    resource_envelope?: {
      sha256?: string;
      scope?: string;
      same_host_required?: boolean;
      fingerprint?: Record<string, unknown>;
    };
    patch_shape?: {
      changed_lines?: number;
      diff_hunks?: number;
      passes?: boolean;
      scope?: string;
      violations?: string[];
    };
  };
}

export interface ApprovalRequest {
  schema?: string;
  version?: number;
  run_id?: string;
  request_kind?: "checkpoint_continuation" | "candidate_promotion" | string;
  title?: string;
  question?: string;
  status?: string;
  created_at?: string;
  request_sha256: string;
  base_commit?: string;
  report_candidate?: string;
  report_kind?: string;
  report_paths?: string[];
  report_files?: Array<{
    path: string;
    git_blob?: string;
    content?: string;
    truncated?: boolean;
  }>;
  proposed_promotion_candidate?: string;
  proposed_promotion_branch?: string;
  changed_paths?: string[];
  action?: {
    type?: "start_successor" | "fast_forward_local" | string;
    effect?: string;
    remote_push?: boolean;
  };
  conclusion?: Partial<RunConclusion>;
  authorization_limits?: string[];
  authorization_required_for?: string[];
}

export interface ApprovalDecision {
  decision?: string;
  decided_by?: string;
  decided_at?: string;
  decision_sha256?: string;
  request_sha256?: string;
}

export interface ApprovalExecution {
  status?: "processing" | "applied" | "failed" | string;
  action?: string;
  error?: string;
  successor_run_id?: string;
  successor_run_dir?: string;
  applied_commit?: string;
  completed_at?: string;
  remote_push?: boolean;
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
    research_director_id?: string | null;
    research_specialist_ids?: string[];
    agents: AgentRecord[];
    routes?: Array<{
      from: string;
      to: string;
      tags?: string[] | null;
    }>;
  };
  worker_goals?: Record<string, WorkerGoal>;
  off_goal_flags?: OffGoalFlag[];
  activities: ActivityRecord[];
  messages: ActivityRecord[];
  telemetry?: ActivityRecord[];
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
  approval_request?: ApprovalRequest | null;
  approval_decision?: ApprovalDecision | null;
  approval_execution?: ApprovalExecution | null;
  approval_capabilities?: {
    approve: boolean;
    reject?: boolean;
    action?: string | null;
  };
  operator_decision?: {
    decision?: "rejected" | string;
    candidate?: string;
    reason?: string;
    decision_sha256?: string;
  } | null;
  artifact_manifest?: Record<string, unknown> | null;
  conclusion?: RunConclusion | null;
  research_cell?: {
    director_id?: string | null;
    specialist_ids?: string[];
    commissions?: ResearchCellRecord[];
    fragments?: ResearchCellRecord[];
    decisions?: ResearchCellRecord[];
  };
  experiment_loop?: {
    enabled?: boolean;
    policy?: string | null;
    contracts?: ExperimentLoopContract[];
    trials?: ExperimentLoopTrial[];
    ledger?: {
      verified?: boolean;
      records?: number;
      head_sha256?: string | null;
      error?: string | null;
    };
    active_workers?: string[];
    halted_workers?: string[];
    candidate_progress?: {
      candidate?: string;
      required?: boolean;
      qualifies?: boolean;
      decision?: "retain" | "discard" | string;
      reason?: string;
      verdict_sha256?: string;
    } | null;
  };
}
