import { existsSync, realpathSync } from 'node:fs';
import { dirname, isAbsolute, relative, resolve, sep } from 'node:path';

function inside(root: string, candidate: string) {
  const rel = relative(root, candidate);
  return rel === '' || (!rel.startsWith('..' + sep) && rel !== '..' && !isAbsolute(rel));
}

export function realWorkspace(workspace: string) {
  if (!existsSync(workspace)) {
    throw new Error(`WORKSPACE_NOT_FOUND: ${workspace}`);
  }
  return realpathSync(workspace);
}

export function safeExistingPath(workspace: string, requestedPath: string) {
  const root = realWorkspace(workspace);
  const requested = requestedPath || '.';
  const lexical = resolve(root, requested);
  if (!inside(root, lexical)) {
    throw new Error('PATH_OUTSIDE_WORKSPACE');
  }
  if (!existsSync(lexical)) {
    throw new Error(`PATH_NOT_FOUND: ${requestedPath}`);
  }
  const actual = realpathSync(lexical);
  if (!inside(root, actual)) {
    throw new Error('SYMLINK_OUTSIDE_WORKSPACE');
  }
  return { root, path: actual };
}

export function safeWritePath(workspace: string, requestedPath: string) {
  if (!requestedPath || requestedPath === '.') {
    throw new Error('INVALID_WRITE_PATH');
  }
  const root = realWorkspace(workspace);
  const lexical = resolve(root, requestedPath);
  if (!inside(root, lexical)) {
    throw new Error('PATH_OUTSIDE_WORKSPACE');
  }

  let probe = dirname(lexical);
  while (!existsSync(probe)) {
    const parent = dirname(probe);
    if (parent === probe) break;
    probe = parent;
  }

  if (existsSync(probe)) {
    const actualParent = realpathSync(probe);
    if (!inside(root, actualParent)) {
      throw new Error('SYMLINK_OUTSIDE_WORKSPACE');
    }
  }

  if (existsSync(lexical)) {
    const actual = realpathSync(lexical);
    if (!inside(root, actual)) {
      throw new Error('SYMLINK_OUTSIDE_WORKSPACE');
    }
  }

  return { root, path: lexical };
}
