# Maintained NAS release workflow

For user-facing changes, configuration and verification limits, see
[What this fork changes](../../FORK_EN.md) / [与原版的区别](../../FORK.md).
This repository is public; its historical `-private` name is retained. Runtime
settings, learner data and credentials remain private.

This is the maintained source tree. Changes to context policy, session caching,
incremental mastery state and Responses tools are normal Git commits, merged
with upstream. There is no post-install patch program or source-file bind mount.

- Upstream: `HKUDS/DeepTutor`; maintained source branch: `io/nas-stable`.
- Daily at 04:10 Asia/Shanghai, GitHub Actions merges the latest stable upstream
  release locally. Conflicts stop the job before publication.
- The matching official amd64 image is locked by manifest/config digests and
  upstream source revision. Python dependencies are reused; the image contains
  this repository's complete Python package and CI-built standalone frontend.
  Frontend assets are published as a public, commit-specific GitHub prerelease;
  their SHA256 and size are recorded in approval metadata. The NAS verifies the
  asset and safely extracts it before building, without running Node compilation.
  Python dependency/CLI changes still require explicit build-recipe review.
- Edit/resend/card tests run against the matching frontend source. Context,
  immutable history, incremental state, compaction, Responses tools and rollback
  contracts run in the resulting image without an online model.
- Only successful jobs publish approval metadata on `io/nas-approved`. Source
  and approval history are preserved; no update is pushed to the upstream project.
- NAS authenticates with a read-only deploy key scoped to this repository.
  Account-wide GitHub credentials never enter the NAS or Docker build.

## NAS operation

The updater fetches the approved source, builds the same digest-based image and
runs its tests. It does not run a separate patcher. Normal scheduled execution
is 04:40–06:59 Asia/Shanghai, with a 15-minute retry when learning/data access is
active. A native exclusive activity lock closes the check/stop race.

Before switching it closes the dedicated browser entry, stops only DeepTutor,
makes a cold data/config backup, and starts an isolated data copy. Existing
messages, request/model history, answers, grades and settings are checked by
hash; original text and keys are not written to reports. Window 1,000,000,
compaction trigger 500,000 and retained-history budget 20,000 are verified.

The first start on real data is loopback-only. After migration and preservation
checks pass, the normal private-network port is restored. A failed candidate
reopens the old service. A failed real-data migration restores the cold copy;
the failed data tree is retained for diagnosis. Backups are kept under the
root-only `.updates/backups/` directory. The updater defers when free disk is
below 12 GiB; it does not delete unrelated images or backups.

The host startup hook is `private-deeptutor-update.sh`; it starts a small local
scheduler and does not depend on Codex or a logged-in Windows session.

Operational facts:

- `.updates/installed.json`: installed source/image and rollback location.
- `.updates/last-result.json`: last successful preparation/update result.
- `.updates/daemon.log`: scheduler, deferred attempts and failures.
- `deploy/private-updater/updater.py --daemon-status`: scheduler status.

Private settings, study material, databases, SSH keys and runtime logs stay
outside this Git repository. The deployment's original `data/` remains its
persistent runtime home.

## Selected text speech

Selecting a word or sentence in chat or the reading pane exposes a play button
on the existing nearby selection toolbar. It uses the configured speech service
through the existing `/api/voice/tts` endpoint, without sending a chat turn or
modifying learning history. English selections use Jenny; Japanese uses Nanami,
with the workspace name resolving kanji-only selections. Chinese explanations
use Xiaoxiao. Set the active TTS provider to `edge` / model `edge-tts` without a
user API key. The Docker image pins `edge-tts==7.3.1`.

Selection speech never auto-plays. A second click stops loading or playback;
selecting different text disposes the old player. Decoded responses are reused
from a browser-memory cache bounded to 32 selections / 8 MiB, scoped by account,
workspace, voice and exact selected text. Cache entries do not enter Git, chat
context or server logs. Whole-message playback retains its existing behavior
and shares the one-player-at-a-time boundary with selection playback.
