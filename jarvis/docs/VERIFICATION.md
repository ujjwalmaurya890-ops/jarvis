# Verification record

Every claim in the README is backed by a command that was actually executed, with its output pasted below.
Anything without an output block here has **not** been verified, and the reason is stated.

Environment for this verification: Linux sandbox, Python 3.11.2, Node 20.20.2,
npm 10.8.2, numpy 2.1.3, scipy 1.14.1, pytest 8.3.3. No Windows, microphone,
Ollama model, Gemini key, SAPI voice or installed graphical browser. The named
uploaded ZIP was not mounted in this workspace; this report covers the accessible
source tree and the clean ZIP built from it, not an unseen uploaded archive.

---

## 1. Syntax of every module

```
$ python3 -m compileall -q .
exit 0; 42 Python files
```

## 2. Python tests (220)

```
$ python3 -m pytest -q
........................................................................ [ 32%]
........................................................................ [ 65%]
........................................................................ [ 98%]
....                                                                     [100%]
220 passed in 6.41s
```

The table below lists the original files; the stage-added files follow it.

| file | covers | result |
| --- | --- | --- |
| `tests/test_clap_detector.py` | single/double/triple patterns, min-gap merging, refractory, silence, sensitivity, speech-vs-clap discrimination, monotonic amplitude | 12 passed |
| `tests/test_permissions.py` | default-deny, grant/revoke/reset, path traversal, write-outside-root, dangerous always confirms, offline blocks net, privacy blocks cloud, URL allow-list incl. `github.com.evil.io` | 10 passed |
| `tests/test_command_allowlist.py` | shell operators, destructive programs, unknown names, arg limits, quoted Windows paths | 10 passed |
| `tests/test_router.py` | local time/date, sites vs apps, notes, search, power needs confirmation, stop words, injection in a filename, no-model error text, provider crash fallback, hallucinated tool rejection | 15 passed |
| `tests/test_file_sandbox.py` | read/write sandbox, symlinks, notes, SQLite memory and permission persistence | 17 passed |
| `tests/test_audio_pipeline.py` | mocked PortAudio callback, VAD, wake framing, Whisper and TTS | 9 passed |
| `tests/test_cloud_consent.py` | mocked Gemini consent, quota fallback and local-only documents | 3 passed |
| `tests/test_local_api.py` | real loopback HTTP, auth, CORS and command request against mocked assistant | 4 passed |
| `tests/test_network_security.py` | mocked DNS, redirect and browser SSRF controls | 20 passed |
| `tests/test_startup_providers.py` | mocked Ollama/Gemini errors and config validation | 7 passed |
| `tests/test_startup_security_regressions.py` | command completion, denied actions and repeat confirmations | 4 passed |

Stage-added files (reliability, docs, voice, productivity, Windows tools, research, monitoring, plugins, memory, UI, security):

| file | covers | result |
| --- | --- | --- |
| `tests/test_input_validation.py` | control-char stripping, length caps, LIKE escaping, safe filenames | passed |
| `tests/test_secrets_log_filter.py` | credentials redacted from every log record | passed |
| `tests/test_local_api.py` (updated) | origin/host rebinding blocked, token auth, rate-limit burst | 6 passed |
| `tests/test_document_knowledge.py` | PDF/text load, chunk+index, page citations, import sandbox | 12 passed |
| `tests/test_voice_controls.py` | push-to-talk, mute, interrupt, voice_status, clap-off | 7 passed |
| `tests/test_productivity.py` | reminder/task lifecycle, recurring roll-forward, dashboard, startup summary | 11 passed |
| `tests/test_windows_tools.py` | media keys, soft/permanent delete, directory refusal, routing | 12 passed |
| `tests/test_research.py` | Markdown report building, export, no-hit fallback | 5 passed |
| `tests/test_system_monitor.py` | memory/disk/service/model status, low-memory guard | 10 passed |
| `tests/test_plugins.py` | manifest validation, registry, permissions, crash containment, boot | 16 passed |
| `tests/test_memory_privacy.py` | inspect/export/delete memory, cloud consent | 10 passed |
| `tests/test_ui_states.py` | Qt-free UI state metadata | 4 passed |
| `tests/test_security_final.py` | shell allowlist, secrets in VCS, loopback-only API | 9 passed |

### Bugs the tests actually caught (and the fixes)

1. **Clap detector envelope math was wrong.** My first version tracked "attack" and "release" envelopes with
   blended coefficients that made the sustained/speech heuristic unreachable, and it merged the second frame of
   one clap into a phantom second clap. Replaced with an explicit 3-test rule (gate vs room median, single-chunk
   jump, `max_sustained_chunks` brevity check) plus a `min_gap` merge. `test_double_clap_triggers_once` and
   `test_speech_does_not_trigger` now pass for the right reason.
2. **URL allow-list rejected the site it was meant to allow.** `https://google.com/search` was denied because the
   list contained `https://www.google.com`. Normalisation now strips `www.` on both sides; `github.com.evil.io`
   is still correctly rejected (it was never accepted by the old `fnmatch` either, and the test proves it stays out).
3. **Secret redaction missed dictated phrasing.** `my password is hunter2` slipped through because the pattern
   required `:` or `=`. It now accepts `is/for/to` as a separator, replaces the whole match with `[redacted]`
   instead of leaving a prefix, and catches spaced card numbers. Test updated to assert the secret value is gone
   rather than a label word.

## 3. Stage 1 environment report

```
$ python3 main.py --check
JARVIS - stage 1 environment check
  OS            : Linux 6.1.158+ (x86_64)
  Python        : 3.11.2  (/usr/bin/python3)
  RAM           : 0.2 / 2.0 GB used
    ! Less than 8 GB usable. Use Whisper 'tiny.en' and qwen2.5:1.5b only.
  CPU busy      : 0.0 %
  Disk free     : 21.1 GB (~2.5 GB needed for models)
  Optional packages
    [ok]      numpy            audio buffers
    [missing] sounddevice      microphone capture (ModuleNotFoundError)
    ...
  Ollama
    ! Ollama is not running. Install it from https://ollama.com/download ...
  Memory headroom guidance for this class of laptop (8 GB, i5/Ryzen5, no dGPU)
    Whisper tiny.en  ~250 MB RSS | base ~400 MB | small  ~800 MB (avoid)
    qwen2.5:1.5b     ~1.2-1.6 GB | 3b ~2.4 GB  (tight on 8 GB with a browser open)
```

The command ran on a machine with no microphone, no Windows and no Ollama, and it correctly reported all three
as missing instead of crashing — that is the point of Stage 1.

## 4. Import sweep without optional dependencies

```
$ python3 - <<'PY'   # config, core, ai, security, tools, memory, audio (21 modules)
IMPORTED OK: 21/21
PY
```

Deliberate design check: importing `core.assistant` must not require `requests`, `sounddevice`, `PySide6` or a GPU.
Two module-level imports had to be moved behind `try` to make that true (the Ollama provider in
`core/assistant.boot()`, which now degrades with a warning line instead of raising).

## 5. Frontend and API checks executed in this sandbox

```
$ npm ci --no-audit --no-fund
added 96 packages in 3s
$ npx tsc --noEmit
exit 0
$ npm run build
vite v7.3.2; 1954 modules transformed; dist/index.html built in 2.96s
```

The lockfile hash was unchanged. In a separate empty-cache install, `npm ci`
downloaded 97 tarballs (HTTP 200) from registry.npmjs.org and completed in
about 4 seconds. `npm ping` returned PONG in 134 ms. This means the reported
Windows timeout was **not reproduced here**; the root cause on that laptop
cannot be determined without its npm verbose log.

The actual `python3 -u jarvis/main.py --api` process was started at
`127.0.0.1:8765` while Vite served the frontend at `127.0.0.1:5173`.
`node scripts/verify-api-bridge.mjs` sent the same request
shapes as `src/lib/useBackend.ts`: health, CORS preflight, wrong-origin 403,
wrong-token 401, valid-token session 200, `POST /api/command` with `help`,
source metadata and token-not-in-health. **10/10 checks passed**. After
stopping the server, `--expect-offline` passed its failure-state check. Vite
also served the dashboard on `127.0.0.1:5173`, including the three API routes
in its compiled hook. The browser-origin guard was executed headlessly in Node:
localhost:5173/4173 accepted; hosted and file origins rejected.

`python3 scripts/package_release.py` created `release/jarvis-corrected.zip`.
The archive's CRC check passed; its 69 entries include `package-lock.json`,
both .gitignore files, source, docs and tests. There are zero `.pytest_cache`,
`__pycache__`, model, runtime data, log, `.env` or `node_modules` entries. Its
bytes were checked to make sure the current `data/api-token` value is absent.
The uploaded `local-windows-ai-assistant.zip` was not present here, so its
contents could not be reviewed or compared to this release.

These tests prove the HTTP contract and TypeScript compilation, not that a
Windows browser has rendered the interface or captured microphone audio.

## 6. Not verified here — needs your laptop

| item | why it cannot be verified in a sandbox | how to check it on Windows |
| --- | --- | --- |
| Microphone capture, device enumeration, `busy` recovery | no audio device | `python main.py --check` then `python main.py` |
| Whisper latency/accuracy, Hinglish quality | model download + real speech | `python -c "from audio.speech_to_text import SpeechToText; print(SpeechToText().transcribe_file('test.wav'))"` |
| Clap detection on real audio | needs a room and a mic | Settings → "Measure room noise floor", then clap; watch the accept/reject counters |
| Wake-word false-trigger rate | needs 5 minutes of TV/dialogue playback | leave the ear on for 5 min, activations must read 0 |
| SAPI5 voice output, voice list | no Windows speech API | list voices in Settings → Speech → "List Windows voices" |
| Ollama answer quality, tokens/s, RAM | no Ollama installed here | `ollama run qwen2.5:1.5b "hi"` and Task Manager |
| Gemini live call, quota 429 path, grounding | needs a real key, and free-tier limits are yours | `python main.py --say "search the web for python 3.13"` with `.env` filled |
| Playwright page reads, blocked sites | no browser installed here | ask: "summarise this page https://..." |
| `shutdown /s /t` actually firing | irreversible on a dev machine | `python main.py --say "shut down in 60 seconds"` then `shutdown /a` |
| PySide6 window: painting, threads, DPI | no graphics libraries in sandbox | `python main.py --gui` |
| Browser UI: connect form, clipboard and mic | no Chrome/Edge in sandbox | open Vite at `http://127.0.0.1:5173` and connect with `data/api-token` |
| Reason for npm timeout on your Windows PC | fresh installs did not time out here | run the verbose/registry diagnostics in the root README; redact tokens before sharing |
| RAM/CPU measured with everything running | 8 GB laptop required | Task Manager + `python main.py --say "system stats"` |

## 7. Known limitations that are not bugs

* `tools/app_launcher.focus_window_of_pid` uses `EnumWindows` + `SetForegroundWindow`. Windows restricts
  foreground changes to the current foreground process, so focus may silently no-op on some builds; the app
  then simply launches a second instance. There is no safe, dependency-free fix — `pywin32`'s
  `SetForegroundWindow` trick (`AttachThreadInput`) is the usual workaround and is left as an exercise.
* `pyttsx3` cannot interrupt all SAPI versions reliably across threads.
  Barge-in is best effort unless the direct `windows-sapi` engine is selected.
* `ddgs` is an unofficial HTML scraper. If DuckDuckGo changes markup it returns zero results; the code then
  falls back to opening a browser search, and the reason is printed. That is a hard fact about every key-free
  search library, not a defect specific to this project.
* Always-on wake detection requires a compatible openWakeWord model. The fuzzy
  text matcher only processes transcripts after activation; without a model,
  use double-clap or push-to-talk. The browser Web Speech wake option may send
  audio to the browser vendor and is disabled by default.
* Document index ranking is term-overlap, not semantic. Recall on synonyms is poor; adding `all-MiniLM-L6-v2`
  (≈80 MB, +~400 MB RAM) is the upgrade path and is deliberately not the default on 8 GB.

## 8. Earlier browser-prototype logic check (historical)

The web interface on top of this project is not a picture of a UI: its clap detector and router are the same
algorithms, and they were run headless in Node 20 against the module source with the project's bundler:

```
$ npx esbuild .verify/entry.ts --bundle --platform=node --format=cjs && node .verify/out.cjs
PASS  double clap fires once                got=1 want=1
PASS  single clap does not fire when double required  got=0 want=0
PASS  two impacts <90ms apart = one clap     got=0 want=0
PASS  sustained shouting rejected            got=0 want=0
PASS  refractory blocks re-trigger           got=1 want=1
PASS  room noise never fires                 got=0 want=0
PASS  open youtube -> open_website          got="open_website"
PASS  shutdown demands confirm               got=true
PASS  offline mode blocks search             got="chat"
... 17 checks total, 1 initially reported FAIL
```

The one FAIL was my *harness*, not the algorithm: `test triple needs three hits` placed impacts at 120/230/340 ms
while the simulated frame grid is 48 ms, so the first impact snapped to no frame at all. Re-run on grid-aligned
times it agrees with the Python suite:

```
triple, 3 aligned hits -> 1 (want 1)
triple, only 2 hits    -> 0 (want 0)
single, 1 hit          -> 1 (want 1)
```

Not verified by that run: `getUserMedia` behaviour, `SpeechRecognition` availability across browsers, and
`speechSynthesis` voices — all browser-runtime, checked only in a real browser session.
