"""PySide6 dashboard.

Rules this file follows on purpose:
* the GUI thread never blocks - recording, transcription, model calls and tools
  all run on the assistant's own threads and report back with signals;
* the visualiser is a single QWidget repainted from a 20 fps timer driven by the
  mic level callback (no per-audio-block repaint, which would eat CPU);
* every subsystem is optional: if Ollama or the mic is missing the window still
  opens and tells you what is wrong.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from config.settings import Settings
from ui.visualizer import VoiceOrb

STATE_COLOR = {
    "offline": "#5b6b80",
    "idle": "#38bdf8",
    "listening": "#22d3ee",
    "transcribing": "#a3e635",
    "thinking": "#fbbf24",
    "executing": "#f97316",
    "speaking": "#60a5fa",
    "error": "#f87171",
}
STATE_LABEL = {
    "offline": "OFFLINE",
    "idle": "STANDBY",
    "listening": "LISTENING",
    "transcribing": "TRANSCRIBING",
    "thinking": "THINKING",
    "executing": "EXECUTING",
    "speaking": "SPEAKING",
    "error": "ERROR",
}


class BootWorker(QThread):
    """Starts subsystems away from the GUI thread (model load takes seconds)."""

    done = Signal(dict)

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self.error: str | None = None

    def run(self) -> None:  # noqa: D401
        try:
            from main import build_components

            components = build_components(self.settings, want_mic=True)
        except Exception as exc:  # surfaced to the user, never a silent crash
            self.error = repr(exc)
            self.done.emit({"error": self.error})
            return
        self.done.emit(components)


class MainWindow(QMainWindow):
    # signals used to hop back onto the GUI thread
    ev_state = Signal(str, str)
    ev_reply = Signal(dict)
    ev_listen = Signal(dict)
    ev_log = Signal(str, str)
    confirm_requested = Signal(str, str, object)

    def __init__(self, settings: Settings | None = None) -> None:
        super().__init__()
        self.settings = settings or Settings.load()
        self.components: dict[str, object] = {}
        self.assistant = None
        self.level = 0.0
        self._closing = False
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="jarvis-ui")
        self.setWindowTitle("J.A.R.V.I.S — local-first assistant")
        self.resize(1180, 760)
        self.setMinimumSize(940, 620)
        self.setStyleSheet(GLOBAL_QSS)
        self._build_ui()
        self._wire_signals()
        self.boot()

    # ------------------------------------------------------------------ UI ---
    def _build_ui(self) -> None:
        central = QWidget(objectName="root")
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(14, 14, 14, 10)
        outer.setSpacing(12)
        outer.addLayout(self._build_header())

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_conversation())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([380, 760])
        outer.addWidget(splitter, 1)
        outer.addLayout(self._build_input_row())

        bar = QStatusBar()
        bar.setSizeGripEnabled(False)
        self.setStatusBar(bar)
        bar.showMessage("Starting subsystems…")

    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        title = QLabel("J.A.R.V.I.S")
        title.setObjectName("title")
        sub = QLabel("LOCAL-FIRST  ·  VOICE  ·  PRIVACY")
        sub.setObjectName("subtitle")
        self.state_badge = QLabel("STARTING")
        self.state_badge.setObjectName("badge")
        self.mic_label = QLabel("● mic: idle")
        self.mic_label.setObjectName("mic")
        self.provider_label = QLabel("model: checking…")
        self.provider_label.setObjectName("provider")
        for w in (title, sub):
            row.addWidget(w)
        row.addStretch(1)
        row.addWidget(self.provider_label)
        row.addWidget(self.mic_label)
        row.addWidget(self.state_badge)
        self.settings_btn = QPushButton("SETTINGS")
        self.settings_btn.setObjectName("ghost")
        self.settings_btn.clicked.connect(self.open_settings)
        row.addWidget(self.settings_btn)
        return row

    def _build_left_panel(self) -> QWidget:
        panel = QFrame(objectName="panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self.orb = VoiceOrb()
        self.orb.setMinimumHeight(230)
        layout.addWidget(self.orb, 3)

        self.status_line = QLabel("Standby")
        self.status_line.setObjectName("statusLine")
        self.status_line.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_line)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.start_btn = QPushButton("START EAR")
        self.start_btn.setObjectName("primary")
        self.start_btn.clicked.connect(self.toggle_listening)
        self.tap_btn = QPushButton("TALK (ENTER)")
        self.tap_btn.setObjectName("ghost")
        self.tap_btn.clicked.connect(self.push_to_talk)
        self.stop_btn = QPushButton("STOP")
        self.stop_btn.setObjectName("danger")
        self.stop_btn.clicked.connect(self.interrupt)
        for b in (self.start_btn, self.tap_btn, self.stop_btn):
            buttons.addWidget(b)
        layout.addLayout(buttons)

        self.mute_btn = QPushButton("VOICE ON")
        self.mute_btn.setObjectName("ghost")
        self.mute_btn.setCheckable(True)
        self.mute_btn.toggled.connect(self.toggle_mute)
        layout.addWidget(self.mute_btn)

        layout.addWidget(QLabel("ACTIVITY LOG"))
        self.log_list = QListWidget(objectName="log")
        self.log_list.setUniformItemSizes(True)
        layout.addWidget(self.log_list, 1)

        layout.addWidget(QLabel("RECENT COMMANDS"))
        self.history_table = QTableWidget(0, 2)
        self.history_table.setHorizontalHeaderLabels(["you said", "result"])
        self.history_table.verticalHeader().setVisible(False)  # type: ignore[union-attr]
        self.history_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.history_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)  # type: ignore[union-attr]
        self.history_table.setMaximumHeight(140)
        layout.addWidget(self.history_table)
        return panel

    def _build_conversation(self) -> QWidget:
        panel = QFrame(objectName="panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 16, 16, 16)
        self.transcript = QTextEditLike(layout)
        layout.addWidget(self.transcript, 1)
        return panel

    def _build_input_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Type a command — e.g.  open youtube   ·   summarise this page   ·   what can you do")
        self.input.returnPressed.connect(self.send_text)
        row.addWidget(self.input, 1)
        send = QPushButton("SEND")
        send.setObjectName("primary")
        send.clicked.connect(self.send_text)
        row.addWidget(send)
        return row

    # ------------------------------------------------------------- signals ---
    def _wire_signals(self) -> None:
        self.ev_state.connect(self._on_state)
        self.ev_reply.connect(self._on_reply)
        self.ev_listen.connect(self._on_listen)
        self.ev_log.connect(self._append_log)
        self.confirm_requested.connect(self._show_confirmation, Qt.QueuedConnection)

    def _on_state(self, state: str, detail: str) -> None:
        color = STATE_COLOR.get(state, "#38bdf8")
        label = STATE_LABEL.get(state, state.upper())
        self.state_badge.setText(label)
        self.state_badge.setStyleSheet(
            f"QLabel#badge{{color:{color};border:1px solid {color}55;background:{color}18;"
            f"padding:5px 12px;border-radius:4px;font-weight:700;letter-spacing:2px;font-size:12px;}}"
        )
        self.orb.set_state(state, color)
        self.status_line.setText(detail or label.lower())
        self.orb.set_active(state in {"listening", "thinking", "speaking", "transcribing", "executing"})
        running = bool(self.assistant and self.assistant.mic and self.assistant.mic.is_running)
        self.start_btn.setText("STOP EAR" if running else "START EAR")
        self.mic_label.setText("mic: live" if running else "mic: off")

    def boot(self) -> None:
        self._boot = BootWorker(self.settings)
        self._boot.done.connect(self._attach)
        self._boot.start()

    def _attach(self, components: dict) -> None:
        if "error" in components:
            self._on_state("error", components["error"][:160])
            self.statusBar().showMessage("Startup failed — see the activity log.")
            self._append_log("fatal", components["error"])
            return
        self.components = components
        self.assistant = components.get("assistant")
        if self.assistant is None:
            return
        self.assistant.on_event(self._assistant_event)
        self.assistant.request_confirmation = self._ask_confirmation
        self.assistant.state.on_state(lambda s, d: self.ev_state.emit(s.value, d))
        self.assistant.state.on_level(lambda v: setattr(self, "level", v))
        self._on_state(self.assistant.state.state.value, "text commands available")

        report = components.get("boot_report") or {}
        providers = report.get("providers") or []
        ok = [p["name"] for p in providers if p["ok"]]
        self.provider_label.setText(f"model: {', '.join(ok) if ok else 'none — text mode only'}")
        self.provider_label.setStyleSheet(f"color:{'#7dd3fc' if ok else '#fbbf24'}")
        for warning in components.get("warnings") or []:
            self._append_log("warn", str(warning))
        summary = report.get("startup_summary")
        if summary:
            self._append_log("info", str(summary).replace("\n", " | ")[:200])
        self._append_log("ok", f"boot complete · {len(self.assistant.registry.tools)} tools registered")  # type: ignore[union-attr]
        self.statusBar().showMessage(f"Ready. {len(ok)} AI provider(s). Mic {'running' if report.get('mic') else 'stopped'}.")

    def _assistant_event(self, kind: str, data: dict) -> None:
        if kind == "reply":
            self.ev_reply.emit(data)
        elif kind in {"listen_start", "listen_end", "listen_error", "transcribing"}:
            data["kind"] = kind
            self.ev_listen.emit(data)
        elif kind == "activation":
            self.ev_log.emit("act", f"activated by {data.get('source', 'manual')}")
        elif kind == "executing":
            self.ev_log.emit("tool", f"{data.get('tool')} {data.get('arguments')}")
        elif kind == "mic_error":
            self.ev_log.emit("error", str(data.get("message")))
        elif kind in {"started", "stopped", "mic_recovered"}:
            state = "idle" if kind != "stopped" else "offline"
            self.ev_state.emit(state, "ear live" if kind != "stopped" else "microphone off")

    def _on_listen(self, data: dict) -> None:
        kind = data.get("kind")
        if kind == "listen_end":
            self._append_log("you", data.get("text", "") or "(nothing heard)")
            if data.get("text"):
                self.transcript.add_line("YOU", str(data["text"]), "voice")
        elif kind == "listen_error":
            self._append_log("error", data.get("message", ""))

    def _on_reply(self, data: dict) -> None:
        text = str(data.get("text", "")).strip()
        if not text:
            return
        meta = data.get("meta") or {}
        tag = f"{data.get('kind','chat')} · {data.get('source','')} · {data.get('seconds',0)}s"
        if meta.get("provider"):
            tag += f" · {meta['provider']}/{meta.get('model','')}"
        self.transcript.add_line("JARVIS", text, tag)
        if data.get("tool"):
            ok = meta.get("tool_ok", False)
            self._append_log("done" if ok else "warn", f"{data['tool']} {'ok' if ok else 'denied or failed'}")
        self.history_table.insertRow(0)
        self.history_table.setItem(0, 0, QTableWidgetItem(str(data.get("input", ""))[:40]))
        self.history_table.setItem(0, 1, QTableWidgetItem(f"{data.get('kind')} → {data.get('tool') or '-'}"))

    # ----------------------------------------------------------- controls ---
    def toggle_listening(self) -> None:
        if self.assistant is None or self._closing:
            return
        if self.assistant.mic is not None and self.assistant.mic.is_running:
            self._executor.submit(self.assistant.stop_listening)
        else:
            self.start_btn.setText("STARTING...")
            self._executor.submit(self.assistant.start_listening)

    def push_to_talk(self) -> None:
        if self.assistant is None or self._closing:
            return

        def activate() -> None:
            self.assistant.push_to_talk("button")

        self._executor.submit(activate)

    def interrupt(self) -> None:
        if self.assistant is not None:
            self.assistant.interrupt()
        self._append_log("warn", "interrupted by user")

    def toggle_mute(self, checked: bool) -> None:
        if self.assistant is not None:
            self.assistant.set_muted(checked)
        self.mute_btn.setText("VOICE MUTED" if checked else "VOICE ON")

    def send_text(self) -> None:
        text = self.input.text().strip()
        if not text or self.assistant is None:
            return
        self.input.clear()
        self.transcript.add_line("YOU", text, "typed")
        future = self._executor.submit(self.assistant.handle_text, text, spoken=False)

        def completed(job) -> None:  # noqa: ANN001
            try:
                job.result()
            except Exception as exc:
                self.ev_log.emit("error", f"Command failed: {exc}")

        future.add_done_callback(completed)

    def _ask_confirmation(self, summary: str, capability: str) -> bool:
        """Workers wait for a GUI-thread-only dialog. Timeout always means No."""
        if self._closing:
            return False
        request = {"event": threading.Event(), "approved": False}
        if QThread.currentThread() == self.thread():
            self._show_confirmation(summary, capability, request)
        else:
            self.confirm_requested.emit(summary, capability, request)
        return bool(request["event"].wait(timeout=65) and request["approved"])

    def _show_confirmation(self, summary: str, capability: str, request: object) -> None:
        if self._closing:
            request["event"].set()
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Confirm action")
        box.setText(f"JARVIS wants to:\n\n{summary}\n\nCapability: {capability}")
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        QTimer.singleShot(60000, box.reject)
        try:
            request["approved"] = box.exec() == QMessageBox.Yes
        finally:
            request["event"].set()

    def open_settings(self) -> None:
        from ui.settings_dialog import SettingsDialog

        dialog = SettingsDialog(self.settings, parent=self)
        if dialog.exec():
            self.settings.save()
            self._append_log("ok", "settings saved — restart the ear to apply audio changes")

    def _append_log(self, kind: str, message: str) -> None:
        colors = {"you": "#7dd3fc", "act": "#a3e635", "tool": "#fbbf24", "done": "#38bdf8", "warn": "#fb923c", "error": "#f87171", "ok": "#4ade80"}
        item = QListWidgetItem(f"[{time.strftime('%H:%M:%S')}] {kind:>4}  {message[:220]}")
        item.setForeground(QBrush(QColor(colors.get(kind, "#cbd5f5"))))
        self.log_list.addItem(item)
        self.log_list.scrollToBottom()
        while self.log_list.count() > 300:
            self.log_list.takeItem(0)

    # -------------------------------------------------------------- events ---
    def keyPressEvent(self, event) -> None:  # noqa: ANN001
        if event.key() == Qt.Key_Space and event.modifiers() & Qt.ControlModifier:
            self.push_to_talk()
        elif event.key() == Qt.Key_Escape:
            self.interrupt()
        elif event.key() == Qt.Key_F2:
            self.toggle_listening()
        super().keyPressEvent(event)

    def timerEvent(self, event) -> None:  # noqa: ANN001
        self.orb.push_level(self.level)

    def showEvent(self, event) -> None:  # noqa: ANN001
        self._tick = self.startTimer(50)
        super().showEvent(event)

    def closeEvent(self, event) -> None:  # noqa: ANN001
        if self._boot.isRunning():
            self._append_log("warn", "Wait for startup to finish before closing")
            event.ignore()
            return
        self._closing = True
        try:
            self.killTimer(self._tick)
        except Exception:
            pass
        if self.assistant is not None:
            self.assistant.shutdown()
        self._executor.shutdown(wait=False, cancel_futures=True)
        browser = self.components.get("browser")
        if browser is not None:
            browser.close()  # type: ignore[attr-defined]
        event.accept()


class QTextEditLike(QFrame):
    """Lightweight transcript: a scrollable stack of message cards.

    Why not QListWidget?  Long markdown replies need word wrap and selection;
    why not QTextEdit? We want per-message styling and pinned auto-scroll.
    """

    def __init__(self, parent_layout=None) -> None:  # noqa: ANN001
        super().__init__()
        from PySide6.QtWidgets import QScrollArea, QTextBrowser

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.body = QWidget()
        self.stack = QVBoxLayout(self.body)
        self.stack.setSpacing(10)
        self.stack.addStretch(1)
        self.scroll.setWidget(self.body)
        outer.addWidget(self.scroll)
        self._browser_factory = QTextBrowser

    def add_line(self, who: str, text: str, tag: str = "") -> None:
        from PySide6.QtWidgets import QSizePolicy

        card = QFrame(objectName="card" if who == "JARVIS" else "cardYou")
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(12, 10, 12, 10)
        head = QHBoxLayout()
        name = QLabel(who)
        name.setObjectName("whoJarvis" if who == "JARVIS" else "whoYou")
        head.addWidget(name)
        if tag:
            meta = QLabel(tag)
            meta.setObjectName("meta")
            head.addWidget(meta)
        head.addStretch(1)
        lay.addLayout(head)
        view = self._browser_factory()
        view.setOpenExternalLinks(True)
        view.setFrameShape(QFrame.NoFrame)
        view.setStyleSheet("background:transparent;border:none;")
        view.setHtml(
            f'<div style="font-family:Segoe UI,Inter,sans-serif;font-size:13px;color:#dbe7ff;'
            f'white-space:pre-wrap;">{_escape(text)}</div>'
        )
        view.document().setDocumentMargin(0)
        view.document().contentsChanged.connect(lambda: view.setFixedHeight(max(24, int(view.document().size().height()) + 6)))
        lay.addWidget(view)
        self.stack.insertWidget(self.stack.count() - 1, card)
        QTimer.singleShot(60, lambda: self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum()))


def _escape(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")

GLOBAL_QSS = """
QWidget#root{background:#070d16;}
QMainWindow, QDialog{background:#070d16;}
QFrame#panel{background:#0c1424;border:1px solid #16233a;border-radius:10px;}
QLabel{color:#93a4bd;font-family:'Segoe UI',Inter,sans-serif;font-size:11px;}
QLabel#title{color:#e8f2ff;font-size:26px;font-weight:800;letter-spacing:5px;}
QLabel#subtitle{color:#41577a;font-size:10px;letter-spacing:3px;padding-top:8px;}
QLabel#statusLine{color:#7dd3fc;font-size:13px;letter-spacing:1px;padding:2px;}
QLabel#meta{color:#3f5573;font-size:10px;letter-spacing:1px;}
QLabel#whoJarvis{color:#67e8f9;font-weight:800;letter-spacing:2px;font-size:11px;}
QLabel#whoYou{color:#c4b5fd;font-weight:800;letter-spacing:2px;font-size:11px;}
QFrame#card{background:#0f1a2d;border:1px solid #1b2c48;border-left:3px solid #22d3ee;border-radius:8px;}
QFrame#cardYou{background:#0b1524;border:1px solid #17253d;border-left:3px solid #8b5cf6;border-radius:8px;}
QPushButton{border-radius:6px;padding:8px 12px;font-weight:700;letter-spacing:1px;font-size:11px;}
QPushButton#primary{background:#0e2b3f;color:#7dd3fc;border:1px solid #1d5d7d;}
QPushButton#primary:hover{background:#12405c;}
QPushButton#ghost{background:transparent;color:#8fa6c4;border:1px solid #1d2b45;}
QPushButton#ghost:hover{color:#cfe6ff;border-color:#2f4468;}
QPushButton#ghost:checked{color:#fbbf24;border-color:#7c5b18;}
QPushButton#danger{background:#2a1113;color:#fca5a5;border:1px solid #55201f;}
QPushButton#danger:hover{background:#3c191b;}
QLineEdit{background:#0a1424;color:#dbe7ff;border:1px solid #1c2c47;border-radius:7px;padding:11px 13px;font-size:13px;}
QLineEdit:focus{border-color:#22d3ee;}
QListWidget#log{background:#08101d;border:1px solid #16233a;border-radius:7px;font-family:Cascadia Code,Consolas,monospace;font-size:11px;padding:6px;}
QTableWidget{background:#08101d;border:1px solid #16233a;border-radius:7px;color:#a9bcd8;font-size:11px;}
QHeaderView::section{background:#0c1424;color:#5c708f;border:none;padding:5px;}
QStatusBar{background:#0a1120;color:#5c708f;border-top:1px solid #16233a;}
QScrollBar:vertical{background:#08101d;width:9px;}
QScrollBar::handle:vertical{background:#1d2c46;border-radius:4px;}
QScrollBar::add-line,QScrollBar::sub-line{height:0;}
"""
