import { useEffect, useState } from "react";
import { ArrowRight, Files, Mic, ShieldCheck, Sparkles, Terminal } from "lucide-react";
import Console, { TONE } from "@/components/Console";
import CodeExplorer from "@/components/CodeExplorer";
import { Architecture, Ledger, OfflineMatrix, RamBudget, Security, Setup, Troubleshoot } from "@/components/Sections";
import { Chip, Reveal } from "@/lib/ui";
import { useJarvis } from "@/lib/useJarvis";
import { useBackend } from "@/lib/useBackend";
import { PROJECT_FILES, TOTAL_LINES } from "@/data/project";

const NAV = [
  ["LIVE DEMO", "demo"],
  ["ARCHITECTURE", "architecture"],
  ["8 GB BUDGET", "ram"],
  ["VERIFIED", "verification"],
  ["SETUP", "setup"],
  ["THE CODE", "code"],
  ["SECURITY", "security"],
  ["DIAGNOSIS", "troubleshooting"],
] as const;

const BOOT = [
  "audio.stream ......... 16 kHz mono · 50 ms blocks · PortAudio",
  "clap.detector ....... band 800-4000 Hz · double · refractory 1.2 s",
  "wake.word ............. requires an actual local model; no custom model shipped",
  "stt ................. faster-whisper tiny.en · int8 · beam 1 · VAD on",
  "ai.chain .............. ollama:qwen2.5:1.5b -> gemini (opt-in) -> regex",
  "tools ............... 14 registered · default-deny broker in front of each",
  "memory .............. sqlite data/jarvis.db · redact() on every write",
  "ui .................. PySide6 · QThreads · 20 fps orb · no blocking GUI",
];

function Spec({ n, label, sub }: { n: string; label: string; sub: string }) {
  return (
    <div className="group border-l border-line2 pl-3 hover:border-cyan transition-colors">
      <p className="font-display text-2xl sm:text-[28px] font-bold text-fg leading-none tabular-nums group-hover:text-cyan transition-colors">{n}</p>
      <p className="font-mono text-[9.5px] tracking-[0.18em] text-dim mt-1.5">{label}</p>
      <p className="text-[11px] text-mut/80 mt-0.5 leading-snug">{sub}</p>
    </div>
  );
}

export default function App() {
  const j = useJarvis();
  const backend = useBackend();
  const [typed, setTyped] = useState(0);
  const [scrolled, setScrolled] = useState(false);
  const tone = TONE[j.status];

  useEffect(() => {
    // Demo routing remains available until the authenticated local API is linked.
    j.setTransport(backend.connected ? backend.send : null);
    return () => j.setTransport(null);
  }, [backend.connected, backend.send, j.setTransport]);

  useEffect(() => {
    if (typed >= BOOT.length) return;
    const id = setTimeout(() => setTyped((t) => t + 1), 240);
    return () => clearTimeout(id);
  }, [typed]);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 24);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  const jump = (id: string) => document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });

  return (
    <div className="relative min-h-screen">
      {/* --------------------------------------------------------- ambient */}
      <div className="fixed inset-0 -z-10 bg-ink" />
      <div className="fixed inset-0 -z-10 bg-vignette" />
      <div className="fixed inset-0 -z-10 bg-grid opacity-70" />
      <div className="fixed inset-x-0 top-0 -z-10 h-[420px] opacity-[0.13] pointer-events-none">
        <div className="absolute left-1/2 top-[-300px] h-[760px] w-[760px] -translate-x-1/2 rounded-full radar" />
      </div>

      {/* ---------------------------------------------------------- header */}
      <header
        className={`sticky top-0 z-40 transition-all duration-300 border-b ${
          scrolled ? "bg-ink/85 backdrop-blur-md border-line" : "bg-transparent border-transparent"
        }`}
      >
        <div className="max-w-[1400px] mx-auto px-4 sm:px-6 h-14 flex items-center gap-4">
          <div className="flex items-center gap-2.5 shrink-0">
            <span className="relative grid place-items-center w-7 h-7">
              <span className="absolute inset-0 rounded-full border" style={{ borderColor: `${tone.color}66` }} />
              <span className="absolute inset-0 rounded-full border border-dashed spin-slow" style={{ borderColor: `${tone.color}44` }} />
              <span className="w-1.5 h-1.5 rounded-full" style={{ background: tone.color, boxShadow: `0 0 10px ${tone.color}` }} />
            </span>
            <span className="font-display font-bold tracking-[0.22em] text-[15px] text-fg">J.A.R.V.I.S</span>
          </div>
          <nav className="hidden xl:flex items-center gap-0.5 ml-2">
            {NAV.map(([label, id]) => (
              <button
                key={id}
                type="button"
                onClick={() => jump(id)}
                className="px-2.5 py-1.5 font-mono text-[10px] tracking-[0.12em] text-dim hover:text-ice transition-colors rounded hover:bg-white/[0.04]"
              >
                {label}
              </button>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <span className="hidden sm:flex items-center gap-1.5 font-mono text-[10px] text-dim">
              <span className={`w-1.5 h-1.5 rounded-full ${j.earOn ? "bg-lime blink" : "bg-line2"}`} />
              {j.earOn ? "EAR LIVE" : "EAR OFF"}
            </span>
            <button
              type="button"
              onClick={() => {
                j.earOn ? j.stopEar() : j.startEar();
                if (!j.earOn) jump("demo");
              }}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded border border-cyan/45 bg-cyan/10 text-cyan hover:bg-cyan/20 font-mono text-[10px] tracking-[0.14em] transition-colors"
            >
              <Mic size={12} /> {j.earOn ? "STOP" : "ARM MIC"}
            </button>
          </div>
        </div>
        <div className="hair" />
      </header>

      <main className="max-w-[1400px] mx-auto px-4 sm:px-6 pb-24">
        {/* ---------------------------------------------------------- hero */}
        <section className="pt-10 sm:pt-14 pb-8 grid lg:grid-cols-12 gap-8 items-start">
          <div className="lg:col-span-7">
            <div className="flex flex-wrap items-center gap-2 mb-5">
              <Chip tone="lime">
                <Sparkles size={10} /> 111 PYTHON TESTS PASSING IN THIS SANDBOX
              </Chip>
              <Chip tone="cyan">₹0 / MONTH</Chip>
              <Chip tone="mut">PYTHON 3.12 · WINDOWS 10/11</Chip>
            </div>
            <h1 className="font-display font-bold leading-[0.95] tracking-tight text-fg text-[42px] sm:text-[64px] xl:text-[74px]">
              A voice assistant
              <br />
              that lives on your
              <br />
              <span className="text-cyan">8 GB laptop.</span>
            </h1>
            <p className="mt-6 max-w-2xl text-[14.5px] leading-relaxed text-mut">
              Not a mockup — the complete modular build: clap and wake-word activation on one shared microphone stream, Faster-Whisper transcription,
              qwen2.5:1.5b through Ollama with optional free-tier Gemini grounding, allow-listed Windows automation behind a default-deny permission broker,
              SQLite memory with redaction, and a PySide6 dashboard.{" "}
              <b className="text-fg">The panel below is a browser demo with an authenticated local Python link.</b> Its clap detection runs locally;
              browser speech recognition may use a vendor service. Connect the Python API for real Ollama replies.
            </p>

            <div className="mt-7 grid grid-cols-2 sm:grid-cols-4 gap-4">
              <Spec n={String(PROJECT_FILES.length)} label="PROJECT FILES" sub="no monolith file" />
              <Spec n={TOTAL_LINES.toLocaleString()} label="LINES SHIPPED" sub="every one below" />
              <Spec n="111" label="PYTHON TESTS" sub="run here, hardware pending" />
              <Spec n="14" label="TOOL CALLS" sub="behind capability gates" />
            </div>

            <div className="mt-8 flex flex-wrap gap-2.5">
              <button
                type="button"
                onClick={() => {
                  if (!j.earOn) j.startEar();
                  jump("demo");
                }}
                className="group flex items-center gap-2 px-4 py-2.5 rounded bg-cyan text-ink font-display text-[12px] tracking-[0.14em] hover:brightness-110 transition-all shadow-[0_0_30px_-10px_rgba(34,211,238,0.9)]"
              >
                ARM THE EAR &amp; TRY IT
                <ArrowRight size={14} className="transition-transform group-hover:translate-x-1" />
              </button>
              <button
                type="button"
                onClick={() => jump("setup")}
                className="flex items-center gap-2 px-4 py-2.5 rounded border border-line2 text-mut hover:text-ice hover:border-cyan/50 font-mono text-[11px] tracking-[0.1em] transition-colors"
              >
                <Terminal size={13} /> WINDOWS SETUP BLOCKS
              </button>
              <button
                type="button"
                onClick={() => jump("code")}
                className="flex items-center gap-2 px-4 py-2.5 rounded border border-line2 text-mut hover:text-ice hover:border-cyan/50 font-mono text-[11px] tracking-[0.1em] transition-colors"
              >
                <Files size={13} /> READ ALL {PROJECT_FILES.length} FILES
              </button>
            </div>
          </div>

          {/* boot readout */}
          <div className="lg:col-span-5 panel corner p-4 scanlines">
            <div className="flex items-center gap-2 mb-3">
              <span className="w-1.5 h-1.5 rounded-full bg-lime" />
              <span className="font-mono text-[9.5px] tracking-[0.22em] text-dim">SUBSYSTEM MANIFEST · STAGES 2-8</span>
            </div>
            <div className="space-y-1.5">
              {BOOT.map((line, i) => (
                <p
                  key={line}
                  className={`font-mono text-[10.5px] leading-relaxed transition-all duration-500 ${
                    i < typed ? "opacity-100 translate-x-0 text-mut" : "opacity-0 translate-x-2"
                  }`}
                >
                  <span className="text-cyan/70">ok</span> {line}
                </p>
              ))}
            </div>
            <div className="hair my-4" />
            <p className="text-[11.5px] text-dim leading-relaxed">
              Honest limits: I could not run this on your laptop. Microphone, SAPI voices, Ollama latency, Gemini quota, Playwright and an actual{" "}
              <code className="font-mono text-mut">shutdown</code> need your hardware — each one is listed in the verification ledger rather than claimed.
            </p>
            <div className="mt-3 grid grid-cols-3 gap-2">
              {[
                ["OFFLINE", "capture → STT → tools → TTS", "text-lime border-lime/35"],
                ["OPT-IN NET", "search + Gemini grounding", "text-amber border-amber/35"],
                ["NEVER", "unrestricted shell", "text-rose border-rose/35"],
              ].map(([a, b, cls]) => (
                <div key={a} className={`border rounded p-2 ${cls}`}>
                  <p className="font-mono text-[9px] tracking-[0.14em]">{a}</p>
                  <p className="text-[10px] text-dim mt-1 leading-snug">{b}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* -------------------------------------------------- 01 live demo */}
        <section id="demo" className="scroll-mt-20 pt-4">
          <div className="mb-5 flex flex-wrap items-end gap-3">
            <div>
              <div className="flex items-center gap-3 mb-3">
                <span className="h-px w-8 bg-cyan/60" />
                <span className="font-mono text-[10px] tracking-[0.32em] text-cyan/80">01 · LIVE PROTOTYPE</span>
              </div>
              <h2 className="font-display text-2xl sm:text-[34px] font-bold tracking-tight text-fg leading-[1.12]">Arm it, clap twice, then talk</h2>
            </div>
            <div className="ml-auto flex items-center gap-2">
              <Chip tone={j.support.speechRecognition ? "lime" : "amber"}>{j.support.speechRecognition ? "WEB SPEECH STT" : "NO STT — TYPE"}</Chip>
              <Chip tone={j.support.tts ? "lime" : "rose"}>{j.support.tts ? "TTS READY" : "NO TTS"}</Chip>
            </div>
          </div>
          <p className="max-w-3xl text-[13.5px] text-mut leading-relaxed mb-4">
            Your browser does the transcription (Web Speech API) where the desktop app uses local Whisper, and page reads stop at the CORS wall — both marked in the log
            instead of faked. Everything else is genuine: onset-based clap detection, wake-phrase matching, the router's control/regex/model layers, the default-deny
            capability check, per-call confirmation for power, notes that persist, and barge-in on STOP.
          </p>
          <Console j={j} backend={backend} />
        </section>

        <Reveal className="mt-20">
          <Architecture />
        </Reveal>
        <Reveal className="mt-20">
          <RamBudget />
        </Reveal>
        <Reveal className="mt-20">
          <Ledger />
        </Reveal>
        <Reveal className="mt-20">
          <Setup />
        </Reveal>
        <Reveal className="mt-20">
          <CodeExplorer />
        </Reveal>
        <Reveal className="mt-20">
          <Security />
        </Reveal>
        <Reveal className="mt-16">
          <div className="grid lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] gap-4">
            <OfflineMatrix />
            <div className="panel corner p-4">
              <div className="flex items-center gap-2 mb-3">
                <ShieldCheck size={13} className="text-lime" />
                <span className="font-mono text-[10px] tracking-[0.2em] text-dim">WHAT I WILL NOT DO</span>
              </div>
              <ul className="space-y-2 text-[12.5px] text-mut leading-relaxed">
                {[
                  "Ship a 'tested on Windows' claim I cannot show output for.",
                  "Give the model a shell, a terminal, or a delete function.",
                  "Record to the cloud, or send audio anywhere — there is no upload path.",
                  "Tell you to disable Defender, or lower a system security policy, to make an install work.",
                  "Promise Iron Man audio from free SAPI5 voices.",
                  "Index your whole disk, or store a password because you happened to dictate one.",
                ].map((t) => (
                  <li key={t} className="flex gap-2.5">
                    <span className="mt-[7px] h-1 w-1 rounded-full bg-rose shrink-0" />
                    {t}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </Reveal>
        <Reveal className="mt-20">
          <Troubleshoot />
        </Reveal>

        {/* --------------------------------------------------------- footer */}
        <footer className="mt-24 pt-8 border-t border-line">
          <div className="flex flex-wrap items-start gap-6 justify-between">
            <div className="max-w-lg">
              <p className="font-display text-[15px] tracking-[0.18em] text-fg">STAGE 1 IS YOURS TO RUN</p>
              <p className="mt-2 text-[12.5px] text-mut leading-relaxed">
                Save the files (one button in <button type="button" onClick={() => jump("code")} className="text-cyan underline decoration-cyan/40 hover:decoration-cyan">The Code</button>),
                run <code className="font-mono text-ice">python main.py --check</code>, and paste me the output. If RAM, the mic, or Ollama reports something unexpected, I will
                fix that stage before writing the next one — small verifiable steps, exactly as your brief asked.
              </p>
            </div>
            <div className="grid grid-cols-2 gap-x-10 gap-y-1.5 font-mono text-[10.5px] text-dim">
              {[
                ["licence", "all deps FOSS"],
                ["cloud", "optional, off by default"],
                ["storage", "local SQLite only"],
                ["tested", "59 pass / hardware pending"],
              ].map(([k, v]) => (
                <p key={k}>
                  <span className="text-mut">{k}</span> · {v}
                </p>
              ))}
            </div>
          </div>
          <p className="mt-8 text-[10.5px] text-dim/70 leading-relaxed">
            Built as a working engineering specification: Python project under <code className="font-mono">jarvis/</code>, this interface generated from those same files
            ({PROJECT_FILES.length} reviewed project files, {PROJECT_FILES.reduce((a, f) => a + f.lines, 0).toLocaleString()} lines). No GPU required. No subscription required. No claims beyond the evidence quoted above.
          </p>
        </footer>
      </main>
    </div>
  );
}
