import ipaddress
import math
import re
from urllib.parse import urlparse, parse_qs
from pydantic import BaseModel, Field, model_validator

class Cue(BaseModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str = Field(min_length=1, max_length=10000)

    @model_validator(mode='after')
    def valid(self):
        if not math.isfinite(self.start) or not math.isfinite(self.end) or self.start >= self.end:
            raise ValueError('字幕开始时间必须小于结束时间')
        if not self.text.strip():
            raise ValueError('字幕不能为空')
        return self

def validate_cues(cues):
    if not cues or len(cues) > 50000:
        raise ValueError('字幕数量无效')
    for a, b in zip(cues, cues[1:]):
        if b.start < a.start:
            raise ValueError('字幕必须按开始时间排序')
    return cues

def youtube_url(value):
    p = urlparse(value.strip())
    if p.scheme != 'https' or p.username or p.password or p.port not in (None, 443):
        raise ValueError('请使用 HTTPS YouTube 视频链接')
    if p.hostname == 'youtu.be':
        vid = p.path.strip('/')
    elif p.hostname in ('youtube.com', 'www.youtube.com', 'm.youtube.com'):
        vid = parse_qs(p.query).get('v', [''])[0] if p.path == '/watch' else p.path.split('/')[-1] if p.path.startswith(('/shorts/', '/embed/')) else ''
    else:
        raise ValueError('只支持 YouTube 视频链接；其他来源请上传文件')
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', vid):
        raise ValueError('YouTube 视频 ID 无效')
    return 'https://www.youtube.com/watch?v=' + vid

def stamp(seconds, sep):
    ms = round(seconds * 1000)
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f'{h:02}:{m:02}:{s:02}{sep}{ms:03}'

def export(cues, kind):
    validate_cues(cues)
    if kind == 'txt':
        return '\n'.join(c.text for c in cues) + '\n'
    sep = ',' if kind == 'srt' else '.'
    blocks = [f'{i}\n{stamp(c.start, sep)} --> {stamp(max(c.end, round(c.start * 1000) / 1000 + .001), sep)}\n{c.text.replace(chr(13), "").replace(chr(10)+chr(10), chr(10))}\n' for i, c in enumerate(cues, 1)]
    return ('WEBVTT\n\n' if kind == 'vtt' else '') + '\n'.join(blocks)
