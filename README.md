# JARVIS: Windows desktop assistant and local web dashboard

This archive contains the existing modular Python assistant in `jarvis/` and
its React/Vite dashboard in the repository root. The dashboard starts in a
browser-only demo. It talks to Python only after you start the authenticated
loopback API and paste its temporary token into the connection panel.

## Features

Ten independently testable feature groups, implemented in small stages:

1. **Voice system** - wake-word / clap activation, push-to-talk fallback,
   barge-in interruption, mute, configurable voice output, mic status.
2. **Document knowledge base** - import PDF/text, chunk + index locally, answer
   with source filename and page number.
3. **Productivity** - SQLite reminders and recurring tasks, task dashboard,
   startup summary.
4. **Windows tools** - launch approved apps, search permitted folders, media
   keys, safe file deletes (recycle folder).
5. **Research mode** - web search, safe page extraction, Markdown report with
   source links.
6. **System monitoring** - CPU/RAM/disk/service/model status, low-memory mode
   for 8 GB.
7. **Plugin architecture** - approved-plugin registry with manifests, schemas,
   permissions, error containment, enable/disable.
8. **Memory & privacy** - inspect/export/delete memory; cloud AI is opt-in and
   always needs explicit consent.
9. **UI** - PySide6 desktop + React dashboard with listening/thinking/speaking/
   error states.
10. **Reliability & security** - input validation, loopback-only API with token
    + rate limit, browser-destination allow-list, no arbitrary shell, secrets
    filtered from logs and kept out of source control.

220 Python tests pass in the Linux sandbox; see `jarvis/docs/VERIFICATION.md`
for the hardware-only items that still need a real laptop.

## Prerequisites (Windows PowerShell)

Run these once in a normal PowerShell window. If winget is unavailable, use
the installers from python.org, nodejs.org and ollama.com instead.

```powershell
winget install -e --id Python.Python.3.12 --scope user
winget install -e --id OpenJS.NodeJS.LTS --scope user
winget install -e --id Ollama.Ollama
py -3.12 --version
node --version
ollama --version
```

Node 20.19+ or newer is required by the existing Vite 7 lockfile. Ollama may
ask for elevation when using its system installer; the app itself needs no
administrator privileges. Restart PowerShell after installing prerequisites.

If you received `jarvis-corrected.zip`, extract it and change to its root:

```powershell
Expand-Archive -LiteralPath .\jarvis-corrected.zip -DestinationPath .\jarvis-fixed -Force
Set-Location .\jarvis-fixed
```

## Python setup

```powershell
py -3.12 -m venv .\jarvis\.venv
& .\jarvis\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\jarvis\.venv\Scripts\python.exe -m pip install -r .\jarvis\requirements.txt
ollama pull qwen2.5:1.5b
& .\jarvis\.venv\Scripts\python.exe .\jarvis\main.py --check
& .\jarvis\.venv\Scripts\python.exe .\jarvis\main.py --say "help"
```

No PowerShell execution-policy change or Defender exception is needed. The
first Faster-Whisper transcription downloads the `tiny.en` model once; then
it can run offline. Ollama must be running locally for open-ended questions.

## Start backend and dashboard

Use **two separate PowerShell windows**, both at the extracted project root:

```powershell
# Window A: authenticated API, bound to 127.0.0.1:8765
& .\jarvis\.venv\Scripts\python.exe .\jarvis\main.py --api
```

```powershell
# Window B: React/Vite, bound to 127.0.0.1:5173
npm ci --no-audit --no-fund
npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
Start-Process http://127.0.0.1:5173
```

Read `jarvis\data\api-token` locally, paste the value into the dashboard's
**Desktop link** panel, and click **Connect**. Never paste it into a hosted
preview, screenshot, ticket or log. It is regenerated each time the API
starts. A typed command such as `help` now goes through Python. Sensitive
actions cannot be approved through the API; use the desktop UI for those.
The browser mic/voice controls remain browser-only. Browser speech recognition
may contact its vendor; use the Python desktop app for private, offline speech.

```powershell
# Optional: the separate PySide6 desktop UI
& .\jarvis\.venv\Scripts\python.exe .\jarvis\main.py --gui

# Tests and production build
& .\jarvis\.venv\Scripts\python.exe -m pytest -q .\jarvis\tests
npm run build
```

## If `npm ci` times out on your laptop

Do not remove `package-lock.json`. This build installed 96 packages with
`npm ci --no-audit --no-fund` in 3 seconds in the Linux sandbox; a fresh npm
cache downloaded 97 tarballs from the public registry in about 4 seconds.
Those measurements do **not** diagnose a timeout on your Windows machine.
Run these there and inspect the first actual network/permission error:

```powershell
node --version
npm --version
npm config get registry
Resolve-DnsName registry.npmjs.org
Test-NetConnection registry.npmjs.org -Port 443
(Invoke-WebRequest -Uri https://registry.npmjs.org/react -Method Head -TimeoutSec 15).StatusCode
npm ci --no-audit --no-fund --fetch-retries=0 --fetch-timeout=25000 --loglevel=verbose *>&1 | Tee-Object .\npm-ci-diagnostic.log
$LASTEXITCODE
```

If the DNS/HTTP checks fail, the network path is the problem; if they pass but
npm times out, read `npm-ci-diagnostic.log` for the *specific* tarball or
postinstall step. Redact any proxy credentials or registry tokens before
sharing that log. A clean `npm ci` removes `node_modules` for you; deleting the
lockfile is not a fix.

For Python permissions, model downloads, voice setup and full configuration,
see `jarvis/README.md`. Actual executed checks and hardware-only checks are in
`jarvis/docs/VERIFICATION.md`.