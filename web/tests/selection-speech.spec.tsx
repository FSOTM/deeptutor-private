import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { PlayAudioButton, selectionVoice } from "@/components/voice/SpeechButton";
import SelectionActions from "@/features/chat/components/SelectionActions";

const mock = vi.hoisted(() => ({ fetch: vi.fn(), notify: vi.fn(), audio: [] as MockAudio[] }));
vi.mock("@/lib/api", () => ({ apiUrl: (url: string) => url, apiFetch: (...args: unknown[]) => mock.fetch(...args) }));
vi.mock("@/lib/notifications", () => ({ notify: mock.notify }));
vi.mock("@/hooks/useVoiceAutoplay", () => ({ useVoiceAutoplay: () => ({ autoplayEnabled: false }) }));
vi.mock("@/lib/workspaces-api", () => ({ listWorkspaces: async () => [{ workspace_id: "japanese", display_name: "日语" }] }));
vi.mock("@/lib/workspace-scope", () => ({ activeWorkspaceId: () => "japanese" }));
vi.mock("@/lib/recovery-principal", () => ({ recoveryPrincipal: () => "test-user" }));
vi.mock("@/components/chat/home/ResponderList", () => ({ default: () => null }));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (s: string) => s }) }));

class MockAudio {
  src = "";
  onended: (() => void) | null = null;
  onerror: (() => void) | null = null;
  play = vi.fn(async () => undefined);
  pause = vi.fn();
  load = vi.fn();
  constructor(src: string) { this.src = src; mock.audio.push(this); }
}

beforeEach(() => {
  mock.fetch.mockReset(); mock.notify.mockReset(); mock.audio.length = 0;
  vi.stubGlobal("Audio", MockAudio);
  vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: vi.fn(() => "blob:speech"), revokeObjectURL: vi.fn() }));
  mock.fetch.mockResolvedValue({ ok: true, blob: async () => new Blob(["test audio"], { type: "audio/mpeg" }) });
});

describe("selected-text speech", () => {
  it("chooses the native language, including kanji-only selections", () => {
    expect(selectionVoice("Although she was tired.", "日语")).toBe("en-US-JennyNeural");
    expect(selectionVoice("すみません")).toBe("ja-JP-NanamiNeural");
    expect(selectionVoice("学校", "日语")).toBe("ja-JP-NanamiNeural");
    expect(selectionVoice("请解释一下", "英语")).toBe("zh-CN-XiaoxiaoNeural");
  });

  it("only submits the selected text, never starts a chat turn, and preserves selection", async () => {
    const ask = vi.fn(); const quote = vi.fn();
    render(<SelectionActions text="Only this sentence." workspaceName="英语" anchor={{ left: 40, right: 140, top: 180, bottom: 200 }} agents={[]} onAsk={ask} onAddToChat={quote} onDismiss={vi.fn()} />);
    const button = screen.getByRole("button", { name: "Read aloud" });
    expect(fireEvent.pointerDown(button)).toBe(false);
    fireEvent.click(button);
    await waitFor(() => expect(mock.fetch).toHaveBeenCalledTimes(1));
    const [url, init] = mock.fetch.mock.calls[0];
    expect(url).toBe("/api/voice/tts");
    expect(JSON.parse(init.body)).toEqual({ text: "Only this sentence.", voice: "en-US-JennyNeural" });
    expect(ask).not.toHaveBeenCalled(); expect(quote).not.toHaveBeenCalled();
    await screen.findByRole("button", { name: "Stop" });
  });

  it("replays cached audio after completion without another synthesis", async () => {
    render(<PlayAudioButton content="Replay this particular selection." autoPlayFresh={false} selection />);
    fireEvent.click(screen.getByRole("button", { name: "Read aloud" }));
    await waitFor(() => expect(mock.audio[0].src).toBe("blob:speech"));
    await act(async () => mock.audio[0].onended?.());
    fireEvent.click(screen.getByRole("button", { name: "Read aloud" }));
    await waitFor(() => expect(mock.audio).toHaveLength(2));
    await waitFor(() => expect(mock.audio[1].src).toBe("blob:speech"));
    expect(mock.fetch).toHaveBeenCalledTimes(1);
  });

  it("stops pending synthesis and ignores a late response", async () => {
    let resolve!: (response: unknown) => void;
    mock.fetch.mockImplementation(() => new Promise(r => { resolve = r; }));
    render(<PlayAudioButton content="Cancel this pending request." autoPlayFresh={false} selection />);
    fireEvent.click(screen.getByRole("button", { name: "Read aloud" }));
    await waitFor(() => expect(mock.fetch).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    expect(mock.fetch.mock.calls[0][1].signal.aborted).toBe(true);
    await act(async () => resolve({ ok: true, blob: async () => new Blob(["late"]) }));
    expect(mock.audio[0].src).not.toBe("blob:speech");
    expect(mock.notify).not.toHaveBeenCalled();
  });

  it("stops the old selection when another selection or full-message audio starts", async () => {
    render(<><PlayAudioButton content="First selection." autoPlayFresh={false} selection /><PlayAudioButton content="Whole message." autoPlayFresh={false} /></>);
    fireEvent.click(screen.getByRole("button", { name: "Read aloud" }));
    await waitFor(() => expect(mock.audio[0].src).toBe("blob:speech"));
    fireEvent.click(screen.getByRole("button", { name: "Play aloud" }));
    await waitFor(() => expect(mock.audio).toHaveLength(2));
    expect(mock.audio[0].pause).toHaveBeenCalled();
    await waitFor(() => expect(mock.audio[1].src).toBe("blob:speech"));
  });

  it("reports provider errors instead of silently pretending to play", async () => {
    mock.fetch.mockResolvedValue({ ok: false, status: 502, json: async () => ({ detail: "Speech service unavailable" }) });
    render(<PlayAudioButton content="Provider error selection." autoPlayFresh={false} selection />);
    fireEvent.click(screen.getByRole("button", { name: "Read aloud" }));
    await waitFor(() => expect(mock.notify).toHaveBeenCalledWith("Speech service unavailable", { tone: "error" }));
    expect(screen.getByRole("button", { name: "Read aloud" })).toBeVisible();
  });
});
