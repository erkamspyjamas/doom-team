import type { RuntimeSkill } from '../skills/types.js';

export type RuntimeDirective = {
  id: string;
  content: string;
  senderType: string;
  senderAgentId?: string | null;
  createdAt: string;
};

export type RuntimeContext = {
  executionId: string;
  agentName: string;
  systemPrompt: string;
  instructions: string;
  task: string;
  responseLocale: string;
  toolGrants: string[];
  autonomyLevel: number;
  permissionPolicy: Record<string, unknown>;
  skills: RuntimeSkill[];
  provider: any;
  model: any;
  runtime: any;
  workspace?: string;
  context: Record<string, unknown>;
  pullDirectives?: () => RuntimeDirective[];
  signal?: AbortSignal;
  onEvent: (type: string, payload: any) => void;
};

export type RuntimeResult = {
  externalSessionId?: string;
  text?: string;
  raw?: any;
};

export interface RuntimeAdapter {
  type: string;
  execute(ctx: RuntimeContext): Promise<RuntimeResult>;
  cancel?(externalSessionId: string): Promise<void>;
  status?(externalSessionId: string): Promise<any>;
  recap?(externalSessionId: string): Promise<any>;
}
