"""Read recordings only; templates and playback plans are not recording inputs."""

from datetime import datetime
from pathlib import Path
import threading

from .storage import read_json, write_json


VIDEO_EXTENSIONS = {".mp4", ".avi", ".mkv", ".webm"}


class RecordingVisibility:
    """Durable list removal without touching source files, DB rows or plans."""

    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()

    def hidden(self):
        with self.lock:
            try:
                value = read_json(self.path)
            except FileNotFoundError:
                return set()
            if not isinstance(value, dict) or not isinstance(value.get("hidden"), list) or any(not isinstance(item, str) for item in value["hidden"]):
                raise ValueError("녹화 목록 삭제 기록이 손상되었습니다. 원본 파일은 보존했습니다.")
            return set(value["hidden"])

    def set_hidden(self, path, hidden):
        with self.lock:
            key = str(Path(path).resolve()).casefold()
            values = self.hidden()
            if hidden:
                values.add(key)
            else:
                values.discard(key)
            write_json(self.path, {"hidden": sorted(values)})
            if (key in self.hidden()) != hidden:
                raise OSError("녹화 목록 변경 저장 재확인 실패")


def recording_catalog(database, recordings_dir):
    files = {}
    for recording in database.get_all_recordings():
        if not recording.video_path:
            continue
        path = Path(recording.video_path).expanduser()
        if path.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        try:
            path = path.resolve(strict=True)
            stat = path.stat()
            if not path.is_file():
                continue
        except OSError:
            continue
        files[str(path).casefold()] = dict(path=str(path), name=recording.name or path.name, media_type="video",
            size_bytes=stat.st_size, modified_at=datetime.fromtimestamp(stat.st_mtime), recording_id=recording.id,
            input_log=recording.input_log_path or None)
    folder = Path(recordings_dir)
    if folder.is_dir():
        for path in folder.iterdir():
            if path.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            try:
                resolved = path.resolve(strict=True)
                stat = resolved.stat()
                if not resolved.is_file():
                    continue
            except OSError:
                continue
            # Do not infer an input-log association just from a similar filename.
            files.setdefault(str(resolved).casefold(), dict(path=str(resolved), name=path.name, media_type="video",
                size_bytes=stat.st_size, modified_at=datetime.fromtimestamp(stat.st_mtime), recording_id=None, input_log=None))
    return sorted(files.values(), key=lambda row: (row["modified_at"], row["name"]), reverse=True)
