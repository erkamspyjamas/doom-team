export const SCHEMA = `
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;


CREATE TABLE IF NOT EXISTS system_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);


CREATE TABLE IF NOT EXISTS system_settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entity_translations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  entity_type TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  locale TEXT NOT NULL,
  field TEXT NOT NULL,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(entity_type, entity_id, locale, field)
);

CREATE INDEX IF NOT EXISTS idx_entity_translations_lookup
ON entity_translations(entity_type, entity_id, locale, field);

CREATE TABLE IF NOT EXISTS model_providers (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  protocol TEXT NOT NULL,
  base_url TEXT NOT NULL,
  auth_type TEXT NOT NULL DEFAULT 'none',
  secret_ref TEXT,
  location TEXT NOT NULL DEFAULT 'local',
  enabled INTEGER NOT NULL DEFAULT 1,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS models (
  id TEXT PRIMARY KEY,
  provider_id TEXT NOT NULL REFERENCES model_providers(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  provider_model_id TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  context_window_configured INTEGER,
  max_context_reported INTEGER,
  capabilities_json TEXT NOT NULL DEFAULT '{}',
  labels_json TEXT NOT NULL DEFAULT '[]',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);


CREATE UNIQUE INDEX IF NOT EXISTS idx_models_provider_model
ON models(provider_id, provider_model_id);

CREATE TABLE IF NOT EXISTS provider_checks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  provider_id TEXT NOT NULL REFERENCES model_providers(id) ON DELETE CASCADE,
  check_type TEXT NOT NULL,
  status TEXT NOT NULL,
  latency_ms INTEGER,
  detail_json TEXT NOT NULL DEFAULT '{}',
  checked_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_provider_checks_provider
ON provider_checks(provider_id, checked_at DESC);

CREATE TABLE IF NOT EXISTS model_checks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  model_id TEXT NOT NULL REFERENCES models(id) ON DELETE CASCADE,
  check_type TEXT NOT NULL,
  status TEXT NOT NULL,
  latency_ms INTEGER,
  detail_json TEXT NOT NULL DEFAULT '{}',
  checked_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_model_checks_model
ON model_checks(model_id, checked_at DESC);

CREATE TABLE IF NOT EXISTS runtimes (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  adapter_type TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  config_json TEXT NOT NULL DEFAULT '{}',
  capabilities_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_runtime_compatibility (
  id TEXT PRIMARY KEY,
  model_id TEXT NOT NULL REFERENCES models(id) ON DELETE CASCADE,
  runtime_id TEXT NOT NULL REFERENCES runtimes(id) ON DELETE CASCADE,
  chat_status TEXT NOT NULL DEFAULT 'unknown',
  tools_status TEXT NOT NULL DEFAULT 'unknown',
  coding_status TEXT NOT NULL DEFAULT 'unknown',
  structured_output_status TEXT NOT NULL DEFAULT 'unknown',
  notes TEXT,
  last_tested_at TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  UNIQUE(model_id, runtime_id)
);


CREATE TABLE IF NOT EXISTS model_policies (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT,
  current_version_id TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS model_policy_versions (
  id TEXT PRIMARY KEY,
  policy_id TEXT NOT NULL REFERENCES model_policies(id) ON DELETE CASCADE,
  version_no INTEGER NOT NULL,
  definition_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL DEFAULT 'boss',
  UNIQUE(policy_id, version_no)
);

CREATE INDEX IF NOT EXISTS idx_model_policy_versions_policy
ON model_policy_versions(policy_id, version_no DESC);

CREATE TABLE IF NOT EXISTS agent_templates (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  category TEXT NOT NULL,
  description TEXT,
  definition_json TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  slug TEXT NOT NULL UNIQUE,
  description TEXT,
  current_version_id TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  lifetime TEXT NOT NULL DEFAULT 'permanent',
  tags_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_versions (
  id TEXT PRIMARY KEY,
  agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  version_no INTEGER NOT NULL,
  system_prompt TEXT NOT NULL,
  instructions TEXT NOT NULL DEFAULT '',
  runtime_policy_json TEXT NOT NULL DEFAULT '{}',
  model_policy_json TEXT NOT NULL DEFAULT '{}',
  required_capabilities_json TEXT NOT NULL DEFAULT '[]',
  tools_json TEXT NOT NULL DEFAULT '[]',
  skills_json TEXT NOT NULL DEFAULT '[]',
  memory_policy_json TEXT NOT NULL DEFAULT '{}',
  permission_policy_json TEXT NOT NULL DEFAULT '{}',
  autonomy_level INTEGER NOT NULL DEFAULT 2,
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL DEFAULT 'boss',
  UNIQUE(agent_id, version_no)
);


CREATE TABLE IF NOT EXISTS skills (
  id TEXT PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  description TEXT,
  current_version_id TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  source_type TEXT NOT NULL DEFAULT 'manual',
  source_uri TEXT,
  tags_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skill_versions (
  id TEXT PRIMARY KEY,
  skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
  version_no INTEGER NOT NULL,
  package_version TEXT NOT NULL DEFAULT '1.0.0',
  instructions_md TEXT NOT NULL,
  manifest_json TEXT NOT NULL DEFAULT '{}',
  files_json TEXT NOT NULL DEFAULT '{}',
  content_hash TEXT NOT NULL,
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL DEFAULT 'boss',
  UNIQUE(skill_id, version_no),
  UNIQUE(skill_id, content_hash)
);

CREATE TABLE IF NOT EXISTS agent_version_skills (
  agent_version_id TEXT NOT NULL REFERENCES agent_versions(id) ON DELETE CASCADE,
  skill_version_id TEXT NOT NULL REFERENCES skill_versions(id) ON DELETE RESTRICT,
  priority INTEGER NOT NULL DEFAULT 100,
  enabled INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY(agent_version_id, skill_version_id)
);

CREATE INDEX IF NOT EXISTS idx_agent_version_skills_agent
ON agent_version_skills(agent_version_id, priority);

CREATE TABLE IF NOT EXISTS teams (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT,
  lead_agent_id TEXT REFERENCES agents(id) ON DELETE SET NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS team_members (
  team_id TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
  agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  role_label TEXT,
  priority INTEGER NOT NULL DEFAULT 100,
  can_delegate INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(team_id, agent_id)
);

CREATE TABLE IF NOT EXISTS executions (
  id TEXT PRIMARY KEY,
  agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
  agent_version_id TEXT NOT NULL REFERENCES agent_versions(id) ON DELETE RESTRICT,
  runtime_id TEXT NOT NULL REFERENCES runtimes(id) ON DELETE RESTRICT,
  model_id TEXT NOT NULL REFERENCES models(id) ON DELETE RESTRICT,
  external_session_id TEXT,
  task_text TEXT NOT NULL,
  status TEXT NOT NULL,
  result_text TEXT,
  error_text TEXT,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  snapshot_json TEXT NOT NULL DEFAULT '{}'
);


CREATE TABLE IF NOT EXISTS missions (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  objective TEXT NOT NULL,
  team_id TEXT NOT NULL REFERENCES teams(id) ON DELETE RESTRICT,
  lead_agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
  status TEXT NOT NULL DEFAULT 'new',
  planning_execution_id TEXT REFERENCES executions(id) ON DELETE SET NULL,
  plan_json TEXT NOT NULL DEFAULT '{}',
  error_text TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  started_at TEXT,
  ended_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_missions_status
ON missions(status, updated_at DESC);

CREATE TABLE IF NOT EXISTS mission_tasks (
  id TEXT PRIMARY KEY,
  mission_id TEXT NOT NULL REFERENCES missions(id) ON DELETE CASCADE,
  task_key TEXT NOT NULL,
  sequence_no INTEGER NOT NULL DEFAULT 100,
  title TEXT NOT NULL,
  instruction TEXT NOT NULL DEFAULT '',
  expected_outcome TEXT NOT NULL DEFAULT '',
  work_mode TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL,
  acceptance_criteria_json TEXT NOT NULL DEFAULT '[]',
  assigned_agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
  status TEXT NOT NULL DEFAULT 'pending',
  priority INTEGER NOT NULL DEFAULT 100,
  execution_id TEXT REFERENCES executions(id) ON DELETE SET NULL,
  attempt_no INTEGER NOT NULL DEFAULT 0,
  result_text TEXT,
  error_text TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  started_at TEXT,
  ended_at TEXT,
  UNIQUE(mission_id, task_key)
);

CREATE INDEX IF NOT EXISTS idx_mission_tasks_mission_status
ON mission_tasks(mission_id, status, priority, sequence_no);

CREATE TABLE IF NOT EXISTS mission_task_dependencies (
  task_id TEXT NOT NULL REFERENCES mission_tasks(id) ON DELETE CASCADE,
  depends_on_task_id TEXT NOT NULL REFERENCES mission_tasks(id) ON DELETE CASCADE,
  PRIMARY KEY(task_id, depends_on_task_id),
  CHECK(task_id <> depends_on_task_id)
);

CREATE TABLE IF NOT EXISTS mission_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mission_id TEXT NOT NULL REFERENCES missions(id) ON DELETE CASCADE,
  task_id TEXT REFERENCES mission_tasks(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_mission_events_mission
ON mission_events(mission_id, id);



CREATE TABLE IF NOT EXISTS agent_factory_requests (
  id TEXT PRIMARY KEY,
  mission_id TEXT NOT NULL REFERENCES missions(id) ON DELETE CASCADE,
  task_id TEXT REFERENCES mission_tasks(id) ON DELETE SET NULL,
  source_execution_id TEXT REFERENCES executions(id) ON DELETE SET NULL,
  requested_by_type TEXT NOT NULL,
  requested_by_agent_id TEXT REFERENCES agents(id) ON DELETE SET NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  spec_json TEXT NOT NULL DEFAULT '{}',
  result_agent_id TEXT REFERENCES agents(id) ON DELETE SET NULL,
  error_text TEXT,
  created_at TEXT NOT NULL,
  resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_agent_factory_requests_mission
ON agent_factory_requests(mission_id, created_at DESC);

CREATE TABLE IF NOT EXISTS mission_specialists (
  id TEXT PRIMARY KEY,
  mission_id TEXT NOT NULL REFERENCES missions(id) ON DELETE CASCADE,
  agent_id TEXT NOT NULL REFERENCES agents(id) ON DELETE RESTRICT,
  request_id TEXT REFERENCES agent_factory_requests(id) ON DELETE SET NULL,
  scope_type TEXT NOT NULL DEFAULT 'mission',
  scope_task_id TEXT REFERENCES mission_tasks(id) ON DELETE SET NULL,
  role_name TEXT NOT NULL,
  objective TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  source TEXT NOT NULL DEFAULT 'agent_factory',
  created_at TEXT NOT NULL,
  archived_at TEXT,
  archive_reason TEXT,
  UNIQUE(mission_id, agent_id)
);

CREATE INDEX IF NOT EXISTS idx_mission_specialists_active
ON mission_specialists(mission_id, status, created_at);

CREATE TABLE IF NOT EXISTS mission_workspaces (
  mission_id TEXT PRIMARY KEY REFERENCES missions(id) ON DELETE CASCADE,
  mode TEXT NOT NULL DEFAULT 'isolated',
  base_workspace TEXT NOT NULL,
  base_commit TEXT,
  integration_workspace TEXT,
  integration_branch TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  error_text TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mission_task_workspaces (
  id TEXT PRIMARY KEY,
  mission_id TEXT NOT NULL REFERENCES missions(id) ON DELETE CASCADE,
  task_id TEXT NOT NULL REFERENCES mission_tasks(id) ON DELETE CASCADE,
  attempt_no INTEGER NOT NULL,
  workspace_path TEXT NOT NULL,
  branch_name TEXT NOT NULL,
  base_commit TEXT,
  commit_hash TEXT,
  merge_commit TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  diff_stat TEXT,
  error_text TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(task_id, attempt_no)
);

CREATE INDEX IF NOT EXISTS idx_mission_task_workspaces_task
ON mission_task_workspaces(task_id, attempt_no DESC);

CREATE TABLE IF NOT EXISTS mission_task_evidence (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  mission_id TEXT NOT NULL REFERENCES missions(id) ON DELETE CASCADE,
  task_id TEXT NOT NULL REFERENCES mission_tasks(id) ON DELETE CASCADE,
  execution_id TEXT REFERENCES executions(id) ON DELETE SET NULL,
  evidence_type TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'info',
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_mission_task_evidence_task
ON mission_task_evidence(task_id, id);

CREATE TABLE IF NOT EXISTS mission_gates (
  id TEXT PRIMARY KEY,
  mission_id TEXT NOT NULL REFERENCES missions(id) ON DELETE CASCADE,
  task_id TEXT REFERENCES mission_tasks(id) ON DELETE CASCADE,
  gate_type TEXT NOT NULL,
  agent_id TEXT REFERENCES agents(id) ON DELETE SET NULL,
  execution_id TEXT REFERENCES executions(id) ON DELETE SET NULL,
  status TEXT NOT NULL DEFAULT 'running',
  summary TEXT,
  detail_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_mission_gates_mission
ON mission_gates(mission_id, gate_type, created_at DESC);



CREATE TABLE IF NOT EXISTS communication_messages (
  id TEXT PRIMARY KEY,
  mission_id TEXT REFERENCES missions(id) ON DELETE CASCADE,
  task_id TEXT REFERENCES mission_tasks(id) ON DELETE CASCADE,
  execution_id TEXT REFERENCES executions(id) ON DELETE SET NULL,
  sender_type TEXT NOT NULL,
  sender_agent_id TEXT REFERENCES agents(id) ON DELETE SET NULL,
  recipient_type TEXT NOT NULL,
  recipient_id TEXT NOT NULL,
  message_type TEXT NOT NULL DEFAULT 'note',
  priority TEXT NOT NULL DEFAULT 'normal',
  content TEXT NOT NULL,
  structured_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL,
  delivered_at TEXT,
  read_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_communication_messages_mission
ON communication_messages(mission_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_communication_messages_recipient
ON communication_messages(recipient_type, recipient_id, status, created_at);

CREATE TABLE IF NOT EXISTS execution_directives (
  id TEXT PRIMARY KEY,
  execution_id TEXT NOT NULL REFERENCES executions(id) ON DELETE CASCADE,
  mission_id TEXT REFERENCES missions(id) ON DELETE CASCADE,
  task_id TEXT REFERENCES mission_tasks(id) ON DELETE CASCADE,
  message_id TEXT REFERENCES communication_messages(id) ON DELETE SET NULL,
  sender_type TEXT NOT NULL,
  sender_agent_id TEXT REFERENCES agents(id) ON DELETE SET NULL,
  content TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL,
  delivered_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_execution_directives_pending
ON execution_directives(execution_id, status, created_at);

CREATE TABLE IF NOT EXISTS task_artifacts (
  id TEXT PRIMARY KEY,
  mission_id TEXT NOT NULL REFERENCES missions(id) ON DELETE CASCADE,
  task_id TEXT NOT NULL REFERENCES mission_tasks(id) ON DELETE CASCADE,
  execution_id TEXT REFERENCES executions(id) ON DELETE SET NULL,
  artifact_type TEXT NOT NULL,
  name TEXT NOT NULL,
  path TEXT,
  content_preview TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_task_artifacts_task
ON task_artifacts(task_id, created_at);

CREATE INDEX IF NOT EXISTS idx_task_artifacts_mission
ON task_artifacts(mission_id, created_at);

CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY,
  approval_type TEXT NOT NULL,
  scope_type TEXT NOT NULL,
  scope_id TEXT NOT NULL,
  mission_id TEXT REFERENCES missions(id) ON DELETE CASCADE,
  task_id TEXT REFERENCES mission_tasks(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'pending',
  title TEXT NOT NULL,
  summary TEXT,
  risk_level TEXT NOT NULL DEFAULT 'medium',
  requested_by TEXT NOT NULL DEFAULT 'doom-core',
  requested_at TEXT NOT NULL,
  decided_by TEXT,
  decided_at TEXT,
  decision_note TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  result_json TEXT NOT NULL DEFAULT '{}',
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_approvals_status
ON approvals(status, requested_at DESC);

CREATE INDEX IF NOT EXISTS idx_approvals_mission
ON approvals(mission_id, requested_at DESC);

CREATE TABLE IF NOT EXISTS model_route_decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  execution_id TEXT NOT NULL UNIQUE REFERENCES executions(id) ON DELETE CASCADE,
  runtime_id TEXT NOT NULL REFERENCES runtimes(id) ON DELETE RESTRICT,
  policy_id TEXT,
  policy_version_id TEXT,
  selected_model_id TEXT NOT NULL REFERENCES models(id) ON DELETE RESTRICT,
  decision_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);


CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  execution_id TEXT REFERENCES executions(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_execution ON events(execution_id, id);
`;
