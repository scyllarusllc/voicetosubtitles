"""Progress unit checks. These do not claim model inference or network download."""
import json
from pathlib import Path
import pytest
from backend.progress import ProgressWriter, transcription_progress
from backend.engine import prepare_model


def test_measured_audio_progress():
    data = transcription_progress(12.5, 50, 4)
    assert data == {'percent': 25.0, 'processed_seconds': 12.5, 'total_seconds': 50, 'segments': 4}
    assert transcription_progress(0, 0, 0)['percent'] is None
    assert transcription_progress(200, 100, 10)['percent'] == 100


def test_progress_snapshot_is_valid_json_and_atomic(tmp_path):
    writer = ProgressWriter(tmp_path)
    writer('checking_model')
    first = json.loads((tmp_path / 'progress.json').read_text())
    assert first['percent'] is None
    writer('transcribing', **transcription_progress(30, 120, 6))
    data = json.loads((tmp_path / 'progress.json').read_text())
    assert data['percent'] == 25 and data['segments'] == 6
    assert not (tmp_path / 'progress.tmp').exists()
    with pytest.raises(ValueError): writer('invented_phase')


def test_cached_model_does_not_report_download(tmp_path, monkeypatch):
    import faster_whisper.utils
    for name in ('model.bin','config.json','tokenizer.json'):
        (tmp_path / name).write_bytes(b'test-only')
    monkeypatch.setattr(faster_whisper.utils, 'download_model', lambda *a, **kw: str(tmp_path))
    events=[]
    assert prepare_model('base', lambda phase, **kw: events.append(phase)) == str(tmp_path)
    assert events == ['checking_model']


def test_missing_model_reports_real_library_file_counts(tmp_path, monkeypatch):
    import faster_whisper.utils
    import huggingface_hub
    from huggingface_hub.errors import LocalEntryNotFoundError
    def missing(*args, **kwargs): raise LocalEntryNotFoundError('not cached')
    monkeypatch.setattr(faster_whisper.utils, 'download_model', missing)
    events=[]
    def fake_download(repo, **kwargs):
        with kwargs['tqdm_class'](total=3, unit='it', mininterval=0) as bar:
            bar.update(1)
        return str(tmp_path)
    monkeypatch.setattr(huggingface_hub, 'snapshot_download', fake_download)
    assert prepare_model('base', lambda phase, **kw: events.append((phase,kw))) == str(tmp_path)
    assert ('downloading_model', {'done_files':1,'total_files':3}) in events
    assert all('percent' not in values for phase,values in events)
