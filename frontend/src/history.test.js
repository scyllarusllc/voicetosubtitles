import test from 'node:test';
import assert from 'node:assert/strict';
import {HISTORY_KEY,MAX_HISTORY_ITEMS,readGuestHistory,upsertGuestHistory,removeGuestHistory,clearGuestHistory,savePendingJob,exportSubtitles} from './history.js';
function memoryStorage() { const data=new Map();return {getItem:k=>data.get(k)??null,setItem:(k,v)=>data.set(k,v)}; }
const sample=(id='a')=>({id,status:'completed',storage:'guest',created_at:1,updated_at:2,title:'中文.wav',source:'ai',cues:[{start:0,end:1.234,text:'你好，世界。'},{start:1.3,end:2,text:'第二句话'}]});
test('guest results and edits survive a new reader',()=>{
 const storage=memoryStorage();upsertGuestHistory(storage,sample());const edited=sample();edited.cues[0].text='已经编辑';upsertGuestHistory(storage,edited);
 assert.equal(readGuestHistory(storage).items[0].cues[0].text,'已经编辑');assert.equal(readGuestHistory(storage).items.length,1);
});
test('only subtitle metadata is persisted, never media or account results',()=>{
 const storage=memoryStorage();upsertGuestHistory(storage,{...sample(),file:'private media',access_token:'secret',user:{email:'private'}});
 const raw=storage.getItem(HISTORY_KEY);assert.ok(!raw.includes('secret'));assert.ok(!raw.includes('private'));
 assert.throws(()=>upsertGuestHistory(storage,{...sample(),storage:'account'}));
});
test('pending task resumes and clears after completion',()=>{
 const storage=memoryStorage();savePendingJob(storage,{id:'a',created_at:1,storage:'guest'});assert.equal(readGuestHistory(storage).pending.id,'a');upsertGuestHistory(storage,sample());assert.equal(readGuestHistory(storage).pending,null);
});
test('empty edit drafts survive but cannot be exported',()=>{
 const storage=memoryStorage(),draft=sample();draft.cues[0].text='';upsertGuestHistory(storage,draft);assert.equal(readGuestHistory(storage).items[0].cues[0].text,'');assert.throws(()=>exportSubtitles(draft.cues,'srt'));
});
test('quota errors preserve existing records and signal failure',()=>{
 const storage=memoryStorage();upsertGuestHistory(storage,sample());const before=storage.getItem(HISTORY_KEY);storage.setItem=()=>{const error=new Error();error.name='QuotaExceededError';throw error};
 assert.throws(()=>upsertGuestHistory(storage,sample('b')),e=>e.code==='storage_full');assert.equal(storage.getItem(HISTORY_KEY),before);
});
test('record count limit does not silently evict',()=>{
 const storage=memoryStorage();for(let i=0;i<MAX_HISTORY_ITEMS;i++)upsertGuestHistory(storage,sample(String(i)));
 assert.throws(()=>upsertGuestHistory(storage,sample('overflow')),e=>e.code==='storage_full');assert.equal(readGuestHistory(storage).items.length,MAX_HISTORY_ITEMS);
});
test('corrupt or unknown schemas are reported without overwriting',()=>{
 const storage=memoryStorage();storage.setItem(HISTORY_KEY,'{"version":99,"items":[]}');assert.throws(()=>readGuestHistory(storage),e=>e.code==='invalid_history');assert.throws(()=>upsertGuestHistory(storage,sample()));
 clearGuestHistory(storage);assert.deepEqual(readGuestHistory(storage).items,[]);
});
test('storage unavailable still produces a useful error',()=>assert.throws(()=>readGuestHistory({getItem(){throw new Error()}}),e=>e.code==='storage_unavailable'));
test('deleting one history item keeps others',()=>{const s=memoryStorage();upsertGuestHistory(s,sample('a'));upsertGuestHistory(s,sample('b'));removeGuestHistory(s,'a');assert.deepEqual(readGuestHistory(s).items.map(x=>x.id),['b']);});
test('offline UTF-8 exports have the backend format and edited text',()=>{
 const cues=sample().cues;const txt=exportSubtitles(cues,'txt');assert.equal(new TextDecoder().decode(new TextEncoder().encode(txt)),'你好，世界。\n第二句话\n');
 assert.equal(exportSubtitles(cues,'srt'),'1\n00:00:00,000 --> 00:00:01,234\n你好，世界。\n\n2\n00:00:01,300 --> 00:00:02,000\n第二句话\n');assert.ok(exportSubtitles(cues,'vtt').startsWith('WEBVTT\n\n1\n00:00:00.000 --> 00:00:01.234'));
 assert.ok(exportSubtitles([{start:0,end:.0001,text:'短'}],'srt').includes('00:00:00,000 --> 00:00:00,001'));
});
test('invalid or out-of-order timestamps are refused',()=>{assert.throws(()=>exportSubtitles(sample().cues.reverse(),'srt'));assert.throws(()=>exportSubtitles([{start:0,end:Infinity,text:'bad'}],'vtt'));});
