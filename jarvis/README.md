# JARVIS — local-first voice assistant for Windows

A modular, free, offline-capable desktop assistant for a laptop with **8 GB RAM, an i5/Ryzen 5 and no
dedicated GPU**. It listens for a clap pattern or a wake phrase, transcribes speech locally with
Faster-Whisper, thinks with a local Ollama model (optional free-tier Gemini fallback), researches on the
web, and performs *allow-listed* actions on Windows.

> **Honesty note.** This repository was written and verified in a Linux CI sandbox: all 111 Python tests
> pass, every module byte-compiles, and `python main.py --check` runs. It has **not** been executed on
> your Windows laptop, and it has **not** been run against a real microphone, real SAPI voices, a real
> Ollama server or a real Gemini key — none of those exist in the sandbox. Anything that needs your
> hardware is marked "verify on your PC" in the code and in `docs/VERIFICATION.md`. Nothing here is
> claimed to be tested unless a command output is recorded.

---

## What works offline vs. what needs internet

| Capability | Offline? | Notes |
|---|---|---|
| Clap / hotkey / button activation | ✅ | numpy only |
| Speech-to-text | ✅ | Faster-Whisper `tiny.en`, model downloaded once |
| Text-to-speech | ✅ | Windows SAPI5 via pyttsx3 |
| Local Q&A, notes, file search, app launching | ✅ | Ollama + SQLite |
| Web search / page reading | ❌ | DuckDuckGo (`ddgs`) or Gemini grounding; browser fallback if no key |
| Gemini cloud model | ❌ | Optional; needs `GEMINI_API_KEY` and `ALLOW_CLOUD=1` |
| Document index | ✅ | Opt-in; only the folders you choose |

## Architecture

```
 microphone (one 16 kHz stream, sounddevice)
   ├─> clap detector (band-pass onset + pattern + refractory)
    └─> wake word (only when a compatible local model is selected)
        └─> CommandRecorder (energy VAD) ─> SpeechToText (Faster-Whisper)
              └─> Router:  control words -> regex tools -> model JSON/tools -> fallback
                    ├─> ToolRegistry -> PermissionBroker (default-deny) -> handlers
                    ├─> ProviderChain: Ollama -> Gemini -> (error, never a crash)
                    └─> TextToSpeech (interruptible)
                         SQLite memory: turns, summaries, facts, permissions, doc index
```

Everything is a module under `core/`, `audio/`, `ai/`, `tools/`, `memory/`, `security/`, `ui/`. No
subsystem imports a GUI; the GUI imports everything. That is what makes the tests possible.

The React dashboard lives one folder above `jarvis/`, next to `package-lock.json`.
For exact two-terminal PowerShell backend/frontend commands, see the root `README.md`.
`python main.py --api` serves typed commands only on 127.0.0.1:8765; use the
temporary token in `data/api-token` to connect from local Vite at port 5173.
The browser demo works without Python but is not the Ollama backend.

## Quick start (Windows PowerShell)

```powershell
# 1. Python 3.12 (winget is the least painful route; no admin needed for --scope user)
winget install -e --id Python.Python.3.12

# 2. Get the project, then go into it
cd path\to\jarvis

# 3. Virtual environment (per-user, no admin rights)
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip

# 4. Dependencies
pip install -r requirements.txt

# 5. Stage 1: prove the machine can do this at all
python main.py --check

# 6. Local model
winget install -e --id Ollama.Ollama      # or download from ollama.com
ollama pull qwen2.5:1.5b
python -c "import requests;print(requests.get('http://127.0.0.1:11434/api/tags').json())"

# 7. Speech model (first run downloads ~75 MB to %USERPROFILE%\.cache)
python -c "from faster_whisper import WhisperModel; WhisperModel('tiny.en', device='cpu', compute_type='int8')"

# 8. Tests, then run it
python -m pytest -q
python main.py --text        # typed mode - do this first
python main.py --gui         # PySide6 dashboard
```

If `.\.venv\Scripts\Activate.ps1` fails with "running scripts is disabled", you do **not** need to
lower PowerShell's policy system-wide. Use the interpreter directly instead:

```powershell
# The .bat file does not activate a parent PowerShell process:
.\.venv\Scripts\python.exe main.py --check
```

Optional: browser rendering and cloud intelligence

```powershell
playwright install chromium                                   # ~150 MB, only for JS-heavy pages
Copy-Item .env.example .env                                   # then edit with Notepad
notepad .env                                                  # add GEMINI_API_KEY=... and ALLOW_CLOUD=1
```

## Memory budget (why the defaults are what they are)

| Piece | Resident RAM | Verdict on 8 GB |
|---|---|---|
| Whisper `tiny.en` (int8) | ~250 MB | default |
| Whisper `base.en` | ~400 MB | fine, slower |
| Whisper `small` | ~800 MB | avoid |
| `qwen2.5:1.5b` | 1.2–1.6 GB | default |
| `qwen2.5:3b` | ~2.4 GB | only with a browser closed |
| Chromium (Playwright) | ~150 MB/context | launch on demand, close after |
| PySide6 UI | ~90 MB | fine |

`Settings → "go to sleep"` (or the *Unload model* button) calls `keep_alive: 0` on Ollama so the RAM
comes back when you are not talking.

## Security model (read this once)

* **Default deny.** Tool capabilities need a grant or confirmation. The microphone is armed
   only when you press START EAR. Cloud AI needs a key, a switch, and user permission.
* **No shell, ever.** `subprocess` is only ever called with a fixed `argv` list from your allow-list,
  with `shell=False`. Model text is never interpolated into a command.
* **Path sandbox.** Reads and writes must resolve *inside* a folder you picked; `..`, symlinks and
  drive-relative tricks are resolved first and rejected.
* **Web content is data.** Page text is stripped of tags, wrapped in `<source>` and quoted to the
  model as untrusted input; prompt-injection-shaped phrases are removed by `sanitise_query()`.
* **Confirmation for the scary stuff.** `system_power` schedules `shutdown /t 60` (cancellable) and
  always requires a dialog; `run_command` and writes outside the workspace likewise.
* **Secrets stay out.** The API key lives only in `.env` (git-ignored). `memory.redact()` scrubs
  password/token/key/email/card patterns before anything is written to SQLite.
* **No silent deletes.** `notes.delete()` moves files to `data/.trash/`.

## Project layout

```
jarvis/
  main.py                     CLI + --check + --gui launcher
  api/server.py               loopback-only typed-command API with bearer auth
  requirements.txt            pinned, all FOSS
  .env.example                copy to .env (never committed)
  config/settings.py          dataclass settings, JSON persistence, no secrets
  core/assistant.py           orchestrator + threads + events
  core/router.py              control words -> regex tools -> model JSON
  core/state.py               state machine (idle/listening/thinking/executing/speaking/error)
  audio/microphone.py         one shared stream + CommandRecorder (VAD)
  audio/clap_detector.py      double-clap detection (tested)
  audio/wake_word.py          openwakeword wrapper + training recipe + fuzzy fallback
  audio/speech_to_text.py     Faster-Whisper, Hinglish option
  audio/text_to_speech.py     pyttsx3/SAPI, interruptible
  ai/base_provider.py         common interface, JSON extraction, arg validation
  ai/ollama_provider.py       local default
  ai/gemini_provider.py       optional cloud + search grounding, quota-aware
  tools/registry.py           schema, validation, permission gate
  tools/system_tools.py       the tools themselves
  tools/web_search.py         ddgs / Brave / browser fallback
  tools/browser.py            Playwright with static-HTML fallback
  tools/app_launcher.py       allow-list launcher + window focus
  tools/file_manager.py       sandboxed read/search/grep
  tools/notes.py              markdown notes, soft delete
  memory/database.py          SQLite: turns, summaries, facts, permissions, index
  memory/document_index.py    term-based retrieval, no embeddings needed
  security/permissions.py     default-deny broker + path/URL checks
  security/command_allowlist.py  parse + validate your named commands
  ui/main_window.py           dashboard (threads + signals)
  ui/settings_dialog.py       every knob, in plain English
  ui/visualizer.py            circular audio orb
  tests/                      111 offline and mocked/loopback integration tests
```

## Tests

```powershell
python -m pytest -q          # 111 passed in the CI sandbox; audio/cloud are mocked
```

Covered: clap pattern acceptance/rejection, refractory, speech-vs-clap discrimination, permission
default-deny, path traversal, URL allow-list, command allow-list injection attempts, router tool
routing + hallucinated-tool rejection + provider-crash fallback, file sandbox, notes soft delete,
SQLite redaction and retention.

Not covered (needs your machine): real audio capture, Whisper accuracy, SAPI voice output, Ollama
latency, Gemini quota behaviour, Playwright page loads, `shutdown` actually firing.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `No input device found` | Settings → System → Sound → Input; then Settings → Privacy & security → Microphone → "Let desktop apps access". |
| `Microphone is busy` | Teams/Meet/Discord hold the device exclusively. Close them, or set `input_device_index`. |
| `Ollama service not reachable` | `ollama serve` in its own terminal, or start Ollama from the Start menu. Then `ollama pull qwen2.5:1.5b`. |
| First `tiny.en` load hangs | It is downloading. Offline machine: copy `%USERPROFILE%\.cache\huggingface` from another PC. |
| `TTS unavailable` | Speech is a Windows feature: Settings → Time & language → Speech → "Add voices". |
| Claps ignored | Raise nothing — *lower* "Trigger threshold" in 0.005 steps; use "Measure room noise floor". |
| False activations | Increase sensitivity, require `triple`, or turn claps off and use Ctrl+Space. |
| `playwright: browser not found` | `playwright install chromium` (needs internet once). |
| UI won't start | `pip install PySide6` — or use `python main.py --text`, which has no GUI dependency. |
| Slow answers | Confirm `num_ctx` is 2048, close Electron apps, keep `beam_size = 1`. |

Never disable Windows Defender or add blanket exclusions to make this run. Verify any blocked
download with its publisher; prefer per-user installs rather than system-wide security changes.

## Deliberately not implemented

* System volume control, keyboard/mouse synthesis, and clicking inside other apps' windows (needs
  `pycaw`/`pyautogui`/UIA and is the highest-risk surface). Extension point: add a tool in
  `tools/system_tools.py`, give it its own `Capability`, and it appears in Settings automatically.
* A trained "Hello JARVIS" model — none is shipped. Recipe in `audio/wake_word.py`.
* Any "always listening in the cloud" behaviour. There is no upload path in this code.

## Startup on login (optional, off by default)

```powershell
 $lnk = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\JARVIS.lnk"
 $ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut($lnk)
 $s.TargetPath = "$PWD\.venv\Scripts\pythonw.exe"; $s.Arguments = "main.py --gui"
 $s.WorkingDirectory = "$PWD"; $s.Save()
```
Remove the file to undo. `pythonw.exe` starts without a console window.
