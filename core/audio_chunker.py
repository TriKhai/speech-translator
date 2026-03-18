class AudioChunker:
    """
    Buffer PCM bytes và emit chunk khi đủ kích thước.

    Chunk: 3s, overlap: 0.5s
    """

    BYTES_PER_SECOND = 32000  # 16000 Hz * 2 bytes (s16le)

    CHUNK_SIZE = BYTES_PER_SECOND * 3       # 3 giây
    STEP_SIZE  = BYTES_PER_SECOND * 3 \
               - BYTES_PER_SECOND // 2      # tiến 2.5s, overlap 0.5s

    def __init__(self):
        self._buffer = bytearray()

    def add(self, data: bytes) -> bytes | None:
        """
        Thêm data vào buffer.
        Trả về chunk nếu đủ kích thước, None nếu chưa đủ.
        """
        self._buffer.extend(data)

        if len(self._buffer) >= self.CHUNK_SIZE:
            chunk = bytes(self._buffer[:self.CHUNK_SIZE])
            del self._buffer[:self.STEP_SIZE]
            return chunk

        return None

    def flush(self) -> bytes | None:
        """Trả về phần còn lại khi stop (nếu đủ dài)."""
        min_useful = self.BYTES_PER_SECOND // 2  # ít nhất 0.5s
        if len(self._buffer) < min_useful:
            return None
        chunk = bytes(self._buffer)
        self._buffer.clear()
        return chunk