"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Loader2, Play, Square, Volume2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import Tooltip from "@/shared/ui/Tooltip";
import { apiFetch, apiUrl } from "@/lib/api";
import { notify } from "@/lib/notifications";
import { useVoiceAutoplay } from "@/hooks/useVoiceAutoplay";
import { SPEECH_PLAYBACK_FAILURE_MESSAGE, SPEECH_TIMEOUT_MESSAGE } from "@/lib/voice-settings";
import { activeWorkspaceId } from "@/lib/workspace-scope";
import { listWorkspaces } from "@/lib/workspaces-api";
import { recoveryPrincipal } from "@/lib/recovery-principal";

const selectionAudio = new Map<string, Blob>();
const MAX_CACHE_BYTES = 8 * 1024 * 1024;

export function selectionVoice(text: string, workspaceName = ""): string {
  if (/[\u3040-\u30ff\uff66-\uff9f]/u.test(text)) return "ja-JP-NanamiNeural";
  if (/[a-z]/i.test(text)) return "en-US-JennyNeural";
  if (/日语|日本語|japanese/i.test(workspaceName)) return "ja-JP-NanamiNeural";
  return "zh-CN-XiaoxiaoNeural";
}

async function selectedVoice(text: string, workspaceName?: string): Promise<string> {
  if (workspaceName !== undefined || /[a-z\u3040-\u30ff\uff66-\uff9f]/iu.test(text))
    return selectionVoice(text, workspaceName);
  const rows = await listWorkspaces();
  return selectionVoice(text, rows.find(row => row.workspace_id === activeWorkspaceId())?.display_name);
}

function cacheSelection(key: string, blob: Blob) {
  if (blob.size > MAX_CACHE_BYTES) return;
  selectionAudio.delete(key);
  selectionAudio.set(key, blob);
  let bytes = [...selectionAudio.values()].reduce((sum, row) => sum + row.size, 0);
  while (selectionAudio.size > 32 || bytes > MAX_CACHE_BYTES) {
    const first = selectionAudio.entries().next().value;
    if (!first) break;
    bytes -= first[1].size;
    selectionAudio.delete(first[0]);
  }
}

// Speaker button: synthesizes this one reply and plays it. Auto-play of later
// replies is a Settings preference (`autoPlayFresh`), not a first-click prompt.
let activePlayback: { owner: object; stop: () => void } | null = null;
// 50 ms of playable unsigned 8-bit PCM silence; an empty WAV cannot unlock playback.
const SILENT_UNLOCK_WAV =
  "data:audio/wav;base64,UklGRrQBAABXQVZFZm10IBAAAAABAAEAQB8AAEAfAAABAAgAZGF0YZABAACAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICA";

async function ttsErrorMessage(resp: Response): Promise<string> {
  try {
    const body = (await resp.json()) as { detail?: unknown };
    if (typeof body.detail === "string" && body.detail.trim()) return body.detail;
    if (Array.isArray(body.detail)) {
      const first = body.detail[0] as { msg?: string } | undefined;
      if (typeof first?.msg === "string" && first.msg.trim()) return first.msg;
    }
  } catch {
    /* non-JSON error body */
  }
  return "";
}

export function PlayAudioButton({
  content,
  conversationKey,
  autoPlayFresh,
  selection = false,
  workspaceName,
  className,
}: {
  content: string;
  conversationKey?: string;
  autoPlayFresh: boolean;
  selection?: boolean;
  workspaceName?: string;
  className?: string;
}) {
  const { t } = useTranslation();
  const { autoplayEnabled } = useVoiceAutoplay(conversationKey);
  const [state, setState] = useState<"idle" | "loading" | "playing">("idle");
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const urlRef = useRef<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const genRef = useRef(0);
  const autoPlayedRef = useRef(false);
  const playbackOwnerRef = useRef<object>({});

  const cleanup = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
    }
    if (urlRef.current) {
      URL.revokeObjectURL(urlRef.current);
      urlRef.current = null;
    }
  }, []);

  const stop = useCallback(() => {
    genRef.current += 1;
    cleanup();
    setState("idle");
    if (activePlayback?.owner === playbackOwnerRef.current) activePlayback = null;
  }, [cleanup]);

  const play = useCallback(async () => {
    activePlayback?.stop();
    const gen = ++genRef.current;
    abortRef.current?.abort();
    const ac = new AbortController();
    abortRef.current = ac;
    activePlayback = { owner: playbackOwnerRef.current, stop };
    setState("loading");

    // WebKit evaluates media playback eligibility when play() is called. Keep
    // this element unlocked in the click task, then replace its source after
    // synthesis completes rather than constructing a new audio element then.
    const audio = new Audio(SILENT_UNLOCK_WAV);
    audioRef.current = audio;
    void Promise.resolve(audio.play()).catch(() => {
      /* Unlocking playback can fail on clients that already permit it. */
    });

    try {
      const voice = selection ? await selectedVoice(content, workspaceName) : undefined;
      if (gen !== genRef.current) return;
      const cacheKey = JSON.stringify([recoveryPrincipal(), activeWorkspaceId(), voice, content]);
      let blob = selection ? selectionAudio.get(cacheKey) : undefined;
      if (!blob) {
        const resp = await apiFetch(apiUrl("/api/voice/tts"), {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text: content, ...(voice ? { voice } : {}) }),
          signal: ac.signal,
        });
        if (gen !== genRef.current) return;
        if (!resp.ok) {
          const detail = await ttsErrorMessage(resp);
          if (gen !== genRef.current) return;
          notify(
            t(detail || (resp.status === 504 ? SPEECH_TIMEOUT_MESSAGE : SPEECH_PLAYBACK_FAILURE_MESSAGE)),
            { tone: "error" },
          );
          stop();
          return;
        }
        blob = await resp.blob();
        if (!blob.size) throw new Error("Speech returned empty audio");
        if (selection && !ac.signal.aborted) cacheSelection(cacheKey, blob);
      }
      if (gen !== genRef.current) return;
      if (urlRef.current) {
        URL.revokeObjectURL(urlRef.current);
        urlRef.current = null;
      }
      const url = URL.createObjectURL(blob);
      urlRef.current = url;
      audio.src = url;
      audio.load();
      audio.onended = () => {
        if (gen !== genRef.current) return;
        setState("idle");
        cleanup();
        if (activePlayback?.owner === playbackOwnerRef.current) activePlayback = null;
      };
      audio.onerror = () => {
        if (gen !== genRef.current) return;
        if (selection) selectionAudio.delete(cacheKey);
        notify(t(SPEECH_PLAYBACK_FAILURE_MESSAGE), {
          tone: "error",
        });
        stop();
      };
      await audio.play();
      if (gen !== genRef.current) {
        audio.pause();
        return;
      }
      setState("playing");
    } catch (err) {
      if (gen !== genRef.current) return;
      if (err instanceof Error && err.name === "AbortError") return;
      notify(t(SPEECH_PLAYBACK_FAILURE_MESSAGE), {
        tone: "error",
      });
      stop();
    }
  }, [cleanup, content, selection, workspaceName, stop, t]);

  const handleClick = useCallback(() => {
    if (state === "playing" || state === "loading") {
      stop();
      return;
    }
    void play();
  }, [play, state, stop]);

  // Auto-play a freshly-generated reply when Settings auto-play is on.
  useEffect(() => {
    if (!autoPlayFresh || !autoplayEnabled) return;
    if (autoPlayedRef.current) return;
    if (!content.trim()) return;
    autoPlayedRef.current = true;
    const id = window.setTimeout(() => void play(), 0);
    return () => window.clearTimeout(id);
  }, [autoPlayFresh, autoplayEnabled, content, play]);

  useEffect(() => () => {
    genRef.current += 1;
    cleanup();
    if (activePlayback?.owner === playbackOwnerRef.current) activePlayback = null;
  }, [cleanup]);

  return (
    <div className={`relative inline-flex ${selection ? "h-full" : ""}`}>
      <Tooltip
        label={state !== "idle" ? t("Stop") : t(selection ? "Read aloud" : "Play aloud")}
        side="top"
      >
        <button
          type="button"
          onClick={handleClick}
          aria-label={state !== "idle" ? t("Stop") : t(selection ? "Read aloud" : "Play aloud")}
          aria-busy={state === "loading"}
          className={className ?? `inline-flex items-center justify-center rounded-md p-1 transition-colors ${
            state === "playing"
              ? "text-[var(--primary)]"
              : "text-[var(--muted-foreground)] hover:bg-[var(--muted)]/50 hover:text-[var(--foreground)]"
          }`}
        >
          {state === "loading" ? (
            <Loader2 size={15} strokeWidth={1.8} className="animate-spin" />
          ) : state === "playing" ? (
            <Square size={13} strokeWidth={1.8} className="fill-current" />
          ) : selection ? (
            <Play size={15} strokeWidth={1.8} className="fill-current" />
          ) : (
            <Volume2 size={15} strokeWidth={1.5} />
          )}
        </button>
      </Tooltip>
    </div>
  );
}
