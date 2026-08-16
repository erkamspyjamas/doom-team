import { existsSync, unlinkSync } from 'node:fs';
import { loadConfig } from '../config.js';

const cfg = loadConfig();
if (existsSync(cfg.dbPath)) {
  unlinkSync(cfg.dbPath);
  console.log(`Deleted ${cfg.dbPath}`);
} else {
  console.log(`No DB at ${cfg.dbPath}`);
}
console.log('Restart Doom Core to recreate/seed the database.');
