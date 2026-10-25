// Turns the real Python project in ./jarvis into a typed TS module the web app
// can display, copy and package — single source of truth, no duplicated code.
import { readdirSync, statSync, readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { join, relative, extname } from "node:path";

const ROOT = "jarvis";
const OUT = "src/data/project.ts";
const SKIP_DIRS = new Set(["__pycache__", "data", "logs", "models", ".venv", "node_modules", ".pytest_cache"]);
const ALLOW = new Set([".py", ".txt", ".md", ".example", ".gitignore", ".cfg", ".toml"]);

const DESCRIPTIONS = {
  "main.py": "Entry point: --check (stage 1), --text, --gui, --say. Builds every subsystem and tolerates anything missing.",
  "requirements.txt": "Pinned, 100% free dependencies chosen for an 8 GB laptop with no dGPU.",
  ".env.example": "Copy to .env. The only place a real API key may ever live.",
  ".gitignore": "Keeps .env, the SQLite DB, logs and model weights out of Git.",
  "README.md": "Setup, memory budget, security model, troubleshooting, honest test status.",
  "docs/VERIFICATION.md": "Command-by-command proof: what was executed with real output, what needs your hardware, and the three bugs the tests caught.",
  "config/settings.py": "Dataclass settings + JSON persistence. Secrets are read from env only, never stored.",
  "core/state.py": "Thread-safe state machine: idle / listening / transcribing / thinking / executing / speaking / error.",
  "core/router.py": "3-layer decision: control words → deterministic regex tools → model JSON tool call, with validation.",
  "core/assistant.py": "Orchestrator + threads + events. Mic → STT → router → tools → TTS, never blocking the GUI.",
  "audio/microphone.py": "One shared 16 kHz stream with subscriber fan-out, plus the energy-VAD CommandRecorder.",
  "audio/clap_detector.py": "Band-pass onset + pattern (single/double/triple) + refractory + sustained-noise rejection.",
  "audio/wake_word.py": "openwakeword wrapper, fuzzy transcript fallback, and the documented custom-model training recipe.",
  "audio/speech_to_text.py": "Faster-Whisper on CPU/int8, VAD trimming, Hinglish mode, file-transcribe for testing.",
  "audio/text_to_speech.py": "pyttsx3/SAPI with a queue thread, markdown cleaning and barge-in stop().",
  "ai/base_provider.py": "Common provider interface, tolerant JSON extraction and argument validation before execution.",
  "ai/ollama_provider.py": "Local brain over the Ollama HTTP API, model fallback, JSON mode, RAM unload.",
  "ai/gemini_provider.py": "Official google-genai SDK: live model probing, quota handling, search grounding, soft errors.",
  "tools/registry.py": "Schema generation, argument validation, permission gate and call log for every tool.",
  "tools/system_tools.py": "The tools themselves: apps, sites, search, files, notes, memory, stats, power, allow-listed commands.",
  "tools/web_search.py": "DuckDuckGo (no key) → Brave (optional) → browser fallback; concurrent text extraction.",
  "tools/browser.py": "Playwright Chromium lifecycle, blocked/404/timeout handling, static-HTML fallback.",
  "tools/app_launcher.py": "Allow-list launcher: fixed argv, shell=False, window focus via Win32, safe URL open.",
  "tools/file_manager.py": "Path sandbox: resolve-then-compare, read caps, binary refusal, filename search and grep.",
  "tools/notes.py": "Markdown notes with front-matter, append, find and soft-delete to .trash.",
  "memory/database.py": "SQLite: turns, summaries, facts, permissions, doc chunks + redaction and retention purge.",
  "memory/document_index.py": "Opt-in local index, term-overlap ranking — no embedding model eating your RAM.",
  "security/permissions.py": "Default-deny capability broker, path/URL checks, per-call confirmation for dangerous ops.",
  "security/command_allowlist.py": "Parses 'name = program args', rejects shell operators and destructive programs.",
  "ui/main_window.py": "PySide6 dashboard: boot worker thread, signals, orb, transcript cards, log, history table.",
  "ui/settings_dialog.py": "Every knob in plain English, with validation before save and a memory-wipe guard.",
  "ui/visualizer.py": "QPainter circular visualiser at 20 fps — cheap enough not to starve the audio callback.",
  "tests/test_clap_detector.py": "Synthetic transient tests: pattern, spacing, refractory, speech rejection.",
  "tests/test_permissions.py": "Default-deny, traversal, URL allow-list, dangerous-op confirmation, offline/privacy gates.",
  "tests/test_command_allowlist.py": "Shell-operator, destructive-program and unknown-name rejection tests.",
  "tests/test_router.py": "Routing, injection resistance, provider-crash fallback, hallucinated-tool rejection.",
  "tests/test_file_sandbox.py": "Sandbox reads/writes, notes soft delete, SQLite redaction and permission persistence.",
  "tests/conftest.py": "Puts the project root on sys.path so tests import like the app does.",
};

const groups = { core: "Core", audio: "Audio", ai: "AI brain", tools: "Tools", memory: "Memory", security: "Security", ui: "Desktop UI", tests: "Tests", config: "Config", docs: "Verification" };

function walk(dir) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    if (SKIP_DIRS.has(entry) || entry.endsWith(".pyc")) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...walk(full));
    else if (ALLOW.has(extname(entry)) || entry === ".gitignore" || entry === ".env.example") out.push(full);
  }
  return out.sort();
}

const files = walk(ROOT).map((full) => {
  const rel = relative(ROOT, full).replace(/\\/g, "/");
  const content = readFileSync(full, "utf8");
  const top = rel.split("/")[0];
  return {
    path: rel,
    lang: extname(rel) === ".py" ? "python" : rel.endsWith(".md") ? "markdown" : "ini",
    group: groups[top] ?? "Project",
    lines: content.split("\n").length,
    bytes: content.length,
    description: DESCRIPTIONS[rel] ?? "",
    content,
  };
});

const order = ["Project", "Verification", "Config", "Core", "Audio", "AI brain", "Tools", "Memory", "Security", "Desktop UI", "Tests"];
files.sort((a, b) => order.indexOf(a.group) - order.indexOf(b.group) || a.path.localeCompare(b.path));

mkdirSync("src/data", { recursive: true });
writeFileSync(
  OUT,
  `// GENERATED by scripts/emit-project-data.mjs — do not edit by hand.\n` +
    `// Source of truth is the real ./jarvis project on disk.\n` +
    `export type ProjectFile = { path: string; lang: string; group: string; lines: number; bytes: number; description: string; content: string };\n` +
    `export const PROJECT_FILES: ProjectFile[] = ${JSON.stringify(files, null, 1)};\n` +
    `export const TOTAL_LINES = ${files.reduce((a, f) => a + f.lines, 0)};\n` +
    `export const TOTAL_BYTES = ${files.reduce((a, f) => a + f.bytes, 0)};\n`
);
console.log(`emitted ${files.length} files, ${files.reduce((a, f) => a + f.lines, 0)} lines, ${Math.round(files.reduce((a, f) => a + f.bytes, 0) / 1024)} KB`);
