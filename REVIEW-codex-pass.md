# Independent Codex review

Review date: 2026-08-29  
Review mode: separate Codex CLI session, `--ephemeral`, read-only sandbox  
Prompt requirement: `从零评估，不要复制 Claude 的分析框架。`

## Pass 1 — NO-GO

### 🔴 Blockers

1. `bookmark_digest.py:59` — commit accepted blank/unvalidated receipt fields, so processed state was not a machine-enforced receipt gate.
2. `bookmark_digest.py:33` — candidate identity accepted non-X URLs and malformed `/status/123suffix` values that could collide with a real tweet ID.
3. `bookmark_digest.py:260` — unbookmark DOM selection could match a quoted status link instead of the article's top-level tweet.
4. `bookmark_digest.py:265` — a click was immediately counted as removal without DOM/server/readback confirmation.
5. `bookmark_digest.py:124` — valid empty bookmarks and source/auth/schema failures could both collapse to an empty result.

### 🟡 Warnings

- Owned-target cleanup was not guaranteed if WebSocket creation/close failed.
- Pagination is driven by scroll heuristics rather than a direct cursor client.
- Partial unbookmark failure did not produce a non-zero CLI exit code.
- `collect` did not expose candidate IDs despite README wording.
- README omitted the sensitivity of bookmark text emitted on stdout.
- Mutation/error-path tests were too shallow.
- Linux setup was claimed but no Linux Chrome command was shown.

### 🟢 OK

- Mutation was concentrated in the unbookmark path rather than collect.
- GraphQL parsing did not recursively treat quoted status payloads as bookmarks.
- Local state used a `0600` write path and the public tree showed no credential/private-path literals.
- The documented basic command names matched argparse.

Pass-1 verdict: **NO-GO; Blockers = 5.**

## Remediation

The release candidate was revised to require a validated receipt artifact, strictly validate X status URLs, identify DOM rows through a primary status-linked timestamp anchor, require fresh readback before claiming removal, and fail closed on source/auth/schema uncertainty.

## Pass 2 — NO-GO

### 🔴 Blockers

1. Mutation authorization could still be forged by editing state with non-empty receipt fields; the gate did not re-open a canonical receipt artifact.
2. Fresh removal proof depended on bookmark-list pagination completeness, which scroll heuristics could not prove.
3. HTTP 200 GraphQL responses containing top-level `errors` or unknown tweet-entry shapes could still collapse to an empty healthy result.

### 🟡 Warnings

- DOM evidence for quoted/nested content was still mostly string-level rather than live-DOM evidence.
- WebSocket exceptions were not normalized into the JSON CLI error contract.
- A real `receipt.json` was not ignored by default.
- Tests did not yet cover GraphQL errors, stored-receipt tampering, or per-tweet fresh verification.

### 🟢 OK

- Strict X status URL identity blocker was closed.
- `commit` itself validated schema, accepted status, non-empty receipt ID/consumer, exact tweet identity, and SHA-256.
- Owned-target cleanup, dry-run ordering, non-zero controlled failures, README CLI syntax, and basic privacy checks were sound.

Pass-2 verdict: **NO-GO; Blockers = 3.**

## Pass 2 remediation

- State upgraded to v2.2. `commit` now copies the accepted receipt into a private state-adjacent `receipts/<candidate>.json`; mutation authorization re-validates that stored artifact, consumer, tweet identity, receipt ID, and SHA-256. Legacy/incomplete/tampered state is inert.
- Removal success no longer depends on proving bookmark-list pagination completeness. After a click and DOM transition, each clicked tweet is opened in a fresh read-only detail target and must expose `bookmark` rather than `removeBookmark` for its primary tweet.
- Bookmark GraphQL parsing now rejects top-level `errors`, unknown instruction/entry/tweet shapes, and any observed non-200 Bookmarks pagination response instead of converting them into an empty inbox.
- `receipt.json` and `receipts/` are ignored; WebSocket exceptions are normalized by the CLI; regression coverage increased to 14 tests.
- Live read-only evidence: the empty bookmark canary returned `health=ok`, one GraphQL page, and `inbox_empty=true`; a public tweet detail canary returned the target as unbookmarked (`bookmark` present, `removeBookmark` absent).

## Pass 3 — NO-GO

### 🔴 Blockers

1. State v2.2 + stored receipt/hash still had no independent trust root: a manually synchronized pair could authorize mutation.
2. DOM selectors did not explicitly exclude nested tweet descendants or bind fresh detail verification to the final page tweet ID.
3. Unknown instruction types and tweet `__typename` values could still be accepted instead of failing closed.
4. A late Bookmarks HTTP 429 arriving during response-body reads could escape the earlier non-200 check; pagination completeness was not represented as an enforceable mutation gate.

### 🟡 Warnings

- URL identity still accepted query/userinfo/Unicode digits.
- Mutation paths ignored collection truncation/completeness.
- Unexpected shape exceptions could escape the JSON CLI error contract.
- Regression coverage lacked the newly demonstrated counterexamples and exit-code paths.
- Custom receipt filenames were not broadly ignored; exit codes were under-documented.

Pass-3 verdict: **NO-GO; Blockers = 4.**

## Pass 3 remediation

- State is now v2.3. A separate 32-byte `.gate-key` (mode `0600`) HMAC-binds the canonical processed entry; state+receipt edits without the gate key are inert. The local operator/state-directory owner remains an explicit trusted boundary.
- DOM selection now filters `time` and bookmark controls by nearest tweet article ownership. Fresh detail verification also requires the final page URL tweet ID to equal the requested tweet before `bookmark` can prove success.
- X URLs are ASCII/host/path strict; GraphQL rejects unknown declared instruction types and tweet typenames.
- Collection drains late network events across stable rounds, re-checks non-200 statuses after body reads, tracks trailing Bottom cursor completeness, and marks incomplete results truncated. Mutation refuses any `complete=false` or truncated collection.
- CLI now normalizes unexpected exceptions to JSON/exit 2; custom `*.receipt.json` and `.gate-key` are ignored; docs specify exit codes and trust boundaries.
- Regression suite expanded to 17 tests. The exact late-429 counterexample now raises `SourceHealthError`; live read-only canaries return empty inbox `complete=true/truncated=false` and exact tweet detail `bookmark=true`.

## Pass 4 — GO

The scheduler/launchd release slice received a fresh independent Codex review after the v2.3 core remediation.

### Initial scheduler review — NO-GO

Four release blockers were found and fixed:

1. Consumer receipts were not yet bound to the exact IDs collected in the current scheduler cycle.
2. Core processed-state/receipt read-modify-write paths lacked a shared cross-process lock.
3. Raw consumer stderr could be forwarded into persistent launchd logs.
4. Launchd interpreter/consumer executable validation did not fully enforce the documented Python 3.10+ execution contract.

Remediation added current-cycle receipt prevalidation, current-cycle-only auto-unbookmark IDs, a shared `flock` around state/receipt operations, bounded scheduler error output, a verified Python 3.10+ launchd interpreter, normalized consumer executable paths, and dedicated scheduler regression tests.

### Final blocker re-review

The penultimate review reported one remaining blocker: an absolute-path consumer that existed but lacked execute permission could still produce a plist. `install_launchd.py` now checks `os.access(..., os.X_OK)` before any plist write; the exact non-executable-file counterexample was reproduced locally and rejected with `consumer executable is not executable`.

Final independent Codex verdict on HEAD `800ab7ef`:

- **Blockers: 0**
- **Warnings: None**
- Non-executable consumer path fails closed before plist write.
- Valid consumer executable is normalized to an absolute path.
- Python 3.10+ launchd probe remains enforced.
- README English/Chinese contract matches implementation.
- **GO**

Local release evidence before final review: core tests `20/20 PASS`, scheduler tests `3/3 PASS`, Python compilation PASS, open-source security preflight PASS, and a real read-only scheduler canary returned `idle / inbox_empty=true`.

Pass-4 verdict: **GO; Blockers = 0.**
