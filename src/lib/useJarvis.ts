/**
 * Live JARVIS prototype that runs in the browser.
 *
 * This is the same control flow as the Python app, mapped onto web APIs so the
 * architecture can be *exercised* rather than merely described:
 *
 *   microphone -> band-energy onset detector -> clap pattern
 *     -> utterance capture (Web Speech API)      (Python: offline Faster-Whisper)
 *     -> router: controls -> regex tools -> demo replies OR authenticated Python API
 *     -> ToolRegistry + PermissionBroker (same default-deny rules)
 *     -> speechSynthesis                          (Python: pyttsx3/SAPI5)
 *
 * Deliberate limits, stated instead of faked:
 *   * no OS shell, no file system, no power control in a browser - those tools
 *     return the same refusal the Python permission broker would produce;
 *   * cross-origin page text cannot be read here, so `read_url` is honest about it;
 *   * Web Speech recognition MAY send audio to the browser vendor. The optional
 *     browser wake mode is off until the user explicitly switches it on.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

export type CommandTransport = (text: string) => Promise<{
  reply: string;
  source: string;
  sources?: { title: string; url: string }[];
}>;

export type Status =
  | "offline"
  | "idle"
  | "listening"
  | "transcribing"
  | "thinking"
  | "executing"
  | "speaking"
  | "error";

export type LogKind = "you" | "act" | "tool" | "done" | "warn" | "error" | "ok" | "grant";
export interface LogEntry {
  id: number;
  kind: LogKind;
  text: string;
  at: string;
}
export interface Turn {
  id: number;
  who: "YOU" | "JARVIS";
  text: string;
  tag: string;
  at: string;
  sources?: { title: string; url: string }[];
}
export type Capability =
  | "mic.listen"
  | "mic.clap"
  | "sites.open"
  | "apps.launch"
  | "net.search"
  | "notes.write"
  | "system.power";

export interface JarvisSettings {
  pattern: "single" | "double" | "triple";
  gate: number;
  sensitivity: number;
  refractoryMs: number;
  wakeEnabled: boolean;
  wakePhrase: string;
  voiceEnabled: boolean;
  rate: number;
  volume: number;
  voiceURI: string;
  offlineMode: boolean;
  grants: Record<Capability, boolean>;
}

export const ALLOWED_SITES = [
  "https://www.google.com",
  "https://www.youtube.com",
  "https://github.com",
  "https://stackoverflow.com",
  "https://duckduckgo.com",
  "https://ollama.com",
];

export const DEFAULT_SETTINGS: JarvisSettings = {
  pattern: "double",
  gate: 0.34,
  sensitivity: 1,
  refractoryMs: 1200,
  wakeEnabled: false,
  wakePhrase: "hello jarvis",
  voiceEnabled: true,
  rate: 1.02,
  volume: 0.9,
  voiceURI: "",
  offlineMode: false,
  grants: {
    "mic.listen": true,
    "mic.clap": true,
    "sites.open": false,
    "apps.launch": false,
    "net.search": false,
    "notes.write": false,
    "system.power": false,
  },
};

const now = () => new Date().toLocaleTimeString("en-GB", { hour12: false });
const clamp = (v: number, a: number, b: number) => Math.max(a, Math.min(b, v));

/* ------------------------------------------------------------------ clap DSP */
/**
 * Mirrors audio/clap_detector.py: gate on band energy, require a single-chunk
 * jump, reject sustained energy (speech/shouting), then require N hits inside a
 * window, then go quiet for the refractory period.
 */
export class ClapMachine {
  private noise: number[] = [];
  private onsets: number[] = [];
  private prev = 0;
  private sustained = 0;
  private candidateAt: number | null = null;
  private candidateGate = 0;
  private cooldownUntil = 0;
  accepted = 0;
  rejected = { "too-quiet": 0, "bad-gap": 0, refractory: 0, "speech-like": 0 };

  constructor(private cfg: JarvisSettings) {}

  update(cfg: JarvisSettings) {
    this.cfg = cfg;
  }

  /** energy: 0..1 mean band energy; returns true once per accepted pattern */
  process(energy: number, t: number): boolean {
    const hitsNeeded = this.cfg.pattern === "single" ? 1 : this.cfg.pattern === "triple" ? 3 : 2;
    const median = this.noise.length ? medianOf(this.noise) : 0;
    let gate = clamp(this.cfg.gate * this.cfg.sensitivity, 0.02, 0.95);
    gate = Math.max(gate, median * 1.8);

    const currentGate = this.candidateAt !== null ? this.candidateGate : gate;
    this.sustained = energy > currentGate ? (this.candidateAt !== null ? this.sustained + 1 : 1) : 0;
    const rising = energy > gate && this.prev <= gate && energy - this.prev > 0.35 * energy;
    this.prev = energy;
    this.noise.push(energy);
    if (this.noise.length > 60) this.noise.shift();

    if (t < this.cooldownUntil) {
      this.candidateAt = null;
      if (rising) this.rejected.refractory += 1;
      return false;
    }
    if (rising) {
      this.candidateAt = t;
      this.candidateGate = gate;
    }
    if (this.candidateAt !== null) {
      if (this.sustained > 6) {
        this.rejected["speech-like"] += 1;
        this.candidateAt = null;
      } else if (energy <= this.candidateGate) {
        const onset = this.candidateAt;
        this.candidateAt = null;
        const gap = this.onsets.length ? onset - this.onsets[this.onsets.length - 1] : null;
        if (gap !== null && gap < 90) this.rejected["bad-gap"] += 1;
        else {
          if (gap !== null && gap > 450) this.onsets = [];
          this.onsets.push(onset);
        }
      }
    }
    if (this.onsets.length >= hitsNeeded) {
      const tail = this.onsets.slice(-hitsNeeded);
      const gaps = tail.slice(1).map((v, i) => v - tail[i]);
      const tight = gaps.every((g) => g >= 90 && g <= 450);
      const span = tail[tail.length - 1] - tail[0];
      if (tight && span <= 450 * hitsNeeded) {
        this.accepted += 1;
        this.cooldownUntil = t + this.cfg.refractoryMs;
        this.onsets = [];
        return true;
      }
      if (!tight) this.rejected["bad-gap"] += 1;
    }
    return false;
  }

  stats() {
    return { accepted: this.accepted, rejected: { ...this.rejected }, floor: medianOf(this.noise.length ? this.noise : [0]) };
  }
}

function medianOf(list: number[]) {
  if (!list.length) return 0;
  const s = [...list].sort((a, b) => a - b);
  return s[Math.floor(s.length / 2)];
}

/* -------------------------------------------------------------------- router */
type Route =
  | { kind: "tool"; tool: string; args: Record<string, unknown>; say?: string; confirm?: boolean }
  | { kind: "chat"; say: string }
  | { kind: "control"; action: string; say: string };

const KNOWN_APPS = ["notepad", "calculator", "code", "terminal", "file explorer"];

export function routeUtterance(raw: string, s: JarvisSettings): Route {
  const text = raw.trim().toLowerCase().replace(/^jarvis[,!\s]+|^okay jarvis[,!\s]+/, "").replace(/[.?!]+$/, "");
  if (!text) return { kind: "control", action: "none", say: "I didn't catch that. Try again." };

  const control: [RegExp, string, string][] = [
    [/^(stop|cancel|never mind|shut up|quiet)$/, "interrupt", "Stopped."],
    [/^(mute|voice off)$/, "mute", "Voice output muted."],
    [/^(unmute|voice on|start talking)$/, "unmute", "Voice output on."],
    [/^(what can you do|help|list tools)$/, "help", ""],
    [/^(status|diagnostics|self test)$/, "status", ""],
  ];
  for (const [rx, action, say] of control) if (rx.test(text)) return { kind: "control", action, say };

  if (/\b(what|tell me).*(time)\b/.test(text) || /^time$/.test(text))
    return { kind: "chat", say: `It's ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}.` };
  if (/\b(date|day is it)\b/.test(text))
    return { kind: "chat", say: `Today is ${new Date().toLocaleDateString([], { weekday: "long", day: "numeric", month: "long", year: "numeric" })}.` };

  const openSite = text.match(/^(?:open|go to|launch)\s+((?:https?:\/\/)?[a-z0-9.-]+\.[a-z]{2,}(?:\/\S*)?)$/);
  if (openSite) return { kind: "tool", tool: "open_website", args: { url: withScheme(openSite[1]) } };
  if (/\bopen\s+(youtube|google|github|stackoverflow|duckduckgo|ollama)\b/.test(text)) {
    const name = text.match(/(youtube|google|github|stackoverflow|duckduckgo|ollama)/)![1];
    return { kind: "tool", tool: "open_website", args: { url: `https://www.${name === "ollama" ? "ollama.com" : name + ".com"}` } };
  }
  if (/\b(open|launch|start|run)\s+([a-z ._-]{2,30})$/.test(text)) {
    const name = (text.match(/\b(?:open|launch|start|run)\s+([a-z ._-]{2,30})$/) || [])[1]?.replace(/\b(app|application|program)\b/g, "").trim() || "";
    if (KNOWN_APPS.includes(name)) return { kind: "tool", tool: "launch_app", args: { name } };
  }

  const note = text.match(/^(?:make|write|create|add)\s+a\s+note\s*(?::|that|saying)?\s*(.+)$/);
  if (note) return { kind: "tool", tool: "write_note", args: { body: note[1] } };
  if (/\b(read|show|list)\s+(my\s+)?(latest\s+)?notes\b/.test(text)) return { kind: "tool", tool: "read_notes", args: {} };
  if (/\b(clear|delete|forget)\s+(my\s+|all\s+)?(notes|memory|history)\b/.test(text))
    return { kind: "tool", tool: "clear_memory", args: {}, confirm: true };
  if (/\b(show|what.*(stored|memory)|inspect memory)\b/.test(text)) return { kind: "tool", tool: "show_memory", args: {} };

  const search = text.match(/^(?:search|google|look up|look for|find on the web)\s+(?:for\s+|up\s+)?(.+)$/) || text.match(/\bsearch\s+(?:the\s+web\s+)?for\s+(.+)$/);
  if (search && !s.offlineMode) return { kind: "tool", tool: "web_search", args: { query: search[1] } };
  if (search && s.offlineMode) return { kind: "chat", say: "Offline mode is on, so I won't touch the network. In the Python app this exact branch returns 'Offline mode is enabled.'" };

  if (/\b(ram|cpu|memory|how much ram|system stats|specs)\b/.test(text)) return { kind: "tool", tool: "system_stats", args: {} };
  if (/\b(shut ?down|turn off|power off|restart|reboot)\b/.test(text))
    return { kind: "tool", tool: "system_power", args: { action: /restart|reboot/.test(text) ? "restart" : "shutdown", delay_seconds: 60 }, confirm: true };
  if (/\bsummar\w*\s+(this\s+)?(page|url|link)\b/.test(text)) return { kind: "tool", tool: "read_url", args: { url: (text.match(/https?:\/\/\S+/) || [""])[0] } };

  return { kind: "chat", say: localBrain(text) };
}

function withScheme(url: string) {
  return /^https?:\/\//.test(url) ? url : `https://${url}`;
}

/* --------------------------------------------------------- offline mini brain */
const KB: [RegExp, string][] = [
  [/^(hi|hey|hello|yo|good (morning|afternoon|evening))\b/, "在线 Ready. Ear is %EAR%. Ask me for a self test, or say 'open youtube'."],
  [/ollama/, "The Python app talks to Ollama over http://127.0.0.1:11434 with qwen2.5:1.5b (about 1.2-1.6 GB resident). If the model is missing it says so instead of guessing: `ollama pull qwen2.5:1.5b`."],
  [/(ram|memory|8 ?gb|8 gb)/, "On 8 GB: Whisper tiny.en ~250 MB, qwen2.5:1.5b ~1.5 GB, PySide6 ~90 MB, Chromium ~150 MB. That fits with one browser window closed. Avoid small.en and 3b+ models."],
  [/(whisper|transcri|speech to text|stt)/, "Faster-Whisper runs locally with int8 compute, beam size 1 and VAD trimming — chosen because it stays under ~250 MB and needs no GPU. Hinglish works via language=hi with a Latin-script prompt."],
  [/(clap|activation|wake)/, "Clap detection is band-pass onset + a pattern window (90-450 ms between hits) + a refractory period, and sustained energy is rejected so speech never counts as a clap. The unit tests in tests/test_clap_detector.py cover all of that."],
  [/(secur|permission|prompt injection|danger)/, "Default-deny capability broker: nothing runs until you grant it. Model arguments are type-checked, paths must resolve inside an allowed root, URLs must match the site allow-list, and page text is wrapped as untrusted <source> data."],
  [/(gemini|cloud|api key|internet)/, "Gemini is opt-in: a key in .env plus ALLOW_CLOUD=1. 429/quota/timeout all fall back to Ollama, and the key is never written to config.json, the DB or the log."],
  [/(test|tested|verified)/, "111 Python tests pass in the Linux sandbox. npm ci, strict TypeScript and the Vite build also passed. Real Windows microphone, SAPI, Ollama and browser UI still need testing on your laptop."],
  [/(ui|pyside|qt|interface|dashboard)/, "The desktop UI is PySide6: a painted audio orb at 20 fps, a state badge, transcript cards, an activity log, and all blocking work on QThreads so the window never freezes."],
  [/(install|setup|powershell|pip|venv)/, "winget Python 3.12 → py -3.12 -m venv .venv → .\\.venv\\Scripts\\Activate.ps1 → pip install -r requirements.txt → python main.py --check → ollama pull qwen2.5:1.5b → python main.py --text."],
  [/(note|notes)/, "Notes are markdown files with front-matter under data/workspace/notes, and delete means move-to-.trash, never rm."],
  [/(shutdown|restart|power)/, "In the desktop app that schedules `shutdown /s /t 60` after your confirmation, so it is cancellable with `shutdown /a`. A browser cannot do that - this prototype only shows the confirmation flow."],
  [/(voice|tts|speak|accent)/, "Output is Windows SAPI5 via pyttsx3 - functional, interruptible, and not the Iron Man voice. Anyone promising you a cinematic voice on a ₹0 budget is selling you something."],
  [/(sqlite|memory|database|store)/, "SQLite in data/jarvis.db holds turns, summaries, facts, permissions and doc chunks, with a redaction pass on every write and a retention purge on startup."],
  [/(browser|playwright)/, "Playwright Chromium is used only when a page needs JavaScript, headless, downloads disabled, and closed after use. Static pages go through a plain HTTP fetch + BeautifulSoup."],
  [/who are you|what are you/, "JARVIS, local-first: microphone, transcripts, model and memory all stay on your laptop. This panel is the browser prototype of the same pipeline."],
];

function localBrain(text: string): string {
  for (const [rx, answer] of KB) if (rx.test(text)) return answer.replace("%EAR%", "on");
  return (
    "I answered that with the browser demo's rule-based brain, which only knows this project. " +
    "The real assistant answers anything with qwen2.5:1.5b through Ollama — start it and the same router " +
    "hands 'chat' to the model instead of to me."
  );
}

/* ---------------------------------------------------------------- persistence */
const NOTES_KEY = "jarvis.demo.notes";
export interface Note {
  title: string;
  body: string;
  at: string;
}
export const loadNotes = (): Note[] => {
  try {
    return JSON.parse(localStorage.getItem(NOTES_KEY) || "[]") as Note[];
  } catch {
    return [];
  }
};
const saveNotes = (notes: Note[]) => localStorage.setItem(NOTES_KEY, JSON.stringify(notes));

/* ------------------------------------------------------------------------ hook */
let idSeq = 1;

export interface JarvisApi {
  status: Status;
  detail: string;
  level: number;
  bars: number[];
  settings: JarvisSettings;
  setSettings: (patch: Partial<JarvisSettings>) => void;
  earOn: boolean;
  startEar: () => void;
  stopEar: () => void;
  talk: () => void;
  interrupt: () => void;
  turns: Turn[];
  logs: LogEntry[];
  voices: SpeechSynthesisVoice[];
  clapStats: { accepted: number; rejected: Record<string, number>; floor: number };
  pendingConfirm: { summary: string; capability: string } | null;
  answerConfirm: (ok: boolean) => void;
  submit: (text: string) => boolean;
  setTransport: (transport: CommandTransport | null) => void;
  support: { speechRecognition: boolean; tts: boolean; mic: string };
}

export function useJarvis(): JarvisApi {
  const [settings, setSettingsState] = useState<JarvisSettings>(DEFAULT_SETTINGS);
  const [status, setStatus] = useState<Status>("offline");
  const [detail, setDetail] = useState("microphone off");
  const [level, setLevel] = useState(0);
  const [bars, setBars] = useState<number[]>(() => new Array(56).fill(0));
  const [turns, setTurns] = useState<Turn[]>([]);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([]);
  const [earOn, setEarOn] = useState(false);
  const [clapStats, setClapStats] = useState({ accepted: 0, rejected: {} as Record<string, number>, floor: 0 });
  const [pendingConfirm, setPendingConfirm] = useState<{ summary: string; capability: string } | null>(null);

  const ctxRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const rafRef = useRef(0);
  const clapRef = useRef(new ClapMachine(DEFAULT_SETTINGS));
  const recRef = useRef<any>(null);
  const busyRef = useRef(false);
  const confirmResolver = useRef<((v: boolean) => void) | null>(null);
  const armedRef = useRef(0);
  const paintRef = useRef(0);
  const settingsRef = useRef(settings);
  settingsRef.current = settings;
  const transportRef = useRef<CommandTransport | null>(null);
  const wakeFailedRef = useRef(false);

  const setTransport = useCallback((transport: CommandTransport | null) => {
    transportRef.current = transport;
  }, []);

  const log = useCallback((kind: LogKind, text: string) => {
    setLogs((prev) => [{ id: idSeq++, kind, text, at: now() }, ...prev].slice(0, 200));
  }, []);

  const pushTurn = useCallback((who: Turn["who"], text: string, tag: string, sources?: Turn["sources"]) => {
    setTurns((prev) => [...prev, { id: idSeq++, who, text, tag, at: now(), sources }].slice(-60));
  }, []);

  const setSettings = useCallback((patch: Partial<JarvisSettings>) => {
    setSettingsState((prev) => {
      const next = { ...prev, ...patch };
      clapRef.current.update(next);
      if (patch.voiceEnabled === false) window.speechSynthesis?.cancel();
      return next;
    });
  }, []);

  /* --------------------------------------------------------------- TTS ---- */
  useEffect(() => {
    const synth = window.speechSynthesis;
    if (!synth) return;
    const load = () => setVoices(synth.getVoices());
    load();
    synth.onvoiceschanged = load;
    return () => {
      synth.onvoiceschanged = null;
      synth.cancel();
    };
  }, []);

  const speak = useCallback((text: string) => {
    const s = settingsRef.current;
    const synth = window.speechSynthesis;
    if (!synth || !s.voiceEnabled || !text) return;
    synth.cancel();
    const u = new SpeechSynthesisUtterance(text.replace(/```|https?:\/\/\S+/g, "").replace(/\s+/g, " ").slice(0, 700));
    const voice = synth.getVoices().find((v) => v.voiceURI === s.voiceURI);
    if (voice) {
      u.voice = voice;
      u.lang = voice.lang;
    }
    u.rate = s.rate;
    u.volume = s.volume;
    u.onstart = () => setStatus("speaking");
    u.onend = () => setStatus(earOnRef.current ? "idle" : "offline");
    u.onerror = () => setStatus(earOnRef.current ? "idle" : "offline");
    synth.speak(u);
  }, []);

  const earOnRef = useRef(false);
  earOnRef.current = earOn;

  const askConfirm = useCallback((summary: string, capability: string) => {
    return new Promise<boolean>((resolve) => {
      confirmResolver.current = resolve;
      setPendingConfirm({ summary, capability });
    });
  }, []);
  const answerConfirm = useCallback((ok: boolean) => {
    setPendingConfirm(null);
    const r = confirmResolver.current;
    confirmResolver.current = null;
    r?.(ok);
  }, []);

  /* ------------------------------------------------------- audio + claps --- */
  const frame = useCallback(() => {
    const analyser = analyserRef.current;
    if (!analyser || !ctxRef.current) return;
    const bins = new Uint8Array(analyser.frequencyBinCount);
    analyser.getByteFrequencyData(bins);
    const rate = ctxRef.current.sampleRate;
    const binHz = rate / 2 / bins.length;
    const lo = Math.max(1, Math.floor(800 / binHz));
    const hi = Math.min(bins.length - 1, Math.floor(4000 / binHz));
    let band = 0;
    for (let i = lo; i <= hi; i++) band += bins[i];
    const energy = band / ((hi - lo + 1) * 255);
    let all = 0;
    for (let i = 0; i < bins.length; i++) all += bins[i];
    const levelNow = all / (bins.length * 255);

    // Paint at ~20 fps, not 60: the audio loop must never starve the UI thread
    // and a 56-bar SVG does not need 60 Hz. (Same reasoning as ui/visualizer.py.)
    const t = performance.now();
    if (t - paintRef.current > 48) {
      paintRef.current = t;
      setLevel((prev) => Math.max(levelNow, prev * 0.86));
      setBars((prev) => [...prev.slice(1), energy]);
    }

    const fired = clapRef.current.process(energy, performance.now());
    if (fired && settingsRef.current.grants["mic.clap"] && performance.now() > armedRef.current) {
      armedRef.current = performance.now() + 2500;
      log("act", `clap pattern accepted (${settingsRef.current.pattern})`);
      triggerCapture("clap");
    }
    if (Math.random() < 0.02) setClapStats(clapRef.current.stats() as any);
    rafRef.current = requestAnimationFrame(frame);
  }, [log]);

  /* ------------------------------------------------- speech recognition ----- */
  const SR: any = typeof window !== "undefined" ? (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition : undefined;

  const stopWake = () => {
    const r = recRef.current;
    if (r && r.__mode === "wake") {
      try {
        r.abort();
      } catch {
        /* ignore */
      }
      recRef.current = null;
    }
  };

  const startWake = useCallback(() => {
    if (!SR || recRef.current || !settingsRef.current.wakeEnabled || !earOnRef.current || wakeFailedRef.current) return;
    const rec = new SR();
    rec.__mode = "wake";
    rec.lang = "en-US";
    rec.continuous = true;
    rec.interimResults = true;
    rec.onresult = (ev: any) => {
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const t = String(ev.results[i][0].transcript || "").trim().toLowerCase();
        if (!t) continue;
        const want = settingsRef.current.wakePhrase.trim().toLowerCase();
        const hit = t.includes(want) || t.includes("jarvis") || levenshtein(t.split(" ").slice(-2).join(" "), want) <= 4;
        if (hit && performance.now() > armedRef.current) {
          armedRef.current = performance.now() + 2500;
          log("act", `wake phrase matched: "${t}"`);
          triggerCapture("wake-word");
        }
      }
    };
    rec.onerror = (ev: any) => {
      if (ev?.error === "not-allowed") {
        wakeFailedRef.current = true;
        setStatus("error");
        setDetail("microphone permission denied");
        log("error", "speech recognition blocked by the browser");
      } else if (ev?.error === "network" || ev?.error === "service-not-allowed") {
        wakeFailedRef.current = true;
        log("warn", "browser wake recognition unavailable; clap and typed input still work");
      }
    };
    rec.onend = () => {
      if (recRef.current === rec) recRef.current = null;
      if (earOnRef.current && !busyRef.current && !wakeFailedRef.current) setTimeout(startWake, 400);
    };
    try {
      rec.start();
      recRef.current = rec;
      } catch {
        wakeFailedRef.current = true;
        log("warn", "browser wake recognition could not start; use claps or the text box");
    }
  }, [SR, log]);

  const triggerCapture = useCallback(
    (source: string) => {
      if (busyRef.current) return;
      if (!SR) {
        setStatus("error");
        setDetail("this browser has no speech recognition - use the text box");
        return;
      }
      busyRef.current = true;
      stopWake();
      window.speechSynthesis?.cancel();
      setStatus("listening");
      setDetail(`activated by ${source} — speak now`);
      log("act", `listening (${source})`);
      const rec = new SR();
      rec.__mode = "capture";
      rec.lang = /hi|hinglish/.test(settingsRef.current.wakePhrase) ? "en-IN" : "en-US";
      rec.interimResults = true;
      rec.continuous = false;
      rec.maxAlternatives = 1;
      let final = "";
      let interim = "";
      const timer = window.setTimeout(() => {
        try {
          rec.stop();
        } catch {
          /* ignore */
        }
      }, 9000);
      rec.onresult = (ev: any) => {
        interim = "";
        for (let i = ev.resultIndex; i < ev.results.length; i++) {
          const r = ev.results[i];
          if (r.isFinal) final += r[0].transcript;
          else interim += r[0].transcript;
        }
        setDetail(final ? `heard: ${final}` : `listening… ${interim}`);
      };
      rec.onerror = (ev: any) => {
        window.clearTimeout(timer);
        log("warn", `capture: ${ev?.error || "error"}`);
      };
      rec.onend = () => {
        window.clearTimeout(timer);
        if (recRef.current !== rec) return; // stopEar() aborted the session
        if (recRef.current === rec) recRef.current = null;
        const text = (final || interim).trim();
        busyRef.current = false;
        armedRef.current = 0;
        if (!text) {
          setStatus(earOnRef.current ? "idle" : "offline");
          setDetail(earOnRef.current ? "no speech detected" : "microphone off");
          log("warn", "empty recording — nothing transcribed");
          if (earOnRef.current) setTimeout(startWake, 200);
          return;
        }
        runCommand(text, "voice");
        if (earOnRef.current) setTimeout(startWake, 400);
      };
      recRef.current = rec;
      try {
        rec.start();
      } catch {
        busyRef.current = false;
      }
    },
    [SR, log, startWake],
  );

  /* ----------------------------------------------------------- execution --- */
  const runCommand = useCallback(
    async (text: string, origin: "voice" | "typed") => {
      busyRef.current = true;
      const s = settingsRef.current;
      pushTurn("YOU", text, origin === "voice" ? "voice → Web Speech API" : "typed");
      log("you", text);
      setStatus("thinking");
      setDetail("routing");
      const t0 = performance.now();
      const transport = transportRef.current;
      if (transport) {
        setDetail("waiting for local Python backend");
        try {
          const response = await transport(text);
          finish(response.reply, `Python · ${response.source}`, t0, response.sources);
        } catch (reason) {
          const message = reason instanceof Error ? reason.message : "Python backend did not answer.";
          log("error", message);
          pushTurn("JARVIS", `Backend error: ${message}\nReconnect or disconnect to use the browser demo.`, "backend · error");
          busyRef.current = false;
          setStatus("error");
          setDetail("backend unavailable — see the connection panel");
        }
        return;
      }
      const route = routeUtterance(text, s);
      await new Promise((r) => setTimeout(r, 180 + Math.random() * 260));

      if (route.kind === "chat") {
        finish(route.say, "chat · local rule-brain", t0);
        return;
      }
      if (route.kind === "control") {
        if (route.action === "interrupt") {
          window.speechSynthesis?.cancel();
          log("warn", "interrupted");
          finish("Stopped.", "control", t0);
          return;
        }
        if (route.action === "mute") {
          setSettings({ voiceEnabled: false });
          finish(route.say, "control", t0);
          return;
        }
        if (route.action === "unmute") {
          setSettings({ voiceEnabled: true });
          finish(route.say, "control", t0);
          return;
        }
        if (route.action === "status") {
          const st = clapRef.current.stats() as any;
          finish(
            `Ear ${earOnRef.current ? "live" : "off"} · claps accepted ${st.accepted} · rejected ${JSON.stringify(st.rejected)} · noise floor ${(st.floor * 100).toFixed(1)} · mic ${navigator.mediaDevices ? "API ok" : "unsupported"}`,
            "control · diagnostics",
            t0,
          );
          return;
        }
        if (route.action === "help") {
          finish(
            "Try: 'open youtube' · 'make a note that ...' · 'read my notes' · 'search the web for transformers' · 'system stats' · 'what is tested?' · 'how much RAM does it need?' · 'shut down the laptop' to see the confirmation gate.",
            "control · help",
            t0,
          );
          return;
        }
        finish(route.say, "control", t0);
        return;
      }

      // tool route: permission gate first (same order as tools/registry.py)
      const cap = capabilityFor(route.tool);
      if (cap && !s.grants[cap]) {
        log("warn", `denied ${route.tool}: capability ${cap} not granted`);
        finish(`Not allowed: ${cap} is not granted. Flip it on in the Permissions panel — that is exactly the check in security/permissions.py.`, "broker · deny", t0);
        return;
      }
      if (route.confirm) {
        log("grant", `confirmation requested for ${route.tool}`);
        const ok = await askConfirm(`${route.tool}(${JSON.stringify(route.args)})`, cap || "confirm");
        if (!ok) {
          finish("Cancelled — you did not confirm that action.", "broker · denied", t0);
          return;
        }
      }
      setStatus("executing");
      setDetail(`executing ${route.tool}`);
      log("tool", `${route.tool} ${JSON.stringify(route.args)}`);
      const result = executeTool(route.tool, route.args, s);
      finish(result.text, `tool · ${route.tool}${result.tag ? " · " + result.tag : ""}`, t0, result.sources);
    },
    [askConfirm, finish, log, pushTurn, setSettings],
  );

  function finish(text: string, tag: string, t0: number, sources?: Turn["sources"]) {
    busyRef.current = false;
    pushTurn("JARVIS", text, `${tag} · ${((performance.now() - t0) / 1000).toFixed(2)}s`, sources);
    log("done", text.slice(0, 140));
    setStatus(earOnRef.current ? "idle" : "offline");
    setDetail(earOnRef.current ? "standby" : "microphone off");
    speak(text);
    if (earOnRef.current && settingsRef.current.wakeEnabled) window.setTimeout(startWake, 300);
  }

  const submit = useCallback(
    (text: string) => {
      if (!text.trim()) return false;
      if (busyRef.current) {
        log("warn", "Finish or stop the current command before sending another.");
        return false;
      }
      void runCommand(text, "typed");
      return true;
    },
    [runCommand, log],
  );

  /* ------------------------------------------------------------- ear ctrl -- */
  const startEar = useCallback(async () => {
    if (!settingsRef.current.grants["mic.listen"]) {
      setStatus("error");
      setDetail("grant microphone access in the Permissions panel first");
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia) {
      setStatus("error");
      setDetail("no mediaDevices API (needs https or localhost)");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: false },
      });
      const AC = window.AudioContext || (window as any).webkitAudioContext;
      const ctx = new AC();
      ctxRef.current = ctx;
      streamRef.current = stream;
      const src = ctx.createMediaStreamSource(stream);
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 1024;
      analyser.smoothingTimeConstant = 0.55;
      src.connect(analyser);
      analyserRef.current = analyser;
      earOnRef.current = true;
      wakeFailedRef.current = false;
      setEarOn(true);
      setStatus("idle");
      setDetail("standby — clap or say the wake phrase");
      log("ok", `ear live · ${stream.getAudioTracks()[0]?.label || "default device"} · clap pattern: ${settingsRef.current.pattern}`);
      rafRef.current = requestAnimationFrame(frame);
      startWake();
    } catch (err: any) {
      streamRef.current?.getTracks().forEach((track) => track.stop());
      void ctxRef.current?.close().catch(() => undefined);
      streamRef.current = null;
      ctxRef.current = null;
      setStatus("error");
      setDetail(`microphone unavailable: ${err?.name || err}`);
      log("error", `getUserMedia failed: ${err?.message || err}`);
    }
  }, [frame, log, startWake]);

  const stopEar = useCallback(() => {
    earOnRef.current = false;
    cancelAnimationFrame(rafRef.current);
    try { recRef.current?.abort(); } catch { /* recognition may already be stopped */ }
    recRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    ctxRef.current?.close().catch(() => undefined);
    analyserRef.current = null;
    ctxRef.current = null;
    streamRef.current = null;
    setEarOn(false);
    setStatus("offline");
    setDetail("microphone off");
    setLevel(0);
    log("warn", "ear stopped — device released");
  }, [log]);

  const talk = useCallback(() => {
    armedRef.current = 0;
    triggerCapture("button");
  }, [triggerCapture]);

  const interrupt = useCallback(() => {
    window.speechSynthesis?.cancel();
    setStatus(earOn ? "idle" : "offline");
    setDetail("interrupted");
    log("warn", "stop requested");
  }, [earOn, log]);

  useEffect(() => {
    if (earOn && settings.wakeEnabled) startWake();
    if (!settings.wakeEnabled) stopWake();
  }, [earOn, settings.wakeEnabled, startWake]);
  useEffect(() => () => stopEar(), [stopEar]);

  const support = useMemo(
    () => ({
      speechRecognition: Boolean(SR),
      tts: typeof window !== "undefined" && "speechSynthesis" in window,
      mic: typeof navigator !== "undefined" && typeof navigator.mediaDevices?.getUserMedia === "function" ? "available" : "unavailable",
    }),
    [SR],
  );

  return {
    status,
    detail,
    level,
    bars,
    settings,
    setSettings,
    earOn,
    startEar: () => void startEar(),
    stopEar,
    talk,
    interrupt,
    turns,
    logs,
    voices,
    clapStats,
    pendingConfirm,
    answerConfirm,
    submit,
    setTransport,
    support,
  };
}

/* --------------------------------------------------------------- tool impl */
function capabilityFor(tool: string): Capability | null {
  const map: Record<string, Capability> = {
    open_website: "sites.open",
    web_search: "net.search",
    read_url: "net.search",
    launch_app: "apps.launch",
    write_note: "notes.write",
    read_notes: "notes.write",
    clear_memory: "notes.write",
    show_memory: "notes.write",
    system_power: "system.power",
  };
  return map[tool] ?? null;
}

function executeTool(tool: string, args: Record<string, unknown>, _s: JarvisSettings): { text: string; tag?: string; sources?: { title: string; url: string }[] } {
  switch (tool) {
    case "open_website": {
      const url = String(args.url || "");
      const host = hostOf(url);
      const allowed = ALLOWED_SITES.some((site) => host === hostOf(site) || host.endsWith("." + hostOf(site)));
      if (!allowed) return { text: `I can't open that: '${host}' is not in the allowed site list. Add it in Settings → Tools & Permissions.`, tag: "check_url → deny" };
      window.open(url, "_blank", "noopener,noreferrer");
      return { text: `Opening ${url}. The desktop app hands the same decision to AppLauncher.open_url after the identical allow-list check.`, tag: "allow" };
    }
    case "launch_app":
      return {
        text: `In the desktop app 'launch_app' resolves '${args.name}' against your allow-list and calls subprocess.Popen(argv, shell=False). A browser sandbox cannot start .exe files, so this prototype stops at the permission gate — on purpose, not as a stub.`,
        tag: "browser limit",
      };
    case "web_search": {
      const q = encodeURIComponent(String(args.query || "").replace(/[^\w \-.,?]/g, "").slice(0, 160));
      const url = `https://duckduckgo.com/?q=${q}`;
      window.open(url, "_blank", "noopener,noreferrer");
      return {
        text: `Search is opened in a new tab for "${args.query}". The desktop version reads the result text back (ddgs, no API key), then asks the model to summarise it with the URLs attached — that read step needs a real HTTP client, not a browser CORS sandbox.`,
        tag: "ddgs fallback path",
        sources: [{ title: `DuckDuckGo: ${args.query}`, url }],
      };
    }
    case "read_url":
      return {
        text: "Cross-origin page text can't be read from a web page (CORS), so this prototype can't summarise URLs. In the desktop app Playwright renders the page, BeautifulSoup strips it, and the text is wrapped as untrusted <source> data before the model sees it.",
        tag: "browser limit",
      };
    case "write_note": {
      const body = String(args.body || "").trim();
      const note: Note = { title: body.slice(0, 42).replace(/\b\w/g, (c) => c.toUpperCase()), body, at: now() };
      const notes = [note, ...loadNotes()].slice(0, 50);
      saveNotes(notes);
      return { text: `Note saved: "${note.title}". The desktop app writes data/workspace/notes/<stamp>-<slug>.md with front-matter.`, tag: "fs.write · workspace" };
    }
    case "read_notes": {
      const notes = loadNotes();
      if (!notes.length) return { text: "You have no notes yet — say \"make a note that ...\" first.", tag: "fs.read" };
      return { text: notes.slice(0, 5).map((n, i) => `${i + 1}. [${n.at}] ${n.body}`).join("\n"), tag: `${notes.length} notes` };
    }
    case "show_memory": {
      const notes = loadNotes();
      return {
        text:
          `Browser demo memory: ${notes.length} note(s) in localStorage; this session's turns stay in React state only.\n` +
          `The desktop app uses data/jarvis.db: turns, summaries, facts, permissions and doc chunks, with a redaction pass on every write and retention purge on boot.`,
        tag: "memory inspector",
      };
    }
    case "clear_memory": {
      saveNotes([]);
      return { text: "Notes cleared from this browser. In the desktop app the same tool calls MemoryDB.clear_conversation() — notes and files are never touched.", tag: "confirmed" };
    }
    case "system_stats": {
      const nav = navigator as any;
      const mem = (performance as any).memory;
      const lines = [
        `CPU threads: ${nav.hardwareConcurrency ?? "n/a"}`,
        `Device memory: ${nav.deviceMemory ? `${nav.deviceMemory} GB (rounded)` : "n/a (Chrome only)"}`,
        `JS heap: ${mem ? `${(mem.usedJSHeapSize / 1e6).toFixed(0)} MB used` : "n/a"}`,
        `Platform: ${nav.userAgentData?.platform ?? navigator.platform}`,
        `Page load: ${Math.round(performance.now())} ms since navigation`,
      ];
      return { text: lines.join("\n"), tag: "live · browser" };
    }
    case "system_power":
      return {
        text: `Confirmed. The desktop app would now run: shutdown /s /t 60  (cancellable with shutdown /a). A web page has no power authority, and I will not fake it.`,
        tag: "allow-listed argv, shell=False",
      };
    default:
      return { text: `I don't have a tool called '${tool}'.` };
  }
}

function hostOf(url: string) {
  try {
    return new URL(withScheme(url)).hostname.toLowerCase();
  } catch {
    return "";
  }
}

function levenshtein(a: string, b: string) {
  if (!a) return b.length;
  if (!b) return a.length;
  const prev = new Array(b.length + 1).fill(0).map((_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    let last = prev[0];
    prev[0] = i;
    for (let j = 1; j <= b.length; j++) {
      const cur = prev[j];
      prev[j] = Math.min(prev[j] + 1, prev[j - 1] + 1, last + (a[i - 1] === b[j - 1] ? 0 : 1));
      last = cur;
    }
  }
  return prev[b.length];
}
