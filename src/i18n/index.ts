import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

export type LocaleMessages = Record<string, any>;

const here = dirname(fileURLToPath(import.meta.url));
const localesDir = join(here, '..', '..', 'locales');
const cache = new Map<string, LocaleMessages>();

export function loadLocale(code: string): LocaleMessages {
  const safe = /^[a-zA-Z0-9_-]+$/.test(code) ? code : 'en';
  const cached = cache.get(safe);
  if (cached) return cached;
  try {
    const parsed = JSON.parse(readFileSync(join(localesDir, `${safe}.json`), 'utf-8'));
    cache.set(safe, parsed);
    return parsed;
  } catch {
    if (safe !== 'en') return loadLocale('en');
    return {};
  }
}

export function availableLocales() {
  return readdirSync(localesDir)
    .filter(x => x.endsWith('.json'))
    .map(file => {
      const code = file.slice(0, -5);
      const messages = loadLocale(code);
      return {
        code,
        label: messages?._meta?.label ?? code,
        nativeLabel: messages?._meta?.nativeLabel ?? code,
      };
    })
    .sort((a, b) => a.code.localeCompare(b.code));
}

export function localeExists(code: string) {
  return availableLocales().some(x => x.code === code);
}

export function localeInstruction(code: string) {
  switch (code) {
    case 'tr': return 'Respond in Turkish. Use correct Turkish characters and natural Turkish.';
    case 'en': return 'Respond in English.';
    default: return `Respond using locale/language code "${code}" when possible.`;
  }
}
