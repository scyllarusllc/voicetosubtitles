"""Replace Engine with another implementation to add a cloud ASR provider."""
from pathlib import Path
from typing import Callable, Protocol
import os

ProgressCallback = Callable[..., None]

class Engine(Protocol):
    def transcribe(self, path: str, language: str | None,
                   progress: ProgressCallback | None = None) -> dict: ...


def prepare_model(model_name, report):
    from faster_whisper.utils import download_model, _MODELS
    from huggingface_hub import snapshot_download
    from huggingface_hub.errors import LocalEntryNotFoundError
    from huggingface_hub.utils import tqdm

    report('checking_model')
    if Path(model_name).is_dir():
        return str(Path(model_name).resolve())
    try:
        cached = download_model(model_name, local_files_only=True)
        if all((Path(cached) / item).exists() for item in ('model.bin', 'config.json', 'tokenizer.json')):
            return cached
    except (LocalEntryNotFoundError, FileNotFoundError):
        pass

    # Resolve the same identifiers as the pinned faster-whisper implementation.
    repo = model_name if '/' in model_name else _MODELS.get(model_name)
    if not repo:
        raise ValueError('不支持的 Whisper 模型名称')
    report('downloading_model')

    class FileProgress(tqdm):
        """Report actual completed files, never treat file count as byte percentage."""
        def __init__(self, *args, **kwargs):
            kwargs['disable'] = False
            super().__init__(*args, **kwargs)

        def display(self, *args, **kwargs):
            if getattr(self, 'unit', None) == 'it' and getattr(self, 'total', None):
                report('downloading_model', done_files=int(self.n), total_files=int(self.total))
            return True

    return snapshot_download(
        repo, allow_patterns=['config.json', 'preprocessor_config.json', 'model.bin',
                              'tokenizer.json', 'vocabulary.*'], tqdm_class=FileProgress,
    )


class FasterWhisperEngine:
    def transcribe(self, path, language, progress=None):
        from faster_whisper import WhisperModel
        from backend.progress import transcription_progress
        report = progress or (lambda *args, **kwargs: None)
        model_path = prepare_model(os.getenv('WHISPER_MODEL', 'base'), report)
        report('loading_model')
        model = WhisperModel(model_path, device=os.getenv('WHISPER_DEVICE', 'cpu'),
                             compute_type=os.getenv('WHISPER_COMPUTE_TYPE', 'int8'),
                             cpu_threads=int(os.getenv('WHISPER_THREADS', '4')))
        report('detecting_speech')
        segments, info = model.transcribe(path, language=language, vad_filter=True,
                                          beam_size=5, condition_on_previous_text=False)
        cues = []
        processed = 0.0
        report('transcribing', **transcription_progress(0, info.duration, 0))
        for segment in segments:
            if segment.text.strip() and segment.end > segment.start:
                cues.append({'start': segment.start, 'end': segment.end, 'text': segment.text.strip()})
            processed = max(processed, segment.end)
            report('transcribing', **transcription_progress(processed, info.duration, len(cues)))
        report('finalizing', processed_seconds=round(info.duration, 2),
               total_seconds=round(info.duration, 2), segments=len(cues))
        return {'cues': cues, 'language': info.language}
