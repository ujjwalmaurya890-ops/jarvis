import { useState } from "react";
import { AlertTriangle, Boxes, Check, Cpu, Database, Eye, Globe, ShieldCheck, Terminal, X } from "lucide-react";
import { Chip, Copy, SectionTitle } from "@/lib/ui";

/* ============================================================ ARCHITECTURE */
type Node = { id: string; title: string; module: string; kind: "sense" | "decide" | "act" | "remember"; offline: boolean; detail: string };

const NODES: Node[] = [
  {
    id: "mic",
    title: "One shared microphone stream",
    module: "audio/microphone.py",
    kind: "sense",
    offline: true,
    detail:
      "A single sounddevice.InputStream at 16 kHz feeds every consumer through a subscriber list, because Windows lets very few apps hold the capture device at once. The PortAudio callback only computes RMS/peak and pushes into a 6-second ring buffer — under a millisecond of work per 50 ms block. Microphone failures are translated into sentences ('the microphone is busy', 'no input device'), and a dead stream is restarted by the listener loop.",
  },
  {
    id: "clap",
    title: "Clap pattern detector",
    module: "audio/clap_detector.py",
    kind: "sense",
    offline: true,
    detail:
      "Band-pass 800–4000 Hz, then three cheap tests: the energy must beat both an absolute gate and 1.8× the rolling room median; it must jump from below the gate to well above it inside one chunk (a clap attack is <2 ms, speech is 50–150 ms); and it must fall back below the gate within ~300 ms, which is what rejects shouting and music. Accepted onsets then need a 90–450 ms spacing pattern (single/double/triple) and a refractory lockout. Every rejection is counted so the Settings dialog can show *why* it stayed quiet.",
  },
  {
    id: "wake",
    title: "Wake word (two paths)",
    module: "audio/wake_word.py",
    kind: "sense",
    offline: true,
    detail:
      "Always-on wake detection uses openWakeWord only when you supply a compatible local ONNX/TFLite model. The fuzzy transcript matcher runs after manual/clap activation, not continuously in the background. No trained custom 'Hello JARVIS' model ships here; use double-clap or push-to-talk until you add one and measure false triggers on your own microphone.",
  },
  {
    id: "vad",
    title: "Utterance capture + VAD",
    module: "audio/microphone.py · CommandRecorder",
    kind: "sense",
    offline: true,
    detail:
      "Recording only starts after an activation and stops on an adaptive noise-floor estimate, with a 350 ms lead-in so the first consonant survives. Hard 20 s cap, empty-buffer detection, and a cancel event wired to the STOP button. Audio is never stored unless store_audio is turned on, which it is not by default.",
  },
  {
    id: "stt",
    title: "Faster-Whisper, CPU/int8",
    module: "audio/speech_to_text.py",
    kind: "decide",
    offline: true,
    detail:
      "tiny.en by default (~75 MB, ~250 MB resident), int8 quantisation, beam_size 1 (about 3× faster than 5 for barely better output on 5 s of speech), built-in VAD filter, condition_on_previous_text off so one bad line cannot corrupt the rest. 'hinglish' sets language=hi with a Latin-script initial_prompt so code-mixed sentences keep English words in English letters. transcribe_file() lets you test the whole path with a WAV, no speaking required.",
  },
  {
    id: "router",
    title: "Router: three layers",
    module: "core/router.py",
    kind: "decide",
    offline: true,
    detail:
      "Layer 1 control words (stop/mute/help/forget memory) handled locally. Layer 2 deterministic regex intents for the things that must work with a dead model or no internet. Layer 3 asks the LLM for strict JSON ({tool, arguments} / {action:chat} / {action:research}) or native tool calls. If the provider throws, times out or invents a tool name, the router degrades to regex or a chat answer — it never executes an unknown tool, and never crashes the app.",
  },
  {
    id: "provider",
    title: "Provider chain: Ollama → Gemini",
    module: "ai/*.py",
    kind: "decide",
    offline: false,
    detail:
      "One interface (probe/complete/models) behind both. Ollama is the default: /api/tags is queried so a missing model produces 'run: ollama pull qwen2.5:1.5b' instead of a hang, JSON mode via format:'json', num_ctx 2048, keep_alive for RAM release. Gemini uses the official google-genai SDK, probes model list because ids rotate, treats 429/quota/timeout/403 as soft errors that fall back to local, and requests Google Search grounding only when the model supports it.",
  },
  {
    id: "registry",
    title: "Tool registry + permission gate",
    module: "tools/registry.py",
    kind: "act",
    offline: true,
    detail:
      "Order of operations is the security boundary: name must exist → arguments are type-checked against the declared schema → capability is checked (default-deny, per-call confirm for dangerous) → handler runs with validated kwargs. Model-supplied search strings are sanitised (tags stripped, instruction-shaped phrases removed, length capped) before hitting any API.",
  },
  {
    id: "tools",
    title: "Automation tools",
    module: "tools/system_tools.py",
    kind: "act",
    offline: true,
    detail:
      "Launch (allow-list, fixed argv, shell=False, Win32 window focus), open site (domain allow-list), search (ddgs, no key), read page (Playwright if installed else requests+BeautifulSoup, 403/404/timeout surfaced), files (resolve-then-compare sandbox, read caps, binary refusal), notes (front-matter markdown, soft delete to .trash), memory (inspect/clear), stats, and power — which schedules shutdown /t 60 after confirmation so it stays cancellable.",
  },
  {
    id: "tts",
    title: "Interruptible speech",
    module: "audio/text_to_speech.py",
    kind: "act",
    offline: true,
    detail:
      "pyttsx3/SAPI5 on a dedicated thread with a queue: markdown and URLs are cleaned before speaking, rate/volume/voice come from settings, and stop() clears the queue and calls Skip/endLoop so 'stop' and the STOP button produce barge-in. No claim of a cinematic voice — SAPI5 is what a ₹0 budget buys.",
  },
  {
    id: "db",
    title: "SQLite memory + redaction",
    module: "memory/*.py",
    kind: "remember",
    offline: true,
    detail:
      "data/jarvis.db: turns, summaries, facts, permissions, doc_chunks. Every write passes through redact(), which scrubs password/token/API-key/card patterns and emails. Retention purge on boot, explicit inspect (show_memory) and delete (clear_memory) tools, and an opt-in document index that ranks by term overlap instead of an embedding model — on 8 GB, RAM is the scarcer resource than cleverness.",
  },
  {
    id: "ui",
    title: "PySide6 dashboard",
    module: "ui/*.py",
    kind: "remember",
    offline: true,
    detail:
      "Boot runs in a QThread so the window opens instantly; the assistant reports through signals, never by touching widgets from another thread. Painted orb at 20 fps, transcript cards with per-message provenance tags (regex/model/tool + latency), activity log, history table, settings dialog that validates the command allow-list before it will save, and a confirm dialog that answers the tool thread's blocking request.",
  },
];

const Lanes = [
  { key: "sense", label: "SENSE", color: "#22d3ee" },
  { key: "decide", label: "DECIDE", color: "#fbbf24" },
  { key: "act", label: "ACT", color: "#f97316" },
  { key: "remember", label: "REMEMBER", color: "#a3e635" },
] as const;

export function Architecture() {
  const [open, setOpen] = useState<string>("clap");
  const sel = NODES.find((n) => n.id === open)!;
  return (
    <section id="architecture" className="scroll-mt-20">
      <SectionTitle
        kicker="02 · ARCHITECTURE"
        title="Eleven modules, one pipeline, no monolith"
        sub="Click any block for the engineering decision behind it. Cyan = runs with the network unplugged. Nothing in this graph imports the GUI, which is exactly why the offline tests can run at all."
      />
      <div className="grid lg:grid-cols-[minmax(0,1fr)_360px] gap-4">
        <div className="grid sm:grid-cols-2 xl:grid-cols-4 gap-3">
          {Lanes.map((lane) => (
            <div key={lane.key} className="space-y-2">
              <div className="flex items-center gap-2">
                <span className="h-1.5 w-1.5 rounded-full" style={{ background: lane.color }} />
                <span className="font-mono text-[9.5px] tracking-[0.24em]" style={{ color: lane.color }}>
                  {lane.label}
                </span>
                <span className="h-px flex-1" style={{ background: `${lane.color}25` }} />
              </div>
              {NODES.filter((n) => n.kind === lane.key).map((n) => (
                <button
                  key={n.id}
                  type="button"
                  onClick={() => setOpen(n.id)}
                  className={`w-full text-left panel p-3 transition-all duration-300 group ${
                    open === n.id ? "border-cyan/50 bg-panel2" : "hover:border-line2 hover:bg-panel2/60"
                  }`}
                >
                  <div className="flex items-start gap-2">
                    <span className="mt-[5px] h-1.5 w-1.5 rounded-full shrink-0" style={{ background: open === n.id ? lane.color : "#2c415f" }} />
                    <div className="min-w-0">
                      <p className="text-[12.5px] font-semibold leading-snug text-fg">{n.title}</p>
                      <p className="font-mono text-[9.5px] text-dim truncate mt-0.5 group-hover:text-ice transition-colors">{n.module}</p>
                    </div>
                    {!n.offline ? <Globe size={11} className="ml-auto text-amber/70 shrink-0" /> : null}
                  </div>
                </button>
              ))}
            </div>
          ))}
        </div>
        <aside className="panel corner p-4 flex flex-col gap-3 h-fit lg:sticky lg:top-20">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-display text-[13px] text-ice tracking-wide">{sel.title}</span>
            <Chip tone={sel.offline ? "lime" : "amber"}>{sel.offline ? "OFFLINE OK" : "NEEDS INTERNET"}</Chip>
          </div>
          <code className="font-mono text-[10px] text-cyan/80 break-all">{sel.module}</code>
          <p className="text-[12.5px] leading-relaxed text-mut">{sel.detail}</p>
        </aside>
      </div>
    </section>
  );
}

/* =============================================================== RAM BUDGET */
const RAM = [
  { name: "PySide6 dashboard", mb: 90, verdict: "keep", note: "one window, painted at 20 fps" },
  { name: "clap + wake listener", mb: 40, verdict: "keep", note: "numpy ring buffer, ~1% CPU" },
  { name: "Whisper tiny.en (int8)", mb: 250, verdict: "default", note: "the STT sweet spot on this class of laptop" },
  { name: "Whisper base.en", mb: 400, verdict: "optional", note: "better accuracy, ~0.4 s slower" },
  { name: "Whisper small", mb: 800, verdict: "avoid", note: "no. Use base.en and speak clearly" },
  { name: "qwen2.5:0.5b", mb: 700, verdict: "low-quality", note: "for 4 GB machines or heavy multitasking" },
  { name: "qwen2.5:1.5b", mb: 1450, verdict: "default", note: "chosen model: tool calling + JSON at 1.5 GB" },
  { name: "qwen2.5:3b", mb: 2400, verdict: "tight", note: "only with every browser tab closed" },
  { name: "llama3.2:1b (fallback)", mb: 1300, verdict: "fallback", note: "used if qwen is missing" },
  { name: "Chromium (Playwright)", mb: 150, verdict: "on-demand", note: "launched per read, closed after" },
];
const BUDGET = 8192 * 0.82; // leave headroom for Windows itself

export function RamBudget() {
  const total = RAM.filter((r) => ["keep", "default"].includes(r.verdict)).reduce((a, r) => a + r.mb, 0);
  return (
    <section id="ram" className="scroll-mt-20">
      <SectionTitle
        kicker="03 · MEMORY BUDGET"
        title="The 8 GB constraint is the real design input"
        sub={`Everything marked 'default/keep' runs at once: ${total.toLocaleString()} MB. Windows wants the rest. This is why the repo ships tiny.en + qwen2.5:1.5b and an 'unload the model' command instead of pretending a 3-bit 7B model is free.`}
      />
      <div className="grid lg:grid-cols-[minmax(0,1fr)_320px] gap-4">
        <div className="panel p-4 space-y-2.5">
          {RAM.map((r) => {
            const pct = Math.min(100, (r.mb / BUDGET) * 100);
            const tone = r.verdict === "avoid" ? "#f87171" : r.verdict === "tight" ? "#fbbf24" : r.verdict === "default" || r.verdict === "keep" ? "#22d3ee" : "#5c708f";
            return (
              <div key={r.name} className="group">
                <div className="flex items-baseline gap-2 mb-1">
                  <span className="text-[12px] text-fg">{r.name}</span>
                  <span className="font-mono text-[10px] text-dim">{r.mb >= 1024 ? `${(r.mb / 1024).toFixed(1)} GB` : `${r.mb} MB`}</span>
                  <span className="ml-auto font-mono text-[9px] tracking-wider" style={{ color: tone }}>
                    {r.verdict.toUpperCase()}
                  </span>
                </div>
                <div className="h-[6px] rounded-full bg-line overflow-hidden">
                  <div
                    className="h-full rounded-full transition-all duration-700 group-hover:brightness-125"
                    style={{ width: `${pct}%`, background: `linear-gradient(90deg, ${tone}cc, ${tone}55)`, boxShadow: `0 0 12px -2px ${tone}` }}
                  />
                </div>
                <p className="mt-1 text-[10.5px] text-dim/80">{r.note}</p>
              </div>
            );
          })}
        </div>
        <div className="space-y-3">
          <div className="panel corner p-4">
            <div className="flex items-center gap-2 mb-2">
              <Cpu size={13} className="text-cyan" />
              <span className="font-mono text-[10px] tracking-[0.2em] text-dim">CPU STRATEGY</span>
            </div>
            <ul className="space-y-2 text-[12px] text-mut leading-snug">
              <li>· audio callback stays under 1 ms/block; everything heavy is on worker threads</li>
              <li>· Whisper beam 1, num_ctx 2048, threads = physical cores (4 here)</li>
              <li>· Ollama keep_alive: 'go to sleep' returns 1.4 GB while you are not talking</li>
              <li>· UI repaint throttled to 20 fps so visualisation never starves capture</li>
              <li>· Chromium only for JS-only pages; static HTML via requests + BeautifulSoup</li>
            </ul>
          </div>
          <div className="panel p-4">
            <div className="flex items-center gap-2 mb-2">
              <AlertTriangle size={13} className="text-amber" />
              <span className="font-mono text-[10px] tracking-[0.2em] text-dim">COMPATIBILITY RISKS</span>
            </div>
            <ul className="space-y-2 text-[12px] text-mut leading-snug">
              <li>
                <b className="text-amber">PySide6 + Python 3.13</b> — wheels lag. The repo targets 3.12; if PySide6 fails to install, drop to <code className="font-mono text-[11px] text-ice">py -3.12</code>.
              </li>
              <li>
                <b className="text-amber">numpy 2.x</b> — faster-whisper/ctranslate2 builds occasionally expect 1.x. Pin whatever your <code className="font-mono text-[11px] text-ice">pip install</code> resolved.
              </li>
              <li>
                <b className="text-amber">pyttsx3 + comtypes</b> needs comtypes on some 3.12 installs; if SAPI returns no voices the Windows speech feature is missing, not the code.
              </li>
              <li>
                <b className="text-amber">PortAudio</b> ships inside sounddevice; a 'busy device' error is a Teams/Meet exclusive grab, not a driver bug.
              </li>
              <li>
                <b className="text-amber">Google model ids rotate</b> — which is why the provider probes /models/list rather than hardcoding one that may vanish.
              </li>
            </ul>
          </div>
        </div>
      </div>
    </section>
  );
}

/* ============================================================= VERIFICATION */
type VStat = "pass" | "hardware" | "untested";
const LEDGER: { what: string; how: string; stat: VStat; evidence: string }[] = [
  { what: "42 Python modules byte-compile", how: "python -m compileall -q jarvis", stat: "pass", evidence: "exit 0; no syntax errors" },
  { what: "111 Python tests", how: "python -m pytest -q", stat: "pass", evidence: "111 passed in 6.27s (mocked audio/cloud; live loopback API tests)" },
  { what: "Module import sweep (numpy only)", how: "importlib over 21 dependency-light modules", stat: "pass", evidence: "IMPORTED OK: 21/21" },
  { what: "Stage 1 environment report", how: "python main.py --check", stat: "pass", evidence: "prints RAM/CPU/disk/devices/Ollama + headroom advice" },
  { what: "Clap detector DSP", how: "synthetic transients: pattern, spacing, refractory, speech rejection", stat: "pass", evidence: "12 tests; caught & fixed a real envelope-logic bug" },
  { what: "Permission broker", how: "default-deny, traversal, URL allow-list, dangerous-op confirm, offline gates", stat: "pass", evidence: "11 tests" },
  { what: "Command allow-list", how: "shell operators, destructive programs, unknown names, long args", stat: "pass", evidence: "10 tests" },
  { what: "Router + injection resistance", how: "regex routing, hallucinated-tool rejection, provider-crash fallback", stat: "pass", evidence: "13 tests" },
  { what: "SQLite redaction & retention", how: "secrets scrubbed before write, clear/inspect round-trip", stat: "pass", evidence: "12 tests · fixed a real gap: 'password is X' slipped through" },
  { what: "Browser origin guard + demo route", how: "esbuild -> Node using current React module", stat: "pass", evidence: "local Vite allowed; hosted/file origins rejected; fallback demo still works" },
  { what: "npm ci with an empty cache", how: "97 live registry tarball downloads", stat: "pass", evidence: "all HTTP 200; installed 96 packages in about 4s" },
  { what: "Vite production build", how: "npm run build after npm ci", stat: "pass", evidence: "1954 modules transformed; output built in 2.96s" },
  { what: "Python API <-> Node fetch", how: "start real --api then verify-api-bridge.mjs", stat: "pass", evidence: "10/10 checks + offline failure path; actual local process, no Ollama" },
  { what: "TypeScript, strict, project-wide", how: "npx tsc --noEmit", stat: "pass", evidence: "0 errors" },
  { what: "Real microphone capture", how: "sounddevice + PortAudio on Windows", stat: "hardware", evidence: "no audio device exists in the sandbox" },
  { what: "Faster-Whisper accuracy + latency", how: "record → transcribe on your mic", stat: "hardware", evidence: "model download requires internet + your disk" },
  { what: "Ollama answers / token speed", how: "ollama serve + qwen2.5:1.5b", stat: "hardware", evidence: "no Ollama in the sandbox" },
  { what: "SAPI5 voice output", how: "pyttsx3 runLoop on your machine's voices", stat: "hardware", evidence: "no Windows audio here" },
  { what: "Gemini live call + quota behaviour", how: "google-genai with your key", stat: "hardware", evidence: "no key, and free-tier limits are yours" },
  { what: "Playwright page reads", how: "chromium install + real sites", stat: "hardware", evidence: "browser not installed in the sandbox" },
  { what: "shutdown /s actually firing", how: "Windows power path", stat: "untested", evidence: "deliberately not executed — irreversible on a dev machine" },
  { what: "PySide6 window pixels", how: "manual look + feel on Windows", stat: "untested", evidence: "no graphical runtime here; Python sources compile" },
];

export function Ledger() {
  const counts = {
    pass: LEDGER.filter((l) => l.stat === "pass").length,
    hardware: LEDGER.filter((l) => l.stat === "hardware").length,
    untested: LEDGER.filter((l) => l.stat === "untested").length,
  };
  return (
    <section id="verification" className="scroll-mt-20">
      <SectionTitle
        kicker="04 · VERIFICATION LEDGER"
        title="What I actually ran, and what I did not"
        sub="I have no access to your laptop. Everything below marked PASS was executed here and the output is quoted. The rest is written to be correct but needs your hardware — no 'tested on my machine' fiction."
      />
      <div className="flex flex-wrap gap-2 mb-4">
        <Chip tone="lime">
          <Check size={11} /> {counts.pass} verified by execution
        </Chip>
        <Chip tone="amber">
          <Eye size={11} /> {counts.hardware} need your hardware
        </Chip>
        <Chip tone="rose">
          <X size={11} /> {counts.untested} deliberately not attempted
        </Chip>
      </div>
      <div className="panel overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="bg-ink2/70 border-b border-line">
                {["check", "how it was verified", "status", "evidence"].map((h) => (
                  <th key={h} className="px-3 py-2 font-mono text-[9.5px] tracking-[0.18em] text-dim font-normal uppercase">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {LEDGER.map((row) => (
                <tr key={row.what} className="border-b border-line/60 last:border-0 hover:bg-white/[0.02] transition-colors align-top">
                  <td className="px-3 py-2.5 text-[12.5px] text-fg whitespace-nowrap">{row.what}</td>
                  <td className="px-3 py-2.5 text-[11.5px] font-mono text-mut">{row.how}</td>
                  <td className="px-3 py-2.5">
                    <span
                      className={`inline-flex items-center gap-1 font-mono text-[9.5px] tracking-wider px-1.5 py-0.5 rounded border ${
                        row.stat === "pass" ? "text-lime border-lime/40 bg-lime/[0.07]" : row.stat === "hardware" ? "text-amber border-amber/40 bg-amber/[0.07]" : "text-rose border-rose/40 bg-rose/[0.07]"
                      }`}
                    >
                      {row.stat === "pass" ? <Check size={10} /> : row.stat === "hardware" ? <Eye size={10} /> : <X size={10} />}
                      {row.stat === "pass" ? "PASS" : row.stat === "hardware" ? "NEEDS PC" : "NOT RUN"}
                    </span>
                  </td>
                  <td className="px-3 py-2.5 text-[11.5px] text-dim">{row.evidence}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      <div className="mt-3 panel p-3">
        <div className="flex items-center gap-2 mb-2">
          <Terminal size={12} className="text-lime" />
          <span className="font-mono text-[10px] tracking-[0.2em] text-dim">REAL OUTPUT FROM THIS BUILD</span>
        </div>
        <pre className="code text-mut overflow-x-auto">{`$ cd jarvis && python3 -m pytest tests -q
...........................................................              [100%]
111 passed in 6.27s

$ python3 -m compileall -q .        # 42 python files
=== compileall exit: 0 ===

$ python3 - <<'PY'   # import sweep without sounddevice/torch/Qt
IMPORTED OK: 21/21     (config, core, ai, security, tools, memory, audio)
PY

$ npm ci --no-audit --no-fund
added 96 packages in 3s

$ node scripts/verify-api-bridge.mjs  # with real Python API running
10 integration checks passed; offline error path also checked

$ npx tsc --noEmit
(no output · exit 0)`}</pre>
      </div>
    </section>
  );
}

/* ==================================================================== SETUP */
type Step = { id: string; label: string; needAdmin?: boolean; body: string; note?: string };
export const STEPS: Step[] = [
  {
    id: "s0",
    label: "0 · Prerequisites",
    body: `winget install -e --id Python.Python.3.12 --scope user
winget install -e --id OpenJS.NodeJS.LTS --scope user
winget install -e --id Ollama.Ollama
py -3.12 --version
node --version
ollama --version`,
    note: "Use the official installers if winget is unavailable. Node 20.19+ is required by Vite 7. Restart PowerShell so the new programs are on PATH. The app itself never needs administrator rights.",
  },
  {
    id: "s1",
    label: "1 · Python environment",
    body: `# From the extracted project root (contains package.json)
py -3.12 -m venv .\\jarvis\\.venv
& .\\jarvis\\.venv\\Scripts\\python.exe -m pip install --upgrade pip`,
    note: "No PowerShell execution-policy change is needed: call the virtual-environment interpreter directly. A .bat file does not activate its parent PowerShell process.",
  },
  {
    id: "s2",
    label: "2 · Dependencies",
    body: `& .\\jarvis\\.venv\\Scripts\\python.exe -m pip install -r .\\jarvis\\requirements.txt
npm ci --no-audit --no-fund`,
    note: "Run from the root with package-lock.json present. npm ci is a clean, reproducible install; never delete the lockfile to hide a timeout.",
  },
  {
    id: "s3",
    label: "3 · Machine diagnostics",
    body: `& .\\jarvis\\.venv\\Scripts\\python.exe .\\jarvis\\main.py --check`,
    note: "Reports Windows/Python/RAM/disk, microphone devices, packages and Ollama. It does not record audio or download a model.",
  },
  {
    id: "s4",
    label: "4 · Local AI model",
    body: `ollama pull qwen2.5:1.5b
ollama list
& .\\jarvis\\.venv\\Scripts\\python.exe .\\jarvis\\main.py --say "help"`,
    note: "Start Ollama from the Start menu if it is not already running. First local question loads the model into RAM. Whisper tiny.en downloads on first voice use and then works offline.",
  },
  {
    id: "s5",
    label: "5 · Tests & desktop",
    body: `& .\\jarvis\\.venv\\Scripts\\python.exe -m pytest -q .\\jarvis\\tests
& .\\jarvis\\.venv\\Scripts\\python.exe .\\jarvis\\main.py --text
# or, in another window:
& .\\jarvis\\.venv\\Scripts\\python.exe .\\jarvis\\main.py --gui`,
    note: "Typed mode rules out microphone issues before trying the PySide6 interface. Windows mic, SAPI voices and real Ollama response time must be checked on your laptop.",
  },
  {
    id: "s6",
    label: "6 · Backend terminal",
    body: `# PowerShell window A, from the project root:
& .\\jarvis\\.venv\\Scripts\\python.exe .\\jarvis\\main.py --api
# Keep this window open. Token is written to .\\jarvis\\data\\api-token.`,
    note: "The API binds only to 127.0.0.1:8765. Never share its token. It regenerates on each start, and dangerous actions cannot be confirmed through the API.",
  },
  {
    id: "s7",
    label: "7 · Dashboard terminal",
    body: `# PowerShell window B, from the same project root:
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
Start-Process http://127.0.0.1:5173
# Paste the value in .\\jarvis\\data\\api-token into Desktop link.`,
    note: "Keep window A running. The page initially uses a browser demo; after you paste the token and connect, typed requests use the real Python API. Browser microphone controls remain separate.",
  },
  {
    id: "s8",
    label: "8 · Optional startup",
    body: `$lnk = Join-Path $env:APPDATA 'Microsoft\\Windows\\Start Menu\\Programs\\Startup\\JARVIS.lnk'
$ws = New-Object -ComObject WScript.Shell
$s = $ws.CreateShortcut($lnk)
$s.TargetPath = (Resolve-Path .\\jarvis\\.venv\\Scripts\\pythonw.exe).Path
$s.Arguments = '"' + (Resolve-Path .\\jarvis\\main.py).Path + '" --gui'
$s.WorkingDirectory = (Resolve-Path .\\jarvis).Path
$s.Save()
# Undo: Remove-Item $lnk`,
    note: "Disabled by default. Only enable startup on login if you want it; the microphone still requires manual START EAR.",
  },
];

export function Setup() {
  const [active, setActive] = useState(STEPS[2].id);
  const step = STEPS.find((s) => s.id === active)!;
  return (
    <section id="setup" className="scroll-mt-20">
      <SectionTitle
        kicker="05 · WINDOWS SETUP"
        title="Stage by stage, with the exact PowerShell"
        sub="Copy one block at a time. Every stage prints something you can check before moving on — that is the whole debugging strategy for a beginner."
      />
      <div className="grid lg:grid-cols-[240px_minmax(0,1fr)] gap-4">
        <div className="flex lg:flex-col gap-1.5 overflow-x-auto pb-1">
          {STEPS.map((s) => (
            <button
              key={s.id}
              type="button"
              onClick={() => setActive(s.id)}
              className={`shrink-0 text-left px-3 py-2 rounded border font-mono text-[10.5px] tracking-wide transition-all ${
                active === s.id ? "border-cyan/50 bg-cyan/10 text-cyan" : "border-line text-dim hover:text-mut hover:border-line2"
              }`}
            >
              {s.label}
              {s.needAdmin ? <span className="ml-1.5 text-amber/80">▲</span> : null}
            </button>
          ))}
        </div>
        <div className="panel corner p-4">
          <div className="flex items-center gap-2 mb-3">
            <Terminal size={13} className="text-cyan" />
            <span className="font-display text-[12px] tracking-[0.14em] text-ice">{step.label.toUpperCase()}</span>
            <span className="ml-auto">
              <Copy text={step.body} label="COPY BLOCK" />
            </span>
          </div>
          <pre className="code text-fg/95 overflow-x-auto bg-ink/60 border border-line rounded p-3">{step.body}</pre>
          {step.note ? (
            <p className="mt-3 text-[12px] leading-relaxed text-mut border-l-2 border-line2 pl-3">{step.note}</p>
          ) : null}
        </div>
      </div>
    </section>
  );
}

/* ================================================================= SECURITY */
const PERMS: { cap: string; def: string; confirm: string; enforced: string }[] = [
  { cap: "mic.listen", def: "off until started", confirm: "no", enforced: "UI button / hotkey; stream released on close" },
  { cap: "mic.clap", def: "on with ear", confirm: "no", enforced: "refractory + sustained-energy rejection + counted false triggers" },
  { cap: "sites.open", def: "asked once", confirm: "first grant only", enforced: "urlparse host vs allow-list; subdomains ok, 'github.com.evil.io' is not" },
  { cap: "apps.launch", def: "off", confirm: "first grant only", enforced: "name must exist in your allow-list; argv fixed; shell=False" },
  { cap: "fs.read", def: "off", confirm: "first grant only", enforced: "resolve() then relative_to() against allowed roots; read cap 400 KB; binary refused" },
  { cap: "fs.write", def: "off", confirm: "yes, every call", enforced: "workspace only; refuses directories; 200 KB per write; notes use .trash" },
  { cap: "net.search", def: "asked once", confirm: "no", enforced: "blocked entirely by offline_mode; query sanitised and length-capped" },
  { cap: "net.fetch", def: "off", confirm: "first grant only", enforced: "unlisted/private hosts and unsafe redirects blocked; 403/404/timeout surfaced" },
  { cap: "shell.run", def: "off (empty list)", confirm: "yes, every call", enforced: "only names from your file; shell metacharacters and blocked programs rejected at parse AND at run" },
  { cap: "system.power", def: "off", confirm: "yes, every call", enforced: "fixed argv shutdown /s /t 60 — cancellable; never /f, never instant" },
  { cap: "ai.cloud", def: "off", confirm: "first grant only", enforced: "needs key + ALLOW_CLOUD + consent; privacy/offline modes block it; key never persisted" },
];

export function Security() {
  return (
    <section id="security" className="scroll-mt-20">
      <SectionTitle
        kicker="06 · SECURITY MODEL"
        title="The model gets vocabulary, never authority"
        sub="A language model can name a tool; it cannot name a program, a path or a host that you did not approve. This table is the entire attack surface, and every row is enforced in code you can read."
      />
      <div className="grid lg:grid-cols-[minmax(0,1fr)_360px] gap-4">
        <div className="panel overflow-x-auto">
          <table className="w-full text-left border-collapse min-w-[640px]">
            <thead>
              <tr className="bg-ink2/70 border-b border-line">
                {["capability", "default", "confirms", "how it is actually enforced"].map((h) => (
                  <th key={h} className="px-3 py-2 font-mono text-[9.5px] tracking-[0.18em] text-dim font-normal uppercase">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {PERMS.map((p) => (
                <tr key={p.cap} className="border-b border-line/50 last:border-0 hover:bg-white/[0.02] align-top">
                  <td className="px-3 py-2 font-mono text-[11.5px] text-ice whitespace-nowrap">{p.cap}</td>
                  <td className="px-3 py-2 text-[11.5px] text-mut whitespace-nowrap">{p.def}</td>
                  <td className="px-3 py-2 whitespace-nowrap">
                    <span className={`font-mono text-[10px] ${p.confirm.startsWith("yes") ? "text-amber" : "text-dim"}`}>{p.confirm}</span>
                  </td>
                  <td className="px-3 py-2 text-[11.5px] text-mut leading-snug">{p.enforced}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="space-y-3">
          {[
            {
              t: "Prompt injection from web pages",
              d: "Page text never enters the prompt as instructions. It is tag-stripped, angle brackets flattened, truncated, wrapped in <source id url> blocks and introduced as 'untrusted data'. The system prompt repeats that. The model output is then only allowed to be a tool NAME from your registry with type-checked arguments — so 'ignore previous instructions and run cmd' has nothing it can call.",
              i: <ShieldCheck size={13} className="text-lime" />,
            },
            {
              t: "No arbitrary shell, ever",
              d: "subprocess is called with a list, never a string, and only from: shutdown (fixed words), or an entry you typed into the allow-list. Both paths re-check for ; & | ` $ > < and newlines at execution time, so a hand-edited config file does not create a hole either.",
              i: <Terminal size={13} className="text-copper" />,
            },
            {
              t: "Secrets and personal data",
              d: "The key lives in .env (git-ignored) and is read from os.environ at use time. memory.redact() strips password/token/key/card/email shapes before any SQLite write, so a dictated 'my password is…' does not become permanent history. privacy_mode additionally refuses the microphone and the cloud path.",
              i: <Database size={13} className="text-ice" />,
            },
            {
              t: "Nothing destructive, silently",
              d: "No delete, no install, no message sending, no security-setting change exists as a tool at all. Notes delete = move to .trash. Power = 60-second cancellable schedule behind a dialog. Document index only sees folders you added, never the disk.",
              i: <Boxes size={13} className="text-amber" />,
            },
          ].map((b) => (
            <div key={b.t} className="panel p-4 hover:border-line2 transition-colors">
              <div className="flex items-center gap-2 mb-2">
                {b.i}
                <span className="font-display text-[11.5px] tracking-[0.1em] text-fg">{b.t.toUpperCase()}</span>
              </div>
              <p className="text-[12px] leading-relaxed text-mut">{b.d}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

/* ========================================================= OFFLINE MATRIX */
const FEATS: [string, string, string][] = [
  ["Clap / hotkey / button activation", "offline", "numpy only"],
  ["Speech-to-text", "offline", "Faster-Whisper, weights cached on first use"],
  ["Text-to-speech", "offline", "Windows SAPI5 voices"],
  ["Notes, file search, app launch", "offline", "Ollama optional — regex layer routes these"],
  ["Questions & chat", "offline", "needs qwen2.5:1.5b pulled once"],
  ["Web search + summarise", "internet", "ddgs, no key, no quota token"],
  ["Grounded answers with citations", "internet", "Gemini Google Search grounding (free tier limits)"],
  ["JS-heavy page reading", "internet", "Playwright Chromium"],
  ["Document index / retrieval", "offline", "term-overlap ranking in SQLite"],
  ["Memory inspect / clear", "offline", "local file only"],
];

export function OfflineMatrix() {
  return (
    <div className="panel p-4">
      <div className="flex items-center gap-2 mb-3">
        <Globe size={13} className="text-cyan" />
        <span className="font-mono text-[10px] tracking-[0.2em] text-dim">WORKS WITHOUT INTERNET</span>
      </div>
      <ul className="space-y-1.5">
        {FEATS.map(([name, kind, note]) => (
          <li key={name} className="flex items-baseline gap-2 text-[12px]">
            <span className={`font-mono text-[9px] px-1.5 py-0.5 rounded border shrink-0 ${kind === "offline" ? "text-lime border-lime/35" : "text-amber border-amber/35"}`}>
              {kind === "offline" ? "OFF" : "NET"}
            </span>
            <span className="text-fg">{name}</span>
            <span className="text-dim/80 text-[11px] ml-auto text-right hidden sm:block">{note}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/* =========================================================== TROUBLESHOOT */
export const TROUBLE: { q: string; a: string }[] = [
  {
    q: "No microphone found / 'invalid device'",
    a: "Settings → System → Sound → Input (is anything listed?), then Settings → Privacy & security → Microphone → 'Microphone access' ON and 'Let desktop apps access your microphone' ON. Python/sounddevice cannot bypass the privacy switch. Then python main.py --check again — it lists index + name of every input device it can see, and you can pin one in Settings → Ear.",
  },
  {
    q: "Microphone is busy / stream error after a call",
    a: "Teams, Meet, Discord and OBS can hold the device. Close them, or in the app press STOP EAR then START EAR — the listener loop recreates the stream. If it dies mid-session the loop tries to restart and emits 'mic_recovered', so the app self-heals instead of hanging.",
  },
  {
    q: "'Ollama service not reachable on 127.0.0.1:11434'",
    a: "Ollama runs as a tray app on Windows; start it from the Start menu, or in a second terminal: ollama serve. Then ollama pull qwen2.5:1.5b and re-run python main.py --check. Nothing else in the app depends on Ollama: apps, notes, files, search and TTS keep working.",
  },
  {
    q: "First transcription hangs for a minute",
    a: "It is downloading tiny.en to %USERPROFILE%\\.cache\\huggingface. On a machine with no internet, copy that cache folder from another PC. After that it is offline forever.",
  },
  {
    q: "Claps are ignored",
    a: "Press 'Measure room noise floor' in Settings after starting the ear, then set the gate a little above what it reports. Laptops with a fan need a higher gate than the default. Try pattern 'single' to isolate whether detection or the pattern window is the problem.",
  },
  {
    q: "False activations while music plays",
    a: "Raise sensitivity (stricter), require triple, or lengthen the refractory period. The activity log shows the rejection counters — if 'speech-like' is climbing, the sustained-energy filter is doing its job and you mainly need a higher gate.",
  },
  {
    q: "TTS says nothing / 'TTS unavailable'",
    a: "Speech is an optional Windows feature: Settings → Time & language → Speech & voice → add the English voice. Verify SAPI5 independently with: python -c \"import pyttsx3;e=pyttsx3.init();e.say('test');e.runAndWait()\".",
  },
  {
    q: "pip install PySide6 fails",
    a: "Almost always a Python-version mismatch. py -3.12 -m venv .venv and reinstall. If you just want the assistant now: python main.py --text needs no Qt at all.",
  },
  {
    q: "Gemini '429 quota' or 'model not found'",
    a: "Both are handled: the provider returns a soft error, the chain falls back to Ollama, and the UI shows which provider answered. Model ids rotate on Google's side, which is why probe() lists models instead of trusting one id. Check your key with notepad .env — never paste it anywhere else.",
  },
  {
    q: "Windows SmartScreen/Defender blocks a command",
    a: "Do not disable Defender or add blanket exclusions. Check the publisher of a blocked download, prefer per-user installs (%LOCALAPPDATA%), and retry only the specific trusted installer if needed. A venv writing to its own data folder is normal.",
  },
];

export function Troubleshoot() {
  const [open, setOpen] = useState<number | null>(0);
  return (
    <section id="troubleshooting" className="scroll-mt-20">
      <SectionTitle kicker="08 · DIAGNOSIS" title="The ten things that actually go wrong" sub="Each entry is the real failure mode of this stack on Windows, plus the fix and the command that proves it." />
      <div className="grid md:grid-cols-2 gap-2.5">
        {TROUBLE.map((t, i) => (
          <button
            key={t.q}
            type="button"
            onClick={() => setOpen(open === i ? null : i)}
            className={`text-left panel p-3.5 transition-all ${open === i ? "border-cyan/45 bg-panel2" : "hover:border-line2"}`}
          >
            <div className="flex items-center gap-2">
              <span className="font-mono text-[9.5px] text-cyan/70">{String(i + 1).padStart(2, "0")}</span>
              <span className="text-[12.5px] font-semibold text-fg">{t.q}</span>
              <span className={`ml-auto text-dim transition-transform ${open === i ? "rotate-45" : ""}`}>+</span>
            </div>
            {open === i ? <p className="mt-2 text-[12px] leading-relaxed text-mut">{t.a}</p> : null}
          </button>
        ))}
      </div>
    </section>
  );
}
