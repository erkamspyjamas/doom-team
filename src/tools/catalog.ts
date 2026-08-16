import type { DoomToolDefinition } from './types.js';

export const TOOL_CATALOG: DoomToolDefinition[] = [



{
  name: 'request_specialist_agent',
  category: 'agent_factory',
  risk: 'write',
  description: 'Mission Lead only: create or reuse a temporary specialist when the permanent roster lacks a suitable role. During planning, create the specialist before returning the final DAG and use the returned agent id.',
  schema: {
    type: 'object',
    properties: {
      name: { type: 'string', description: 'Short human-readable specialist name.' },
      role_name: { type: 'string', description: 'Specialist role such as Technical Documentation Specialist.' },
      objective: { type: 'string', description: 'Specific reason this specialist is required for the mission.' },
      scope_type: { type: 'string', enum: ['mission','task'] },
      assign_task_key: { type: 'string', description: 'Optional existing task key. Do not use during initial planning because DAG tasks do not exist yet.' },
      tool_profiles: {
        type: 'array',
        items: { type: 'string', enum: ['read','write','git','tests','skills','communication'] },
        maxItems: 6
      },
      skill_ids: { type: 'array', items: { type: 'string' }, maxItems: 10 },
      required_capabilities: {
        type: 'array',
        items: { type: 'string', enum: ['chat','tools','reasoning','vision','structuredOutput'] },
        maxItems: 5
      },
      model_strategy: {
        type: 'string',
        enum: ['best_available','best_local_general','best_local_coder','best_local_creative','tools_required','vision_required']
      },
      autonomy_level: { type: 'integer', minimum: 0, maximum: 3 }
    },
    required: ['name','role_name','objective'],
    additionalProperties: false
  }
},
{
  name: 'list_mission_specialists',
  category: 'agent_factory',
  risk: 'read',
  description: 'Mission Lead only: list temporary specialists currently attached to this mission.',
  schema: {
    type: 'object',
    properties: {},
    additionalProperties: false
  }
},

{
  name: 'send_agent_message',
  category: 'communication',
  risk: 'write',
  description: 'Send a structured Doom Message Bus message to Boss, Lead, another team agent, or another mission task. Recipients: boss, lead, agent:<agent-id>, task:<task-key>.',
  schema: {
    type: 'object',
    properties: {
      recipient: { type: 'string', description: 'boss | lead | agent:<agent-id> | task:<task-key>' },
      message_type: { type: 'string', enum: ['handoff','question','answer','note','status'] },
      priority: { type: 'string', enum: ['low','normal','high'] },
      content: { type: 'string' },
      structured: { type: 'object', additionalProperties: true },
    },
    required: ['recipient','content'],
    additionalProperties: false,
  },
},
{
  name: 'list_agent_messages',
  category: 'communication',
  risk: 'read',
  description: 'Read recent Doom Message Bus messages relevant to the current mission task/execution.',
  schema: {
    type: 'object',
    properties: {
      limit: { type: 'integer', minimum: 1, maximum: 50 },
    },
    additionalProperties: false,
  },
},

  {
    name: 'list_skill_files',
    category: 'skill',
    risk: 'read',
    description: 'List reference/resource files packaged with an installed Doom skill.',
    schema: {
      type: 'object',
      properties: { skill: { type: 'string', description: 'Skill id or slug.' } },
      required: ['skill'], additionalProperties: false,
    },
  },
  {
    name: 'read_skill_file',
    category: 'skill',
    risk: 'read',
    description: 'Read a UTF-8 reference/resource file from an installed Doom skill. Skill files are read-only.',
    schema: {
      type: 'object',
      properties: {
        skill: { type: 'string', description: 'Skill id or slug.' },
        path: { type: 'string', description: 'Relative packaged file path.' },
        start_line: { type: 'integer', minimum: 1 },
        end_line: { type: 'integer', minimum: 1 },
      },
      required: ['skill','path'], additionalProperties: false,
    },
  },

  {
    name: 'list_files',
    category: 'filesystem',
    risk: 'read',
    description: 'List files and directories inside the assigned workspace. Use relative paths.',
    schema: {
      type: 'object',
      properties: {
        path: { type: 'string', description: 'Relative directory path. Defaults to .' },
        recursive: { type: 'boolean', description: 'List recursively when true.' },
        max_entries: { type: 'integer', minimum: 1, maximum: 500 },
      },
      additionalProperties: false,
    },
  },
  {
    name: 'read_file',
    category: 'filesystem',
    risk: 'read',
    description: 'Read a UTF-8 text file inside the assigned workspace with optional line range.',
    schema: {
      type: 'object',
      properties: {
        path: { type: 'string', description: 'Relative file path.' },
        start_line: { type: 'integer', minimum: 1 },
        end_line: { type: 'integer', minimum: 1 },
      },
      required: ['path'],
      additionalProperties: false,
    },
  },
  {
    name: 'write_file',
    category: 'filesystem',
    risk: 'write',
    description: 'Create or overwrite a UTF-8 text file inside the assigned workspace.',
    schema: {
      type: 'object',
      properties: {
        path: { type: 'string', description: 'Relative file path.' },
        content: { type: 'string', description: 'Complete new file content.' },
      },
      required: ['path', 'content'],
      additionalProperties: false,
    },
  },
  {
    name: 'replace_in_file',
    category: 'filesystem',
    risk: 'write',
    description: 'Replace exact text in a UTF-8 file inside the workspace. Safer than rewriting a whole file for small edits.',
    schema: {
      type: 'object',
      properties: {
        path: { type: 'string' },
        old_text: { type: 'string' },
        new_text: { type: 'string' },
        replace_all: { type: 'boolean' },
      },
      required: ['path', 'old_text', 'new_text'],
      additionalProperties: false,
    },
  },
  {
    name: 'git_status',
    category: 'git',
    risk: 'read',
    description: 'Show git branch and working-tree status for the assigned workspace.',
    schema: { type: 'object', properties: {}, additionalProperties: false },
  },
  {
    name: 'git_diff',
    category: 'git',
    risk: 'read',
    description: 'Show tracked git diff and, by default, synthetic diffs for untracked files. Can be limited to one relative path or staged changes.',
    schema: {
      type: 'object',
      properties: {
        path: { type: 'string' },
        cached: { type: 'boolean', description: 'When true, show staged changes using git diff --cached.' },
        include_untracked: { type: 'boolean', description: 'Include untracked files as new-file diffs. Defaults to true when cached=false.' },
      },
      additionalProperties: false,
    },
  },
  {
    name: 'run_tests',
    category: 'tests',
    risk: 'execute',
    description: 'Auto-detect and run the project test suite inside the assigned workspace.',
    schema: {
      type: 'object',
      properties: {
        target: { type: 'string', description: 'Optional relative test target.' },
        timeout_ms: { type: 'integer', minimum: 1000, maximum: 180000 },
      },
      additionalProperties: false,
    },
  },
  {
    name: 'run_command',
    category: 'shell',
    risk: 'execute',
    description: 'Run one restricted project-scoped program without a shell. Dangerous host/system commands are not available.',
    schema: {
      type: 'object',
      properties: {
        program: { type: 'string', description: 'Allowed executable name, not an absolute path.' },
        args: { type: 'array', items: { type: 'string' }, maxItems: 50 },
        timeout_ms: { type: 'integer', minimum: 1000, maximum: 120000 },
      },
      required: ['program'],
      additionalProperties: false,
    },
  },
];

const CATEGORY_TOOLS: Record<string, string[]> = {
  filesystem: ['list_files', 'read_file', 'write_file', 'replace_in_file'],
  git: ['git_status', 'git_diff'],
  tests: ['run_tests'],
  shell: ['run_command'],
  skill: ['list_skill_files', 'read_skill_file'],
  communication: ['send_agent_message','list_agent_messages'],
  agent_factory: ['request_specialist_agent','list_mission_specialists'],
};

export function resolveToolDefinitions(grants: string[]) {
  const names = new Set<string>();
  for (const grant of grants ?? []) {
    if (CATEGORY_TOOLS[grant]) {
      for (const name of CATEGORY_TOOLS[grant]) names.add(name);
    } else if (TOOL_CATALOG.some(x => x.name === grant)) {
      names.add(grant);
    }
  }
  return TOOL_CATALOG.filter(x => names.has(x.name));
}

export function openAIToolDefinitions(grants: string[]) {
  return resolveToolDefinitions(grants).map(tool => ({
    type: 'function',
    function: {
      name: tool.name,
      description: tool.description,
      parameters: tool.schema,
    },
  }));
}

export function publicToolCatalog() {
  return TOOL_CATALOG.map(tool => ({
    name: tool.name,
    category: tool.category,
    risk: tool.risk,
  }));
}
