import Database from 'better-sqlite3';
import { mkdirSync } from 'node:fs';
import { dirname } from 'node:path';
import { randomUUID } from 'node:crypto';
import { loadConfig } from '../config.js';
import { SCHEMA } from './schema.js';

const cfg = loadConfig();
mkdirSync(dirname(cfg.dbPath), { recursive: true });

export const db = new Database(cfg.dbPath);
db.pragma('foreign_keys = ON');
db.exec(SCHEMA);

function ensureColumn(table:string,column:string,definition:string){
  const columns=db.prepare(`PRAGMA table_info(${table})`).all() as any[];
  if(columns.some((row:any)=>String(row.name)===column))return;
  db.exec(`ALTER TABLE ${table} ADD COLUMN ${column} ${definition}`);
}

// Forward-only, additive compatibility migration for pre-V0.12.17 databases.
// Values are backfilled from mission.plan_json by missions.resumeAutonomousMissions().
ensureColumn('mission_tasks','instruction',"TEXT NOT NULL DEFAULT ''");
ensureColumn('mission_tasks','expected_outcome',"TEXT NOT NULL DEFAULT ''");
ensureColumn('mission_tasks','work_mode',"TEXT NOT NULL DEFAULT ''");

export function now() {
  return new Date().toISOString();
}

export function id(prefix: string) {
  return `${prefix}_${randomUUID()}`;
}

export function parseJson<T = any>(value: any, fallback: T): T {
  try {
    if (typeof value !== 'string') return fallback;
    return JSON.parse(value) as T;
  } catch {
    return fallback;
  }
}

export function seed() {
  const t = now();

  db.prepare(`
    INSERT OR IGNORE INTO model_providers
    (id,name,protocol,base_url,auth_type,location,enabled,created_at,updated_at)
    VALUES (@id,@name,@protocol,@base_url,'none','local',1,@created_at,@updated_at)
  `).run({
    id: 'provider_ollama_local',
    name: 'Local Ollama',
    protocol: 'openai-compatible',
    base_url: cfg.ollamaBaseUrl,
    created_at: t,
    updated_at: t,
  });

  db.prepare(`
    INSERT OR IGNORE INTO models
    (id,provider_id,name,provider_model_id,enabled,context_window_configured,
     max_context_reported,capabilities_json,labels_json,created_at,updated_at)
    VALUES (@id,@provider_id,@name,@provider_model_id,1,8192,262144,
            @capabilities,@labels,@created_at,@updated_at)
  `).run({
    id: 'model_qwen35_local',
    provider_id: 'provider_ollama_local',
    name: 'Qwen3.5 9B Local',
    provider_model_id: 'qwen3.5:9b-q4_K_M',
    capabilities: JSON.stringify({
      chat: true,
      tools: true,
      reasoning: true,
      vision: true,
      streaming: true,
      structuredOutput: true,
    }),
    labels: JSON.stringify(['local', 'coding', 'qwen']),
    created_at: t,
    updated_at: t,
  });

  db.prepare(`
    INSERT OR IGNORE INTO runtimes
    (id,name,adapter_type,enabled,config_json,capabilities_json,created_at,updated_at)
    VALUES (@id,@name,@adapter_type,1,@config,@caps,@created_at,@updated_at)
  `).run({
    id: 'runtime_generic_openai',
    name: 'Generic OpenAI-Compatible Runtime',
    adapter_type: 'generic-openai',
    config: JSON.stringify({ workspace: cfg.defaultWorkspace }),
    caps: JSON.stringify({
      chat: true,
      tools: true,
      streaming: false,
      steer: false,
      events: true,
      doomToolLoop: true,
    }),
    created_at: t,
    updated_at: t,
  });

  db.prepare(`
    INSERT OR IGNORE INTO runtimes
    (id,name,adapter_type,enabled,config_json,capabilities_json,created_at,updated_at)
    VALUES (@id,@name,@adapter_type,1,@config,@caps,@created_at,@updated_at)
  `).run({
    id: 'runtime_qwen_code',
    name: 'Qwen Code Runtime',
    adapter_type: 'qwen-code',
    config: JSON.stringify({
      daemonUrl: cfg.qwenDaemonUrl,
      workspace: cfg.qwenWorkspace,
    }),
    caps: JSON.stringify({
      chat: true,
      tools: true,
      streaming: true,
      events: true,
      recap: true,
      cancel: true,
      steer: true,
      codingHarness: true,
    }),
    created_at: t,
    updated_at: t,
  });

    db.prepare(`
    INSERT OR IGNORE INTO model_runtime_compatibility
    (id,model_id,runtime_id,chat_status,tools_status,coding_status,
     structured_output_status,notes,last_tested_at,metadata_json)
    VALUES (@id,@model,@runtime,@chat,@tools,@coding,@structured,@notes,@tested,'{}')
  `).run({
    id: 'compat_qwen_generic',
    model: 'model_qwen35_local',
    runtime: 'runtime_generic_openai',
    chat: 'pass',
    tools: 'pass',
    coding: 'unknown',
    structured: 'unknown',
    notes: 'Direct Ollama OpenAI-compatible tool-call verified by Boss on 2026-08-15.',
    tested: t,
  });

  db.prepare(`
    INSERT OR IGNORE INTO model_runtime_compatibility
    (id,model_id,runtime_id,chat_status,tools_status,coding_status,
     structured_output_status,notes,last_tested_at,metadata_json)
    VALUES (@id,@model,@runtime,@chat,@tools,@coding,@structured,@notes,@tested,'{}')
  `).run({
    id: 'compat_qwen_qwencode',
    model: 'model_qwen35_local',
    runtime: 'runtime_qwen_code',
    chat: 'pass',
    tools: 'fail',
    coding: 'blocked',
    structured: 'unknown',
    notes: 'First Doom smoke test emitted textual [tool_call: ...] instead of executing the tool.',
    tested: t,
  });

  const templateStmt = db.prepare(`
    INSERT OR IGNORE INTO agent_templates
    (id,name,category,description,definition_json,enabled,created_at,updated_at)
    VALUES (@id,@name,@category,@description,@definition,1,@created_at,@updated_at)
  `);

  const templates = [
    {
      id: 'template_backend',
      name: 'Backend Engineer',
      category: 'software',
      description: 'Backend/API/database engineer',
      definition: {
        systemPrompt: 'You are a senior backend engineer. Inspect real sources before making claims. Implement secure, maintainable backend changes and verify them.',
        runtimePolicy: { mode: 'fixed', runtimeId: 'runtime_generic_openai' },
        modelPolicy: { mode: 'fixed', modelId: 'model_qwen35_local' },
        tools: ['filesystem', 'shell', 'git', 'tests'],
        skills: [],
        autonomyLevel: 3,
      },
    },
    {
      id: 'template_story_writer',
      name: 'Story Writer',
      category: 'creative',
      description: 'Creative fiction and narrative writer',
      definition: {
        systemPrompt: 'You are a professional fiction writer. Maintain character, setting, tone and continuity. Produce polished prose and revise when requested.',
        runtimePolicy: { mode: 'fixed', runtimeId: 'runtime_generic_openai' },
        modelPolicy: { mode: 'fixed', modelId: 'model_qwen35_local' },
        tools: ['filesystem'],
        skills: ['story-structure'],
        autonomyLevel: 2,
      },
    },
    {
      id: 'template_researcher',
      name: 'Researcher',
      category: 'research',
      description: 'Research and synthesis specialist',
      definition: {
        systemPrompt: 'You are a rigorous research specialist. Distinguish evidence, inference and uncertainty. Produce concise findings and source-oriented notes.',
        runtimePolicy: { mode: 'fixed', runtimeId: 'runtime_generic_openai' },
        modelPolicy: { mode: 'fixed', modelId: 'model_qwen35_local' },
        tools: ['filesystem', 'web'],
        skills: [],
        autonomyLevel: 2,
      },
    },
  ];

  for (const x of templates) {
    templateStmt.run({
      id: x.id,
      name: x.name,
      category: x.category,
      description: x.description,
      definition: JSON.stringify(x.definition),
      created_at: t,
      updated_at: t,
    });
  }

  // ---------------------------------------------------------------------------
  // One-time seed: Doom's default software company.
  //
  // These are NOT hard-coded runtime roles. They are ordinary Agent records
  // created once and can later be versioned, edited, reassigned or deleted.
  // system_meta prevents deleted defaults from being recreated on every boot.
  // ---------------------------------------------------------------------------
  const defaultSeedKey = 'default_software_agents_v1_seeded';
  const defaultSeeded = db.prepare(`SELECT value FROM system_meta WHERE key=?`).get(defaultSeedKey) as any;

  if (!defaultSeeded) {
    const defaultRuntimePolicy = {
      mode: 'fixed',
      runtimeId: 'runtime_generic_openai',
    };
    const defaultModelPolicy = {
      mode: 'fixed',
      modelId: 'model_qwen35_local',
    };


    const defaultAgents = [
      {
        key: 'lead',
        name: 'Lead',
        description: "Takımı koordine eder, işi parçalara ayırır, bağımlılıkları yönetir ve Boss'a raporlar.",
        autonomy: 3,
        tools: ['filesystem', 'git', 'tests'],
        skills: ['planning', 'delegation', 'integration'],
        prompt: `You are the Lead of Doom Team.
Your job is to understand the Boss objective, decompose work, coordinate specialists, track blockers, and demand evidence before declaring work complete.
Do not pretend a tool, file, test, commit or deployment happened when you cannot verify it.
If the current runtime lacks a required capability, explicitly mark the work BLOCKED and say what capability is missing.
Prefer concise decisions, clear ownership, acceptance criteria and next actions.`,
      },
      {
        key: 'product',
        name: 'Product',
        description: 'Gereksinimleri, kullanıcı ihtiyaçlarını ve kabul kriterlerini netleştirir.',
        autonomy: 2,
        tools: ['filesystem'],
        skills: ['requirements', 'product-analysis', 'acceptance-criteria'],
        prompt: `You are Doom Team's Product specialist.
Translate the Boss objective into concrete user needs, scope, requirements, edge cases and measurable acceptance criteria.
Separate must-have, should-have and optional work.
Do not invent implementation evidence. Flag ambiguity and missing business decisions explicitly.`,
      },
      {
        key: 'architect',
        name: 'Architect',
        description: 'Teknik mimariyi, sınırları, veri akışlarını ve entegrasyon kararlarını tasarlar.',
        autonomy: 2,
        tools: ['filesystem', 'git'],
        skills: ['software-architecture', 'api-design', 'data-design'],
        prompt: `You are Doom Team's Software Architect.
Inspect available project context before proposing architecture.
Design maintainable boundaries, data flows, interfaces, failure handling, observability and migration paths.
Prefer reversible decisions and loose coupling.
Do not claim repository facts you have not verified.`,
      },
      {
        key: 'ux',
        name: 'UX',
        description: 'Kullanıcı akışlarını, ekran davranışlarını ve arayüz kabul kriterlerini tasarlar.',
        autonomy: 2,
        tools: ['filesystem'],
        skills: ['ux', 'ui-specification', 'accessibility'],
        prompt: `You are Doom Team's UX specialist.
Turn product goals into clear user flows, screen states, interaction rules, empty/error/loading states and accessibility-aware UI specifications.
Keep implementation constraints in mind without replacing the Frontend engineer.
State assumptions explicitly.`,
      },
      {
        key: 'frontend',
        name: 'Frontend',
        description: 'Web arayüzleri, component yapısı, state ve API entegrasyonlarını geliştirir.',
        autonomy: 3,
        tools: ['filesystem', 'shell', 'git', 'tests'],
        skills: ['frontend', 'typescript', 'react', 'testing'],
        prompt: `You are Doom Team's senior Frontend Engineer.
Work from real repository state and the agreed UX/API contract.
Implement maintainable UI code, loading/error/empty states, validation and tests.
Use actual tools when available; never output pseudo tool calls or claim edits/tests that did not execute.
If tools are unavailable, stop and report BLOCKED instead of fabricating completion.`,
      },
      {
        key: 'backend',
        name: 'Backend',
        description: 'API, servis, veritabanı ve backend iş mantığı geliştirir.',
        autonomy: 3,
        tools: ['filesystem', 'shell', 'git', 'tests'],
        skills: ['backend', 'api', 'database', 'testing'],
        prompt: `You are Doom Team's senior Backend Engineer.
Inspect the real repository and data model before changing code.
Implement secure, maintainable APIs, services and database changes with validation, error handling and tests.
Use actual tools when available; never print pseudo tool calls or invent commands, diffs or test results.
If execution tools are unavailable, mark the task BLOCKED and explain exactly what is needed.`,
      },
      {
        key: 'qa',
        name: 'QA',
        description: 'Kabul kriterleri, regresyon, edge-case ve otomasyon testlerini yürütür.',
        autonomy: 3,
        tools: ['filesystem', 'shell', 'git', 'tests'],
        skills: ['qa', 'test-design', 'regression', 'automation'],
        prompt: `You are Doom Team's QA Engineer.
Verify behavior against explicit acceptance criteria.
Design and execute reproducible happy-path, edge-case, regression and failure tests when tools are available.
Never report a test as passed unless it actually ran and evidence is available.
Return failures with precise reproduction steps and ownership suggestions.`,
      },
      {
        key: 'security',
        name: 'Security',
        description: 'Kimlik doğrulama, yetki, veri sızıntısı, dependency ve saldırı yüzeyi incelemelerini yapar.',
        autonomy: 2,
        tools: ['filesystem', 'shell', 'git'],
        skills: ['security-review', 'auth', 'threat-modeling'],
        prompt: `You are Doom Team's Application Security specialist.
Review authentication, authorization, tenant boundaries, input validation, secrets, data exposure, dependencies and dangerous operations.
Prioritize findings by exploitability and impact.
Do not claim vulnerabilities without evidence; distinguish confirmed findings from hypotheses.
Never perform destructive or production actions without explicit Boss approval.`,
      },
      {
        key: 'reviewer',
        name: 'Reviewer',
        description: 'Kod ve tasarım değişikliklerini kalite, bakım maliyeti ve kabul kriterleri açısından inceler.',
        autonomy: 2,
        tools: ['filesystem', 'git', 'tests'],
        skills: ['code-review', 'maintainability', 'correctness'],
        prompt: `You are Doom Team's independent Reviewer.
Review proposed changes against requirements, architecture, correctness, maintainability, tests and regressions.
Prefer specific evidence-backed comments over stylistic preferences.
Do not approve work merely because the implementing agent says it is complete.`,
      },
      {
        key: 'devops',
        name: 'DevOps',
        description: 'Build, container, CI/CD, runtime, deployment ve operasyonel sağlığı yönetir.',
        autonomy: 3,
        tools: ['filesystem', 'shell', 'git'],
        skills: ['devops', 'docker', 'kubernetes', 'ci-cd', 'observability'],
        prompt: `You are Doom Team's DevOps Engineer.
Own reproducible builds, runtime configuration, containers, CI/CD, deployment plans, health checks, logs and rollback procedures.
Treat production, destructive infrastructure changes and host-level package installation as approval-sensitive actions.
Never claim a deployment or command succeeded without execution evidence.`,
      },
    ];

    const agentInsert = db.prepare(`
      INSERT INTO agents
      (id,name,slug,description,current_version_id,enabled,lifetime,tags_json,created_at,updated_at)
      VALUES (@id,@name,@slug,@description,@version_id,1,'permanent',@tags,@created_at,@updated_at)
    `);

    const versionInsert = db.prepare(`
      INSERT INTO agent_versions
      (id,agent_id,version_no,system_prompt,instructions,runtime_policy_json,model_policy_json,
       required_capabilities_json,tools_json,skills_json,memory_policy_json,permission_policy_json,
       autonomy_level,created_at,created_by)
      VALUES (@id,@agent_id,1,@system_prompt,'',@runtime_policy,@model_policy,
              @required_capabilities,@tools,@skills,@memory_policy,@permission_policy,
              @autonomy_level,@created_at,'system-seed')
    `);

    const teamId = 'team_default_software';
    const memberInsert = db.prepare(`
      INSERT OR IGNORE INTO team_members
      (team_id,agent_id,role_label,priority,can_delegate)
      VALUES (?,?,?,?,?)
    `);

    db.transaction(() => {
      for (const spec of defaultAgents) {
        const agentId = `agent_default_${spec.key}`;
        const versionId = `agentver_default_${spec.key}_v1`;

        const existing = db.prepare(`SELECT id FROM agents WHERE id=? OR slug=?`).get(agentId, spec.key) as any;
        if (!existing) {
          agentInsert.run({
            id: agentId,
            name: spec.name,
            slug: spec.key,
            description: spec.description,
            version_id: versionId,
            tags: JSON.stringify(['default', 'software-team', spec.key]),
            created_at: t,
            updated_at: t,
          });

          versionInsert.run({
            id: versionId,
            agent_id: agentId,
            system_prompt: spec.prompt,
            runtime_policy: JSON.stringify(defaultRuntimePolicy),
            model_policy: JSON.stringify(defaultModelPolicy),
            required_capabilities: JSON.stringify(
              ['frontend', 'backend', 'qa', 'devops'].includes(spec.key)
                ? ['chat', 'tools']
                : ['chat']
            ),
            tools: JSON.stringify(spec.tools),
            skills: JSON.stringify(spec.skills),
            memory_policy: JSON.stringify({ scope: ['project', 'mission', 'task'] }),
            permission_policy: JSON.stringify({ profile: 'default-safe' }),
            autonomy_level: spec.autonomy,
            created_at: t,
          });
        }
      }

      db.prepare(`
        INSERT OR IGNORE INTO teams
        (id,name,description,lead_agent_id,enabled,created_at,updated_at)
        VALUES (?,?,?,?,1,?,?)
      `).run(
        teamId,
        'Default Software Team',
        'Doom Team ile birlikte gelen varsayılan yazılım geliştirme ekibi.',
        'agent_default_lead',
        t,
        t
      );

      const memberSpecs = [
        ['lead', 10, 1],
        ['product', 20, 0],
        ['architect', 30, 0],
        ['ux', 40, 0],
        ['frontend', 50, 0],
        ['backend', 50, 0],
        ['qa', 60, 0],
        ['security', 60, 0],
        ['reviewer', 70, 0],
        ['devops', 70, 0],
      ];

      for (const [key, priority, canDelegate] of memberSpecs) {
        memberInsert.run(
          teamId,
          `agent_default_${key}`,
          String(key),
          Number(priority),
          Number(canDelegate),
        );
      }

      db.prepare(`
        INSERT INTO system_meta(key,value,updated_at)
        VALUES (?,?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at
      `).run(defaultSeedKey, '1', t);
    })();
  }

// ---------------------------------------------------------------------------
// Localization defaults and translations.
// Separate marker upgrades existing V0.2.1 databases without recreating agents.
// ---------------------------------------------------------------------------
db.prepare(`
  INSERT OR IGNORE INTO system_settings(key,value,updated_at)
  VALUES ('ui_locale', ?, ?)
`).run(cfg.defaultLocale, t);

db.prepare(`
  INSERT OR IGNORE INTO system_settings(key,value,updated_at)
  VALUES ('agent_response_locale', ?, ?)
`).run(cfg.defaultLocale, t);

const i18nSeedKey = 'default_i18n_v1_seeded';
const i18nSeeded = db.prepare(`SELECT value FROM system_meta WHERE key=?`).get(i18nSeedKey) as any;

if (!i18nSeeded) {
  const upsertTranslation = db.prepare(`
    INSERT INTO entity_translations(entity_type,entity_id,locale,field,value,updated_at)
    VALUES (?,?,?,?,?,?)
    ON CONFLICT(entity_type,entity_id,locale,field)
    DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at
  `);

  const translations: Array<[string,string,string,string,string]> = [
    ['team','team_default_software','tr','name','Varsayılan Yazılım Ekibi'],
    ['team','team_default_software','tr','description','Doom Team ile birlikte gelen varsayılan yazılım geliştirme ekibi.'],
    ['team','team_default_software','en','name','Default Software Team'],
    ['team','team_default_software','en','description','The default software development team included with Doom Team.'],

    ['agent','agent_default_lead','tr','description','Takımı koordine eder, işi parçalara ayırır, bağımlılıkları yönetir ve Boss’a raporlar.'],
    ['agent','agent_default_product','tr','description','Gereksinimleri, kullanıcı ihtiyaçlarını ve kabul kriterlerini netleştirir.'],
    ['agent','agent_default_architect','tr','description','Teknik mimariyi, sınırları, veri akışlarını ve entegrasyon kararlarını tasarlar.'],
    ['agent','agent_default_ux','tr','description','Kullanıcı akışlarını, ekran davranışlarını ve arayüz kabul kriterlerini tasarlar.'],
    ['agent','agent_default_frontend','tr','description','Web arayüzleri, component yapısı, state ve API entegrasyonlarını geliştirir.'],
    ['agent','agent_default_backend','tr','description','API, servis, veritabanı ve backend iş mantığı geliştirir.'],
    ['agent','agent_default_qa','tr','description','Kabul kriterleri, regresyon, edge-case ve otomasyon testlerini yürütür.'],
    ['agent','agent_default_security','tr','description','Kimlik doğrulama, yetki, veri sızıntısı, dependency ve saldırı yüzeyi incelemelerini yapar.'],
    ['agent','agent_default_reviewer','tr','description','Kod ve tasarım değişikliklerini kalite, bakım maliyeti ve kabul kriterleri açısından inceler.'],
    ['agent','agent_default_devops','tr','description','Build, container, CI/CD, runtime, deployment ve operasyonel sağlığı yönetir.'],

    ['agent','agent_default_lead','en','description','Coordinates the team, decomposes work, manages dependencies and reports to the Boss.'],
    ['agent','agent_default_product','en','description','Clarifies requirements, user needs and measurable acceptance criteria.'],
    ['agent','agent_default_architect','en','description','Designs technical architecture, boundaries, data flows and integration decisions.'],
    ['agent','agent_default_ux','en','description','Designs user flows, screen behavior and interface acceptance criteria.'],
    ['agent','agent_default_frontend','en','description','Builds web interfaces, component architecture, state and API integrations.'],
    ['agent','agent_default_backend','en','description','Builds APIs, services, databases and backend business logic.'],
    ['agent','agent_default_qa','en','description','Runs acceptance, regression, edge-case and automated tests.'],
    ['agent','agent_default_security','en','description','Reviews authentication, authorization, data exposure, dependencies and attack surface.'],
    ['agent','agent_default_reviewer','en','description','Reviews code and design changes for quality, maintainability and acceptance criteria.'],
    ['agent','agent_default_devops','en','description','Owns builds, containers, CI/CD, runtime, deployment and operational health.'],

    ['agent_template','template_backend','tr','name','Backend Mühendisi'],
    ['agent_template','template_backend','tr','description','Backend, API ve veritabanı mühendisi'],
    ['agent_template','template_story_writer','tr','name','Hikaye Yazarı'],
    ['agent_template','template_story_writer','tr','description','Kurgu ve anlatı odaklı yaratıcı yazar'],
    ['agent_template','template_researcher','tr','name','Araştırmacı'],
    ['agent_template','template_researcher','tr','description','Araştırma ve sentez uzmanı'],

    ['runtime','runtime_generic_openai','tr','name','Genel OpenAI-Uyumlu Runtime'],
    ['runtime','runtime_qwen_code','tr','name','Qwen Code Runtime'],

    ['compatibility','compat_qwen_generic','tr','notes','Otomatik doğrudan tool-call smoke testi başarılı.'],
    ['compatibility','compat_qwen_qwencode','tr','notes','İlk Doom smoke testinde tool çalıştırılmak yerine [tool_call: ...] metin olarak üretildi.'],
  ];

  db.transaction(() => {
    for (const item of translations) {
      upsertTranslation.run(item[0],item[1],item[2],item[3],item[4],t);
    }
    db.prepare(`
      INSERT INTO system_meta(key,value,updated_at)
      VALUES (?,?,?)
      ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at
    `).run(i18nSeedKey,'1',t);
  })();
}

  const toolRuntimeSeedKey = 'tool_runtime_v03_seeded';
  const toolRuntimeSeeded = db.prepare(`SELECT value FROM system_meta WHERE key=?`).get(toolRuntimeSeedKey) as any;
  if (!toolRuntimeSeeded) {
    const genericConfig = JSON.stringify({ workspace: cfg.defaultWorkspace });
    const genericCaps = JSON.stringify({
      chat: true,
      tools: true,
      streaming: false,
      steer: false,
      events: true,
      doomToolLoop: true,
    });

    db.transaction(() => {
      db.prepare(`
        UPDATE runtimes
        SET config_json=?, capabilities_json=?, updated_at=?
        WHERE id='runtime_generic_openai'
      `).run(genericConfig,genericCaps,t);

      const backendTemplate = db.prepare(`SELECT definition_json FROM agent_templates WHERE id='template_backend'`).get() as any;
      if (backendTemplate?.definition_json) {
        const definition = parseJson<any>(backendTemplate.definition_json,{});
        definition.runtimePolicy = { mode: 'fixed', runtimeId: 'runtime_generic_openai' };
        db.prepare(`UPDATE agent_templates SET definition_json=?,updated_at=? WHERE id='template_backend'`)
          .run(JSON.stringify(definition),t);
      }

      const upsertV03Translation = db.prepare(`
        INSERT INTO entity_translations(entity_type,entity_id,locale,field,value,updated_at)
        VALUES (?,?,?,?,?,?)
        ON CONFLICT(entity_type,entity_id,locale,field)
        DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at
      `);
      const v03Translations: Array<[string,string,string,string,string]> = [
        ['team','team_default_software','tr','name','Varsayılan Yazılım Ekibi'],
        ['team','team_default_software','tr','description','Doom Team ile birlikte gelen varsayılan yazılım geliştirme ekibi.'],
        ['agent','agent_default_lead','tr','name','Takım Lideri'],
        ['agent','agent_default_product','tr','name','Ürün'],
        ['agent','agent_default_architect','tr','name','Mimar'],
        ['agent','agent_default_security','tr','name','Güvenlik'],
        ['agent','agent_default_reviewer','tr','name','Kod İnceleyici'],
        ['runtime','runtime_generic_openai','tr','name','Doom Genel Agent Runtime'],
      ];
      for (const item of v03Translations) {
        upsertV03Translation.run(item[0],item[1],item[2],item[3],item[4],t);
      }

      db.prepare(`
        INSERT INTO system_meta(key,value,updated_at)
        VALUES (?,?,?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at
      `).run(toolRuntimeSeedKey,'1',t);
    })();
  }


const modelPolicySeedKey = 'model_router_v07_seeded';
const modelPolicySeeded = db.prepare(`SELECT value FROM system_meta WHERE key=?`).get(modelPolicySeedKey) as any;

if (!modelPolicySeeded) {
  const policyInsert = db.prepare(`
    INSERT OR IGNORE INTO model_policies
    (id,name,description,current_version_id,enabled,created_at,updated_at)
    VALUES (?,?,?,?,1,?,?)
  `);
  const versionInsert = db.prepare(`
    INSERT OR IGNORE INTO model_policy_versions
    (id,policy_id,version_no,definition_json,created_at,created_by)
    VALUES (?,?,1,?,?,'system-seed')
  `);

  const defaults = [
    {
      id:'policy_best_local_general',
      versionId:'policyver_best_local_general_v1',
      name:'Best Local General',
      description:'Prefer the healthiest low-latency local chat model.',
      definition:{
        location:'local',
        require:{chat:true},
        preferredLabels:[],
        preferredCapabilities:[],
        preferLowerLatency:true,
        preferLargerContext:true,
        fallbackModelIds:['model_qwen35_local']
      }
    },
    {
      id:'policy_best_local_coder',
      versionId:'policyver_best_local_coder_v1',
      name:'Best Local Coder',
      description:'Prefer a local model with native tool calling and coding-oriented labels.',
      definition:{
        location:'local',
        require:{chat:true,tools:true},
        preferredLabels:['coding','coder','code'],
        preferredCapabilities:['reasoning'],
        preferLowerLatency:true,
        preferLargerContext:true,
        fallbackModelIds:['model_qwen35_local']
      }
    },
    {
      id:'policy_best_local_creative',
      versionId:'policyver_best_local_creative_v1',
      name:'Best Local Creative',
      description:'Prefer a local general model with creative/writer labels when available.',
      definition:{
        location:'local',
        require:{chat:true},
        preferredLabels:['creative','writer','story'],
        preferredCapabilities:['reasoning'],
        preferLowerLatency:true,
        preferLargerContext:true,
        fallbackModelIds:['model_qwen35_local']
      }
    },
    {
      id:'policy_tools_required',
      versionId:'policyver_tools_required_v1',
      name:'Tools Required',
      description:'Choose only a model/runtime combination proven to support chat and native tools.',
      definition:{
        location:'any',
        require:{chat:true,tools:true},
        preferredLabels:[],
        preferredCapabilities:['reasoning'],
        preferLowerLatency:true,
        preferLargerContext:true,
        fallbackModelIds:['model_qwen35_local']
      }
    },
    {
      id:'policy_vision_required',
      versionId:'policyver_vision_required_v1',
      name:'Vision Required',
      description:'Choose only a chat model declaring vision capability.',
      definition:{
        location:'any',
        require:{chat:true,vision:true},
        preferredLabels:['vision'],
        preferredCapabilities:['reasoning'],
        preferLowerLatency:true,
        preferLargerContext:true,
        fallbackModelIds:['model_qwen35_local']
      }
    },
    {
      id:'policy_best_available',
      versionId:'policyver_best_available_v1',
      name:'Best Available',
      description:'Choose the healthiest compatible model regardless of local/cloud location.',
      definition:{
        location:'any',
        require:{chat:true},
        preferredLabels:[],
        preferredCapabilities:['reasoning','tools'],
        preferLowerLatency:true,
        preferLargerContext:true,
        fallbackModelIds:['model_qwen35_local']
      }
    }
  ];

  db.transaction(() => {
    for (const p of defaults) {
      policyInsert.run(p.id,p.name,p.description,p.versionId,t,t);
      versionInsert.run(p.versionId,p.id,JSON.stringify(p.definition),t);
      db.prepare(`UPDATE model_policies SET current_version_id=?,updated_at=? WHERE id=?`)
        .run(p.versionId,t,p.id);
    }

    db.prepare(`
      INSERT INTO system_meta(key,value,updated_at)
      VALUES (?,?,?)
      ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at
    `).run(modelPolicySeedKey,'1',t);
  })();
}

const roleGrantSeedKey='default_role_grants_v081_seeded';
const roleGrantSeeded=db.prepare(`SELECT value FROM system_meta WHERE key=?`).get(roleGrantSeedKey) as any;

if(!roleGrantSeeded){
  const roleGrants:Record<string,string[]>={
    lead:['list_files','read_file','git_status','git_diff','run_tests'],
    product:['list_files','read_file'],
    architect:['list_files','read_file','git_status','git_diff'],
    ux:['list_files','read_file'],
    security:['list_files','read_file','git_status','git_diff','run_command'],
    reviewer:['list_files','read_file','git_status','git_diff','run_tests']
  };

  db.transaction(()=>{
    for(const [key,tools] of Object.entries(roleGrants)){
      const agentId=`agent_default_${key}`;
      const current:any=db.prepare(`
        SELECT a.current_version_id,av.*
        FROM agents a JOIN agent_versions av ON av.id=a.current_version_id
        WHERE a.id=?
      `).get(agentId);

      if(!current || current.version_no!==1 || current.created_by!=='system-seed')continue;

      const newVersionId=`agentver_default_${key}_v2_role_grants`;
      db.prepare(`
        INSERT OR IGNORE INTO agent_versions
        (id,agent_id,version_no,system_prompt,instructions,runtime_policy_json,model_policy_json,
         required_capabilities_json,tools_json,skills_json,memory_policy_json,permission_policy_json,
         autonomy_level,created_at,created_by)
        VALUES (?,?,2,?,?,?,?,?,?,?,?,?,?,?,'system-upgrade')
      `).run(
        newVersionId,agentId,
        current.system_prompt,current.instructions,current.runtime_policy_json,current.model_policy_json,
        current.required_capabilities_json,JSON.stringify(tools),current.skills_json,
        current.memory_policy_json,current.permission_policy_json,current.autonomy_level,t
      );

      db.prepare(`UPDATE agents SET current_version_id=?,updated_at=? WHERE id=? AND current_version_id=?`)
        .run(newVersionId,t,agentId,current.current_version_id);
    }

    db.prepare(`
      INSERT INTO system_meta(key,value,updated_at)
      VALUES (?,?,?)
      ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at
    `).run(roleGrantSeedKey,'1',t);
  })();
}

const communicationGrantSeedKey='default_communication_grants_v011_seeded';
const communicationGrantSeeded=db.prepare(`SELECT value FROM system_meta WHERE key=?`).get(communicationGrantSeedKey) as any;

if(!communicationGrantSeeded){
  const agents=db.prepare(`
    SELECT
      a.id AS agent_id,
      a.current_version_id AS agent_current_version_id,
      av.*
    FROM agents a
    JOIN agent_versions av ON av.id=a.current_version_id
    WHERE a.id LIKE 'agent_default_%'
  `).all() as any[];

  db.transaction(()=>{
    for(const current of agents){
      const tools=parseJson<string[]>(current.tools_json,[]);
      if(tools.includes('communication') || tools.includes('send_agent_message'))continue;

      // Do not mutate an immutable version. Create a new version only from
      // system-owned current versions. User-customized versions remain untouched.
      if(!String(current.created_by ?? '').startsWith('system-'))continue;

      const last:any=db.prepare(`SELECT MAX(version_no) n FROM agent_versions WHERE agent_id=?`).get(current.agent_id);
      const versionNo=Number(last?.n ?? 0)+1;
      const versionId=id('agentver');
      const upgradedTools=[...new Set([...tools,'communication'])];

      db.prepare(`
        INSERT INTO agent_versions
        (id,agent_id,version_no,system_prompt,instructions,runtime_policy_json,model_policy_json,
         required_capabilities_json,tools_json,skills_json,memory_policy_json,permission_policy_json,
         autonomy_level,created_at,created_by)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
      `).run(
        versionId,current.agent_id,versionNo,current.system_prompt,current.instructions,
        current.runtime_policy_json,current.model_policy_json,current.required_capabilities_json,
        JSON.stringify(upgradedTools),current.skills_json,current.memory_policy_json,
        current.permission_policy_json,current.autonomy_level,t,'system-upgrade-v0.11'
      );

      db.prepare(`UPDATE agents SET current_version_id=?,updated_at=? WHERE id=? AND current_version_id=?`)
        .run(versionId,t,current.agent_id,current.agent_current_version_id);
    }

    db.prepare(`
      INSERT INTO system_meta(key,value,updated_at)
      VALUES (?,?,?)
      ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at
    `).run(communicationGrantSeedKey,'1',t);
  })();
}

const agentFactorySeedKey='lead_agent_factory_grant_v012_seeded';
const agentFactorySeeded=db.prepare(`SELECT value FROM system_meta WHERE key=?`).get(agentFactorySeedKey) as any;

if(!agentFactorySeeded){
  const current:any=db.prepare(`
    SELECT
      a.id AS agent_id,
      a.current_version_id AS agent_current_version_id,
      av.*
    FROM agents a
    JOIN agent_versions av ON av.id=a.current_version_id
    WHERE a.id='agent_default_lead'
  `).get();

  db.transaction(()=>{
    if(current && String(current.created_by ?? '').startsWith('system-')){
      const tools=parseJson<string[]>(current.tools_json,[]);
      if(!tools.includes('agent_factory')){
        const last:any=db.prepare(`
          SELECT MAX(version_no) n FROM agent_versions WHERE agent_id=?
        `).get(current.agent_id);
        const versionNo=Number(last?.n ?? 0)+1;
        const versionId=id('agentver');
        const upgradedTools=[...new Set([...tools,'agent_factory'])];

        db.prepare(`
          INSERT INTO agent_versions
          (id,agent_id,version_no,system_prompt,instructions,runtime_policy_json,model_policy_json,
           required_capabilities_json,tools_json,skills_json,memory_policy_json,permission_policy_json,
           autonomy_level,created_at,created_by)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        `).run(
          versionId,current.agent_id,versionNo,current.system_prompt,current.instructions,
          current.runtime_policy_json,current.model_policy_json,current.required_capabilities_json,
          JSON.stringify(upgradedTools),current.skills_json,current.memory_policy_json,
          current.permission_policy_json,current.autonomy_level,t,'system-upgrade-v0.12'
        );

        db.prepare(`
          UPDATE agents SET current_version_id=?,updated_at=?
          WHERE id=? AND current_version_id=?
        `).run(versionId,t,current.agent_id,current.agent_current_version_id);
      }
    }

    db.prepare(`
      INSERT INTO system_meta(key,value,updated_at)
      VALUES (?,?,?)
      ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at
    `).run(agentFactorySeedKey,'1',t);
  })();
}
}

export function getSetting(key: string, fallback = ''): string {
  const row = db.prepare(`SELECT value FROM system_settings WHERE key=?`).get(key) as any;
  return row?.value ?? fallback;
}

export function setSetting(key: string, value: string) {
  db.prepare(`
    INSERT INTO system_settings(key,value,updated_at)
    VALUES (?,?,?)
    ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at
  `).run(key,value,now());
}

seed();
