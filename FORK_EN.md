# What this fork changes

This is a community-maintained fork of [HKUDS/DeepTutor](https://github.com/HKUDS/DeepTutor), focused on personal self-hosted learning: selected-text speech, long-session context and caching, and guarded NAS upgrades. Upstream attribution, license and product documentation are retained. These changes are maintained here and do not imply upstream acceptance.

[简体中文完整说明](FORK.md) · [Compare with the baseline](https://github.com/FSOTM/deeptutor-private/compare/f7aed0744...io/nas-stable) · [Release checks](https://github.com/FSOTM/deeptutor-private/actions/workflows/nas-release.yml)

The comparison baseline is **upstream v1.7.1, reviewed November 1, 2026**. Later upstream releases may address the same issues.

## Changes visible to users

| Area | Change relative to the baseline |
| --- | --- |
| Selected-text speech | Select a word or passage in chat, Mastery Path or Reading, then click the nearby play button. Click again to stop. Selection alone never requests audio; speech does not create a teaching turn. |
| Stop, then send again | Wait for server-confirmed cancellation and released session ownership before enabling another send, including when Stop precedes receipt of the server turn ID. Preserve drafts typed while stopping. This addresses premature sends rejected with `Session already has an active or recovering turn`. |
| Prompt-cache continuity | Replay previously sent history without rewriting or removing its snapshots. Append new authoritative state instead of modifying the old request prefix. Actual cache hits still depend on the provider, routing and cache lifetime. |
| Incremental teaching state | Freeze representations only for newly added state messages. Send full, delta or unchanged state as appropriate; leave historical representations unchanged. Grading uses original authoritative data. Restore a full baseline from the archive if compaction or branching removes a delta's base. |
| Total-input compaction | An opt-in policy measures the final request, including instructions, tool schemas, history and current additions. It delays early history-only summarization until the configured total threshold is reached. Scope and configuration are below. |
| Responses tool compatibility | Tools without an explicit strict setting use `strict: false`, preserving optional arguments instead of allowing Responses defaults to make them required. Explicit strict settings remain intact. |
| Maintained NAS updates | Merge upstream stable releases with normal Git commits, test and approve builds, back up before switching, and roll back failures. There is no post-install source patcher. |

## Selected-text speech setup

The maintained NAS image includes `edge-tts==7.3.1`. Enable the Edge speech provider (`binding: edge`, `model: edge-tts`) in voice settings; no personal API key is needed. Frontend changes alone do not configure the backend service.

English uses Jenny (`en-US-JennyNeural`), Japanese Nanami (`ja-JP-NanamiNeural`), and Chinese Xiaoxiao (`zh-CN-XiaoxiaoNeural`). The workspace name helps resolve kanji-only Japanese selections. Automatic language detection can misidentify short or mixed-language text. This is listening playback, not pronunciation scoring or real-time voice tutoring.

Only one clip plays at a time. A browser-memory cache reuses up to 32 clips / 8 MiB, scoped by account, workspace, voice and exact text. It does not enter chat history. Edge speech requires network access and uses an online consumer service; permanent free availability is not guaranteed.

## Context policy: capacity, trigger and retained history

These are separate limits, not universal installation defaults. To opt in, set the backend environment variable:

```text
DEEPTUTOR_AUTO_COMPACT_TOKENS=500000
```

Explicitly configure the model's actual `context_window` as well; `1000000` is an example only when the deployed model/provider really supports it. The policy currently recognizes **`gpt-6.1-sol` and `gpt-6-astra`**. Without the variable, or for other model names, the existing history-budget behavior remains. The threshold must be positive and smaller than the configured model window.

| Example value | Meaning |
| --- | --- |
| 1,000,000 | Configured model window; must match actual provider capability |
| 500,000 | Estimated final input size that triggers compaction before each model call |
| 20,000 | Post-compaction budget for recent history and summary; current input, instructions and tools still take additional space |

Original conversation archives remain intact. Oversized current-turn tool results can be summarized in the request copy. Empty, ineffective or insufficient compaction fails visibly instead of sending an oversized request.

With this policy enabled, an OpenAI binding and a real session ID, the fork also supplies a stable opaque session-scoped `prompt_cache_key` for compatible gateways. **Neither this identity nor unchanged history guarantees cache hits. Compaction changes the prefix and can reset cache reuse.** The optional total-budget policy is separate from the fork's general history replay, state delta, tool compatibility and cancellation fixes.

## Updates and deployment scope

`io/nas-stable` holds maintained source; `io/nas-approved` holds metadata for checked releases. GitHub Actions checks upstream stable releases daily at 04:10 Asia/Shanghai. Merge conflicts and failed checks block publication. CI builds the maintained frontend, checks backend and rollback contracts, then publishes commit-specific frontend assets with size and SHA256 verification.

The NAS scheduler checks approved versions during 04:40–06:59 Asia/Shanghai and defers while learning/data access is active. Switching uses cold backups, isolated-copy checks and a loopback-only real-data startup check; failures restore the old service or cold backup.

This workflow targets the maintainer's **amd64 NAS deployment** and includes repository-specific addresses and host integration assumptions. It is not a universal one-click installer. Read [the NAS workflow](deploy/nas/README.md) and adapt it to your environment. Credentials and complete runtime configuration are not included.

## Verification and limits

- Cancellation fix [a47297961](https://github.com/FSOTM/deeptutor-private/commit/a47297961561879cf0c273ff76530a8d8d1d3f80): [CI run](https://github.com/FSOTM/deeptutor-private/actions/runs/41298302449) passed 105 frontend tests, 17 transport tests, 62 backend/update tests and a frontend build.
- The maintainer also ran real model conversations in an isolated learning session: Stop before and after server turn-ID receipt, then successfully send the next turn, retaining drafts. Controlled browser checks cover terminal events arriving before cancellation acknowledgement. This does not claim coverage of every edit, disconnect or recovery path.
- Selected English and Japanese passages were synthesized, decoded and played to completion in a browser; replay reuse and stopping were checked. Naturalness is a subjective listening assessment.
- [`deploy/nas/tests/`](deploy/nas/tests/) covers immutable history, lossless state reconstruction, full-state recovery after compaction, and threshold behavior. Real tutoring checks also exercised questions, grading and cache reuse. Observed cache rates in one deployment are not promises for other providers.

These are dated maintenance records. Automatic CI runs without an online model; it does not repeat live conversations or listening checks on every release. Private learner transcripts and raw deployment records are not published.

The repository contains source, tests and maintenance tooling. It does **not** include API keys, accounts, textbooks, teaching audio, knowledge-base indexes, learning progress, databases or private NAS settings. Import your own material and configure your own retrieval/model services. No free API allowance is bundled.

Upstream Apache-2.0 licensing and attribution are retained. This is not an official upstream release, and these changes have not been submitted as an upstream PR. The `-private` repository suffix is historical; the repository is public.
