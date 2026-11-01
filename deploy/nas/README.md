# Private NAS release workflow

This is the maintained source tree. Changes to context policy, session caching,
incremental mastery state and Responses tools are normal Git commits, merged
with upstream. There is no post-install patch program or source-file bind mount.

- Upstream: `HKUDS/DeepTutor`; private source branch: `io/nas-stable`.
- Daily at 04:10 Asia/Shanghai, GitHub Actions merges the latest stable upstream
  release locally. Conflicts stop the job before publication.
- The matching official amd64 image is locked by manifest/config digests and
  upstream source revision. The unchanged official frontend/dependencies are
  reused; the image contains this repository's complete Python package. A local
  frontend/dependency change requires a different build recipe and is rejected.
- Edit/resend/card tests run against the matching frontend source. Context,
  immutable history, incremental state, compaction, Responses tools and rollback
  contracts run in the resulting image without an online model.
- Only successful jobs publish approval metadata on `io/nas-approved`. Source
  and approval history are preserved; no update is pushed to the public project.
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
