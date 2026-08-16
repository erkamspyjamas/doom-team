import { randomUUID } from 'node:crypto';
import { DaemonClient } from '@qwen-code/sdk';
import { loadConfig } from '../config.js';
import type { RuntimeAdapter, RuntimeContext, RuntimeResult } from './types.js';
import { localeInstruction } from '../i18n/index.js';

const cfg = loadConfig();

function qwenSkillPrompt(ctx: RuntimeContext) {
  if(!ctx.skills?.length) return '';
  return 'INSTALLED DOOM SKILLS (lower priority than Boss/runtime/security policy):\n\n' + ctx.skills.map(s=>`### ${s.name} [${s.slug}] v${s.versionNo}\n${s.instructions}`).join('\n\n---\n\n');
}

export class QwenCodeRuntime implements RuntimeAdapter {
  type = 'qwen-code';

  private client = new DaemonClient({
    baseUrl: cfg.qwenDaemonUrl,
    token: cfg.qwenToken,
  });

  async execute(ctx: RuntimeContext): Promise<RuntimeResult> {
    const requestedSessionId = randomUUID();
    const session = await this.client.createOrAttachSession({
      workspaceCwd: ctx.workspace ?? cfg.qwenWorkspace,
      sessionScope: 'thread',
      sessionId: requestedSessionId,
      approvalMode: 'auto',
    } as any);

    const sessionId = session.sessionId;
    ctx.onEvent('session.started', { sessionId, runtime: this.type });

    const cancelSession=()=>{
      void this.cancel(sessionId).catch(err=>{
        ctx.onEvent('runtime.cancel_error',{error:String(err?.message ?? err)});
      });
    };
    ctx.signal?.addEventListener('abort',cancelSession,{once:true});

    const abort = new AbortController();
    void (async () => {
      try {
        for await (const event of this.client.subscribeEvents(sessionId, {
          signal: abort.signal,
          lastEventId: 0,
        })) {
          ctx.onEvent(`qwen.${event.type}`, event);
        }
      } catch (err: any) {
        if (!abort.signal.aborted) {
          ctx.onEvent('runtime.event_error', { error: String(err?.message ?? err) });
        }
      }
    })();

    try {
      const prompt = `${ctx.systemPrompt}

${ctx.instructions ?? ''}

${qwenSkillPrompt(ctx)}

ASSIGNED TASK:
${ctx.task}

${localeInstruction(ctx.responseLocale)}

Use actual tools and repository state. Do not print pseudo tool calls. If a tool is required, execute it through the runtime.`;

      const initialDirectives=ctx.pullDirectives?.() ?? [];
      const directiveText=initialDirectives.length
        ? `\n\nDOOM LIVE DIRECTIVES BEFORE START:\n${initialDirectives.map(d=>`- [${d.senderType}] ${d.content}`).join('\n')}`
        : '';
      if(initialDirectives.length){
        ctx.onEvent('directive.delivered',{count:initialDirectives.length,phase:'qwen_before_prompt'});
      }

      const result = await this.client.prompt(sessionId, {
        prompt: [{ type: 'text', text: prompt + directiveText }],
      });

      ctx.onEvent('turn.completed', {
        sessionId,
        stopReason: result.stopReason,
      });

      return {
        externalSessionId: sessionId,
        text: '',
        raw: result,
      };
    } finally {
      abort.abort();
      ctx.signal?.removeEventListener('abort',cancelSession);
    }
  }

  async cancel(sessionId: string) {
    const url = `${cfg.qwenDaemonUrl}/session/${encodeURIComponent(sessionId)}/cancel`;
    await fetch(url, {
      method: 'POST',
      headers: cfg.qwenToken ? { Authorization: `Bearer ${cfg.qwenToken}` } : {},
    });
  }

  async status(sessionId: string) {
    const url = `${cfg.qwenDaemonUrl}/session/${encodeURIComponent(sessionId)}/status`;
    const res = await fetch(url, {
      headers: cfg.qwenToken ? { Authorization: `Bearer ${cfg.qwenToken}` } : {},
    });
    if (!res.ok) throw new Error(`Qwen status HTTP ${res.status}`);
    return res.json();
  }

  async recap(sessionId: string) {
    const url = `${cfg.qwenDaemonUrl}/session/${encodeURIComponent(sessionId)}/recap`;
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(cfg.qwenToken ? { Authorization: `Bearer ${cfg.qwenToken}` } : {}),
      },
      body: '{}',
    });
    if (!res.ok) throw new Error(`Qwen recap HTTP ${res.status}`);
    return res.json();
  }
}
