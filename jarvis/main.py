"""JARVIS entry point (headless / terminal mode).

    python main.py                 # talk to it in the console
    python main.py --text          # type instead of speak
    python main.py --gui           # open the PySide6 dashboard
    python main.py --check         # stage 1: environment report, no mic access
    python main.py --say "hi"      # one-shot test of the whole pipeline

Exit codes: 0 ok, 2 partial (something unavailable but usable), 3 fatal.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import DATA_DIR, LOGS_DIR, Settings, WORKSPACE_DIR  # noqa: E402


def setup_logging(level: str = "INFO", to_file: bool = True) -> logging.Logger:
    """Console + rotating file log. Keys are never written to either."""
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)-18s %(message)s", "%H:%M:%S")
    root = logging.getLogger("jarvis")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()
    # Defence in depth: scrub credentials from every record before it is emitted,
    # so a mistaken log line can never persist a key or a dictated password.
    from security.secrets_filter import install_secrets_filter

    install_secrets_filter(root)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    if to_file:
        try:
            fh = RotatingFileHandler(
                LOGS_DIR / "jarvis.log",
                maxBytes=1_000_000,
                backupCount=3,
                encoding="utf-8",
            )
            fh.setFormatter(fmt)
            root.addHandler(fh)
        except OSError:
            pass
    return root


# --------------------------------------------------------------------------- #
# Stage 1: environment report
# --------------------------------------------------------------------------- #
def check_environment() -> int:
    """Print what this machine can and cannot do. Touches no microphone, no model."""
    import platform

    from tools.system_tools import _quick_stats

    stats = _quick_stats()
    print("=" * 68)
    print("JARVIS - stage 1 environment check")
    print("=" * 68)
    print(f"  OS            : {stats.get('system')}")
    print(f"  Python        : {platform.python_version()}  ({sys.executable})")
    ram_total = float(stats.get("ram_total") or 0)
    ram_used = float(stats.get("ram_used") or 0)
    print(f"  RAM           : {ram_used:.1f} / {ram_total:.1f} GB used")
    if ram_total and ram_total < 7:
        print("    ! Less than 8 GB usable. Use Whisper 'tiny.en' and qwen2.5:1.5b only.")
    print(f"  CPU busy      : {stats.get('cpu', 'n/a')} %")
    print(f"  Disk free     : {stats.get('disk_free', 'n/a')} GB (~2.5 GB needed for models)")
    print(f"  Project root  : {ROOT}")
    print(f"  Workspace     : {WORKSPACE_DIR}")

    print("\n  Optional packages")
    for pkg, why in [
        ("numpy", "audio buffers"),
        ("sounddevice", "microphone capture"),
        ("scipy", "clap band-pass filter"),
        ("faster_whisper", "offline speech to text"),
        ("pyttsx3", "offline text to speech"),
        ("requests", "Ollama HTTP"),
        ("bs4", "web page extraction"),
        ("ddgs", "key-free web search"),
        ("playwright", "JS-rendered pages (optional)"),
        ("PySide6", "desktop UI"),
        ("google.genai", "cloud fallback (optional)"),
    ]:
        try:
            __import__(pkg)
            print(f"    [ok]      {pkg:<16} {why}")
        except Exception as exc:
            note = f"({type(exc).__name__})"
            print(f"    [missing] {pkg:<16} {why} {note}")

    print("\n  Microphone devices")
    try:
        import sounddevice as sd

        devices = sd.query_devices()
        inputs = [d for d in devices if int(d.get("max_input_channels", 0)) > 0]
        if not inputs:
            print("    [!] No input device found. Check Settings > Sound > Input, and")
            print("        Windows privacy: Settings > Privacy & security > Microphone.")
        for i, d in enumerate(devices):
            if int(d.get("max_input_channels", 0)) > 0:
                print(f"    [{i}] {d['name']}  {int(d['default_samplerate'])} Hz")
    except ImportError:
        print("    sounddevice not installed yet - install requirements first.")
    except Exception as exc:
        print(f"    Could not enumerate devices: {exc}")

    print("\n  Ollama")
    try:
        import requests

        r = requests.get("http://127.0.0.1:11434/api/tags", timeout=2.5)
        names = [m.get("name") for m in r.json().get("models", [])]
        print(f"    service   : running (version {r.json().get('version', '?')})" if r.ok else "    service   : HTTP error")
        print(f"    models    : {', '.join(n for n in names if n) or 'none pulled yet'}")
        if "qwen2.5:1.5b" not in " ".join(names or []):
            print("    ! Run:  ollama pull qwen2.5:1.5b")
    except Exception:
        print("    ! Ollama is not running. Install it from https://ollama.com/download")
        print("      then:  ollama serve   (a new terminal)   and   ollama pull qwen2.5:1.5b")

    print("\n  Memory headroom guidance for this class of laptop (8 GB, i5/Ryzen5, no dGPU)")
    print("    Whisper tiny.en  ~250 MB RSS | base ~400 MB | small  ~800 MB (avoid)")
    print("    qwen2.5:1.5b     ~1.2-1.6 GB | 3b ~2.4 GB  (tight on 8 GB with a browser open)")
    print("    Chromium/Playwright ~150 MB per context - keep it headless and closed when idle")
    print("    Close one Electron app (VS Code/Teams/Discord) before you start a voice session.")
    print("=" * 68)
    missing = []
    for pkg in ("numpy", "sounddevice", "requests"):
        try:
            __import__(pkg)
        except Exception:
            missing.append(pkg)
    return 0 if not missing else 2


# --------------------------------------------------------------------------- #
# Component factory (shared by CLI and GUI)
# --------------------------------------------------------------------------- #
def build_components(
    settings: Settings, *, want_mic: bool = True, want_tts: bool = True
):  # noqa: ANN201, ANN202
    """Wire subsystems without downloading weights or launching Chromium."""
    from ai.gemini_provider import GeminiProvider
    from ai.ollama_provider import OllamaProvider
    from audio.clap_detector import ClapConfig, ClapDetector
    from audio.microphone import CommandRecorder, Microphone
    from audio.speech_to_text import SpeechToText
    from audio.text_to_speech import TextToSpeech
    from audio.wake_word import WakeConfig, WakeWordDetector
    from core.assistant import Jarvis
    from memory.database import MemoryDB
    from memory.document_index import DocumentIndex

    out: dict[str, object] = {"warnings": []}
    mic = None
    if want_mic and not settings.privacy.privacy_mode:
        try:
            mic = Microphone(
                sample_rate=settings.audio.sample_rate,
                block_duration=settings.audio.block_duration,
                device=settings.audio.input_device_index,
            )
        except Exception as exc:
            out["warnings"].append(str(exc))
    out["mic"] = mic

    a = settings.audio
    clap = ClapDetector(
        ClapConfig(
            pattern=a.clap_pattern,
            threshold=a.clap_threshold,
            min_gap=a.clap_min_gap,
            max_gap=a.clap_max_gap,
            refractory=a.refractory,
            sensitivity=a.clap_sensitivity,
        ),
        sample_rate=a.sample_rate,
    )
    out["clap"] = clap if a.enabled else None

    wake = WakeWordDetector(
        WakeConfig(
            wake_word=a.wake_word,
            model_dir=a.wake_model_dir,
            use_fuzzy_transcript=True,
        ),
        sample_rate=a.sample_rate,
    )
    out["wake"] = wake

    stt = SpeechToText(
        model_size=settings.stt.model_size,
        compute_type=settings.stt.compute_type,
        threads=settings.stt.threads,
        beam_size=settings.stt.beam_size,
        language=settings.stt.language,
        initial_prompt=settings.stt.initial_prompt,
        condition_on_previous_text=settings.stt.condition_on_previous_text,
    )
    out["stt"] = stt
    if mic is not None:
        out["recorder"] = CommandRecorder(
            mic,
            min_energy=settings.audio.min_energy,
            max_seconds=settings.audio.max_command_seconds,
            start_timeout=settings.audio.auto_stop_after,
            silence_seconds=0.9,
        )

    # The voice engine is created now but initialised on its worker thread only
    # when the first reply is spoken. --say/--api never open an audio device.
    out["tts"] = TextToSpeech(
        engine=settings.tts.engine,
        rate=settings.tts.rate,
        volume=settings.tts.volume,
        voice_name=settings.tts.voice_name,
    ) if want_tts else None

    db = MemoryDB(ROOT / "data" / "jarvis.db", retention_days=settings.privacy.retention_days)
    out["db"] = db
    index = DocumentIndex(db) if settings.privacy.index_documents else None
    out["index"] = index

    providers: list[object] = []
    local = OllamaProvider(settings.ai.ollama_host, settings.ai.ollama_model, settings.ai.ollama_fallback_model)
    key = settings.gemini_api_key
    cloud = GeminiProvider(key, settings.ai.gemini_model, settings.ai.temperature) if key else None
    # "gemini" selects cloud first; a quota/network failure ALWAYS tries Ollama.
    if settings.ai.provider == "gemini" and cloud is not None:
        providers = [cloud, local]
    else:
        providers = [local]
        if settings.ai.provider == "auto" and cloud is not None:
            providers.append(cloud)
    out["providers"] = providers

    browser = None
    if not settings.privacy.offline_mode and settings.search.engine != "none":
        from tools.browser import BrowserManager

        browser = BrowserManager(headless=settings.search.playwright_headless)
    out["browser"] = browser

    assistant = Jarvis(settings)
    report = assistant.boot({**out, "browser": browser, "index": index})
    out["assistant"] = assistant
    out["boot_report"] = report
    return out


# --------------------------------------------------------------------------- #
# Terminal mode
# --------------------------------------------------------------------------- #
def run_cli(args: argparse.Namespace, settings: Settings) -> int:
    log = logging.getLogger("jarvis")

    components = build_components(settings, want_mic=not args.text)
    assistant = components["assistant"]
    for warning in components["warnings"]:
        print(f"  ! {warning}")

    print("\nJARVIS is up.  Type a command, or press Ctrl+C to quit.")
    print("Try: 'what can you do' | 'open youtube' | 'make a note that ...' | 'system stats'\n")
    for entry in (components.get("boot_report") or {}).get("providers", []):
        print(f"  [{'ok' if entry['ok'] else '--'}] {entry['name']}: {entry['status']}")

    def confirm(summary: str, capability: str) -> bool:
        # A background voice worker cannot safely race the CLI's own input().
        if __import__("threading").current_thread() is not __import__("threading").main_thread():
            print(f"\n  Action '{summary}' needs approval. Use --text for sensitive commands.")
            return False
        answer = input(f"  Confirm '{summary}' ({capability})? [y/N] ").strip().lower()
        return answer in {"y", "yes"}

    assistant.request_confirmation = confirm

    try:
        if not args.text:
            if not assistant.start_listening():
                print("  ! Voice activation unavailable - falling back to typed input.")
            else:
                print("  Clap twice, or press Enter to talk. Wake-word requires a selected model.")
        while True:
            try:
                line = input("\nYou   > ").strip()
            except EOFError:
                break
            if not line:
                if not args.text:
                    assistant.trigger("manual")
                    time.sleep(0.2)
                continue
            if line in {"quit", "exit", ":q"}:
                break
            reply = assistant.handle_text(line, spoken=bool(components.get("tts")))
            print(f"JARVIS > {reply}")
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        assistant.shutdown()
        if components.get("browser"):
            components["browser"].close()
        log.info("bye")
    return 0


# --------------------------------------------------------------------------- #
# GUI mode
# --------------------------------------------------------------------------- #
def run_gui(settings: Settings) -> int:
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print(
            "PySide6 is not installed. Run: pip install PySide6\n"
            "Or start the terminal version instead:  python main.py --text"
        )
        return 3
    from ui.main_window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("JARVIS")
    app.setOrganizationName("LocalFirst")
    win = MainWindow(settings)
    win.show()
    return int(app.exec())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local-first JARVIS assistant")
    parser.add_argument("--check", action="store_true", help="stage 1: environment report and exit")
    parser.add_argument("--text", action="store_true", help="typed input only, no microphone")
    parser.add_argument("--gui", action="store_true", help="open the PySide6 dashboard")
    parser.add_argument("--offline", action="store_true", help="disable every network tool for this run")
    parser.add_argument("--privacy", action="store_true", help="no cloud, no audio retention, no mic")
    parser.add_argument("--model", default="", help="override the local Ollama model for this run")
    parser.add_argument("--say", default="", help="one-shot: process this text and exit")
    parser.add_argument("--api", action="store_true", help="start loopback-only API for the React dashboard")
    args = parser.parse_args(argv)

    if args.check:
        return check_environment()

    settings = Settings.load()
    if args.offline:
        settings.privacy.offline_mode = True
    if args.privacy:
        settings.privacy.privacy_mode = True
        settings.privacy.offline_mode = True
    if args.model:
        settings.ai.ollama_model = args.model
    settings.validate()
    setup_logging(settings.logs.level, settings.logs.to_file)

    if args.say:
        components = build_components(settings, want_mic=False, want_tts=False)
        assistant = components["assistant"]
        try:
            reply = assistant.handle_text(args.say, spoken=False)
            print(reply)
        finally:
            assistant.shutdown()
        return 0

    if args.gui:
        return run_gui(settings)
    if args.api:
        from api.server import serve_api

        components = build_components(settings, want_mic=False, want_tts=False)
        try:
            try:
                serve_api(components["assistant"], components["boot_report"], DATA_DIR / "api-token")
            except OSError as exc:
                print(f"Could not start the local API: {exc}. Check whether port 8765 is already in use.")
                return 2
        finally:
            components["assistant"].shutdown()
        return 0
    return run_cli(args, settings)


if __name__ == "__main__":
    sys.exit(main())
