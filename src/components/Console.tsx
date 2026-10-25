import { useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  Hand,
  Mic,
  MicOff,
  Radar,
  Send,
  ShieldCheck,
  SlidersHorizontal,
  Volume2,
  VolumeX,
  AudioWaveform,
} from "lucide-react";
import { ALLOWED_SITES, type Capability, type JarvisApi, type Status } from "@/lib/useJarvis";
import type { BackendApi } from "@/lib/useBackend";
import { Chip } from "@/lib/ui";
import BackendPanel from "@/components/BackendPanel";

const TONE: Record<Status, { color: string; label: string; note: string }> = {
  offline: { color: "#5b6b80", label: "OFFLINE", note: "ear stopped — nothing is being captured" },
  idle: { color: "#38bdf8", label: "STANDBY", note: "listening for the activation pattern" },
  listening: { color: "#22d3ee", label: "LISTENING", note: "speak now" },
  transcribing: { color: "#a3e635", label: "TRANSCRIBING", note: "decoding audio" },
  thinking: { color: "#fbbf24", label: "THINKING", note: "routing the request" },
  executing: { color: "#f97316", label: "EXECUTING", note: "running a tool" },
  speaking: { color: "#60a5fa", label: "SPEAKING", note: "voice output — say stop to interrupt" },
  error: { color: "#f87171", label: "ERROR", note: "recoverable — see the log" },
};

function safeSourceUrl(value: string): string | null {
  try {
    const parsed = new URL(value);
    const host = parsed.hostname.toLowerCase();
    if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password ||
        host === "localhost" || host.endsWith(".localhost") || host.startsWith("[") ||
        /^(127\.|10\.|192\.168\.|169\.254\.|0\.)/.test(host) ||
        /^172\.(1[6-9]|2\d|3[01])\./.test(host)) return null;
    return parsed.href;
  } catch {
    return null;
  }
}

/* ------------------------------------------------------------------ the orb */
function Orb({ status, level, bars, active }: { status: Status; level: number; bars: number[]; active: boolean }) {
  const tone = TONE[status];
  const R = 82;
  const N = bars.length;
  const paths = useMemo(
    () =>
      bars.map((v, i) => {
        const a = (i / N) * Math.PI * 2 - Math.PI / 2;
        const inner = R + 6;
        const outer = inner + 4 + v * 46;
        return {
          x1: 130 + Math.cos(a) * inner,
          y1: 130 + Math.sin(a) * inner,
          x2: 130 + Math.cos(a) * outer,
          y2: 130 + Math.sin(a) * outer,
          o: 0.25 + Math.min(1, v * 2.1) * 0.75,
        };
      }),
    [bars, N],
  );
  return (
    <div className="relative select-none" style={{ width: 260, height: 260 }}>
      <div
        className="absolute inset-0 rounded-full transition-all duration-700"
        style={{ background: `radial-gradient(circle, ${tone.color}${active ? "2e" : "18"} 0%, transparent 68%)`, filter: "blur(6px)" }}
      />
      {active ? <div className="absolute inset-6 rounded-full pulse-ring" style={{ border: `1px solid ${tone.color}55` }} /> : null}
      <svg viewBox="0 0 260 260" className="absolute inset-0">
        <g className={active ? "spin-slow" : ""} style={{ transformOrigin: "130px 130px" }}>
          <circle cx="130" cy="130" r="118" fill="none" stroke={tone.color} strokeOpacity="0.28" strokeWidth="1" strokeDasharray="2 9" />
        </g>
        <circle cx="130" cy="130" r="104" fill="none" stroke="#16233a" strokeWidth="1" />
        <circle cx="130" cy="130" r={R + 4} fill="#081120" stroke={tone.color} strokeOpacity="0.35" strokeWidth="1" />
        {paths.map((p, i) => (
          <line key={i} x1={p.x1} y1={p.y1} x2={p.x2} y2={p.y2} stroke={tone.color} strokeOpacity={p.o} strokeWidth="2.6" strokeLinecap="round" />
        ))}
        <circle cx="130" cy="130" r={R * (0.4 + level * 0.14)} fill="none" stroke={tone.color} strokeOpacity="0.55" strokeWidth="1.2" />
        <text x="130" y="126" textAnchor="middle" fill="#e8f2ff" style={{ font: "700 13px 'Chakra Petch',sans-serif", letterSpacing: "3px" }}>
          {TONE[status].label}
        </text>
        <text x="130" y="146" textAnchor="middle" fill={tone.color} style={{ font: "500 10px 'JetBrains Mono',monospace", letterSpacing: "1.5px" }}>
          {active ? `${Math.round(level * 100)}% IN` : "MIC OFF"}
        </text>
      </svg>
    </div>
  );
}

/* --------------------------------------------------------------- legend */
function StateLegend({ status }: { status: Status }) {
  return (
    <div className="w-full flex flex-wrap gap-1.5">
      {(Object.keys(TONE) as Status[]).map((s) => (
        <span
          key={s}
          className={`px-1.5 py-[2px] rounded border font-mono text-[9px] tracking-wider ${
            status === s ? "border-ice/60 text-fg bg-white/[0.04]" : "border-line2 text-dim"
          }`}
          title={TONE[s].note}
        >
          <span className="mr-1" style={{ color: TONE[s].color }}>
            ●
          </span>
          {TONE[s].label}
        </span>
      ))}
    </div>
  );
}

/* -------------------------------------------------------------- sub-widgets */
function Slider({
  label,
  value,
  min,
  max,
  step,
  suffix = "",
  onChange,
  hint,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  suffix?: string;
  onChange: (v: number) => void;
  hint?: string;
}) {
  return (
    <label className="block group">
      <div className="flex items-baseline justify-between mb-1.5">
        <span className="text-[10px] font-mono tracking-[0.14em] text-dim group-hover:text-mut transition-colors">{label}</span>
        <span className="font-mono text-[11px] text-ice tabular-nums">
          {value.toFixed(step < 1 ? 2 : 0)}
          {suffix}
        </span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full h-[3px] appearance-none rounded-full bg-line2 accent-cyan cursor-pointer
                   [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3 [&::-webkit-slider-thumb]:h-3
                   [&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-cyan
                   [&::-webkit-slider-thumb]:shadow-[0_0_10px_rgba(34,211,238,0.8)]"
      />
      {hint ? <p className="mt-1.5 text-[10px] leading-snug text-dim/80">{hint}</p> : null}
    </label>
  );
}

function Segmented<T extends string>({ options, value, onChange }: { options: readonly T[]; value: T; onChange: (v: T) => void }) {
  return (
    <div className="inline-flex border border-line2 rounded overflow-hidden">
      {options.map((o) => (
        <button
          key={o}
          type="button"
          onClick={() => onChange(o)}
          className={`px-2.5 py-1 font-mono text-[10px] tracking-wider transition-colors ${
            value === o ? "bg-cyan/15 text-cyan" : "text-dim hover:text-mut hover:bg-white/[0.03]"
          }`}
        >
          {o.toUpperCase()}
        </button>
      ))}
    </div>
  );
}

const CAPS: { key: Capability; label: string; desc: string }[] = [
  { key: "mic.listen", label: "microphone", desc: "capture audio for clap + wake detection" },
  { key: "mic.clap", label: "clap activation", desc: "pattern detector on the shared stream" },
  { key: "sites.open", label: "open allow-listed sites", desc: "6 domains, editable in the Python app" },
  { key: "net.search", label: "web search", desc: "needs internet; falls back to a browser tab" },
  { key: "notes.write", label: "notes", desc: "write to the workspace folder only" },
  { key: "apps.launch", label: "launch apps", desc: "off by default — desktop app only" },
  { key: "system.power", label: "power control", desc: "off by default, always confirms per call" },
];

/* --------------------------------------------------------------- console --- */
export default function Console({ j, backend }: { j: JarvisApi; backend: BackendApi }) {
  const [text, setText] = useState("");
  const scroller = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const tone = TONE[j.status];
  const active = j.status !== "offline" && j.status !== "error";

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  }, [j.turns]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLElement && ["INPUT", "TEXTAREA"].includes(e.target.tagName)) {
        if (e.key === "Escape") (e.target as HTMLInputElement).blur();
        return;
      }
      if (e.code === "Space" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        j.talk();
      } else if (e.key === "F2") {
        e.preventDefault();
        j.earOn ? j.stopEar() : j.startEar();
      } else if (e.key === "Escape") j.interrupt();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [j]);

  const suggestions = [
    "what can you do",
    "open youtube",
    "make a note that the lab report is due Friday",
    "system stats",
    "how much RAM does this need",
    "is any of this actually tested",
    "shut down the laptop",
    "search the web for faster-whisper",
  ];

  return (
    <div className="grid lg:grid-cols-[minmax(0,340px)_minmax(0,1fr)] gap-4">
      <BackendPanel backend={backend} />
      {/* ---------------------------------------------------- left: instrument */}
      <div className="panel corner p-5 flex flex-col items-center gap-4 overflow-hidden">
        <div className="absolute inset-x-0 -top-24 h-48 radar opacity-40 pointer-events-none" />
        <Orb status={j.status} level={j.level} bars={j.bars} active={active} />
        <p className="text-center text-[11.5px] leading-snug text-mut min-h-[32px]">
          <span style={{ color: tone.color }}>{tone.label}</span> · {j.detail || tone.note}
        </p>

        <div className="w-full h-[3px] rounded-full bg-line overflow-hidden">
          <div
            className="h-full rounded-full transition-[width] duration-100"
            style={{ width: `${Math.min(100, j.level * 130)}%`, background: `linear-gradient(90deg, ${tone.color}, #a3e635)` }}
          />
        </div>

        <div className="grid grid-cols-2 gap-2 w-full">
          <button
            type="button"
            onClick={() => (j.earOn ? j.stopEar() : j.startEar())}
            className={`col-span-2 flex items-center justify-center gap-2 py-2.5 rounded font-display text-[12px] tracking-[0.18em] transition-all ${
              j.earOn
                ? "bg-rose/10 text-rose border border-rose/40 hover:bg-rose/20"
                : "bg-cyan/10 text-cyan border border-cyan/40 hover:bg-cyan/20 shadow-[0_0_24px_-8px_rgba(34,211,238,0.6)]"
            }`}
          >
            {j.earOn ? <MicOff size={15} /> : <Mic size={15} />}
            {j.earOn ? "STOP EAR" : "START EAR"}
          </button>
          <button
            type="button"
            onClick={j.talk}
            className="flex items-center justify-center gap-1.5 py-2 rounded border border-line2 text-mut hover:text-ice hover:border-cyan/50 font-mono text-[10px] tracking-wider transition-colors"
          >
            <AudioWaveform size={13} /> TALK
          </button>
          <button
            type="button"
            onClick={j.interrupt}
            className="flex items-center justify-center gap-1.5 py-2 rounded border border-line2 text-mut hover:text-amber hover:border-amber/50 font-mono text-[10px] tracking-wider transition-colors"
          >
            <Hand size={13} /> STOP
          </button>
          <button
            type="button"
            onClick={() => j.setSettings({ voiceEnabled: !j.settings.voiceEnabled })}
            className={`col-span-2 flex items-center justify-center gap-1.5 py-1.5 rounded border font-mono text-[10px] tracking-wider transition-colors ${
              j.settings.voiceEnabled ? "border-lime/40 text-lime bg-lime/[0.06]" : "border-line2 text-dim"
            }`}
          >
            {j.settings.voiceEnabled ? <Volume2 size={12} /> : <VolumeX size={12} />}
            {j.settings.voiceEnabled ? "VOICE ON" : "MUTED"}
          </button>
        </div>

        <div className="w-full grid grid-cols-2 gap-x-3 gap-y-1 pt-1 border-t border-line text-[10px] font-mono text-dim">
          <span>
            claps <b className="text-lime">{j.clapStats.accepted}</b>
          </span>
          <span>
            floor <b className="text-mut">{(j.clapStats.floor * 100).toFixed(1)}</b>
          </span>
          <span>
            STT <b className={j.support.speechRecognition ? "text-lime" : "text-amber"}>{j.support.speechRecognition ? "ready" : "none"}</b>
          </span>
          <span>
            TTS <b className={j.support.tts ? "text-lime" : "text-rose"}>{j.support.tts ? "ready" : "none"}</b>
          </span>
        </div>
        <StateLegend status={j.status} />
      </div>

      {/* --------------------------------------------------- right: transcript */}
      <div className="panel flex flex-col overflow-hidden min-h-[560px]">
        <div className="flex items-center gap-2 px-4 py-2.5 border-b border-line bg-ink2/60">
          <Radar size={13} className="text-cyan" />
          <span className="font-display text-[11px] tracking-[0.22em] text-ice">COMMAND CHANNEL · {backend.connected ? "PYTHON" : "DEMO"}</span>
          <span className="ml-auto font-mono text-[9.5px] text-dim hidden sm:block">Ctrl+Space talk · F2 ear · Esc interrupt</span>
        </div>

        <div ref={scroller} className="flex-1 overflow-y-auto px-4 py-4 space-y-3 max-h-[420px]">
          {j.turns.length === 0 ? (
            <div className="h-full flex flex-col items-center justify-center text-center gap-3 py-10">
              <SlidersHorizontal size={22} className="text-line2 drift" />
              <p className="text-[13px] text-mut max-w-sm">
                Nothing heard yet. Type below, or start the browser ear for local clap detection. Browser speech recognition may send audio to its vendor.
              </p>
              <div className="flex flex-wrap gap-1.5 justify-center max-w-md">
                {suggestions.map((sg) => (
                  <button
                    key={sg}
                    type="button"
                    onClick={() => {
                      setText(sg);
                      inputRef.current?.focus();
                    }}
                    className="px-2 py-1 border border-line2 rounded text-[10px] font-mono text-dim hover:text-cyan hover:border-cyan/45 transition-colors"
                  >
                    {sg}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            j.turns.map((t) => (
              <div key={t.id} className={`typein border-l-2 pl-3 ${t.who === "JARVIS" ? "border-cyan/70" : "border-amber/60"}`}>
                <div className="flex items-baseline gap-2 mb-1">
                  <span className={`font-display text-[10px] tracking-[0.2em] ${t.who === "JARVIS" ? "text-cyan" : "text-amber"}`}>
                    {t.who === "JARVIS" ? "J.A.R.V.I.S" : "YOU"}
                  </span>
                  <span className="font-mono text-[9px] text-dim/80">{t.tag}</span>
                  <span className="ml-auto font-mono text-[9px] text-dim/50">{t.at}</span>
                </div>
                <p className="text-[13px] leading-relaxed text-fg whitespace-pre-wrap">{t.text}</p>
                {t.sources?.length ? (
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    {t.sources.filter((sr) => safeSourceUrl(sr.url)).map((sr, i) => (
                      <a
                        key={i}
                        href={safeSourceUrl(sr.url) || undefined}
                        target="_blank"
                        rel="noreferrer"
                        className="font-mono text-[9.5px] text-ice/80 hover:text-cyan border border-line2 rounded px-1.5 py-0.5 transition-colors"
                      >
                        {sr.title.slice(0, 44)}
                      </a>
                    ))}
                  </div>
                ) : null}
              </div>
            ))
          )}
        </div>

        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!text.trim()) return j.talk();
                    if (j.submit(text)) setText("");
          }}
          className="flex gap-2 p-3 border-t border-line bg-ink2/50"
        >
          <input
            ref={inputRef}
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={j.earOn ? "Type a command, or leave empty and press Enter to talk" : "Type a command — the ear is off"}
            className="flex-1 bg-panel2/70 border border-line2 rounded px-3 py-2.5 text-[13px] text-fg placeholder:text-dim/70 outline-none focus:border-cyan/60 transition-colors"
          />
          <button
            type="submit"
            className="flex items-center gap-1.5 px-4 rounded bg-cyan/10 border border-cyan/40 text-cyan hover:bg-cyan/20 font-display text-[11px] tracking-[0.15em] transition-colors"
          >
            <Send size={13} /> SEND
          </button>
        </form>

        <div className="grid sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] border-t border-line">
          <div className="p-3 border-r border-line">
            <div className="flex items-center gap-1.5 mb-2">
              <Activity size={11} className="text-lime" />
              <span className="font-mono text-[9.5px] tracking-[0.2em] text-dim">EVENT LOG</span>
            </div>
            <div className="space-y-[3px] max-h-[132px] overflow-y-auto font-mono text-[10px] leading-relaxed pr-1">
              {j.logs.length === 0 ? <p className="text-dim/60">— quiet —</p> : null}
              {j.logs.map((l) => (
                <p key={l.id} className="flex gap-2">
                  <span className="text-dim/50 shrink-0">{l.at}</span>
                  <span
                    className={
                      l.kind === "error"
                        ? "text-rose"
                        : l.kind === "warn"
                          ? "text-amber"
                          : l.kind === "act"
                            ? "text-lime"
                            : l.kind === "tool"
                              ? "text-copper"
                              : l.kind === "grant"
                                ? "text-sky"
                                : "text-mut"
                    }
                  >
                    {l.kind}
                  </span>
                  <span className="text-mut/90 break-words">{l.text}</span>
                </p>
              ))}
            </div>
          </div>
          <div className="p-3">
            <div className="flex items-center gap-1.5 mb-2">
              <ShieldCheck size={11} className="text-cyan" />
              <span className="font-mono text-[9.5px] tracking-[0.2em] text-dim">
                {backend.connected ? "BROWSER DEMO GRANTS (Python grants are separate)" : "DEMO CAPABILITY GRANTS (default-deny)"}
              </span>
            </div>
            <div className="space-y-1 max-h-[132px] overflow-y-auto pr-1">
              {CAPS.map((c) => (
                <label key={c.key} className="flex items-center gap-2 cursor-pointer group">
                  <input
                    type="checkbox"
                    checked={Boolean(j.settings.grants[c.key])}
                    disabled={backend.connected}
                    onChange={(e) => j.setSettings({ grants: { ...j.settings.grants, [c.key]: e.target.checked } })}
                    className="appearance-none w-3 h-3 border border-line2 rounded-[3px] checked:border-lime checked:bg-lime/25 relative after:absolute after:inset-0 after:content-[''] after:[box-shadow:0_0_8px_rgba(163,230,53,0.5)] transition-all"
                  />
                  <span className={`font-mono text-[10px] ${j.settings.grants[c.key] ? "text-fg" : "text-dim line-through"} group-hover:text-ice transition-colors`}>
                    {c.label}
                  </span>
                  <span className="font-mono text-[9px] text-dim/70 truncate hidden md:inline">— {c.desc}</span>
                </label>
              ))}
            </div>
          </div>
        </div>
      </div>

      {/* --------------------------------------------------------- tuning row */}
      <div className="panel lg:col-span-2 p-4 grid sm:grid-cols-2 lg:grid-cols-4 gap-x-6 gap-y-4">
        <div>
          <span className="block text-[10px] font-mono tracking-[0.14em] text-dim mb-2">ACTIVATION PATTERN</span>
          <Segmented options={["single", "double", "triple"] as const} value={j.settings.pattern} onChange={(v) => j.setSettings({ pattern: v })} />
          <p className="mt-2 text-[10px] text-dim/80 leading-snug">
            Same rules as <span className="font-mono text-mut">audio/clap_detector.py</span>: 90–450 ms between hits, sustained energy rejected.
          </p>
        </div>
        <Slider
          label="LOUDNESS GATE"
          value={j.settings.gate}
          min={0.08}
          max={0.8}
          step={0.01}
          onChange={(v) => j.setSettings({ gate: v })}
          hint="Web Audio band energy is 0–1 of 255, so this scale differs from the Python RMS gate — the algorithm is identical."
        />
        <Slider
          label="SENSITIVITY (higher = stricter)"
          value={j.settings.sensitivity}
          min={0.5}
          max={4}
          step={0.05}
          onChange={(v) => j.setSettings({ sensitivity: v })}
          hint="Multiplies the gate. Raise it if doors/coughs wake me."
        />
        <Slider
          label="REFRACTORY"
          value={j.settings.refractoryMs}
          min={400}
          max={4000}
          step={100}
          suffix=" ms"
          onChange={(v) => j.setSettings({ refractoryMs: v })}
          hint="Quiet time after an activation, so one applause burst fires once."
        />
        <div className="sm:col-span-2 lg:col-span-2 grid sm:grid-cols-2 gap-4 items-end">
          <label className="block">
            <span className="block text-[10px] font-mono tracking-[0.14em] text-dim mb-2">OPTIONAL BROWSER WAKE PHRASE</span>
            <input
              value={j.settings.wakePhrase}
              onChange={(e) => j.setSettings({ wakePhrase: e.target.value })}
              className="w-full bg-panel2/70 border border-line2 rounded px-2.5 py-1.5 text-[12px] font-mono text-fg outline-none focus:border-cyan/60"
            />
            <label className="mt-1.5 flex items-start gap-2 text-[10px] text-amber/90 leading-snug cursor-pointer">
              <input
                type="checkbox"
                checked={j.settings.wakeEnabled}
                onChange={(e) => j.setSettings({ wakeEnabled: e.target.checked })}
                className="mt-0.5 accent-cyan"
              />
              Enable Web Speech wake matching. Your browser MAY send audio to its vendor. Off by default; Python needs an actual local model for always-on wake.
            </label>
          </label>
          <label className="block">
            <span className="block text-[10px] font-mono tracking-[0.14em] text-dim mb-2">SPEAKER VOICE</span>
            <select
              value={j.settings.voiceURI}
              onChange={(e) => j.setSettings({ voiceURI: e.target.value })}
              className="w-full bg-panel2/70 border border-line2 rounded px-2 py-1.5 text-[12px] font-mono text-fg outline-none focus:border-cyan/60"
            >
              <option value="">system default</option>
              {j.voices.map((v) => (
                <option key={v.voiceURI} value={v.voiceURI}>
                  {v.name.slice(0, 34)} · {v.lang}
                </option>
              ))}
            </select>
            <p className="mt-1.5 text-[10px] text-dim/80">
              {j.voices.length} voices from the OS. Windows SAPI5 voices in the desktop app — functional, not cinematic.
            </p>
          </label>
        </div>
        <Slider label="SPEECH RATE" value={j.settings.rate} min={0.6} max={1.8} step={0.02} onChange={(v) => j.setSettings({ rate: v })} hint="1.00 = normal. SAPI maps this to words-per-minute." />
        <div className="flex flex-col gap-2">
          <Slider label="VOLUME" value={j.settings.volume} min={0} max={1} step={0.05} onChange={(v) => j.setSettings({ volume: v })} />
          <div className="flex flex-wrap gap-1.5">
            <Chip tone={j.settings.offlineMode ? "rose" : "lime"}>{j.settings.offlineMode ? "OFFLINE MODE" : "NETWORK ALLOWED"}</Chip>
            <button
              type="button"
              onClick={() => j.setSettings({ offlineMode: !j.settings.offlineMode })}
              className="px-2 py-[3px] border border-line2 rounded font-mono text-[10px] text-dim hover:text-cyan hover:border-cyan/50 transition-colors"
            >
              TOGGLE
            </button>
          </div>
          <p className="text-[10px] text-dim/80 leading-snug">
            Offline mode blocks every network tool in the router, exactly like <span className="font-mono text-mut">privacy.offline_mode</span>.
          </p>
        </div>
      </div>

      {/* ------------------------------------------------------- confirm modal */}
      {j.pendingConfirm ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-6 bg-ink/80 backdrop-blur-sm">
          <div className="panel corner w-full max-w-md p-5 border-amber/40">
            <div className="flex items-center gap-2 mb-3">
              <ShieldCheck size={15} className="text-amber" />
              <span className="font-display text-[13px] tracking-[0.16em] text-amber">CONFIRM ACTION</span>
            </div>
            <p className="text-[13px] text-fg leading-relaxed mb-1">{j.pendingConfirm.summary}</p>
            <p className="font-mono text-[10.5px] text-dim mb-4">capability: {j.pendingConfirm.capability}</p>
            <div className="flex gap-2 justify-end">
              <button
                type="button"
                onClick={() => j.answerConfirm(false)}
                className="px-3 py-1.5 rounded border border-line2 text-mut hover:text-fg font-mono text-[11px] transition-colors"
              >
                NO — CANCEL
              </button>
              <button
                type="button"
                onClick={() => j.answerConfirm(true)}
                className="px-3 py-1.5 rounded border border-amber/50 bg-amber/10 text-amber hover:bg-amber/20 font-mono text-[11px] transition-colors"
              >
                YES — ALLOW ONCE
              </button>
            </div>
            <p className="mt-3 text-[10px] text-dim/80 leading-snug">
              Sites I may open in this demo: {ALLOWED_SITES.map((u) => u.replace("https://www.", "")).join(" · ")}. Destructive actions are always per-call — a
              session grant never skips this dialog.
            </p>
          </div>
        </div>
      ) : null}
    </div>
  );
}

export { TONE };
export type { Status };
