"""Atomic worker progress snapshots; no guessed time-based percentages."""
import json
import math
import os
from pathlib import Path
import threading
import time

PHASE_MESSAGES = {
    'checking_model': '正在检查语音识别模型',
    'downloading_model': '正在下载语音识别模型；首次使用需要下载，完成后会自动开始识别',
    'loading_model': '正在加载语音识别模型',
    'detecting_speech': '正在检测人声与语言',
    'transcribing': '正在语音识别',
    'finalizing': '识别已结束，正在整理字幕',
}

class ProgressWriter:
    def __init__(self, directory):
        self.path = Path(directory) / 'progress.json'
        self.lock = threading.Lock()

    def __call__(self, phase, **values):
        if phase not in PHASE_MESSAGES:
            raise ValueError('Unknown progress phase')
        data = {'phase': phase, 'percent': None, 'processed_seconds': None,
                'total_seconds': None, 'segments': 0, **values, 'reported_at': time.time()}
        with self.lock:
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(data, allow_nan=False), encoding='utf-8')
            os.replace(temporary, self.path)


def transcription_progress(processed, duration, segments):
    if not math.isfinite(duration) or duration <= 0:
        return {'percent': None, 'processed_seconds': max(0, processed),
                'total_seconds': None, 'segments': segments}
    processed = min(duration, max(0, processed))
    return {'percent': round(processed / duration * 100, 1),
            'processed_seconds': round(processed, 2), 'total_seconds': round(duration, 2),
            'segments': segments}
