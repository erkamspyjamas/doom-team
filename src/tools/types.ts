import type { RuntimeSkill } from '../skills/types.js';

export type ToolRisk = 'read' | 'write' | 'execute';

export type DoomToolDefinition = {
  name: string;
  category: string;
  description: string;
  risk: ToolRisk;
  schema: Record<string, unknown>;
};

export type ToolExecutionContext = {
  workspace: string;
  executionId: string;
  autonomyLevel: number;
  skills: RuntimeSkill[];
  context: Record<string, unknown>;
  signal?: AbortSignal;
  onEvent: (type: string, payload: any) => void;
};

export type ToolExecutionResult = {
  ok: boolean;
  output?: unknown;
  error?: string;
  metadata?: Record<string, unknown>;
};
