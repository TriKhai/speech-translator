import os
from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QScrollArea,
    QPushButton, QLabel, QFrame, QStackedWidget,
    QLineEdit, QMessageBox, QInputDialog,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui  import QFont, QColor

from db.database_service import DatabaseService
from ui.widgets.audio_player        import AudioPlayer
from ui.widgets.clickable_transcript import ClickableTranscript
from ui.constants import (
    BG_BASE, BG_SIDEBAR, BG_CARD, BG_CARD2,
    BORDER, BORDER2,
    TEXT_PRI, TEXT_SEC, TEXT_DIM,
    ACCENT, ACCENT_HO, DANGER, DANGER_HO,
    SPEAKER_COLORS,
)


class RecordingCard(QFrame):
    """
    Hiển thị các bản ghi âm (namwf bên trái)
    """
    selected = pyqtSignal(str)   # recording_id (signal khi user click vào card)

    def __init__(self, rec: dict, parent=None):
        super().__init__(parent)
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
        icon = QLabel("◎")
        icon.setFixedWidth(24)
        icon.setFont(QFont("JetBrains Mono", 14))
        icon.setStyleSheet(f"color:{ACCENT}; background:transparent; border:none;")
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
        lbl_title.setStyleSheet(f"color:{TEXT_PRI}; background:transparent;")

        date_str = ""
        if rec.get("recorded_at"):
            try:
                dt = rec["recorded_at"]
                if isinstance(dt, str):
                    dt = datetime.fromisoformat(dt)
                date_str = dt.strftime("%d/%m/%Y %H:%M")
            except Exception:
                pass
        lbl_date = QLabel(date_str)
        lbl_date.setFont(QFont("JetBrains Mono", 8))
        lbl_date.setStyleSheet(f"color:{TEXT_DIM}; background:transparent;")

        row1.addWidget(lbl_title)
        row1.addStretch()
        row1.addWidget(lbl_date)

        # Row 2: meta info
        dur  = rec.get("duration_seconds") or 0
        spks = rec.get("speaker_count") or 0
        wds  = rec.get("word_count") or 0
        meta = f"{_fmt(dur)}  •  {spks} speaker{'s' if spks != 1 else ''}  •  {wds} words"
        lbl_meta = QLabel(meta)
        lbl_meta.setFont(QFont("JetBrains Mono", 8))
        lbl_meta.setStyleSheet(f"color:{TEXT_SEC}; background:transparent;")

        tl.addLayout(row1)
        tl.addWidget(lbl_meta)
        lay.addWidget(txt, stretch=1)

    def set_active(self, active: bool):
        self._active = active
        self._set_style(active)

    def _set_style(self, active: bool):
        border_color = ACCENT if active else BORDER
        bg           = BG_CARD2 if active else "transparent"
        self.setStyleSheet(f"""
            RecordingCard {{
                background: {bg};
                border: none;
                border-left: 2px solid {border_color};
            }}
            RecordingCard:hover {{
                background: {BG_CARD2};
                border-left: 2px solid {ACCENT if active else BORDER2};
            }}
        """)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit(self._rec_id)


class DetailPanel(QWidget):
    """
    Hiển thị chi tiết 1 recording đã chọn.

    State:
        _current_rec_id  — ID recording đang xem
        _current_segments — list[dict] từ DB

    Khi load(rec_data, segments):
        1. Cập nhật header (title, meta)
        2. Load AudioPlayer với wav_path
        3. Xóa và điền lại 2 ClickableTranscript (EN + VI)
           với start_time cho từng segment (có thể click để seek)
        4. Kết nối AudioPlayer.seek_requested <= ClickableTranscript.seek_requested
        5. Bật QTimer 250ms để highlight từ đang phát
    """
    deleted = pyqtSignal(str)   # recording_id — yêu cầu xóa

    def __init__(self, db: DatabaseService, parent=None):
        super().__init__(parent)
        self._db              = db
        self._current_rec_id  = None
        self._current_rec     = None
        self.setStyleSheet(f"background:{BG_BASE};")

        self._build_ui()

        # Timer highlight từ đang phát (mỗi 250ms)
        self._hl_timer = QTimer(self)
        self._hl_timer.setInterval(250)
        self._hl_timer.timeout.connect(self._on_hl_tick)

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
        icon.setStyleSheet(f"color:{TEXT_DIM}; background:transparent;")

        msg = QLabel("Chọn một bản ghi âm để xem chi tiết")
        msg.setFont(QFont("JetBrains Mono", 10))
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg.setStyleSheet(f"color:{TEXT_SEC}; background:transparent;")

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
        hdr_card = QFrame()
        hdr_card.setStyleSheet(f"""
            QFrame {{
                background:{BG_CARD};
                border:1px solid {BORDER};
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
        self._lbl_title.setStyleSheet(f"color:{TEXT_PRI}; background:transparent;")
        title_row.addWidget(self._lbl_title)
        title_row.addStretch()

        self._btn_delete = self._mk_danger_btn("✕  DELETE")
        self._btn_delete.clicked.connect(self._on_delete)
        title_row.addWidget(self._btn_delete)

        # Meta row
        self._lbl_meta = QLabel("—")
        self._lbl_meta.setFont(QFont("JetBrains Mono", 8))
        self._lbl_meta.setStyleSheet(f"color:{TEXT_SEC}; background:transparent;")

        hdr_lay.addLayout(title_row)
        hdr_lay.addWidget(self._lbl_meta)
        lay.addWidget(hdr_card)

        # ── AudioPlayer
        player_card = QFrame()
        player_card.setStyleSheet(f"""
            QFrame {{
                background:{BG_CARD};
                border:1px solid {BORDER};
                border-radius:10px;
            }}
        """)
        pc_lay = QVBoxLayout(player_card)
        pc_lay.setContentsMargins(16, 12, 16, 12)
        pc_lay.setSpacing(8)

        ph = QHBoxLayout()
        ph_icon = QLabel("◈")
        ph_icon.setFont(QFont("JetBrains Mono", 10))
        ph_icon.setStyleSheet(f"color:{ACCENT}; background:transparent;")
        ph_title = QLabel("Playback")
        ph_title.setFont(QFont("JetBrains Mono", 10))
        ph_title.setStyleSheet(f"color:{TEXT_PRI}; background:transparent;")
        ph.addWidget(ph_icon)
        ph.addSpacing(6)
        ph.addWidget(ph_title)
        ph.addStretch()
        pc_lay.addLayout(ph)
        pc_lay.addWidget(_divider())

        self._player = AudioPlayer()
        # AudioPlayer trong history không cần nút "New Recording"
        self._player._btn_new.hide()
        pc_lay.addWidget(self._player)
        lay.addWidget(player_card)

        # ── Toggle pills EN / VI 
        pill_row = QHBoxLayout()
        self._pill_en = self._mk_pill("EN", active=True)
        self._pill_vi = self._mk_pill("VI", active=True)
        self._pill_en.clicked.connect(
            lambda: self._card_en.setVisible(self._pill_en.isChecked())
        )
        self._pill_vi.clicked.connect(
            lambda: self._card_vi.setVisible(self._pill_vi.isChecked())
        )
        pill_row.addWidget(self._pill_en)
        pill_row.addSpacing(6)
        pill_row.addWidget(self._pill_vi)
        pill_row.addStretch()
        lay.addLayout(pill_row)

        # ── Transcript panels 
        trans_row = QHBoxLayout()
        trans_row.setSpacing(12)

        self._card_en, self._trans_en = self._mk_transcript_card("TIẾNG ANH",    "#60A5FA")
        self._card_vi, self._trans_vi = self._mk_transcript_card("TIẾNG VIỆT", "#34D399")

        # Kết nối seek: click từ trong transcript → AudioPlayer seek
        self._trans_en.seek_requested.connect(self._on_seek)
        self._trans_vi.seek_requested.connect(self._on_seek)

        trans_row.addWidget(self._card_en)
        trans_row.addWidget(self._card_vi)
        lay.addLayout(trans_row, stretch=1)
        return w

    def _mk_transcript_card(self, title: str, color: str):
        """Trả về (card_frame, ClickableTranscript)."""
        card = QFrame()
        card.setStyleSheet(f"""
            QFrame {{
                background:{BG_CARD};
                border:1px solid {BORDER};
                border-radius:10px;
            }}
        """)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(8)

        # Header với speaker labels (được điền động khi load)
        hdr = QHBoxLayout()
        lbl = QLabel(title)
        lbl.setFont(QFont("JetBrains Mono", 9))
        lbl.setStyleSheet(f"color:{color}; background:transparent; letter-spacing:2px;")
        hdr.addWidget(lbl)
        hdr.addStretch()
        cl.addLayout(hdr)
        cl.addWidget(_divider())

        trans = ClickableTranscript()
        trans.set_seek_enabled(True)
        cl.addWidget(trans, stretch=1)
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
                    background: {"rgba(59,110,245,0.15)" if on else "transparent"};
                    color: {"#93B8FC" if on else TEXT_SEC};
                    border: 1px solid {"rgba(59,110,245,0.4)" if on else BORDER2};
                    border-radius: 13px; padding: 0 12px; letter-spacing: 1px;
                }}
                QPushButton:hover {{ background:rgba(59,110,245,0.1); color:#7AA8F8; }}
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
                background:transparent; color:{DANGER};
                border:1px solid rgba(224,82,82,0.35); border-radius:5px;
                padding:0 12px; letter-spacing:1px;
            }}
            QPushButton:hover {{ background:rgba(224,82,82,0.12); border-color:{DANGER}; }}
        """)
        return b

    # ── Public API 
    def load(self, rec: dict):
        """
        Load recording vào detail panel.

        Quy trình:
        1. Lưu current_rec_id để timer highlight dùng
        2. Cập nhật header title + meta
        3. Nếu có audio file → load AudioPlayer
        4. Query DB lấy segments
        5. Điền ClickableTranscript EN + VI
        6. Bật seek mode + bật timer highlight
        7. Hiện loaded panel (stack index 1)
        """
        self._hl_timer.stop()
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
        self._lbl_meta.setText(
            f"{date_str}   •   {_fmt(dur)}   •   {spks} speakers   •   {wds} words"
        )

        # AudioPlayer
        audio_path = rec.get("audio_path")
        if audio_path and os.path.exists(audio_path):
            self._player.load(audio_path)
            self._player.setEnabled(True)
        else:
            self._player.setEnabled(False)

        # Transcript segments từ DB
        segments = self._db.get_recording_segments(rec["id"])
        self._fill_transcripts(segments)

        # Bật highlight timer (chỉ khi có audio)
        if audio_path and os.path.exists(audio_path):
            self._hl_timer.start()

        self._stack.setCurrentIndex(1)

    def _fill_transcripts(self, segments: list[dict]):
        """
        Xóa transcript cũ rồi điền lại từ segments.

        Mỗi segment có:
            speaker_index, text_en, text_vi, start_time

        Gọi ClickableTranscript.insert_segment() cho từng segment
        -> widget tự xử lý grouping speaker + gắn timestamp vào block.
        """
        self._trans_en.clear()
        self._trans_vi.clear()

        for seg in segments:
            spk_idx = seg.get("speaker_index", 0)
            color   = SPEAKER_COLORS[(spk_idx - 1) % len(SPEAKER_COLORS)]
            ts      = seg.get("start_time", 0.0)

            self._trans_en.insert_segment(
                text=seg.get("text_en", ""),
                speaker_id=spk_idx,
                color=color,
                ts=ts,
            )
            self._trans_vi.insert_segment(
                text=seg.get("text_vi", ""),
                speaker_id=spk_idx,
                color=color,
                ts=ts,
            )

    def _on_seek(self, timestamp: float):
        """
        User click vào 1 từ trong transcript → seek đến đúng giây đó.
        ClickableTranscript emit seconds (float), AudioPlayer._on_seek_ratio
        nhận ratio 0-1 nên cần convert.
        """
        if not self._player._duration:
            return
        print(f"[SEEK] timestamp={timestamp:.2f}s  duration={self._player._duration:.2f}s")
        ratio = max(0.0, min(1.0, timestamp / self._player._duration))
        self._player._on_seek_ratio(ratio)

    def _on_hl_tick(self):
        """
        Chạy mỗi 250ms: lấy vị trí hiện tại của player
        → highlight block transcript tương ứng trong cả 2 panel.
        """
        pos = self._player._position
        self._trans_en.highlight_at(pos)
        self._trans_vi.highlight_at(pos)

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
            self._hl_timer.stop()
            self._player.stop()
            self._stack.setCurrentIndex(0)
            self.deleted.emit(self._current_rec_id)


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

    def __init__(self, db: DatabaseService, parent=None):
        super().__init__(parent)
        self._db            = db
        self._cards: dict[str, RecordingCard] = {}
        self._active_id: str | None = None
        self.setStyleSheet(f"background:{BG_BASE};")
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

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_left())
        root.addWidget(_vline())
        root.addWidget(self._build_right(), stretch=1)

    def _build_left(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(280)
        w.setStyleSheet(f"background:{BG_SIDEBAR}; border:none;")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # Header
        hdr = QWidget()
        hdr.setFixedHeight(56)
        hdr.setStyleSheet(
            f"background:{BG_SIDEBAR}; border-bottom:1px solid {BORDER};"
        )
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(16, 0, 16, 0)
        lbl_h = QLabel("HISTORY")
        lbl_h.setFont(QFont("JetBrains Mono", 10))
        lbl_h.setStyleSheet(
            f"color:{TEXT_PRI}; letter-spacing:3px; background:transparent;"
        )
        self._lbl_count = QLabel("")
        self._lbl_count.setFont(QFont("JetBrains Mono", 8))
        self._lbl_count.setStyleSheet(
            f"color:{TEXT_SEC}; background:transparent;"
        )
        hl.addWidget(lbl_h)
        hl.addStretch()
        hl.addWidget(self._lbl_count)
        lay.addWidget(hdr)

        # Search bar
        search_wrap = QWidget()
        search_wrap.setFixedHeight(48)
        search_wrap.setStyleSheet(
            f"background:{BG_SIDEBAR}; border-bottom:1px solid {BORDER};"
        )
        sl = QHBoxLayout(search_wrap)
        sl.setContentsMargins(12, 8, 12, 8)
        self._search = QLineEdit()
        self._search.setPlaceholderText("⌕  Search recordings…")
        self._search.setFont(QFont("JetBrains Mono", 9))
        self._search.setStyleSheet(f"""
            QLineEdit {{
                background:{BG_CARD};
                color:{TEXT_PRI};
                border:1px solid {BORDER2};
                border-radius:6px;
                padding:4px 10px;
            }}
            QLineEdit:focus {{ border-color:{ACCENT}; }}
        """)
        self._search.textChanged.connect(self._on_search)
        sl.addWidget(self._search)
        lay.addWidget(search_wrap)

        # Scrollable card list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(f"""
            QScrollArea {{ background:{BG_SIDEBAR}; border:none; }}
            QScrollBar:vertical {{
                background:{BG_SIDEBAR}; width:4px; border-radius:2px;
            }}
            QScrollBar::handle:vertical {{
                background:{BORDER2}; border-radius:2px; min-height:20px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
        """)

        self._list_widget = QWidget()
        self._list_widget.setStyleSheet(f"background:{BG_SIDEBAR};")
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
            f"background:{BG_BASE}; border-bottom:1px solid {BORDER};"
        )
        tbl = QHBoxLayout(title_bar)
        tbl.setContentsMargins(20, 0, 20, 0)
        pt = QLabel("Recording detail")
        pt.setFont(QFont("JetBrains Mono", 13))
        pt.setStyleSheet(
            f"color:{TEXT_PRI}; background:transparent; letter-spacing:1px;"
        )
        tbl.addWidget(pt)
        tbl.addStretch()
        lay.addWidget(title_bar)

        # DetailPanel (chiếm phần còn lại)
        self._detail = DetailPanel(db=None)   # db injected later via db property
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
        card = RecordingCard(rec)
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
        1. Xóa khỏi DB (và xóa file WAV nếu có)
        2. Xóa RecordingCard khỏi list
        3. Cập nhật label đếm
        """
        # Lấy audio path trước khi xóa
        # (không có method delete trong DB hiện tại → cần thêm)
        # Tạm thời chỉ xóa card khỏi UI + ghi log
        if rec_id in self._cards:
            card = self._cards.pop(rec_id)
            card.deleteLater()

        self._db.delete_recording(rec_id)
        print(f"[HISTORY] Deleted recording: {rec_id}")

        # Cập nhật count
        n = len(self._cards)
        self._lbl_count.setText(f"{n} recording{'s' if n != 1 else ''}")


# ── Helpers ────────────────────────────────────────────────
def _fmt(sec: float) -> str: # format giây thành MM:SS
    s = int(max(0, sec))
    m = s // 60
    return f"{m:02d}:{s % 60:02d}"

def _divider() -> QFrame:  # line ngang
    d = QFrame()
    d.setFrameShape(QFrame.Shape.HLine)
    d.setFixedHeight(1)
    d.setStyleSheet(f"background:{BORDER}; border:none;")
    return d

def _vline() -> QFrame: # line dọc phân chia left/right
    d = QFrame()
    d.setFrameShape(QFrame.Shape.VLine)
    d.setFixedWidth(1)
    d.setStyleSheet(f"background:{BORDER}; border:none;")
    return d