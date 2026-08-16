export type Config = {
  host: string;
  port: number;
  dbPath: string;
  defaultLocale: string;
  defaultWorkspace: string;
  worktreeRoot: string;
  maxMissionSpecialists: number;
  maxToolSteps: number;
  qwenDaemonUrl: string;
  qwenWorkspace: string;
  qwenToken?: string;
  ollamaBaseUrl: string;
};

export function loadConfig(): Config {
  return {
    host: process.env.DOOM_HOST ?? '127.0.0.1',
    port: Number(process.env.DOOM_PORT ?? '8780'),
    dbPath:
      process.env.DOOM_DB ??
      '/home/eko/doom-team/data/doom-v02.sqlite',
    defaultLocale:
      process.env.DOOM_DEFAULT_LOCALE ??
      'tr',
    defaultWorkspace:
      process.env.DOOM_DEFAULT_WORKSPACE ??
      '/home/eko/doom-team/projects/doom-test',
    worktreeRoot:
      process.env.DOOM_WORKTREE_ROOT ??
      '/home/eko/doom-team/worktrees',
    maxMissionSpecialists: Math.max(
      1,
      Math.min(20, Number(process.env.DOOM_MAX_MISSION_SPECIALISTS ?? 5))
    ),
    maxToolSteps: Math.max(1, Math.min(Number(process.env.DOOM_MAX_TOOL_STEPS ?? '20'), 50)),
    qwenDaemonUrl:
      process.env.QWEN_DAEMON_URL ??
      'http://127.0.0.1:4170',
    qwenWorkspace:
      process.env.QWEN_WORKSPACE ??
      '/home/eko/doom-team/projects/doom-test',
    qwenToken: process.env.QWEN_SERVER_TOKEN || undefined,
    ollamaBaseUrl:
      process.env.OLLAMA_BASE_URL ??
      'http://127.0.0.1:11434/v1',
  };
}
