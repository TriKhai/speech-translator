import uuid
import os
from datetime import datetime, timezone
from sqlalchemy import (
    create_engine, Column, String, Float,
    Integer, DateTime, ForeignKey, Text
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "database.db")


def _uuid() -> str:
    return str(uuid.uuid4())

def _now() -> datetime:
    return datetime.now(timezone.utc)


class Recording(Base):
    __tablename__ = "recordings"

    id               = Column(String,  primary_key=True, default=_uuid)
    title            = Column(String,  nullable=True)
    duration_seconds = Column(Float,   nullable=True)
    recorded_at      = Column(DateTime, default=_now)
    language         = Column(String,  default="en")
    status           = Column(String,  default="recording")  # recording | done
    speaker_count    = Column(Integer, default=0)
    word_count       = Column(Integer, default=0)
    audio_path       = Column(String,  nullable=True)  # path tới file WAV

    segments = relationship("Segment", back_populates="recording",
                            cascade="all, delete-orphan",
                            order_by="Segment.segment_index")
    speakers = relationship("Speaker", back_populates="recording",
                            cascade="all, delete-orphan")


class Speaker(Base):
    __tablename__ = "speakers"

    id            = Column(String,  primary_key=True, default=_uuid)
    recording_id  = Column(String,  ForeignKey("recordings.id"), nullable=False)
    speaker_index = Column(Integer, nullable=False)
    label         = Column(String,  nullable=True)
    created_at    = Column(DateTime, default=_now)

    recording = relationship("Recording", back_populates="speakers")
    segments  = relationship("Segment",   back_populates="speaker")

    @property
    def display_name(self) -> str:
        return self.label or f"Speaker {self.speaker_index}"


class Segment(Base):
    __tablename__ = "segments"

    id            = Column(String,  primary_key=True, default=_uuid)
    recording_id  = Column(String,  ForeignKey("recordings.id"), nullable=False)
    speaker_id    = Column(String,  ForeignKey("speakers.id"),   nullable=True)
    segment_index = Column(Integer, nullable=False)
    text_en       = Column(Text,    default="")
    text_vi       = Column(Text,    default="")
    start_time    = Column(Float,   default=0.0)
    end_time      = Column(Float,   default=0.0)
    confidence    = Column(Float,   nullable=True)
    created_at    = Column(DateTime, default=_now)

    recording = relationship("Recording", back_populates="segments")
    speaker   = relationship("Speaker",   back_populates="segments")
    words     = relationship("Word",      back_populates="segment",
                             cascade="all, delete-orphan",
                             order_by="Word.word_index")


class Word(Base):
    __tablename__ = "words"

    id           = Column(String,  primary_key=True, default=_uuid)
    segment_id   = Column(String,  ForeignKey("segments.id"),   nullable=False)
    recording_id = Column(String,  ForeignKey("recordings.id"), nullable=False)
    word_index   = Column(Integer, nullable=False)
    word_text    = Column(String,  nullable=False)
    start_time   = Column(Float,   nullable=False)
    end_time     = Column(Float,   nullable=False)
    confidence   = Column(Float,   nullable=True)

    segment   = relationship("Segment",   back_populates="words")
    recording = relationship("Recording")


def get_engine(db_path: str = DB_PATH):
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    return create_engine(f"sqlite:///{os.path.abspath(db_path)}", echo=False)


def init_db(db_path: str = DB_PATH):
    engine = get_engine(db_path)
    Base.metadata.create_all(engine)
    return engine