export type DoomSkillManifest = {
  name: string;
  slug: string;
  version?: string;
  description?: string;
  requiredTools?: string[];
  recommendedCapabilities?: string[];
  tags?: string[];
  source?: string;
  metadata?: Record<string, unknown>;
};

export type DoomSkillPackageV1 = {
  format: 'doom-skill/v1';
  skill: DoomSkillManifest & { instructions?: string };
  files?: Record<string, string>;
};

export type RuntimeSkill = {
  skillId: string;
  skillVersionId: string;
  slug: string;
  name: string;
  description: string;
  versionNo: number;
  packageVersion: string;
  instructions: string;
  manifest: DoomSkillManifest;
  files: Record<string, string>;
  contentHash: string;
};
