export const HISTORY_KEY = 'ai-subtitles.guest-history.v1';
export const HISTORY_VERSION = 1;
export const MAX_HISTORY_ITEMS = 50;
export const MAX_STORAGE_BYTES = 4 * 1024 * 1024;

export class HistoryError extends Error {
  constructor(code) { super(code); this.code = code; }
}

function validTime(value) { return typeof value === 'number' && Number.isFinite(value) && value >= 0; }

export function validateCues(cues, {draft = false} = {}) {
  if (!Array.isArray(cues) || !cues.length || cues.length > 50000) throw new HistoryError('invalid_cues');
  let previous = -1;
  for (const cue of cues) {
    if (!cue || !validTime(cue.start) || !validTime(cue.end) || cue.start >= cue.end || cue.start < previous || typeof cue.text !== 'string' || cue.text.length > 10000 || (!draft && !cue.text.trim())) throw new HistoryError('invalid_cues');
    previous = cue.start;
  }
  return cues;
}

function cleanSnapshot(job) {
  if (!job || job.storage === 'account' || typeof job.id !== 'string' || !/^[a-zA-Z0-9_-]{1,100}$/.test(job.id) || job.status !== 'completed') throw new HistoryError('invalid_history');
  validateCues(job.cues, {draft:true});
  if (!validTime(job.created_at) || !validTime(job.updated_at)) throw new HistoryError('invalid_history');
  return {
    id:job.id, storage:'guest', status:'completed',
    title:typeof job.title === 'string' ? job.title.slice(0,300) : '',
    kind:job.kind === 'link' ? 'link' : 'upload',
    language:typeof job.language === 'string' ? job.language.slice(0,20) : 'auto',
    detected_language:typeof job.detected_language === 'string' ? job.detected_language.slice(0,20) : '',
    source:['manual','automatic','ai'].includes(job.source) ? job.source : 'ai',
    caption_check:typeof job.caption_check === 'string' ? job.caption_check.slice(0,100) : '',
    created_at:job.created_at, updated_at:job.updated_at,
    duration:validTime(job.duration) ? job.duration : undefined,
    cues:job.cues.map(c=>({start:c.start,end:c.end,text:c.text})),
    notices:Array.isArray(job.notices) ? job.notices.filter(n=>typeof n === 'string').slice(0,10).map(n=>n.slice(0,1000)) : [],
  };
}

function emptyDocument() { return {version:HISTORY_VERSION,items:[],selectedId:null,pending:null}; }

export function readGuestHistory(storage) {
  let value;
  try { value = storage.getItem(HISTORY_KEY); } catch { throw new HistoryError('storage_unavailable'); }
  if (!value) return emptyDocument();
  try {
    const data = JSON.parse(value);
    if (data.version !== HISTORY_VERSION || !Array.isArray(data.items) || data.items.length > MAX_HISTORY_ITEMS) throw new Error();
    const items = data.items.map(cleanSnapshot);
    if (new Set(items.map(i=>i.id)).size !== items.length) throw new Error();
    const pending = data.pending;
    if (pending && (typeof pending.id !== 'string' || !/^[a-zA-Z0-9_-]{1,100}$/.test(pending.id) || !validTime(pending.created_at))) throw new Error();
    return {version:HISTORY_VERSION,items,selectedId:items.some(i=>i.id === data.selectedId) ? data.selectedId : null,pending:pending ? {id:pending.id,created_at:pending.created_at,storage:'guest'} : null};
  } catch { throw new HistoryError('invalid_history'); }
}

function writeDocument(storage, document) {
  const value = JSON.stringify(document);
  if (document.items.length > MAX_HISTORY_ITEMS || value.length * 2 > MAX_STORAGE_BYTES) throw new HistoryError('storage_full');
  try { storage.setItem(HISTORY_KEY, value); }
  catch (error) { throw new HistoryError(error?.name === 'QuotaExceededError' ? 'storage_full' : 'storage_unavailable'); }
  return document;
}

export function upsertGuestHistory(storage, job) {
  const item = cleanSnapshot(job);
  const previous = readGuestHistory(storage);
  const items = [item,...previous.items.filter(i=>i.id !== item.id)].sort((a,b)=>b.updated_at-a.updated_at);
  return writeDocument(storage,{...previous,items,selectedId:item.id,pending:previous.pending?.id === item.id ? null : previous.pending});
}

export function removeGuestHistory(storage, id) {
  const previous = readGuestHistory(storage);
  const items = previous.items.filter(i=>i.id !== id);
  return writeDocument(storage,{...previous,items,selectedId:previous.selectedId === id ? items[0]?.id ?? null : previous.selectedId});
}

export function clearGuestHistory(storage) { return writeDocument(storage,emptyDocument()); }

export function savePendingJob(storage, job) {
  if (job?.storage === 'account') throw new HistoryError('invalid_history');
  const previous = readGuestHistory(storage);
  return writeDocument(storage,{...previous,pending:job ? {id:job.id,created_at:job.created_at,storage:'guest'} : null});
}

// Python round uses ties-to-even; match backend/core.py at exact half milliseconds.
export function roundMilliseconds(seconds) {
  const value = seconds * 1000;
  const floor = Math.floor(value);
  return value - floor === .5 ? floor + (floor % 2) : Math.round(value);
}

export function timestamp(seconds, separator='.') {
  let ms = roundMilliseconds(seconds);
  const hours = Math.floor(ms/3600000); ms %= 3600000;
  const minutes = Math.floor(ms/60000); ms %= 60000;
  const secs = Math.floor(ms/1000); ms %= 1000;
  return `${String(hours).padStart(2,'0')}:${String(minutes).padStart(2,'0')}:${String(secs).padStart(2,'0')}${separator}${String(ms).padStart(3,'0')}`;
}

export function exportSubtitles(cues,kind) {
  validateCues(cues);
  if (!['txt','srt','vtt'].includes(kind)) throw new HistoryError('invalid_format');
  if (kind === 'txt') return cues.map(c=>c.text).join('\n')+'\n';
  const separator = kind === 'srt' ? ',' : '.';
  const blocks = cues.map((c,i)=>`${i+1}\n${timestamp(c.start,separator)} --> ${timestamp(Math.max(c.end,roundMilliseconds(c.start)/1000+.001),separator)}\n${c.text.replace(/\r/g,'').replace(/\n\n/g,'\n')}\n`);
  return (kind === 'vtt' ? 'WEBVTT\n\n' : '')+blocks.join('\n');
}
