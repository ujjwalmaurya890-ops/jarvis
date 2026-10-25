"""Settings dialog: every value the user may change, in plain language.

Nothing here can silently widen the security boundary:
* allowed folders / apps / sites are free-text but validated on Save and shown
  with their resolved absolute path before they are accepted;
* turning on cloud AI requires BOTH a key in .env and this checkbox, and the
  key itself is never stored in config.json (only the env var NAME is);
* "reset everything" wipes grants + conversation, never files.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from config.settings import Settings, WORKSPACE_DIR
from security.command_allowlist import CommandNotAllowed, parse_allowlist
from security.permissions import Capability

LOCAL_MODELS = ["qwen2.5:1.5b", "qwen2.5:0.5b", "llama3.2:1b", "phi3:mini", "gemma2:2b"]
WHISPER_MODELS = ["tiny.en", "base.en", "small.en", "tiny", "base"]


class CalibrationWorker(QThread):
    finished_calibration = Signal(float)

    def __init__(self, detector, mic) -> None:  # noqa: ANN001
        super().__init__(QApplication.instance())
        self.detector = detector
        self.mic = mic

    def run(self) -> None:
        from audio.clap_detector import calibrate

        self.finished_calibration.emit(calibrate(self.detector, self.mic))


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.s = settings
        self.setWindowTitle("JARVIS settings")
        self.setMinimumSize(720, 600)
        self.setStyleSheet(
            "QDialog{background:#0a1220;} QGroupBox{color:#7dd3fc;border:1px solid #1b2c48;"
            "border-radius:8px;margin-top:14px;padding-top:10px;font-weight:700;}"
            "QGroupBox::title{subcontrol-origin:margin;left:12px;padding:0 6px;}"
            "QLabel{color:#93a4bd;} QLineEdit,QPlainTextEdit,QComboBox,QSpinBox,QDoubleSpinBox{"
            "background:#0f1a2d;color:#dbe7ff;border:1px solid #1f314e;border-radius:6px;padding:6px;}"
            "QTabBar::tab{background:#0c1424;color:#8fa6c4;padding:8px 14px;border:1px solid #1b2c48;}"
            "QTabBar::tab:selected{background:#12283d;color:#7dd3fc;}"
        )
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(self._tab_audio(), "Ear & Activation")
        tabs.addTab(self._tab_speech(), "Speech")
        tabs.addTab(self._tab_ai(), "AI Brain")
        tabs.addTab(self._tab_tools(), "Tools & Permissions")
        tabs.addTab(self._tab_privacy(), "Privacy & Memory")
        layout.addWidget(tabs)

        note = QLabel(
            "Everything except cloud AI and web search works offline. Changing audio values needs a "
            "restart of the ear (STOP → START EAR) to take effect."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color:#5c708f;font-size:11px;")
        layout.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.save_and_close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------ tabs --
    def _tab_audio(self) -> QWidget:
        w, form = self._widget()
        a = self.s.audio
        self.cb_ear = QCheckBox("Enable the microphone ear (clap + wake word)")
        self.cb_ear.setChecked(a.enabled)
        self.cb_clap = QCheckBox("Activate on a clap pattern")
        self.cb_clap.setChecked(a.clap_pattern != "none")
        self.combo_device = QComboBox()
        self.combo_device.addItem("System default", None)
        try:
            import sounddevice as sd

            for idx, device in enumerate(sd.query_devices()):
                if int(device.get("max_input_channels", 0)) > 0:
                    self.combo_device.addItem(f"{idx}: {device['name']}", idx)
                    if a.input_device_index == idx:
                        self.combo_device.setCurrentIndex(self.combo_device.count() - 1)
        except Exception as exc:  # PortAudioError is not an OSError
            self.combo_device.setToolTip(f"Device list unavailable: {exc}. System default will be used.")
        self.combo_pattern = QComboBox()
        self.combo_pattern.addItems(["single", "double", "triple"])
        self.combo_pattern.setCurrentText(a.clap_pattern if a.clap_pattern in {"single", "double", "triple"} else "double")
        self.spin_sens = QDoubleSpinBox()
        self.spin_sens.setRange(0.4, 6.0)
        self.spin_sens.setSingleStep(0.1)
        self.spin_sens.setValue(a.clap_sensitivity)
        self.spin_thresh = QDoubleSpinBox()
        self.spin_thresh.setRange(0.005, 0.4)
        self.spin_thresh.setDecimals(4)
        self.spin_thresh.setSingleStep(0.005)
        self.spin_thresh.setValue(a.clap_threshold)
        self.spin_gap = QDoubleSpinBox()
        self.spin_gap.setRange(0.15, 4.0)
        self.spin_gap.setValue(a.refractory)
        self.edit_wake = QLineEdit(a.wake_word)
        self.edit_model_dir = QLineEdit(a.wake_model_dir)
        pick = QPushButton("Choose…")
        pick.clicked.connect(self._pick_model_dir)
        row = QHBoxLayout()
        row.addWidget(self.edit_model_dir, 1)
        row.addWidget(pick)
        calib = QPushButton("Measure room noise floor (stay quiet 3 s)")
        calib.clicked.connect(self._calibrate)
        form.addRow(self.cb_ear)
        form.addRow("Microphone device", self.combo_device)
        form.addRow(self.cb_clap)
        form.addRow("Clap pattern", self.combo_pattern)
        form.addRow("Trigger threshold", self.spin_thresh)
        form.addRow("Sensitivity (higher = stricter)", self.spin_sens)
        form.addRow("Cooldown after activation (s)", self.spin_gap)
        form.addRow("Wake phrase", self.edit_wake)
        form.addRow("Wake model folder", row)
        form.addRow("", calib)
        self._hint(form, "Always-on wake detection requires a compatible local ONNX/TFLite model. "
                         "Without one, use double-clap or Ctrl+Space; no custom phrase detector is silently assumed.")
        return w

    def _tab_speech(self) -> QWidget:
        w, form = self._widget()
        self.combo_whisper = QComboBox()
        self.combo_whisper.addItems(WHISPER_MODELS)
        self.combo_whisper.setCurrentText(self.s.stt.model_size)
        self.combo_lang = QComboBox()
        self.combo_lang.addItems(["en", "hinglish", "hi", "auto"])
        self.combo_lang.setCurrentText(self.s.stt.language)
        self.spin_threads = QSpinBox()
        self.spin_threads.setRange(1, 16)
        self.spin_threads.setValue(self.s.stt.threads)
        self.spin_rate = QSpinBox()
        self.spin_rate.setRange(80, 320)
        self.spin_rate.setValue(self.s.tts.rate)
        self.spin_vol = QSpinBox()
        self.spin_vol.setRange(0, 100)
        self.spin_vol.setValue(int(self.s.tts.volume * 100))
        self.combo_tts = QComboBox()
        self.combo_tts.addItems(["pyttsx3", "windows-sapi"])
        self.combo_tts.setCurrentText(self.s.tts.engine)
        self.edit_voice = QLineEdit(self.s.tts.voice_name)
        voices = QPushButton("List Windows voices")
        voices.clicked.connect(self._list_voices)
        vr = QHBoxLayout()
        vr.addWidget(self.edit_voice, 1)
        vr.addWidget(voices)
        form.addRow("Whisper model", self.combo_whisper)
        form.addRow("Language", self.combo_lang)
        form.addRow("CPU threads for STT", self.spin_threads)
        form.addRow("TTS engine", self.combo_tts)
        form.addRow("Speech rate (wpm)", self.spin_rate)
        form.addRow("Volume %", self.spin_vol)
        form.addRow("Voice name (blank = default)", vr)
        self._hint(form, "For Hindi/Hinglish select the multilingual tiny or base model (not tiny.en). "
                         "Roman-script output is not guaranteed; test accuracy on your own microphone.")
        return w

    def _tab_ai(self) -> QWidget:
        w, form = self._widget()
        self.combo_provider = QComboBox()
        self.combo_provider.addItems(["auto", "local", "ollama", "gemini", "offline"])
        self.combo_provider.setCurrentText(self.s.ai.provider)
        self.combo_model = QComboBox()
        self.combo_model.setEditable(True)
        self.combo_model.addItems(LOCAL_MODELS)
        self.combo_model.setCurrentText(self.s.ai.ollama_model)
        self.edit_host = QLineEdit(self.s.ai.ollama_host)
        self.edit_gemini_model = QLineEdit(self.s.ai.gemini_model)
        self.cb_cloud = QCheckBox("Allow cloud AI (Gemini) when a key is present in .env")
        self.cb_cloud.setChecked(self.s.ai.allow_cloud)
        self.cb_grounding = QCheckBox("Permit Gemini Google Search grounding (quota may cost money)")
        self.cb_grounding.setChecked(self.s.ai.allow_search_grounding)
        self.combo_search = QComboBox()
        self.combo_search.addItems(["auto", "duckduckgo", "brave", "gemini", "none"])
        self.combo_search.setCurrentText(self.s.search.engine)
        self.spin_temp = QDoubleSpinBox()
        self.spin_temp.setRange(0.0, 1.5)
        self.spin_temp.setSingleStep(0.05)
        self.spin_temp.setValue(self.s.ai.temperature)
        self.spin_timeout = QSpinBox()
        self.spin_timeout.setRange(10, 180)
        self.spin_timeout.setValue(int(self.s.ai.request_timeout))
        self.lbl_key = QLabel(self._key_status())
        self.lbl_key.setWordWrap(True)
        form.addRow("Provider", self.combo_provider)
        form.addRow("Local model", self.combo_model)
        form.addRow("Ollama host", self.edit_host)
        form.addRow("Gemini model id", self.edit_gemini_model)
        form.addRow(self.cb_cloud)
        form.addRow("Web search engine", self.combo_search)
        form.addRow(self.cb_grounding)
        form.addRow("Temperature", self.spin_temp)
        form.addRow("Timeout (s)", self.spin_timeout)
        form.addRow("API key", self.lbl_key)
        self._hint(form, "The key itself lives only in the .env file. JARVIS never writes it to "
                         "config.json, the database or the log.")
        return w

    def _tab_tools(self) -> QWidget:
        w, form = self._widget()
        box_dirs = QGroupBox("Readable folders (only the workspace is writable)")
        self.list_dirs = QListWidget()
        self.list_dirs.addItems(self.s.automation.allowed_dirs)
        add = QPushButton("Add folder…")
        add.clicked.connect(self._add_dir)
        rm = QPushButton("Remove selected")
        rm.clicked.connect(self._remove_dir)
        dl = QVBoxLayout(box_dirs)
        dl.addWidget(self.list_dirs, 1)
        hr = QHBoxLayout()
        hr.addWidget(add)
        hr.addWidget(rm)
        dl.addLayout(hr)
        form.addRow(box_dirs)

        box_apps = QGroupBox("Allow-listed applications (name = executable)")
        self.edit_apps = QPlainTextEdit()
        self.edit_apps.setPlainText("\n".join(f"{k} = {v}" for k, v in self.s.automation.allowed_apps.items()))
        self.edit_apps.setMaximumHeight(120)
        QVBoxLayout(box_apps).addWidget(self.edit_apps)
        form.addRow(box_apps)

        box_sites = QGroupBox("Allow-listed websites")
        self.edit_sites = QPlainTextEdit()
        self.edit_sites.setPlainText("\n".join(self.s.automation.allowed_sites))
        self.edit_sites.setMaximumHeight(100)
        QVBoxLayout(box_sites).addWidget(self.edit_sites)
        form.addRow(box_sites)

        box_cmd = QGroupBox("Allow-listed commands (name = program args) — nothing else can ever run")
        self.edit_cmds = QPlainTextEdit()
        self.edit_cmds.setPlainText("\n".join(self.s.automation.allowed_commands))
        self.edit_cmds.setMaximumHeight(90)
        QVBoxLayout(box_cmd).addWidget(self.edit_cmds)
        form.addRow(box_cmd)

        self.cb_confirm = QCheckBox("Always ask me before a dangerous action (recommended)")
        self.cb_confirm.setChecked(True)
        self.cb_confirm.setEnabled(False)
        form.addRow(self.cb_confirm)
        perms = QGroupBox("Tool permissions (dangerous actions STILL ask every time)")
        perm_form = QFormLayout(perms)
        broker = getattr(getattr(self.parent(), "assistant", None), "broker", None)
        self.permission_checks: dict[Capability, QCheckBox] = {}
        for cap in (Capability.SITE_OPEN, Capability.APP_LAUNCH, Capability.FS_READ,
                    Capability.FS_WRITE, Capability.NET_SEARCH, Capability.NET_FETCH,
                    Capability.RUN_COMMAND, Capability.POWER, Capability.CLOUD_AI):
            check = QCheckBox(cap.value)
            check.setChecked(bool(broker and broker.is_granted(cap)))
            perm_form.addRow(check)
            self.permission_checks[cap] = check
        form.addRow(perms)
        return w

    def _tab_privacy(self) -> QWidget:
        w, form = self._widget()
        self.cb_privacy = QCheckBox("Privacy mode — no cloud, no microphone, text only")
        self.cb_privacy.setChecked(self.s.privacy.privacy_mode)
        self.cb_offline = QCheckBox("Offline mode — ignore every network tool")
        self.cb_offline.setChecked(self.s.privacy.offline_mode)
        self.cb_store = QCheckBox("Keep a transcript history in SQLite")
        self.cb_store.setChecked(self.s.privacy.store_transcripts)
        self.spin_days = QSpinBox()
        self.spin_days.setRange(0, 3650)
        self.spin_days.setValue(self.s.privacy.retention_days)
        self.spin_days.setSuffix(" days")
        self.combo_log = QComboBox()
        self.combo_log.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        self.combo_log.setCurrentText(self.s.logs.level)
        self.cb_index = QCheckBox("Build a local search index of the allowed folders (manual refresh)")
        self.cb_index.setChecked(self.s.privacy.index_documents)
        danger = QPushButton("Delete conversation memory now")
        danger.setStyleSheet("color:#fca5a5;border:1px solid #55201f;background:#2a1113;")
        danger.clicked.connect(self._wipe_memory)
        form.addRow(self.cb_privacy)
        form.addRow(self.cb_offline)
        form.addRow(self.cb_store)
        form.addRow("Keep history for", self.spin_days)
        form.addRow("Log level", self.combo_log)
        form.addRow(self.cb_index)
        form.addRow("", danger)
        self._hint(form, "Passwords, API keys and long tokens are redacted before anything is stored. "
                         "Notes and documents are never uploaded; only your question text goes to a cloud "
                         "model, and only if you enabled it.")
        return w

    # --------------------------------------------------------------- helpers --
    def _widget(self) -> tuple[QWidget, QFormLayout]:  # noqa: ANN202
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(16, 16, 16, 16)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        lay.addLayout(form)
        lay.addStretch(1)
        return w, form

    def _hint(self, form: QFormLayout, text: str) -> None:  # noqa: ANN001
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet("color:#5c708f;font-size:11px;")
        form.addRow(label)

    def _key_status(self) -> str:
        from config.settings import PROJECT_ROOT

        has = bool(os.environ.get("GEMINI_API_KEY", "").strip())
        if (PROJECT_ROOT / ".env").exists():
            for line in (PROJECT_ROOT / ".env").read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.strip().startswith("GEMINI_API_KEY=") and line.split("=", 1)[1].strip():
                    has = True
        return (
            "GEMINI_API_KEY found in .env — cloud fallback available."
            if has
            else "No key in .env. Local model only (copy .env.example to .env to add one)."
        )

    def _pick_model_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Wake-word model folder")
        if path:
            self.edit_model_dir.setText(path)

    def _calibrate(self) -> None:
        parent = self.parent()
        mic = getattr(getattr(parent, "assistant", None), "mic", None)
        detector = getattr(getattr(parent, "assistant", None), "clap", None)
        if mic is None or detector is None or not mic.is_running:
            QMessageBox.information(self, "Not available", "Start the ear first so the microphone is open.")
            return
        self._calibration = CalibrationWorker(detector, mic)
        self._calibration.finished_calibration.connect(self.spin_thresh.setValue)
        self._calibration.finished.connect(self._calibration.deleteLater)
        self._calibration.start()

    def _add_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Choose a folder JARVIS may read")
        if path:
            resolved = str(Path(path).resolve())
            if not any(self.list_dirs.item(i).text() == resolved for i in range(self.list_dirs.count())):
                self.list_dirs.addItem(resolved)

    def _remove_dir(self) -> None:
        row = self.list_dirs.currentRow()
        if row >= 0:
            self.list_dirs.takeItem(row)

    def _list_voices(self) -> None:
        try:
            from audio.text_to_speech import TextToSpeech

            names = TextToSpeech().list_voices()
        except Exception as exc:
            names = []
            QMessageBox.warning(self, "Voices", f"Could not enumerate voices: {exc}")
        if not names:
            QMessageBox.information(self, "Voices", "No SAPI voices were returned by Windows.")
            return
        choice, ok = QInputDialog.getItem(self, "Windows voices", "Pick a voice", names, 0, False)
        if ok and choice:
            self.edit_voice.setText(choice)

    def _wipe_memory(self) -> None:
        parent = self.parent()
        assistant = getattr(parent, "assistant", None)
        db = getattr(assistant, "db", None)
        if db is None:
            QMessageBox.information(self, "Memory", "No memory database is attached.")
            return
        confirm = QMessageBox.question(
            self,
            "Delete conversation memory?",
            "This removes stored turns, summaries and facts. Notes and your files are NOT touched.\nContinue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm == QMessageBox.Yes:
            db.clear_memory()
            QMessageBox.information(self, "Memory", "Turns, summaries, facts and document index deleted. Notes and grants stay.")

    # ------------------------------------------------------------------ save --
    def save_and_close(self) -> None:
        candidate = copy.deepcopy(self.s)
        a = candidate.audio
        a.enabled = self.cb_ear.isChecked()
        a.input_device_index = self.combo_device.currentData()
        a.clap_pattern = self.combo_pattern.currentText() if self.cb_clap.isChecked() else "none"
        a.clap_threshold = self.spin_thresh.value()
        a.clap_sensitivity = self.spin_sens.value()
        a.refractory = self.spin_gap.value()
        a.wake_word = self.edit_wake.text().strip().lower() or "hello jarvis"
        a.wake_model_dir = self.edit_model_dir.text().strip()

        candidate.stt.model_size = self.combo_whisper.currentText()
        candidate.stt.language = self.combo_lang.currentText()
        candidate.stt.threads = self.spin_threads.value()
        if candidate.stt.language in {"hi", "hinglish"} and candidate.stt.model_size.endswith(".en"):
            QMessageBox.warning(self, "Whisper model", "For Hindi/Hinglish choose multilingual 'tiny' or 'base', not an .en model.")
            return
        candidate.tts.engine = self.combo_tts.currentText()
        candidate.tts.rate = self.spin_rate.value()
        candidate.tts.volume = self.spin_vol.value() / 100.0
        candidate.tts.voice_name = self.edit_voice.text().strip()

        candidate.ai.provider = self.combo_provider.currentText()
        candidate.ai.ollama_model = self.combo_model.currentText().strip()
        candidate.ai.ollama_host = self.edit_host.text().strip() or "http://127.0.0.1:11434"
        candidate.ai.gemini_model = self.edit_gemini_model.text().strip() or "gemini-2.5-flash"
        candidate.ai.allow_cloud = self.cb_cloud.isChecked()
        candidate.ai.allow_search_grounding = self.cb_grounding.isChecked()
        candidate.search.engine = self.combo_search.currentText()
        candidate.ai.temperature = self.spin_temp.value()
        candidate.ai.request_timeout = float(self.spin_timeout.value())

        candidate.automation.allowed_dirs = [self.list_dirs.item(i).text() for i in range(self.list_dirs.count())] or [str(WORKSPACE_DIR)]
        apps: dict[str, str] = {}
        for line in self.edit_apps.toPlainText().splitlines():
            if "=" in line:
                k, _, v = line.partition("=")
                if k.strip() and v.strip():
                    apps[k.strip().lower()] = v.strip()
        candidate.automation.allowed_apps = apps
        candidate.automation.allowed_sites = [ln.strip() for ln in self.edit_sites.toPlainText().splitlines() if ln.strip()]
        commands = [ln.strip() for ln in self.edit_cmds.toPlainText().splitlines() if ln.strip()]
        try:
            parse_allowlist(commands)
            candidate.automation.allowed_commands = commands
        except CommandNotAllowed as exc:
            QMessageBox.critical(self, "Command allow-list rejected", str(exc))
            return
        candidate.automation.require_confirmation = True

        candidate.privacy.privacy_mode = self.cb_privacy.isChecked()
        candidate.privacy.offline_mode = self.cb_offline.isChecked()
        candidate.privacy.store_transcripts = self.cb_store.isChecked()
        candidate.privacy.index_documents = self.cb_index.isChecked()
        candidate.privacy.retention_days = self.spin_days.value()
        candidate.logs.level = self.combo_log.currentText()
        try:
            candidate.save()
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Settings not saved", str(exc))
            return
        self.s.apply(candidate.as_dict())
        broker = getattr(getattr(self.parent(), "assistant", None), "broker", None)
        if broker is not None:
            for cap, checkbox in self.permission_checks.items():
                if checkbox.isChecked():
                    broker.grant(cap, "permanent")
                else:
                    broker.revoke(cap)
        self.accept()
