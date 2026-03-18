"""
database_service.py — Thêm update_segment_speakers() cho post-save diarization
"""

import time
import os
from datetime import datetime
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from db.models import init_db, Recording, Speaker, Segment, Word, DB_PATH


class DatabaseService:

    def __init__(self, db_path: str = DB_PATH):
        self._engine         = init_db(db_path)
        self._current_rec_id = None
        self._segment_index  = 0
        self._start_ts       = None
        self._speaker_map    = {}
        print(f"[DB] Connected: {db_path}")

    # ──────────────────────────────────────────────────────
    # Recording lifecycle
    # ──────────────────────────────────────────────────────
    def start_recording(self, title: str = None, audio_path: str = None) -> str:
        self._start_ts      = time.time()
        self._segment_index = 0
        self._speaker_map   = {}

        with Session(self._engine) as session:
            rec = Recording(
                title      = title or f"Recording {datetime.now().strftime('%d/%m/%Y %H:%M')}",
                status     = "recording",
                audio_path = audio_path,
            )
            session.add(rec)
            session.commit()
            self._current_rec_id = rec.id
            print(f"[DB] Started: {rec.id[:8]} audio={audio_path}")
            return rec.id

    def stop_recording(self):
        if not self._current_rec_id:
            return
        duration = time.time() - self._start_ts if self._start_ts else 0

        with Session(self._engine) as session:
            rec = session.get(Recording, self._current_rec_id)
            if rec:
                word_count_result = session.execute(
                    select(func.sum(
                        func.length(Segment.text_en)
                        - func.length(func.replace(Segment.text_en, " ", ""))
                        + 1
                    )).where(
                        Segment.recording_id == self._current_rec_id,
                        func.length(func.trim(Segment.text_en)) > 0,
                    )
                ).scalar()

                rec.duration_seconds = round(duration, 2)
                rec.status           = "done"
                rec.speaker_count    = len(self._speaker_map)
                rec.word_count       = int(word_count_result or 0)
                session.commit()
                print(f"[DB] Stopped: {rec.id[:8]} ({duration:.1f}s, {rec.word_count} words)")

        self._current_rec_id = None
        self._start_ts       = None

    def delete_recording(self, recording_id: str):
        with Session(self._engine) as session:
            rec = session.get(Recording, recording_id)
            if rec:
                audio_path = rec.audio_path
                session.delete(rec)
                session.commit()
                print(f"[DB] Deleted: {recording_id[:8]}")
                if audio_path and os.path.exists(audio_path):
                    try:
                        os.remove(audio_path)
                    except Exception as e:
                        print(f"[DB] Could not delete audio: {e}")

    # ──────────────────────────────────────────────────────
    # Post-save diarization: re-assign speaker labels
    # ──────────────────────────────────────────────────────
    def update_segment_speakers(
        self,
        recording_id: str,
        speaker_assignments: dict[int, int],
    ):
        """
        Cập nhật speaker_id cho các segment sau khi diarization xong.

        Args:
            recording_id:        UUID của recording
            speaker_assignments: {segment_index: new_speaker_index}
                                 Output từ SpeakerDiarizer.assign_speakers()
        """
        if not speaker_assignments:
            print("[DB] update_segment_speakers: nothing to update")
            return

        with Session(self._engine) as session:
            # Reset speaker_map để tạo lại từ đầu
            new_speaker_map: dict[int, str] = {}   # speaker_index → speaker UUID

            updated = 0
            for seg_idx, new_spk_idx in speaker_assignments.items():
                seg = session.query(Segment).filter(
                    Segment.recording_id  == recording_id,
                    Segment.segment_index == seg_idx,
                ).first()

                if seg is None:
                    continue

                # Tạo hoặc lấy Speaker row cho new_spk_idx
                if new_spk_idx not in new_speaker_map:
                    spk = session.query(Speaker).filter(
                        Speaker.recording_id  == recording_id,
                        Speaker.speaker_index == new_spk_idx,
                    ).first()

                    if spk is None:
                        spk = Speaker(
                            recording_id  = recording_id,
                            speaker_index = new_spk_idx,
                        )
                        session.add(spk)
                        session.flush()

                    new_speaker_map[new_spk_idx] = spk.id

                seg.speaker_id = new_speaker_map[new_spk_idx]
                updated += 1

            # Cập nhật speaker_count trên Recording
            rec = session.get(Recording, recording_id)
            if rec:
                rec.speaker_count = len(new_speaker_map)

            session.commit()
            print(f"[DB] Updated {updated} segments → "
                  f"{len(new_speaker_map)} speakers for {recording_id[:8]}")

    # ──────────────────────────────────────────────────────
    # Speaker
    # ──────────────────────────────────────────────────────
    def _get_or_create_speaker(self, session: Session, speaker_index: int) -> str:
        if speaker_index in self._speaker_map:
            return self._speaker_map[speaker_index]
        spk = Speaker(
            recording_id  = self._current_rec_id,
            speaker_index = speaker_index,
        )
        session.add(spk)
        session.flush()
        self._speaker_map[speaker_index] = spk.id
        return spk.id

    # ──────────────────────────────────────────────────────
    # Segment + Words
    # ──────────────────────────────────────────────────────
    def save_segment(
        self,
        text_en:       str,
        text_vi:       str,
        speaker_index: int,
        start_time:    float = 0.0,
        end_time:      float = 0.0,
        confidence:    float = None,
        words:         list[dict] = None,
    ):
        if not self._current_rec_id:
            return

        with Session(self._engine) as session:
            speaker_uuid = self._get_or_create_speaker(session, speaker_index)

            seg = Segment(
                recording_id  = self._current_rec_id,
                speaker_id    = speaker_uuid,
                segment_index = self._segment_index,
                text_en       = text_en.strip(),
                text_vi       = text_vi.strip(),
                start_time    = round(start_time, 3),
                end_time      = round(end_time, 3),
                confidence    = confidence,
            )
            session.add(seg)
            session.flush()

            if words:
                for i, w in enumerate(words):
                    session.add(Word(
                        segment_id   = seg.id,
                        recording_id = self._current_rec_id,
                        word_index   = i,
                        word_text    = w.get("word", "").strip(),
                        start_time   = round(w.get("start", 0.0), 3),
                        end_time     = round(w.get("end", 0.0), 3),
                        confidence   = w.get("probability", None),
                    ))

            session.commit()
            self._segment_index += 1
            print(f"[DB] Saved segment #{self._segment_index} "
                  f"Speaker {speaker_index}: {text_en[:40]}")

    def get_current_segments(self) -> list[dict]:
        if not self._current_rec_id:
            return []
        return self.get_recording_segments(self._current_rec_id)

    # ──────────────────────────────────────────────────────
    # Query
    # ──────────────────────────────────────────────────────
    def get_all_recordings(self) -> list[dict]:
        with Session(self._engine) as session:
            recs = session.query(Recording)\
                          .order_by(Recording.recorded_at.desc()).all()
            return [
                {
                    "id":               r.id,
                    "title":            r.title,
                    "recorded_at":      r.recorded_at,
                    "duration_seconds": r.duration_seconds,
                    "speaker_count":    r.speaker_count,
                    "word_count":       r.word_count,
                    "status":           r.status,
                    "audio_path":       r.audio_path,
                }
                for r in recs
            ]

    def get_recording_segments(self, recording_id: str) -> list[dict]:
        with Session(self._engine) as session:
            segs = session.query(Segment)\
                          .filter(Segment.recording_id == recording_id)\
                          .order_by(Segment.segment_index).all()
            return [
                {
                    "segment_index": s.segment_index,
                    "speaker_index": s.speaker.speaker_index if s.speaker else 0,
                    "speaker_label": s.speaker.display_name if s.speaker else "Unknown",
                    "text_en":       s.text_en,
                    "text_vi":       s.text_vi,
                    "start_time":    s.start_time,
                    "end_time":      s.end_time,
                    "confidence":    s.confidence,
                    "words": [
                        {
                            "word":       w.word_text,
                            "start_time": w.start_time,
                            "end_time":   w.end_time,
                            "confidence": w.confidence,
                        }
                        for w in s.words
                    ],
                }
                for s in segs
            ]

    def get_words_at_time(self, recording_id: str, timestamp: float) -> dict | None:
        with Session(self._engine) as session:
            word = session.query(Word)\
                          .filter(
                              Word.recording_id == recording_id,
                              Word.start_time   <= timestamp,
                              Word.end_time     >= timestamp,
                          ).first()
            if word:
                return {
                    "word":       word.word_text,
                    "start_time": word.start_time,
                    "end_time":   word.end_time,
                    "segment_id": word.segment_id,
                }
            return None

    def search_segments(self, query: str) -> list[dict]:
        with Session(self._engine) as session:
            segs = session.query(Segment)\
                          .filter(
                              Segment.text_en.ilike(f"%{query}%") |
                              Segment.text_vi.ilike(f"%{query}%")
                          )\
                          .order_by(Segment.created_at.desc())\
                          .limit(50).all()
            return [
                {
                    "recording_id":  s.recording_id,
                    "speaker_label": s.speaker.display_name if s.speaker else "Unknown",
                    "text_en":       s.text_en,
                    "text_vi":       s.text_vi,
                    "start_time":    s.start_time,
                    "created_at":    s.created_at,
                }
                for s in segs
            ]

    def rename_speaker(self, recording_id: str, speaker_index: int, new_label: str):
        with Session(self._engine) as session:
            spk = session.query(Speaker)\
                         .filter(
                             Speaker.recording_id  == recording_id,
                             Speaker.speaker_index == speaker_index,
                         ).first()
            if spk:
                spk.label = new_label
                session.commit()
                print(f"[DB] Renamed Speaker {speaker_index} → {new_label}")