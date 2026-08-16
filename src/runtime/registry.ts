import type { RuntimeAdapter } from './types.js';
import { GenericOpenAIRuntime } from './generic-openai.js';
import { QwenCodeRuntime } from './qwen-code.js';

const adapters = new Map<string, RuntimeAdapter>();

for (const adapter of [
  new GenericOpenAIRuntime(),
  new QwenCodeRuntime(),
]) {
  adapters.set(adapter.type, adapter);
}

export function runtimeAdapter(type: string): RuntimeAdapter {
  const adapter = adapters.get(type);
  if (!adapter) throw new Error(`Runtime adapter not installed: ${type}`);
  return adapter;
}

export function installedRuntimeAdapters() {
  return [...adapters.keys()];
}
