# Doom pre-major-rework full snapshot

Created: 2026-08-24T04:26:24+03:00

Product repository:
erkamspyjamas/doom-team

Product HEAD:
944521be3af68c333df577adccdfa2874d641299

Product branch:
openclaw/autopilot

Code-only recovery branch:
backup/pre-major-rework-20260824-code

Full working-state recovery branch:
backup/pre-major-rework-20260824-full

This snapshot contains all discoverable non-ignored untracked
working-state files beneath the doom-core repository, including
diagnostic outputs, backups, WAL files, workspaces, and nested
repository files where safely readable.

Files matching credential/private-key heuristics were intentionally
excluded and are listed in SECRET_EXCLUDED.txt.

Non-regular or unreadable filesystem entries are listed in
SPECIAL_EXCLUDED.txt.

Restore:

  cat parts/untracked-working-state.tar.gz.part-* > /tmp/doom-untracked.tar.gz
  tar -xzf /tmp/doom-untracked.tar.gz -C /path/to/doom-core

Verify archive pieces:

  cd .pre-major-rework-snapshot
  sha256sum -c SHA256SUMS
