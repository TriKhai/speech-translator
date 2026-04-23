from datetime import datetime
from PyQt6.QtWidgets import (
    QTextEdit, QSizePolicy,
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea,
    QPushButton, QLabel, QFrame, QStackedWidget,
    QLineEdit, QMessageBox, QInputDialog, QDialog
)
from PyQt6.QtCore import Qt, QTimer, QSize, pyqtSignal
from PyQt6.QtGui  import QFont, QColor, QTextCursor, QTextCharFormat

from db.database_service import DatabaseService
from ui.widgets.mindmap              import MindmapWidget
from ui.constants import SPEAKER_COLORS
from ui.theme import DARK as _THEME_FALLBACK
from core.minutes_service import MinutesWorker, MinutesResult, export_docx

def _p(obj):
    """Lấy palette: dùng self._theme nếu có, fallback DARK."""
    t = getattr(obj, '_theme', None)
    return t.palette if t is not None else _THEME_FALLBACK

import ui.icons as icons


class RecordingCard(QFrame):
    """
    Hiển thị các bản ghi âm (namwf bên trái)
    """
    selected = pyqtSignal(str)   # recording_id (signal khi user click vào card)

    def __init__(self, rec: dict, theme_manager=None, parent=None):
        super().__init__(parent)
        self._theme  = theme_manager
        self._rec_id = rec["id"] # id record
        self._active = False     # state: có đang được chọn hay không (để highlight)
        self.setCursor(Qt.CursorShape.PointingHandCursor) # cusor khi hover
        self.setFixedHeight(72)  # chiều cao card
        self._build(rec)         # xây dựng UI bên trong card
        self._set_style(active=False) # style mặc định (inactive)

    def _build(self, rec: dict):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.setSpacing(10)

        # Icon
        icon = QLabel()
        src = rec.get("source", "live")
        icon_name = "upload" if src == "upload" else "session"
        icon.setPixmap(icons.get(icon_name, _p(self)['ACCENT']).pixmap(QSize(16, 16)))
        icon.setFixedWidth(24)
        icon.setStyleSheet("background:transparent; border:none;")
        lay.addWidget(icon)

        # Text block
        txt = QWidget()
        txt.setStyleSheet("background:transparent; border:none;")
        tl = QVBoxLayout(txt)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(3)

        # Row 1: title + date
        row1 = QHBoxLayout()
        row1.setContentsMargins(0, 0, 0, 0)

        title_str = rec.get("title") or "Untitled"
        lbl_title = QLabel(title_str)
        lbl_title.setFont(QFont("JetBrains Mono", 9))
        lbl_title.setStyleSheet(f"color:{_p(self)['TEXT_PRI']}; background:transparent;")

        date_str = ""
        if rec.get("recorded_at"):
            try:
                dt = rec["recorded_at"]
                if isinstance(dt, str):
                    dt = datetime.fromisoformat(dt)
                date_str = dt.strftime("%d/%m/%Y %H:%M")
            except Exception:
                pass

        row1.addWidget(lbl_title)

        # Row 2: meta info
        dur  = rec.get("duration_seconds") or 0
        spks = rec.get("speaker_count") or 0
        wds  = rec.get("word_count") or 0
        meta = f"{date_str}  •  {_fmt(dur)}"
        lbl_meta = QLabel(meta)
        lbl_meta.setFont(QFont("JetBrains Mono", 8))
        lbl_meta.setStyleSheet(f"color:{_p(self)['TEXT_SEC']}; background:transparent;")

        tl.addLayout(row1)
        tl.addWidget(lbl_meta)
        lay.addWidget(txt, stretch=1)

    def set_active(self, active: bool):
        self._active = active
        self._set_style(active)

    def _set_style(self, active: bool):
        border_color = _p(self)['ACCENT'] if active else _p(self)['BORDER']
        bg           = _p(self)['BG_CARD2'] if active else "transparent"
        self.setStyleSheet(f"""
            RecordingCard {{
                background: {bg};
                border: none;
                border-left: 2px solid {border_color};
            }}
            RecordingCard:hover {{
                background: {_p(self)['BG_CARD2']};
                border-left: 2px solid {_p(self)['ACCENT'] if active else _p(self)['BORDER2']};
            }}
        """)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit(self._rec_id)


class SummaryCard(QWidget):
    """Hiển thị summary đã lưu trong DB."""

    def __init__(self, theme_manager=None, parent=None):
        super().__init__(parent)
        self._theme        = theme_manager
        self._db           = None   # inject từ DetailPanel
        self._recording_id = None   # set khi load()
        self.setStyleSheet("background:transparent;")
        self._build_ui()

    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"""
            QScrollArea {{ background:transparent; border:none; }}
            QScrollBar:vertical {{
                background:{_p(self)['BG_CARD']}; width:4px; border-radius:2px;
            }}
            QScrollBar::handle:vertical {{
                background:{_p(self)['BORDER2']}; border-radius:2px; min-height:20px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
        """)
        inner = QWidget()
        inner.setStyleSheet("background:transparent;")
        self._inner_lay = QVBoxLayout(inner)
        self._inner_lay.setContentsMargins(0, 0, 0, 0)
        self._inner_lay.setSpacing(12)
        self._inner_lay.addStretch()
        scroll.setWidget(inner)
        lay.addWidget(scroll)

    def load(self, data: dict | None, recording_id: str = None):
        self._recording_id = recording_id
        while self._inner_lay.count() > 1:
            item = self._inner_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not data or not data.get("summary"):
            lbl = QLabel("Chưa có summary cho recording này.")
            lbl.setFont(QFont("JetBrains Mono", 10))
            lbl.setStyleSheet(f"color:{_p(self)['TEXT_DIM']}; background:transparent;")
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._inner_lay.insertWidget(0, lbl)
            return

        idx = 0
        # Overview
        self._inner_lay.insertWidget(idx, self._card("OVERVIEW", data["summary"], "#60A5FA"))
        idx += 1
        # Key points
        if data.get("key_points"):
            self._inner_lay.insertWidget(idx, self._bullets("KEY POINTS", data["key_points"], "#34D399"))
            idx += 1
        # Action items
        if data.get("action_items"):
            self._inner_lay.insertWidget(idx, self._bullets("ACTION ITEMS", data["action_items"], "#F59E0B", checkbox=True))
            idx += 1
        # Topics + sentiment footer
        if data.get("topics") or data.get("sentiment"):
            self._inner_lay.insertWidget(idx, self._footer(data.get("topics", []), data.get("sentiment", "neutral")))

    def _card(self, title, body, color):
        p = _p(self)
        f = QFrame()
        f.setStyleSheet(f"QFrame {{ background:{p['BG_CARD']}; border:1px solid {p['BORDER']}; border-radius:10px; }}")
        l = QVBoxLayout(f)
        l.setContentsMargins(16, 14, 16, 14)
        l.setSpacing(8)

        # Header row: title label + edit/save/cancel buttons
        hdr = QHBoxLayout()
        t = QLabel(title)
        t.setFont(QFont("JetBrains Mono", 8))
        t.setStyleSheet(f"color:{color}; background:transparent; letter-spacing:2px; border:none")
        hdr.addWidget(t)
        hdr.addStretch()

        btn_edit   = QPushButton("✎ Chỉnh sửa")
        btn_save   = QPushButton("✔ Lưu")
        btn_cancel = QPushButton("✕ Huỷ")
        for b in (btn_edit, btn_save, btn_cancel):
            b.setFont(QFont("JetBrains Mono", 8))
            b.setFixedHeight(22)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_save.setVisible(False)
        btn_cancel.setVisible(False)
        btn_edit.setStyleSheet(f"""
            QPushButton {{ background:transparent; color:{p['TEXT_DIM']};
                border:1px solid {p['BORDER']}; border-radius:4px; padding:0 8px; }}
            QPushButton:hover {{ color:#60A5FA; border-color:#60A5FA; }}
        """)
        btn_save.setStyleSheet(f"""
            QPushButton {{ background:#3B6EF5; color:#fff;
                border:none; border-radius:4px; padding:0 9px; }}
        """)
        btn_cancel.setStyleSheet(f"""
            QPushButton {{ background:transparent; color:{p['TEXT_DIM']};
                border:1px solid {p['BORDER']}; border-radius:4px; padding:0 8px; }}
            QPushButton:hover {{ color:#E05252; border-color:#E05252; }}
        """)
        hdr.addWidget(btn_edit)
        hdr.addSpacing(4)
        hdr.addWidget(btn_save)
        hdr.addSpacing(4)
        hdr.addWidget(btn_cancel)
        l.addLayout(hdr)
        l.addWidget(_divider(self._theme))

        # Body: QTextEdit thay cho QLabel
        b_edit = QTextEdit(body)
        b_edit.setFont(QFont("JetBrains Mono", 11))
        b_edit.setReadOnly(True)
        b_edit.setFrameShape(QFrame.Shape.NoFrame)
        b_edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        b_edit.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        b_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        b_edit.setStyleSheet(f"color:{p['TEXT_PRI']}; background:transparent; border:none;")

        def _adjust():
            h = int(b_edit.document().size().height())
            b_edit.setFixedHeight(max(h + 8, 40))

        b_edit.document().contentsChanged.connect(_adjust)
        _adjust()
        l.addWidget(b_edit)

        # ── Wiring ───────────────────────────────────────────────────
        def _on_edit():
            p2 = _p(self)
            b_edit.setReadOnly(False)
            b_edit.setStyleSheet(f"""
                color:{p2['TEXT_PRI']}; background:{p2['BG_CARD']};
                border:1px solid #3B6EF5; border-radius:6px; padding:4px;
            """)
            b_edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            b_edit.setFixedHeight(max(int(b_edit.document().size().height()) + 8, 80))
            b_edit.setFocus()
            btn_edit.setVisible(False)
            btn_save.setVisible(True)
            btn_cancel.setVisible(True)
            b_edit.setProperty("_backup", b_edit.toPlainText())

        def _on_save():
            p2 = _p(self)
            new_text = b_edit.toPlainText().strip()
            b_edit.setReadOnly(True)
            b_edit.setStyleSheet(f"color:{p2['TEXT_PRI']}; background:transparent; border:none;")
            b_edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            btn_edit.setVisible(True)
            btn_save.setVisible(False)
            btn_cancel.setVisible(False)
            _adjust()
            # Lưu vào DB
            if self._db and self._recording_id:
                try:
                    self._db.update_summary_text(self._recording_id, new_text)
                except Exception as e:
                    print(f"[SummaryCard] Lỗi lưu overview: {e}")

        def _on_cancel():
            p2 = _p(self)
            backup = b_edit.property("_backup") or ""
            b_edit.setPlainText(backup)
            b_edit.setReadOnly(True)
            b_edit.setStyleSheet(f"color:{p2['TEXT_PRI']}; background:transparent; border:none;")
            b_edit.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            btn_edit.setVisible(True)
            btn_save.setVisible(False)
            btn_cancel.setVisible(False)
            _adjust()

        btn_edit.clicked.connect(_on_edit)
        btn_save.clicked.connect(_on_save)
        btn_cancel.clicked.connect(_on_cancel)

        return f

    def _bullets(self, title, items, color, checkbox=False):
        from PyQt6.QtWidgets import QCheckBox
        p = _p(self)
        f = QFrame()
        f.setStyleSheet(f"QFrame {{ background:{p['BG_CARD']}; border:1px solid {p['BORDER']}; border-radius:10px; }}")
        l = QVBoxLayout(f)
        l.setContentsMargins(16, 14, 16, 14)
        l.setSpacing(8)

        # Header: title + nút edit (chỉ cho key points, không có checkbox)
        hdr = QHBoxLayout()
        t = QLabel(title)
        t.setFont(QFont("JetBrains Mono", 8))
        t.setStyleSheet(f"color:{color}; background:transparent; letter-spacing:2px; border:none")
        hdr.addWidget(t)
        hdr.addStretch()
        l.addLayout(hdr)
        l.addWidget(_divider(self._theme))

        # List các checkbox / bullet items
        checkboxes: list[tuple[QCheckBox, str]] = []   # (widget, raw_text)

        for item in items:
            # Hỗ trợ prefix "✔ " để restore trạng thái đã tick từ DB
            is_checked = item.startswith("✔ ")
            raw_text   = item[2:] if is_checked else item

            if checkbox:
                cb = QCheckBox(raw_text)
                cb.setChecked(is_checked)
                cb.setFont(QFont("JetBrains Mono", 10))
                cb.setStyleSheet(f"""
                    QCheckBox {{ color:{p['TEXT_PRI']}; background:transparent; spacing:8px; border:none}}
                    QCheckBox::indicator {{ width:13px; height:13px; border:1px solid {p['BORDER2']}; border-radius:3px; background:transparent; }}
                    QCheckBox::indicator:hover {{ border-color:{p['ACCENT']}; }}
                    QCheckBox::indicator:checked {{ background:{p['ACCENT']}; border-color:{p['ACCENT']}; }}
                    QCheckBox:checked {{ color:{p['TEXT_DIM']}; text-decoration:line-through; }}
                """)
                checkboxes.append((cb, raw_text))

                def _make_save(all_cbs):
                    def _save(_=None):
                        if not self._db or not self._recording_id:
                            return
                        new_items = [
                            ("✔ " + txt if cb.isChecked() else txt)
                            for cb, txt in all_cbs
                        ]
                        try:
                            self._db.update_action_items(self._recording_id, new_items)
                        except Exception as e:
                            print(f"[SummaryCard] Lỗi lưu action items: {e}")
                    return _save

                cb.stateChanged.connect(_make_save(checkboxes))
                l.addWidget(cb)
            else:
                row = QWidget()
                row.setStyleSheet("background:transparent; border:none")
                rl = QHBoxLayout(row)
                rl.setContentsMargins(0, 0, 0, 0)
                rl.setSpacing(8)
                dot = QLabel("·")
                dot.setFont(QFont("JetBrains Mono", 14))
                dot.setStyleSheet(f"color:{color}; background:transparent; border:none")
                dot.setFixedWidth(14)
                txt_lbl = QLabel(raw_text)
                txt_lbl.setFont(QFont("JetBrains Mono", 10))
                txt_lbl.setStyleSheet(f"color:{p['TEXT_PRI']}; background:transparent; border:none")
                txt_lbl.setWordWrap(True)
                txt_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                rl.addWidget(dot)
                rl.addWidget(txt_lbl, stretch=1)
                l.addWidget(row)
        return f

    def _footer(self, topics, sentiment):
        _SC = {
            "positive": ("#1E9654", "rgba(30,150,84,0.12)", "rgba(30,150,84,0.3)"),
            "negative": ("#E05252", "rgba(224,82,82,0.12)", "rgba(224,82,82,0.3)"),
            "neutral":  ("#4A80C4", "rgba(74,128,196,0.12)", "rgba(74,128,196,0.3)"),
        }
        _SL = {"positive": "Tích cực", "negative": "Tiêu cực", "neutral": "Trung tính"}
        f = QFrame()
        f.setStyleSheet(f"QFrame {{ background:{_p(self)['BG_CARD']}; border:1px solid {_p(self)['BORDER']}; border-radius:10px; }}")
        l = QHBoxLayout(f)
        l.setContentsMargins(16, 12, 16, 12)
        l.setSpacing(8)
        if topics:
            tt = QLabel("TOPICS")
            tt.setFont(QFont("JetBrains Mono", 8))
            tt.setStyleSheet(f"color:{_p(self)['TEXT_SEC']}; background:transparent; letter-spacing:2px; border:none")
            tv = QLabel("  ·  ".join(topics[:4]))
            tv.setFont(QFont("JetBrains Mono", 10))
            tv.setStyleSheet(f"color:{_p(self)['TEXT_PRI']}; background:transparent; border:none")
            l.addWidget(tt)
            l.addSpacing(8)
            l.addWidget(tv, stretch=1)
        # if sentiment:
        #     s = sentiment if sentiment in _SC else "neutral"
        #     fg, bg, bd = _SC[s]
        #     sl = QLabel(_SL[s])
        #     sl.setFont(QFont("JetBrains Mono", 9))
        #     sl.setStyleSheet(f"color:{fg}; background:{bg}; border:1px solid {bd}; border-radius:4px; padding:2px 10px;")
        #     l.addWidget(sl)
        return f


class DetailPanel(QWidget):
    """
    Hiển thị chi tiết 1 recording đã chọn.

    State:
        _current_rec_id  — ID recording đang xem
        _current_segments — list[dict] từ DB

    Khi load(rec_data, segments):
        1. Cập nhật header (title, meta)
        2. Xóa và điền lại 2 QTextEdit transcript (EN + VI)
        3. Load Summary và Mindmap
    """
    deleted = pyqtSignal(str)   # recording_id — yêu cầu xóa

    def __init__(self, db: DatabaseService, theme_manager=None, parent=None):
        super().__init__(parent)
        self._db              = db
        self._theme           = theme_manager
        self._current_rec_id  = None
        self._current_rec     = None
        self.setStyleSheet(f"background:{_p(self)['BG_BASE']};")

        self._build_ui()


    def _build_ui(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # Stack: empty state vs loaded state
        self._stack = QStackedWidget()
        self._stack.setStyleSheet("background:transparent;")
        self._stack.addWidget(self._build_empty())     # index 0
        self._stack.addWidget(self._build_loaded())    # index 1
        lay.addWidget(self._stack)

    def _build_empty(self) -> QWidget:
        """Màn hình placeholder khi chưa chọn recording."""
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        l = QVBoxLayout(w)
        l.setAlignment(Qt.AlignmentFlag.AlignCenter)
        l.setSpacing(12)

        icon = QLabel("◷")
        icon.setFont(QFont("JetBrains Mono", 48))
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(f"color:{_p(self)['TEXT_DIM']}; background:transparent;")

        msg = QLabel("Chọn một bản ghi âm để xem chi tiết")
        msg.setFont(QFont("JetBrains Mono", 10))
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg.setStyleSheet(f"color:{_p(self)['TEXT_SEC']}; background:transparent;")

        l.addWidget(icon)
        l.addWidget(msg)
        return w

    def _build_loaded(self) -> QWidget:
        """Panel chi tiết — ẩn khi chưa load."""
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(14)

        # ── Header: title + meta + actions ───
        self._hdr_card = QFrame()
        hdr_card = self._hdr_card
        hdr_card.setStyleSheet(f"""
            QFrame {{
                background:{_p(self)['BG_CARD']};
                border:1px solid {_p(self)['BORDER']};
                border-radius:10px;
            }}
        """)
        hdr_lay = QVBoxLayout(hdr_card)
        hdr_lay.setContentsMargins(16, 12, 16, 12)
        hdr_lay.setSpacing(6)

        # Row: title + buttons
        title_row = QHBoxLayout()
        self._lbl_title = QLabel("—")
        self._lbl_title.setFont(QFont("JetBrains Mono", 13))
        self._lbl_title.setStyleSheet(f"color:{_p(self)['TEXT_PRI']}; background:transparent;")
        title_row.addWidget(self._lbl_title)
        title_row.addStretch()

        self._btn_rename = QPushButton("  Chỉnh tên")
        self._btn_rename.setIcon(icons.get("rename", _p(self)['TEXT_SEC']))
        self._btn_rename.setIconSize(QSize(13, 13))
        self._btn_rename.setFixedHeight(28)
        self._btn_rename.setFont(QFont("JetBrains Mono", 8))
        self._btn_rename.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_rename.clicked.connect(self._on_rename)
        self._btn_rename.setStyleSheet(f"""
            QPushButton {{
                background:transparent; color:{_p(self)['TEXT_SEC']};
                border:1px solid {_p(self)['BORDER2']}; border-radius:5px;
                padding:0 12px; letter-spacing:1px;
            }}
            QPushButton:hover {{ color:{_p(self)['TEXT_PRI']}; border-color:{_p(self)['ACCENT']}; }}
        """)
        self._btn_delete = self._mk_danger_btn("  Xóa")
        self._btn_delete.setIcon(icons.get("delete", _p(self)['DANGER']))
        self._btn_delete.setIconSize(QSize(13, 13))
        self._btn_delete.clicked.connect(self._on_delete)
        title_row.addWidget(self._btn_rename)
        title_row.addSpacing(6)
        title_row.addWidget(self._btn_delete)

        # Meta row
        self._lbl_meta = QLabel("—")
        self._lbl_meta.setFont(QFont("JetBrains Mono", 8))
        self._lbl_meta.setStyleSheet(f"color:{_p(self)['TEXT_SEC']}; background:transparent;")

        hdr_lay.addLayout(title_row)
        hdr_lay.addWidget(self._lbl_meta)
        lay.addWidget(hdr_card)


        # ── Tab bar: Transcript | Summary | Mindmap ───────────
        tab_row = QHBoxLayout()
        tab_row.setSpacing(6)
        self._tab_btns = []
        tab_defs = [
            ("  Văn bản", "transcript"),
            ("  Tóm tắt",    "summary"),
            ("  Sơ đồ tư duy",    "mindmap"),
            ("  Biên bản",   "document"),
        ]
        for i, (label, icon_name) in enumerate(tab_defs):
            btn = QPushButton(label)
            btn.setIcon(icons.get(icon_name, _p(self)['TEXT_SEC']))
            btn.setIconSize(QSize(13, 13))
            btn.setCheckable(True)
            btn.setFixedHeight(28)
            btn.setFont(QFont("JetBrains Mono", 8))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _, idx=i: self._switch_tab(idx))
            self._tab_btns.append(btn)
            tab_row.addWidget(btn)
        tab_row.addStretch()

        # EN/VI pills — chỉ hiện ở tab Transcript
        self._pill_en = self._mk_pill("EN", active=True)
        self._pill_vi = self._mk_pill("VI", active=True)
        self._pill_en.clicked.connect(
            lambda: self._card_en.setVisible(self._pill_en.isChecked())
        )
        self._pill_vi.clicked.connect(
            lambda: self._card_vi.setVisible(self._pill_vi.isChecked())
        )
        self._pills_widget = QWidget()
        self._pills_widget.setStyleSheet("background:transparent;")
        pw = QHBoxLayout(self._pills_widget)
        pw.setContentsMargins(0, 0, 0, 0)
        pw.setSpacing(6)
        pw.addWidget(self._pill_en)
        pw.addWidget(self._pill_vi)
        tab_row.addWidget(self._pills_widget)
        lay.addLayout(tab_row)

        # ── Tab content stack ─────────────────────────────────
        self._tab_stack = QStackedWidget()
        self._tab_stack.setStyleSheet("background:transparent;")

        # Tab 0: Transcript
        trans_w = QWidget()
        trans_w.setStyleSheet("background:transparent;")
        trans_lay = QHBoxLayout(trans_w)
        trans_lay.setContentsMargins(0, 0, 0, 0)
        trans_lay.setSpacing(12)
        self._card_en, self._trans_en = self._mk_transcript_card("TIẾNG ANH",   "#60A5FA")
        self._card_vi, self._trans_vi = self._mk_transcript_card("TIẾNG VIỆT",  "#34D399")
        trans_lay.addWidget(self._card_en)
        trans_lay.addWidget(self._card_vi)
        self._tab_stack.addWidget(trans_w)

        # Tab 1: Summary
        self._summary_card = SummaryCard(theme_manager=self._theme)
        self._summary_card._db = self._db   # inject db ngay khi tạo
        self._tab_stack.addWidget(self._summary_card)

        # Tab 2: Mindmap — dùng MindmapWidget độc lập, theme-aware
        self._mindmap_widget = MindmapWidget(theme_manager=self._theme)
        self._tab_stack.addWidget(self._mindmap_widget)

        # Tab 3: Biên bản cuộc họp
        self._minutes_widget = self._build_minutes_widget()
        self._tab_stack.addWidget(self._minutes_widget)

        lay.addWidget(self._tab_stack, stretch=1)

        self._switch_tab(0)
        return w

    def _switch_tab(self, idx: int):
        self._tab_stack.setCurrentIndex(idx)
        _ACTIVE   = f"background:{_p(self)['BG_ACTIVE']}; color:{_p(self)['TEXT_ACTIVE']}; border:1px solid {_p(self)['BORDER_ACTIVE']}; border-radius:13px; padding:0 12px; letter-spacing:1px;"
        _INACTIVE = f"background:transparent; color:{_p(self)['TEXT_SEC']}; border:1px solid {_p(self)['BORDER2']}; border-radius:13px; padding:0 12px; letter-spacing:1px;"
        for i, btn in enumerate(self._tab_btns):
            btn.setChecked(i == idx)
            btn.setStyleSheet(
                f"QPushButton {{ {_ACTIVE if i == idx else _INACTIVE} }}"
                f"QPushButton:hover {{ background:{_p(self)['BG_HOVER']}; color:{_p(self)['TEXT_HOVER']}; border:1px solid {_p(self)['BORDER_HOVER']}; }}"
            )
        self._pills_widget.setVisible(idx == 0)

        # Trigger mindmap generation lazy khi mở tab lần đầu
        if idx == 2 and self._current_rec_id and self._db:
            segs  = self._db.get_recording_segments(self._current_rec_id)
            title = self._current_rec.get("title", "") if self._current_rec else ""
            self._mindmap_widget.load(self._db, self._current_rec_id, segs, title=title)
    def _mk_transcript_card(self, title: str, color: str):
        """Trả về (card_frame, QTextEdit)."""
        p = _p(self)
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background:{p['BG_CARD']};
                border:1px solid {p['BORDER']};
                border-radius:10px;
            }}
        """)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(8)

        # Header với title + nút edit
        hdr = QHBoxLayout()
        lbl = QLabel(title)
        lbl.setFont(QFont("JetBrains Mono", 9))
        lbl.setStyleSheet(f"color:{color}; background:transparent; letter-spacing:2px;")
        hdr.addWidget(lbl)
        hdr.addStretch()

        btn_edit   = QPushButton("✎ Chỉnh sửa")
        btn_save   = QPushButton("✔ Lưu")
        btn_cancel = QPushButton("✕ Huỷ")
        for b in (btn_edit, btn_save, btn_cancel):
            b.setFont(QFont("JetBrains Mono", 8))
            b.setFixedHeight(22)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_save.setVisible(False)
        btn_cancel.setVisible(False)
        btn_edit.setStyleSheet(f"""
            QPushButton {{ background:transparent; color:{p['TEXT_DIM']};
                border:1px solid {p['BORDER']}; border-radius:4px; padding:0 8px; }}
            QPushButton:hover {{ color:{p['ACCENT']}; border-color:{p['ACCENT']}; }}
        """)
        btn_save.setStyleSheet(f"""
            QPushButton {{ background:{p['ACCENT']}; color:#fff;
                border:none; border-radius:4px; padding:0 9px; }}
        """)
        btn_cancel.setStyleSheet(f"""
            QPushButton {{ background:transparent; color:{p['TEXT_DIM']};
                border:1px solid {p['BORDER']}; border-radius:4px; padding:0 8px; }}
            QPushButton:hover {{ color:{p['DANGER']}; border-color:{p['DANGER']}; }}
        """)
        hdr.addWidget(btn_edit)
        hdr.addSpacing(4)
        hdr.addWidget(btn_save)
        hdr.addSpacing(4)
        hdr.addWidget(btn_cancel)
        cl.addLayout(hdr)
        cl.addWidget(_divider(self._theme))

        trans = QTextEdit()
        trans.setReadOnly(True)
        trans.setFont(QFont("JetBrains Mono", 11))
        trans.setStyleSheet(f"""
            QTextEdit {{
                background:transparent; color:{p['TEXT_PRI']};
                border:none;
            }}
            QScrollBar:vertical {{
                background:transparent; width:4px; border-radius:2px;
            }}
            QScrollBar::handle:vertical {{
                background:{p['BORDER2']}; border-radius:2px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
        """)
        cl.addWidget(trans, stretch=1)

        # ── Wiring edit / save / cancel ───────────────────────────────
        def _on_edit():
            p2 = _p(self)
            trans.setReadOnly(False)
            trans.setStyleSheet(f"""
                QTextEdit {{
                    background:{p2['BG_CARD']}; color:{p2['TEXT_PRI']};
                    border:1px solid {p2['ACCENT']}; border-radius:6px; padding:4px;
                }}
                QScrollBar:vertical {{ background:transparent; width:4px; border-radius:2px; }}
                QScrollBar::handle:vertical {{ background:{p2['BORDER2']}; border-radius:2px; }}
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
            """)
            trans.setFocus()
            btn_edit.setVisible(False)
            btn_save.setVisible(True)
            btn_cancel.setVisible(True)
            trans.setProperty("_backup", trans.toPlainText())

        def _on_save():
            p2 = _p(self)
            trans.setReadOnly(True)
            trans.setStyleSheet(f"""
                QTextEdit {{
                    background:transparent; color:{p2['TEXT_PRI']}; border:none;
                }}
                QScrollBar:vertical {{ background:transparent; width:4px; border-radius:2px; }}
                QScrollBar::handle:vertical {{ background:{p2['BORDER2']}; border-radius:2px; }}
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
            """)
            btn_edit.setVisible(True)
            btn_save.setVisible(False)
            btn_cancel.setVisible(False)

        def _on_cancel():
            p2 = _p(self)
            backup = trans.property("_backup") or ""
            trans.setPlainText(backup)
            trans.setReadOnly(True)
            trans.setStyleSheet(f"""
                QTextEdit {{
                    background:transparent; color:{p2['TEXT_PRI']}; border:none;
                }}
                QScrollBar:vertical {{ background:transparent; width:4px; border-radius:2px; }}
                QScrollBar::handle:vertical {{ background:{p2['BORDER2']}; border-radius:2px; }}
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
            """)
            btn_edit.setVisible(True)
            btn_save.setVisible(False)
            btn_cancel.setVisible(False)

        btn_edit.clicked.connect(_on_edit)
        btn_save.clicked.connect(_on_save)
        btn_cancel.clicked.connect(_on_cancel)

        return card, trans

    def _mk_pill(self, label: str, active: bool = True) -> QPushButton:
        btn = QPushButton(label)
        btn.setCheckable(True)
        btn.setChecked(active)
        btn.setFixedHeight(26)
        btn.setFont(QFont("JetBrains Mono", 8))
        btn.setCursor(Qt.CursorShape.PointingHandCursor)

        def _style():
            on = btn.isChecked()
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {_p(self)['BG_ACTIVE'] if on else "transparent"};
                    color: {_p(self)['TEXT_ACTIVE'] if on else _p(self)['TEXT_SEC']};
                    border: 1px solid {_p(self)['BORDER_ACTIVE'] if on else _p(self)['BORDER2']};
                    border-radius: 13px; padding: 0 12px; letter-spacing: 1px;
                }}
                QPushButton:hover {{ background:{_p(self)['BG_HOVER']}; color:{_p(self)['TEXT_HOVER']}; }}
            """)
        _style()
        btn.toggled.connect(lambda _: _style())
        return btn

    def _mk_danger_btn(self, text: str) -> QPushButton:
        b = QPushButton(text)
        b.setFixedHeight(28)
        b.setFont(QFont("JetBrains Mono", 8))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setStyleSheet(f"""
            QPushButton {{
                background:transparent; color:{_p(self)['DANGER']};
                border:1px solid rgba(224,82,82,0.35); border-radius:5px;
                padding:0 12px; letter-spacing:1px;
            }}
            QPushButton:hover {{ background:rgba(224,82,82,0.12); border-color:{_p(self)['DANGER']}; }}
        """)
        return b

    def _build_minutes_widget(self) -> QWidget:
        p = _p(self)
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        # ── Prompt options + custom textarea ─────────────────────────────────
        self._prompt_options = self._load_prompt_options()
        self._selected_prompt_key: str = next(iter(self._prompt_options), "Khác")

        self._custom_prompt_edit = QTextEdit()
        self._custom_prompt_edit.setPlaceholderText(
            "Nhập prompt tùy chỉnh của bạn tại đây…\n"
            "Ví dụ: Tóm tắt cuộc họp theo định dạng ngắn gọn, chỉ gồm các quyết định chính."
        )
        self._custom_prompt_edit.setFont(QFont("JetBrains Mono", 9))
        self._custom_prompt_edit.setFixedHeight(90)
        self._custom_prompt_edit.setVisible(False)
        self._custom_prompt_edit.setStyleSheet(f"""
            QTextEdit {{
                background:{p['BG_CARD']}; color:{p['TEXT_PRI']};
                border:1px solid {p['BORDER2']}; border-radius:6px;
                padding:8px;
            }}
            QScrollBar:vertical {{ background:transparent; width:4px; }}
            QScrollBar::handle:vertical {{ background:{p['BORDER2']}; border-radius:2px; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
        """)
        lay.addWidget(self._custom_prompt_edit)

        # Toolbar: Combo + Generate + Export
        from PyQt6.QtWidgets import QComboBox
        toolbar = QHBoxLayout()

        self._prompt_combo = QComboBox()
        self._prompt_combo.setFont(QFont("JetBrains Mono", 9))
        self._prompt_combo.setFixedHeight(30)
        self._prompt_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        for key in self._prompt_options:
            self._prompt_combo.addItem(key)
        self._prompt_combo.setStyleSheet(f"""
            QComboBox {{
                background:{p['BG_CARD']}; color:{p['TEXT_PRI']};
                border:1px solid {p['BORDER2']}; border-radius:6px;
                padding:0 10px; min-width:180px;
            }}
            QComboBox:hover {{ border-color:{p['ACCENT']}; }}
            QComboBox::drop-down {{ border:none; width:24px; }}
            QComboBox::down-arrow {{
                image: none;
                border-left:4px solid transparent;
                border-right:4px solid transparent;
                border-top:5px solid {p['TEXT_SEC']};
                margin-right:8px;
            }}
            QComboBox QAbstractItemView {{
                background:{p['BG_CARD']};
                color:{p['TEXT_PRI']};
                border:1px solid {p['BORDER2']};
                border-radius:6px;
                selection-background-color:rgba(59,110,245,0.2);
                selection-color:{p['TEXT_PRI']};
                padding:4px;
                outline:none;
            }}
            QComboBox QAbstractItemView::item {{ padding:6px 10px; border-radius:4px; }}
        """)
        self._prompt_combo.currentTextChanged.connect(self._on_prompt_type_selected)
        self._on_prompt_type_selected(self._selected_prompt_key)
        toolbar.addWidget(self._prompt_combo)
        toolbar.addSpacing(8)

        self._btn_gen_minutes = QPushButton("  Tạo biên bản")
        self._btn_gen_minutes.setIcon(icons.get("summary", p['ACCENT']))
        self._btn_gen_minutes.setIconSize(QSize(13, 13))
        self._btn_gen_minutes.setFixedHeight(30)
        self._btn_gen_minutes.setFont(QFont("JetBrains Mono", 8))
        self._btn_gen_minutes.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_gen_minutes.clicked.connect(self._on_gen_minutes)
        self._btn_gen_minutes.setStyleSheet(f"""
            QPushButton {{
                background:rgba(59,110,245,0.15); color:#000000;
                border:1px solid rgba(59,110,245,0.4); border-radius:5px;
                padding:0 12px; letter-spacing:1px;
            }}
            QPushButton:hover {{ background:rgba(59,110,245,0.25); }}
            QPushButton:disabled {{ background:transparent; color:{p['TEXT_DIM']};
                                    border-color:{p['BORDER']}; }}
        """)

        self._btn_export_minutes = QPushButton("  Export .docx")
        self._btn_export_minutes.setIcon(icons.get("save", "#34D399"))
        self._btn_export_minutes.setIconSize(QSize(13, 13))
        self._btn_export_minutes.setFixedHeight(30)
        self._btn_export_minutes.setFont(QFont("JetBrains Mono", 8))
        self._btn_export_minutes.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_export_minutes.setEnabled(False)
        self._btn_export_minutes.clicked.connect(self._on_export_minutes)
        self._btn_export_minutes.setStyleSheet(f"""
            QPushButton {{
                background:rgba(52,211,153,0.15); color:#000000;
                border:1px solid rgba(52,211,153,0.3); border-radius:5px;
                padding:0 12px; letter-spacing:1px;
            }}
            QPushButton:hover {{ background:rgba(52,211,153,0.25); }}
            QPushButton:disabled {{ background:transparent; color:{p['TEXT_DIM']};
                                    border-color:{p['BORDER']}; }}
        """)

        self._btn_edit_minutes = QPushButton("  Chỉnh sửa")
        self._btn_edit_minutes.setIcon(icons.get("rename", p['TEXT_SEC']))
        self._btn_edit_minutes.setIconSize(QSize(13, 13))
        self._btn_edit_minutes.setFixedHeight(30)
        self._btn_edit_minutes.setFont(QFont("JetBrains Mono", 8))
        self._btn_edit_minutes.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_edit_minutes.setEnabled(False)
        self._btn_edit_minutes.clicked.connect(self._on_toggle_edit_minutes)
        self._btn_edit_minutes.setStyleSheet(f"""
            QPushButton {{
                background:transparent; color:{p['TEXT_SEC']};
                border:1px solid {p['BORDER2']}; border-radius:5px;
                padding:0 12px; letter-spacing:1px;
            }}
            QPushButton:hover {{ color:{p['TEXT_PRI']}; border-color:{p['ACCENT']}; }}
            QPushButton:disabled {{ background:transparent; color:{p['TEXT_DIM']};
                                    border-color:{p['BORDER']}; }}
        """)

        self._lbl_minutes_status = QLabel("")
        self._lbl_minutes_status.setFont(QFont("JetBrains Mono", 8))
        self._lbl_minutes_status.setStyleSheet(
            f"color:{p['TEXT_SEC']}; background:transparent;")

        toolbar.addWidget(self._btn_gen_minutes)
        toolbar.addSpacing(8)
        toolbar.addWidget(self._btn_export_minutes)
        toolbar.addSpacing(8)
        toolbar.addWidget(self._btn_edit_minutes)
        toolbar.addSpacing(12)
        toolbar.addWidget(self._lbl_minutes_status)
        toolbar.addStretch()
        lay.addLayout(toolbar)

        # Text area hiển thị biên bản
        self._txt_minutes = QTextEdit()
        self._txt_minutes.setReadOnly(True)
        self._txt_minutes.setFont(QFont("JetBrains Mono", 10))
        self._txt_minutes.setPlaceholderText(
            "Nhấn 'Tạo biên bản' để generate từ transcript…")
        self._txt_minutes.setStyleSheet(f"""
            QTextEdit {{
                background:{p['BG_CARD']}; color:{p['TEXT_PRI']};
                border:1px solid {p['BORDER']}; border-radius:8px;
                padding:12px; line-height:1.7;
            }}
            QScrollBar:vertical {{
                background:transparent; width:4px; border-radius:2px;
            }}
            QScrollBar::handle:vertical {{
                background:{p['BORDER2']}; border-radius:2px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
        """)
        lay.addWidget(self._txt_minutes, stretch=1)

        self._minutes_result: MinutesResult | None = None
        self._minutes_worker: MinutesWorker | None = None
        self._minutes_editing: bool = False
        return w

    def _markdown_to_html(self, md: str) -> str:
        """Chuyển Markdown đơn giản sang HTML để hiển thị trong QTextEdit."""
        p       = _p(self)
        bg      = p['BG_CARD']
        pri     = p['TEXT_PRI']
        sec     = p['TEXT_SEC']
        accent  = p.get('ACCENT', '#3B6EF5')
        dim     = p['TEXT_DIM']

        lines      = md.split("\n")
        html       = []
        in_table   = False
        table_rows = []

        def flush_table():
            nonlocal in_table, table_rows
            if not table_rows:
                return
            tbl = (
                f'<table style="border-collapse:collapse; width:100%; '
                f'margin:8px 0; font-family:JetBrains Mono,monospace; font-size:10pt;">'
            )
            hd = False
            for row in table_rows:
                cells = [c.strip() for c in row.strip("|").split("|")]
                if all(set(c) <= set("-: ") for c in cells):
                    hd = True
                    continue
                tag = "th" if not hd else "td"
                cell_style = (
                    f'padding:6px 12px; border:1px solid {dim};'
                    + (f' background:rgba(59,110,245,0.12); color:{accent};'
                       if tag == "th" else f' color:{pri};')
                )
                tbl += "<tr>" + "".join(
                    f'<{tag} style="{cell_style}">{c}</{tag}>' for c in cells
                ) + "</tr>"
            tbl += "</table>"
            html.append(tbl)
            table_rows.clear()
            in_table = False

        for line in lines:
            s = line.strip()

            if s.startswith("|"):
                in_table = True
                table_rows.append(s)
                continue
            else:
                if in_table:
                    flush_table()

            if not s:
                html.append("<br/>")
            elif s.startswith("# "):
                html.append(
                    f'<h1 style="color:{accent}; font-family:JetBrains Mono,monospace; '
                    f'font-size:14pt; margin:12px 0 4px 0;">{s[2:].strip()}</h1>'
                )
            elif s.startswith("## "):
                html.append(
                    f'<h2 style="color:{accent}; font-family:JetBrains Mono,monospace; '
                    f'font-size:12pt; margin:10px 0 4px 0; border-bottom:1px solid {dim}; '
                    f'padding-bottom:4px;">{s[3:].strip()}</h2>'
                )
            elif s.startswith("### "):
                html.append(
                    f'<h3 style="color:{sec}; font-family:JetBrains Mono,monospace; '
                    f'font-size:11pt; margin:8px 0 2px 0;">{s[4:].strip()}</h3>'
                )
            elif s.startswith("- ") or s.startswith("* "):
                content = self._inline_md(s[2:].strip(), pri, accent)
                html.append(
                    f'<p style="margin:2px 0 2px 16px; color:{pri}; '
                    f'font-family:JetBrains Mono,monospace; font-size:10pt;">'
                    f'<span style="color:{accent};">·</span>&nbsp;&nbsp;{content}</p>'
                )
            else:
                content = self._inline_md(s, pri, accent)
                html.append(
                    f'<p style="margin:3px 0; color:{pri}; '
                    f'font-family:JetBrains Mono,monospace; font-size:10pt;">{content}</p>'
                )

        if in_table:
            flush_table()

        body = "\n".join(html)
        return (
            f'<html><body style="background:{bg}; padding:4px;">'
            f'{body}</body></html>'
        )

    def _inline_md(self, text: str, pri: str, accent: str) -> str:
        """Xử lý **bold** và `code` inline."""
        import re
        text = re.sub(r'\*\*(.+?)\*\*',
                      rf'<b style="color:{accent};">\1</b>', text)
        text = re.sub(r'`(.+?)`',
                      r'<code style="background:rgba(255,255,255,0.08); '
                      r'padding:1px 4px; border-radius:3px;">\1</code>', text)
        return text

    def _load_prompt_options(self) -> dict:
        """Load các loại prompt từ dialogue_categories.json (root dự án)."""
        import os, json as _json
        # history_page.py nằm ở ui/pages/ → root = ../../
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        path = os.path.join(root, "dialogue_categories.json")
        try:
            with open(path, encoding="utf-8") as f:
                data = _json.load(f)
            options = {k: v for k, v in data.items() if k != "Khác"}
            options["Khác"] = ""
            return options
        except Exception as e:
            print(f"[MINUTES] Không load được dialogue_categories.json: {e}")
            return {"Khác": ""}

    def _on_prompt_type_selected(self, key: str):
        """Show/hide custom textarea khi chọn 'Khác'."""
        self._selected_prompt_key = key
        self._custom_prompt_edit.setVisible(key == "Khác")

    def _on_toggle_edit_minutes(self):
        """Toggle giữa chế độ xem (ReadOnly) và chỉnh sửa."""
        p = _p(self)
        if not self._minutes_editing:
            # ── Vào chế độ chỉnh sửa ────────────────────────────────────
            self._minutes_editing = True
            # Lấy nội dung markdown từ result để edit (không edit HTML)
            raw_md = self._minutes_result.text if self._minutes_result else ""
            self._txt_minutes.setReadOnly(False)
            self._txt_minutes.setPlainText(raw_md)
            self._txt_minutes.setStyleSheet(f"""
                QTextEdit {{
                    background:{p['BG_CARD']}; color:{p['TEXT_PRI']};
                    border:1px solid {p['ACCENT']}; border-radius:8px;
                    padding:12px; line-height:1.7;
                }}
                QScrollBar:vertical {{ background:transparent; width:4px; border-radius:2px; }}
                QScrollBar::handle:vertical {{ background:{p['BORDER2']}; border-radius:2px; }}
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
            """)
            self._btn_edit_minutes.setText("  Lưu")
            self._btn_edit_minutes.setIcon(icons.get("save", "#34D399"))
            self._btn_edit_minutes.setStyleSheet(f"""
                QPushButton {{
                    background:rgba(52,211,153,0.15); color:#000000;
                    border:1px solid rgba(52,211,153,0.4); border-radius:5px;
                    padding:0 12px; letter-spacing:1px;
                }}
                QPushButton:hover {{ background:rgba(52,211,153,0.25); }}
            """)
            self._lbl_minutes_status.setText("Đang chỉnh sửa…")
        else:
            # ── Lưu và thoát chỉnh sửa ──────────────────────────────────
            new_text = self._txt_minutes.toPlainText().strip()
            self._minutes_editing = False

            if self._minutes_result is None:
                from core.minutes_service import MinutesResult as _MR
                self._minutes_result = _MR(text=new_text)
            else:
                self._minutes_result.text = new_text

            # Hiển thị lại HTML
            self._txt_minutes.setReadOnly(True)
            self._txt_minutes.setHtml(self._markdown_to_html(new_text))
            self._txt_minutes.setStyleSheet(f"""
                QTextEdit {{
                    background:{p['BG_CARD']}; color:{p['TEXT_PRI']};
                    border:1px solid {p['BORDER']}; border-radius:8px;
                    padding:12px; line-height:1.7;
                }}
                QScrollBar:vertical {{ background:transparent; width:4px; border-radius:2px; }}
                QScrollBar::handle:vertical {{ background:{p['BORDER2']}; border-radius:2px; }}
                QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
            """)
            self._btn_edit_minutes.setText("  Chỉnh sửa")
            self._btn_edit_minutes.setIcon(icons.get("rename", _p(self)['TEXT_SEC']))
            p2 = _p(self)
            self._btn_edit_minutes.setStyleSheet(f"""
                QPushButton {{
                    background:transparent; color:{p2['TEXT_SEC']};
                    border:1px solid {p2['BORDER2']}; border-radius:5px;
                    padding:0 12px; letter-spacing:1px;
                }}
                QPushButton:hover {{ color:{p2['TEXT_PRI']}; border-color:{p2['ACCENT']}; }}
            """)

            # Lưu vào DB
            if self._current_rec_id and self._db and new_text:
                try:
                    self._db.save_minutes(self._current_rec_id, new_text)
                    self._lbl_minutes_status.setText("✔ Đã lưu")
                except Exception as e:
                    self._lbl_minutes_status.setText(f"Lỗi lưu: {str(e)[:40]}")
            else:
                self._lbl_minutes_status.setText("✔ Hoàn thành")

    def _on_gen_minutes(self):
        if not self._current_rec_id or not self._db:
            return
        segments = self._db.get_recording_segments(self._current_rec_id)
        if not segments:
            self._lbl_minutes_status.setText("Không có transcript.")
            return

        # Lấy prompt hiện tại
        if self._selected_prompt_key == "Khác":
            custom = self._custom_prompt_edit.toPlainText().strip()
            if not custom:
                self._lbl_minutes_status.setText("Vui lòng nhập prompt.")
                return
            prompt = custom
        else:
            prompt = self._prompt_options.get(self._selected_prompt_key, "")

        self._btn_gen_minutes.setEnabled(False)
        self._btn_export_minutes.setEnabled(False)
        self._lbl_minutes_status.setText("Đang generate…")
        self._txt_minutes.setPlainText("")

        title = (self._current_rec or {}).get("title", "")
        # Lấy ngày giờ họp từ record
        recorded_at = (self._current_rec or {}).get("recorded_at", "")
        if recorded_at:
            try:
                from datetime import datetime as _dt
                dt = recorded_at if isinstance(recorded_at, _dt) else _dt.fromisoformat(str(recorded_at))
                recorded_at = dt.strftime("%d/%m/%Y %H:%M")
            except Exception:
                recorded_at = str(recorded_at)
        self._minutes_worker = MinutesWorker(segments, title=title, custom_prompt=prompt, recorded_at=recorded_at)
        self._minutes_worker.done.connect(self._on_minutes_done)
        self._minutes_worker.start()

    def _on_minutes_done(self, result: MinutesResult):
        self._minutes_result = result
        self._btn_gen_minutes.setEnabled(True)

        if result.error:
            self._lbl_minutes_status.setText(f"Lỗi: {result.error[:50]}")
            return

        self._txt_minutes.setHtml(self._markdown_to_html(result.text))
        self._lbl_minutes_status.setText("✔ Hoàn thành")
        self._btn_export_minutes.setEnabled(True)
        self._btn_edit_minutes.setEnabled(True)

        # ── Lưu biên bản vào DB ──────────────────────────────────────────
        if self._current_rec_id and self._db:
            try:
                self._db.save_minutes(self._current_rec_id, result.text)
            except Exception as e:
                print(f"[MINUTES] Không lưu được vào DB: {e}")

    def _on_export_minutes(self):
        if not self._minutes_result or not self._minutes_result.text:
            return
        from PyQt6.QtWidgets import QFileDialog
        title = (self._current_rec or {}).get("title", "bien_ban")
        default_name = title.replace(" ", "_").replace("/", "-") + ".docx"
        path, _ = QFileDialog.getSaveFileName(
            self, "Lưu biên bản", default_name,
            "Word Document (*.docx)")
        if not path:
            return
        try:
            export_docx(self._minutes_result.text, title, path)
            self._lbl_minutes_status.setText(f"✔ Đã lưu: {path.split('/')[-1]}")
        except Exception as e:
            self._lbl_minutes_status.setText(f"Lỗi export: {str(e)[:50]}")

    # ── Public API 
    def load(self, rec: dict):
        """
        Load recording vào detail panel.
        Truyền None để clear panel về trạng thái rỗng.
        """
        if rec is None:
            self._current_rec_id = None
            self._current_rec    = None
            self._stack.setCurrentIndex(0)   # show empty state
            return

        self._current_rec_id = rec["id"]
        self._current_rec    = rec

        # Header
        self._lbl_title.setText(rec.get("title") or "Untitled")
        dur   = rec.get("duration_seconds") or 0
        spks  = rec.get("speaker_count") or 0
        wds   = rec.get("word_count") or 0
        date_str = ""
        if rec.get("recorded_at"):
            try:
                dt = rec["recorded_at"]
                if isinstance(dt, str):
                    dt = datetime.fromisoformat(dt)
                date_str = dt.strftime("%d/%m/%Y %H:%M")
            except Exception:
                pass

        src = rec.get("source", "live")
        src_label = {"live": "Thu trực tiếp", "upload": "Tải tệp", "meeting": "Meeting"}.get(src, src)

        # self._lbl_meta.setText(
        #     f"{date_str}   •   {_fmt(dur)}   •   {spks} speakers   •   {wds} words"
        # )
        self._lbl_meta.setText(
            f"{date_str}   •   {_fmt(dur)}   •   {spks} speakers   •   {wds} words   •   {src_label}"
        )

        # Transcript segments từ DB
        segments = self._db.get_recording_segments(rec["id"])
        self._fill_transcripts(segments)

        # Summary từ DB
        summary_data = self._db.get_summary(rec["id"])
        self._summary_card._db = self._db          # đảm bảo db luôn up-to-date
        self._summary_card.load(summary_data, recording_id=rec["id"])

        # Reset / load minutes tab
        self._minutes_result = None
        self._minutes_editing = False
        self._btn_export_minutes.setEnabled(False)
        self._btn_edit_minutes.setEnabled(False)
        self._lbl_minutes_status.setText("")

        saved_minutes = self._db.get_minutes(rec["id"]) if self._db else None
        if saved_minutes:
            from core.minutes_service import MinutesResult as _MR
            self._minutes_result = _MR(text=saved_minutes)
            self._txt_minutes.setReadOnly(True)
            self._txt_minutes.setHtml(self._markdown_to_html(saved_minutes))
            self._lbl_minutes_status.setText("✔ Đã lưu")
            self._btn_export_minutes.setEnabled(True)
            self._btn_edit_minutes.setEnabled(True)
        else:
            self._txt_minutes.setReadOnly(True)
            self._txt_minutes.setPlainText("")

        # Mindmap load lazy khi click tab

        self._switch_tab(0)
        self._stack.setCurrentIndex(1)

    def _fill_transcripts(self, segments: list[dict]):
        """Xóa transcript cũ rồi điền lại từ segments."""
        self._trans_en.clear()
        self._trans_vi.clear()

        last_spk_en = None
        last_spk_vi = None

        for seg in segments:
            spk_idx = seg.get("speaker_index", 0)
            color   = SPEAKER_COLORS[(spk_idx - 1) % len(SPEAKER_COLORS)]

            last_spk_en = self._insert_text(self._trans_en, seg.get("text_en", ""), spk_idx, color, last_spk_en)
            last_spk_vi = self._insert_text(self._trans_vi, seg.get("text_vi", ""), spk_idx, color, last_spk_vi)

    def _insert_text(self, widget: QTextEdit, text: str, spk_idx: int, color: str, last_spk) -> int:
        """Insert 1 segment vào QTextEdit với speaker label + màu."""
        p   = _p(self)
        cur = widget.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)

        if spk_idx != last_spk:
            if last_spk is not None:
                fmt = QTextCharFormat()
                fmt.setForeground(QColor(p["TEXT_PRI"]))
                cur.insertText("\n", fmt)
            fmt_spk = QTextCharFormat()
            fmt_spk.setForeground(QColor(color))
            fmt_spk.setFontWeight(QFont.Weight.Bold)
            fmt_spk.setFontPointSize(8)
            cur.insertText(f"SPEAKER {spk_idx}\n", fmt_spk)

        fmt_txt = QTextCharFormat()
        fmt_txt.setForeground(QColor(p["TEXT_PRI"]))
        fmt_txt.setFontPointSize(11)
        cur.insertText(text + " ", fmt_txt)
        widget.setTextCursor(cur)
        widget.verticalScrollBar().setValue(widget.verticalScrollBar().maximum())
        return spk_idx

    def _on_delete(self):
        """
        Xác nhận rồi emit signal deleted.
        MainWindow / HistoryPage sẽ xử lý xóa khỏi DB + list.
        """
        if not self._current_rec_id:
            return
        title = self._current_rec.get("title") or "this recording"
        reply = QMessageBox.question(
            self,
            "Delete recording",
            f"Delete \"{title}\"?\n\nThis will remove the transcript and audio file.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._stack.setCurrentIndex(0)
            self.deleted.emit(self._current_rec_id)

    def _on_rename(self):
        if not self._current_rec_id or not self._db:
            return
        current_title = self._current_rec.get("title") or ""
        
        dlg = QDialog(self)
        dlg.setWindowTitle("Đổi tên")
        dlg.setFixedWidth(400)
        p = _p(self)
        dlg.setStyleSheet(f"background:{p['BG_CARD']}; color:{p['TEXT_PRI']};")
        
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)
        
        lbl = QLabel("Tên mới:")
        lbl.setFont(QFont("JetBrains Mono", 9))
        lbl.setStyleSheet(f"color:{p['TEXT_SEC']};")
        
        edit = QLineEdit(current_title)
        edit.setFont(QFont("JetBrains Mono", 10))
        edit.setStyleSheet(f"""
            QLineEdit {{
                background:{p['BG_BASE']}; color:{p['TEXT_PRI']};
                border:1px solid {p['BORDER2']}; border-radius:6px;
                padding:6px 10px;
            }}
            QLineEdit:focus {{ border-color:{p['ACCENT']}; }}
        """)
        edit.selectAll()
        
        btn_row = QHBoxLayout()
        btn_ok = QPushButton("OK")
        btn_cancel = QPushButton("Hủy")
        for b in (btn_ok, btn_cancel):
            b.setFixedHeight(28)
            b.setFont(QFont("JetBrains Mono", 8))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_ok.setStyleSheet(f"background:{p['ACCENT']}; color:#fff; border:none; border-radius:5px; padding:0 12px;")
        btn_cancel.setStyleSheet(f"background:transparent; color:{p['TEXT_SEC']}; border:1px solid {p['BORDER2']}; border-radius:5px; padding:0 12px;")
        btn_ok.clicked.connect(dlg.accept)
        btn_cancel.clicked.connect(dlg.reject)
        btn_row.addStretch()
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_ok)
        
        lay.addWidget(lbl)
        lay.addWidget(edit)
        lay.addLayout(btn_row)
        
        edit.returnPressed.connect(dlg.accept)
        
        if dlg.exec() == QDialog.DialogCode.Accepted:
            new_title = edit.text().strip()
            if new_title:
                self._db.rename_recording(self._current_rec_id, new_title)
                self._current_rec["title"] = new_title
                self._lbl_title.setText(new_title)


# HistoryPage — main
class HistoryPage(QWidget):
    """
    Trang lịch sử. Đặt vào QStackedWidget của MainWindow,
    hiển thị khi user click NavItem "History".

    Layout:
        [left panel 280px fixed] | [right detail panel, stretch]

    Left panel:
        - Search bar (lọc theo tên recording)
        - Scroll area chứa danh sách RecordingCard
        - Số lượng recording

    Right panel:
        - DetailPanel (empty state → loaded state)

    Flow khi chọn recording:
        RecordingCard.selected -> _on_card_selected()
            -> DetailPanel.load(rec)
            -> RecordingCard tương ứng set_active(True)
    """

    def __init__(self, db: DatabaseService, theme_manager=None, parent=None):
        super().__init__(parent)
        self._db            = db
        self._theme         = theme_manager
        self._cards: dict[str, RecordingCard] = {}
        self._active_id: str | None = None
        self.setStyleSheet(f"background:{_p(self)['BG_BASE']};")
        self._build_ui()

    @property
    def db(self):
        return self._db

    @db.setter
    def db(self, value):
        """Khi main_window inject DB → truyền xuống DetailPanel luôn."""
        self._db = value
        if hasattr(self, '_detail'):
            self._detail._db = value

    def apply_theme(self):
        """Đổi theme: xóa layout cũ đúng cách rồi rebuild."""
        # Lưu state
        current_rec = getattr(self._detail, "_current_rec", None) if hasattr(self, "_detail") else None
        active_id   = getattr(self, "_active_id", None)
        search_text = self._search.text() if hasattr(self, "_search") else ""
        saved_db    = self._db  # giữ lại DB reference

        # Xóa tất cả widget con khỏi layout
        root_layout = self.layout()
        if root_layout:
            while root_layout.count():
                item = root_layout.takeAt(0)
                if item.widget():
                    item.widget().setParent(None)
            # Xóa chính layout để _build_ui có thể tạo layout mới
            from PyQt6.QtWidgets import QWidget
            tmp = QWidget()
            tmp.setLayout(root_layout)  # chuyển ownership layout sang tmp → self không còn layout

        # Reset state
        self._cards     = {}
        self._active_id = None

        # Rebuild UI với palette mới
        self._build_ui()

        # Inject lại DB vào detail panel
        if saved_db and hasattr(self, "_detail"):
            self._detail._db = saved_db

        # Restore search
        if search_text and hasattr(self, "_search"):
            self._search.setText(search_text)

        # Reload list
        if saved_db:
            self._db = saved_db
            self.refresh()
            if active_id and active_id in self._cards:
                self._active_id = active_id
                self._cards[active_id].set_active(True)
                if current_rec:
                    self._detail.load(current_rec)


    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_left())
        root.addWidget(_vline(self._theme))
        root.addWidget(self._build_right(), stretch=1)

    def _build_left(self) -> QWidget:
        self._left_panel = QWidget()
        w = self._left_panel
        w.setFixedWidth(280)
        w.setStyleSheet(f"background:{_p(self)['BG_SIDEBAR']}; border:none;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # Header
        self._left_hdr = QWidget()
        hdr = self._left_hdr
        hdr.setFixedHeight(56)
        hdr.setStyleSheet(
            f"background:{_p(self)['BG_SIDEBAR']}; border-bottom:1px solid {_p(self)['BORDER']};"
        )
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(16, 0, 16, 0)
        lbl_h = QLabel("HISTORY")
        lbl_h.setFont(QFont("JetBrains Mono", 10))
        lbl_h.setStyleSheet(
            f"color:{_p(self)['TEXT_PRI']}; letter-spacing:3px; background:transparent;"
        )
        self._lbl_count = QLabel("")
        self._lbl_count.setFont(QFont("JetBrains Mono", 8))
        self._lbl_count.setStyleSheet(
            f"color:{_p(self)['TEXT_SEC']}; background:transparent;"
        )
        btn_reload = QPushButton()
        btn_reload.setIcon(icons.get("reload", _p(self)['TEXT_SEC']))
        btn_reload.setIconSize(QSize(14, 14))
        btn_reload.setFixedSize(24, 24)
        btn_reload.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_reload.setToolTip("Tải lại danh sách")
        btn_reload.clicked.connect(self.refresh)
        btn_reload.setStyleSheet(f"""
            QPushButton {{
                background:transparent;
                border:none; border-radius:4px;
            }}
            QPushButton:hover {{ background:{_p(self)['BG_CARD2']}; }}
        """)
        hl.addWidget(lbl_h)
        hl.addStretch()
        hl.addWidget(self._lbl_count)
        hl.addSpacing(4)
        hl.addWidget(btn_reload)
        lay.addWidget(hdr)

        # Search bar
        self._search_wrap = QWidget()
        search_wrap = self._search_wrap
        search_wrap.setFixedHeight(48)
        search_wrap.setStyleSheet(
            f"background:{_p(self)['BG_SIDEBAR']}; border-bottom:1px solid {_p(self)['BORDER']};"
        )
        sl = QHBoxLayout(search_wrap)
        sl.setContentsMargins(12, 8, 12, 8)
        self._search = QLineEdit()
        self._search.setPlaceholderText("⌕  Search recordings…")
        self._search.setFont(QFont("JetBrains Mono", 9))
        self._search.setStyleSheet(f"""
            QLineEdit {{
                background:{_p(self)['BG_CARD']};
                color:{_p(self)['TEXT_PRI']};
                border:1px solid {_p(self)['BORDER2']};
                border-radius:6px;
                padding:4px 10px;
            }}
            QLineEdit:focus {{ border-color:{_p(self)['ACCENT']}; }}
        """)
        self._search.textChanged.connect(self._on_search)
        sl.addWidget(self._search)
        lay.addWidget(search_wrap)

        # Scrollable card list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(f"""
            QScrollArea {{ background:{_p(self)['BG_SIDEBAR']}; border:none; }}
            QScrollBar:vertical {{
                background:{_p(self)['BG_SIDEBAR']}; width:4px; border-radius:2px;
            }}
            QScrollBar::handle:vertical {{
                background:{_p(self)['BORDER2']}; border-radius:2px; min-height:20px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
        """)

        self._list_widget = QWidget()
        self._list_widget.setStyleSheet(f"background:{_p(self)['BG_SIDEBAR']};")
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setContentsMargins(0, 4, 0, 4)
        self._list_layout.setSpacing(0)
        self._list_layout.addStretch()   # giữ cards về phía trên

        scroll.setWidget(self._list_widget)
        lay.addWidget(scroll, stretch=1)
        return w

    def _build_right(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # Page title bar
        title_bar = QWidget()
        title_bar.setFixedHeight(56)
        title_bar.setStyleSheet(
            f"background:{_p(self)['BG_BASE']}; border-bottom:1px solid {_p(self)['BORDER']};"
        )
        tbl = QHBoxLayout(title_bar)
        tbl.setContentsMargins(20, 0, 20, 0)
        pt = QLabel("Recording detail")
        pt.setFont(QFont("JetBrains Mono", 13))
        pt.setStyleSheet(
            f"color:{_p(self)['TEXT_PRI']}; background:transparent; letter-spacing:1px;"
        )
        tbl.addWidget(pt)
        tbl.addStretch()
        lay.addWidget(title_bar)

        # DetailPanel (chiếm phần còn lại)
        self._detail = DetailPanel(db=None, theme_manager=self._theme)
        self._detail.deleted.connect(self._on_deleted)
        lay.addWidget(self._detail, stretch=1)
        return w

    # ── Public
    def refresh(self):
        """
        Tải lại toàn bộ danh sách recording từ DB.
        Gọi mỗi khi tab History được mở để có data mới nhất.

        Quy trình:
        1. Xóa tất cả RecordingCard cũ
        2. Query DB get_all_recordings() (đã sort theo ngày giảm dần)
        3. Tạo lại RecordingCard cho từng recording
        4. Cập nhật label đếm
        """
        # Xóa cards cũ (trừ stretch item cuối)
        while self._list_layout.count() > 1:
            item = self._list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._cards.clear()
        self._active_id = None

        recs = self._db.get_all_recordings()
        for rec in recs:
            self._add_card(rec)

        n = len(recs)
        self._lbl_count.setText(f"{n} recording{'s' if n != 1 else ''}")

    def _add_card(self, rec: dict):
        """Tạo RecordingCard và chèn vào list (trước stretch)."""
        card = RecordingCard(rec, theme_manager=self._theme)
        card.selected.connect(lambda rid, r=rec: self._on_card_selected(rid, r))
        # Chèn trước stretch item (index = count-1)
        insert_at = max(0, self._list_layout.count() - 1)
        self._list_layout.insertWidget(insert_at, card)
        self._cards[rec["id"]] = card

    # ── Event handlers 
    def _on_card_selected(self, rec_id: str, rec: dict):
        """
        User click vào 1 RecordingCard.
        -> Deactivate card cũ, activate card mới, load detail.
        """
        if self._active_id and self._active_id in self._cards:
            self._cards[self._active_id].set_active(False)

        self._active_id = rec_id
        self._cards[rec_id].set_active(True)
        self._detail.load(rec)

    def _on_search(self, query: str):
        """
        Lọc danh sách card theo tên recording.
        Ẩn card không khớp, hiện card khớp.
        So sánh case-insensitive.
        """
        q = query.strip().lower()
        for card in self._cards.values():
            # Lấy title từ label đầu tiên trong card
            # Cách đơn giản: tìm QLabel có font size 9 (title label)
            title_lbl = card.findChildren(QLabel)
            title = ""
            for lbl in title_lbl:
                if lbl.font().pointSize() == 9:
                    title = lbl.text().lower()
                    break
            card.setVisible(not q or q in title)

    def _on_deleted(self, rec_id: str):
        """
        DetailPanel yêu cầu xóa recording.
        1. Xóa khỏi DB
        2. Xóa RecordingCard khỏi list
        3. Clear DetailPanel
        4. Cập nhật label đếm
        """
        if rec_id in self._cards:
            card = self._cards.pop(rec_id)
            card.deleteLater()

        self._db.delete_recording(rec_id)
        print(f"[HISTORY] Deleted recording: {rec_id}")

        # Clear detail panel
        self._detail.load(None)

        # Chọn card đầu tiên còn lại nếu có
        if self._cards:
            first_id = next(iter(self._cards))
            first_rec = self._db.get_recording(first_id)
            if first_rec:
                self._cards[first_id].set_active(True)
                self._detail.load(first_rec)

        # Cập nhật count
        n = len(self._cards)
        self._lbl_count.setText(f"{n} recording{'s' if n != 1 else ''}")


# ── Helpers ────────────────────────────────────────────────
def _fmt(sec: float) -> str: # format giây thành MM:SS
    s = int(max(0, sec))
    m = s // 60
    return f"{m:02d}:{s % 60:02d}"

def _divider(theme=None) -> QFrame:  # line ngang
    d = QFrame()
    d.setFrameShape(QFrame.Shape.HLine)
    d.setFixedHeight(1)
    p = theme.palette if theme else _THEME_FALLBACK
    d.setStyleSheet(f"background:{p['BORDER']}; border:none;")
    return d

def _vline(theme=None) -> QFrame: # line dọc phân chia left/right
    d = QFrame()
    d.setFrameShape(QFrame.Shape.VLine)
    d.setFixedWidth(1)
    p = theme.palette if theme else _THEME_FALLBACK
    d.setStyleSheet(f"background:{p['BORDER']}; border:none;")
    return d