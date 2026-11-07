import React, {useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {zh as t} from './i18n';
import {readGuestHistory, upsertGuestHistory, removeGuestHistory, clearGuestHistory, savePendingJob, exportSubtitles, timestamp} from './history';
import './style.css';
const terminal = ['completed', 'failed', 'cancelled'];
async function api(path, options={}) {
  const response = await fetch('/api'+path, options);
  if (!response.ok) {
    let data;try { data = await response.json(); } catch {}
    const message = typeof data?.detail === 'string' ? data.detail : Array.isArray(data?.detail) ? data.detail.map(e=>e.msg).join('；') : t.requestError(response.status);
    const error = new Error(message); error.status = response.status; throw error;
  }
  return response.status === 204 ? null : response.json();
}
function localStorageAccess() {try { return window.localStorage; } catch {const error=new Error();error.code='storage_unavailable';throw error}}
function Icon({name='wave'}) {return <span aria-hidden="true" className={'icon '+name}>{name==='wave'?'≋':name==='upload'?'↑':name==='link'?'↗':name==='check'?'✓':'↓'}</span>}
function saveBlob(blob, kind) {const anchor=document.createElement('a');anchor.href=URL.createObjectURL(blob);anchor.download='subtitles.'+kind;anchor.click();setTimeout(()=>URL.revokeObjectURL(anchor.href),1000)}
function Progress({job}) {
  const [now,setNow]=useState(Date.now());
  useEffect(()=>{if(terminal.includes(job.status))return;const timer=setInterval(()=>setNow(Date.now()),1000);return()=>clearInterval(timer)},[job.id,job.status]);
  if(terminal.includes(job.status))return null;
  const progress=job.progress;
  const elapsed=Math.max(0,Math.floor(now/1000-(job.started_at??job.created_at)));
  const percent=typeof progress?.percent==='number'&&Number.isFinite(progress.percent)?Math.max(0,Math.min(100,progress.percent)):null;
  const position=typeof progress?.processed_seconds==='number'&&typeof progress?.total_seconds==='number';
  return <div className="progress-panel" aria-label={t.progressLabel}>
    <div className="progress-top"><span>{t.progressPhases[progress?.phase]??t.stages[job.status]}</span><span>{t.elapsed(elapsed)}</span></div>
    {percent!==null?<><div className="progress-track" role="progressbar" aria-label={progress?.phase==='transcribing'?t.timelineProgress:t.measuredProgress} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}><div style={{width:`${percent}%`}}/></div><div className="progress-details"><strong>{Math.floor(percent)}%</strong><span>{position?t.audioPosition(progress.processed_seconds,progress.total_seconds):t.measuredProgress}</span></div></>:<div className="progress-activity"><span/>{t.phaseOnly}</div>}
    <div className="progress-notes">{progress?.phase==='transcribing'&&<span>{t.segmentProgress(progress.segments??0)}</span>}{Number.isFinite(progress?.done_files)&&Number.isFinite(progress?.total_files)&&<span>{t.fileProgress(progress.done_files,progress.total_files)}</span>}{progress?.phase==='transcribing'&&<span>{t.progressMeaning}</span>}</div>
  </div>;
}
function App() {
  const [mode,setMode]=useState('link'),[url,setUrl]=useState(''),[file,setFile]=useState(null),[language,setLanguage]=useState('auto');
  const [config,setConfig]=useState(null),[auth,setAuth]=useState({ready:false,user:null,google_enabled:false});
  const [job,setJob]=useState(null),[cues,setCues]=useState([]),[history,setHistory]=useState([]);
  const [error,setError]=useState(''),[notice,setNotice]=useState(''),[storageWarning,setStorageWarning]=useState('');
  const [busy,setBusy]=useState(false),[saving,setSaving]=useState(false),[dirty,setDirty]=useState(false),[pollError,setPollError]=useState(false),[historyBusy,setHistoryBusy]=useState(false);
  const dirtyRef=useRef(false);dirtyRef.current=dirty;
  const latestGuestDraft=useRef(null);
  latestGuestDraft.current=auth.ready&&!auth.user&&job?.storage!=='account'&&job?.status==='completed'&&cues.length?{...job,cues,updated_at:dirty?Date.now()/1000:job.updated_at}:null;
  const identityEpoch=useRef(0);
  const active=job&&!terminal.includes(job.status), isAccount=job?.storage==='account';
  const needsSave=job?.status==='completed'&&(dirty||(isAccount&&job.history_saved===false)||(!isAccount&&Boolean(storageWarning)));
  function storageError(error) {setStorageWarning(t.storageErrors[error.code]??t.storageErrors.storage_unavailable)}
  function restoreGuest() {
    try {
      const document=readGuestHistory(localStorageAccess());setHistory(document.items);
      const selected=document.items.find(i=>i.id===document.selectedId)??document.items[0];
      if(document.pending) {setJob({...document.pending,status:'waiting',message:t.restoring,cues:[]});setCues([])}
      else {setJob(selected?{...selected,message:t.localRestored}:null);setCues(selected?.cues??[])}
      setDirty(false);
    } catch(error) {setHistory([]);setJob(null);setCues([]);storageError(error)}
  }
  async function refreshAccountHistory() {const epoch=identityEpoch.current;const data=await api('/history');if(epoch===identityEpoch.current)setHistory(data.items??[])}
  useEffect(()=>{const flush=()=>{if(latestGuestDraft.current)try{upsertGuestHistory(localStorageAccess(),latestGuestDraft.current)}catch(error){storageError(error)}};window.addEventListener('pagehide',flush);return()=>window.removeEventListener('pagehide',flush)},[]);
  useEffect(()=>{
    let gone=false;
    Promise.all([api('/config'),api('/auth/me')]).then(async([settings,identity])=>{
      if(gone)return;setConfig(settings);setAuth({...identity,ready:true});
      if(identity.user) {try {await refreshAccountHistory()} catch(error) {if(!gone)setError(error.message)}} else restoreGuest();
      const params=new URLSearchParams(window.location.search);
      if(params.get('auth')==='error')setError(t.loginFailed);
      if(params.get('auth')==='success')setNotice(t.loginSucceeded);
      if(params.has('auth')) {params.delete('auth');window.history.replaceState(null,'',window.location.pathname+(params.size?'?'+params.toString():''))}
    }).catch(error=>{if(!gone)setError(error.message)});
    return()=>{gone=true};
  },[]);
  useEffect(()=>{
    if(!job||terminal.includes(job.status))return;
    let stopped=false,timer;
    async function poll() {
      try {
        const data=await api('/jobs/'+job.id);if(stopped)return;setJob(data);setPollError(false);
        if(data.status==='completed') {if(!dirtyRef.current)setCues(data.cues);if(data.storage==='account')refreshAccountHistory().catch(error=>setError(error.message))}
        if(terminal.includes(data.status)) {if(data.storage!=='account'&&data.status!=='completed')try{savePendingJob(localStorageAccess(),null)}catch(error){storageError(error)};return}
      } catch(error) {
        if(stopped)return;
        if([401,403,404].includes(error.status)) {
          setJob(previous=>({...previous,status:'failed',message:t.taskUnavailable,error_code:'task_expired'}));setPollError(false);
          if(job.storage!=='account')try{savePendingJob(localStorageAccess(),null)}catch(error){storageError(error)};return;
        }
        setPollError(true);
      }
      if(!stopped)timer=setTimeout(poll,1500);
    }
    timer=setTimeout(poll,300);return()=>{stopped=true;clearTimeout(timer)};
  },[job?.id,job?.status]);
  useEffect(()=>{
    if(!auth.ready||auth.user||job?.storage==='account'||job?.status!=='completed'||!cues.length)return;
    const timer=setTimeout(()=>{
      try {const saved={...job,cues,updated_at:dirty?Date.now()/1000:job.updated_at};const document=upsertGuestHistory(localStorageAccess(),saved);setHistory(document.items);setStorageWarning('');setDirty(false)}catch(error){storageError(error)}
    },350);return()=>clearTimeout(timer);
  },[auth.ready,auth.user,job?.id,job?.status,cues]);
  async function submit(event) {
    event?.preventDefault();if(active||busy)return;setError('');setNotice('');setBusy(true);
    try {
      if(needsSave)await save();
      let data;
      if(mode==='link') {if(!url.trim())throw new Error(t.enterLink);data=await api('/jobs/link',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url,language})})}
      else {if(!file)throw new Error(t.pickFile);if(config&&file.size>config.max_upload_mb*1024**2)throw new Error(t.sizeError(config.max_upload_mb));const body=new FormData();body.append('file',file);body.append('language',language);data=await api('/jobs/upload',{method:'POST',body})}
      setCues([]);setDirty(false);setJob(data);setPollError(false);
      if(data.storage!=='account')try{savePendingJob(localStorageAccess(),data)}catch(error){storageError(error)}
    } catch(error) {if(error.code)storageError(error);else setError(error.message)}finally{setBusy(false)}
  }
  async function save() {
    if(isAccount) {
      const endpoint=job.history_saved===false?'/jobs/'+job.id+'/cues':'/history/'+job.id;
      const data=await api(endpoint,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({cues})});
      setJob({...data,storage:'account'});
      if(data.history_saved===false)throw new Error(t.accountSaveFailed);
      setDirty(false);await refreshAccountHistory();return data;
    }
    const saved={...job,cues,updated_at:Date.now()/1000};const document=upsertGuestHistory(localStorageAccess(),saved);setHistory(document.items);setStorageWarning('');setDirty(false);return saved;
  }
  async function saveClick() {setSaving(true);setError('');try{await save();setNotice(isAccount?t.savedAccount:t.savedLocal)}catch(error){if(error.code)storageError(error);else setError(error.message)}finally{setSaving(false)}}
  async function download(kind) {
    setSaving(true);setError('');
    try {
      if(!isAccount) {const content=exportSubtitles(cues,kind);try{await save()}catch(error){storageError(error)};saveBlob(new Blob([content],{type:kind==='vtt'?'text/vtt;charset=utf-8':'text/plain;charset=utf-8'}),kind)}
      else {
        if(dirty||job.history_saved===false) {
          try{await save()}catch(error){saveBlob(new Blob([exportSubtitles(cues,kind)],{type:kind==='vtt'?'text/vtt;charset=utf-8':'text/plain;charset=utf-8'}),kind);setNotice(t.accountDownloadBackup);setError(error.message);return}
        }
        const response=await fetch(`/api/history/${job.id}/download/${kind}`);
        if(!response.ok)throw new Error(t.downloadFailed);
        saveBlob(await response.blob(),kind);
      }
    } catch(error) {setError(error.code==='invalid_cues'?t.invalidCues:error.message)}finally{setSaving(false)}
  }
  async function cancel() {try{setJob(await api('/jobs/'+job.id+'/cancel',{method:'POST'}));if(!isAccount)savePendingJob(localStorageAccess(),null)}catch(error){setError(error.message)}}
  async function login() {
    if(active||busy||saving)return;
    try{if(job?.status==='completed'&&job.storage!=='account')await save();window.location.href='/api/auth/google'}catch(error){if(error.code)storageError(error);else setError(error.message)}
  }
  async function logout() {
    if(active)return;setBusy(true);identityEpoch.current+=1;
    try{if(needsSave)await save();await api('/auth/logout',{method:'POST'});setAuth(previous=>({...previous,user:null}));setJob(null);setCues([]);setDirty(false);setError('');setNotice(t.loggedOut);restoreGuest()}catch(error){setError(error.message)}finally{setBusy(false)}
  }
  async function openHistory(item) {
    if(active)return;setHistoryBusy(true);setError('');setNotice('');
    try {if(needsSave)await save();const data=auth.user?{...await api('/history/'+item.id),storage:'account'}:item;setJob({...data,message:data.status==='completed'?(auth.user?t.accountRestored:t.localRestored):data.message});setCues(data.cues??[]);setDirty(false);document.querySelector('.results')?.scrollIntoView({behavior:'smooth',block:'start'})}catch(error){if(error.code)storageError(error);else setError(error.message)}finally{setHistoryBusy(false)}
  }
  async function deleteHistory(item) {
    if(active)return;setHistoryBusy(true);
    try {if(auth.user){await api('/history/'+item.id,{method:'DELETE'});await refreshAccountHistory()}else{const document=removeGuestHistory(localStorageAccess(),item.id);setHistory(document.items);setStorageWarning('')};if(job?.id===item.id){setJob(null);setCues([]);setDirty(false)}}catch(error){if(error.code)storageError(error);else setError(error.message)}finally{setHistoryBusy(false)}
  }
  function clearLocalHistory() {if(active)return;try{clearGuestHistory(localStorageAccess());setHistory([]);setStorageWarning('');if(!isAccount){setJob(null);setCues([]);setDirty(false)}}catch(error){storageError(error)}}
  const date=value=>new Date(value*1000).toLocaleString('zh-CN',{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});
  return <>
    <header><a className="brand" href="/"><Icon/>{t.brand}<span className="beta">BETA</span></a><div className="account-controls">{auth.user?<><span className="account-name" title={auth.user.email}>{auth.user.name||auth.user.email}</span><button className="subtle" disabled={active||busy||saving||historyBusy} onClick={logout}>{t.logout}</button></>:<><span className="local"><i/>{t.guestMode}</span><button className="google-login" disabled={!auth.ready||!auth.google_enabled||active||busy||saving||historyBusy} title={auth.google_enabled?t.loginBenefit:t.googleUnavailable} onClick={login}><span aria-hidden="true">G</span>{auth.ready&&!auth.google_enabled?t.googleUnavailable:t.googleLogin}</button></>}</div></header>
    <main><section className="hero"><div className="eyebrow"><span/>{t.badge}</div><h1>{t.title}<br/><em>{t.accent}</em></h1><p>{t.intro}</p></section>
      <section className="workspace"><div className="input-card"><nav className="tabs" aria-label={t.inputMethod}>{['link','upload'].map(value=><button key={value} type="button" disabled={active||busy} className={mode===value?'selected':''} onClick={()=>{setMode(value);setError('')}}><Icon name={value}/>{t[value]}</button>)}</nav>
          <form onSubmit={submit}>{mode==='link'?<div className="field"><label htmlFor="url">{t.urlLabel}</label><input id="url" type="url" placeholder={t.placeholder} value={url} onChange={event=>setUrl(event.target.value)} disabled={active||busy}/><small>{t.priorityHint}</small></div>:<label className="drop" onDragOver={event=>event.preventDefault()} onDrop={event=>{event.preventDefault();if(!active&&!busy)setFile(event.dataTransfer.files[0])}}><Icon name="upload"/><strong>{file?file.name:t.fileTitle}</strong><span>{file?`${(file.size/1024**2).toFixed(2)} MB`:t.fileHint}</span><input aria-label={t.fileTitle} type="file" disabled={active||busy} accept=".mp4,.mov,.mp3,.m4a,.wav,.webm,.ogg,.flac,.aac,.aiff" onChange={event=>setFile(event.target.files[0])}/></label>}
            <div className="form-bottom"><div><label htmlFor="language">{t.language}</label><select id="language" disabled={active||busy} value={language} onChange={event=>setLanguage(event.target.value)}>{Object.entries(t.languages).map(([key,value])=><option key={key} value={key}>{value}</option>)}</select></div><button className="primary" disabled={busy||active||!config||!auth.ready}>{busy?t.submitting:t.submit}<span>→</span></button></div>{config&&<p className="limits">{t.limits(config)}</p>}
          </form><p className="limits">{auth.user?t.accountSaveHint:t.guestSaveHint}</p><div className="rights">ⓘ {t.rights}</div></div>
        <aside className="steps"><span className="section-kicker">{t.pipeline}</span>{t.process.map((step,index)=><div key={step}><b>0{index+1}</b><span>{step}</span></div>)}<p>{t.note}</p></aside></section>
      {error&&<div role="alert" className="alert error">{error}</div>}{notice&&<div role="status" className="alert">{notice}</div>}{storageWarning&&<div role="alert" className="alert error storage-warning">{storageWarning}{!auth.user&&<button className="subtle" disabled={active} onClick={clearLocalHistory}>{t.clearLocalHistory}</button>}</div>}
      <section className="results"><div className="result-heading"><h2>{job?t.preview:t.status}</h2>{job?.source&&<span className="source">{t.sources[job.source]} · {job.detected_language}</span>}</div>
        {!job?<div className="empty"><div className="empty-icon">≋</div><h3>{t.empty}</h3><p>{t.emptyHint}</p><div className="formats"><span>SRT</span><span>VTT</span><span>TXT</span></div></div>:<>
          <div className={'status '+job.status} role="status"><div className={'status-dot '+(active?'pulse':'')}/><div><strong>{t.stages[job.status]}</strong><p>{job.status==='recognizing'?t.recognizingNotice:job.message}</p>{job.error_code&&<small>{t.diagnostic}{job.error_code}</small>}</div>{active&&<button className="subtle" onClick={cancel}>{t.cancel}</button>}</div>
          <Progress job={job}/>{pollError&&<p className="alert">{t.connection}</p>}{job.notices?.map((note,index)=><p className="notice" key={index}>{note}</p>)}
          {['failed','cancelled'].includes(job.status)&&<div className="recovery"><button onClick={submit} disabled={busy} className="subtle">{t.retry}</button><button onClick={()=>{setMode('upload');document.querySelector('.input-card').scrollIntoView({behavior:'smooth'})}} className="primary">{t.uploadInstead} ↑</button></div>}
          {job.status==='completed'&&<><div className="editor-bar"><span>{t.cueCount(cues.length)} · {isAccount?t.editable:t.guestEditable}{dirty?' · '+t.unsaved:''}</span><button className="subtle" disabled={(!dirty&&!storageWarning&&!(isAccount&&job.history_saved===false))||saving} onClick={saveClick}>{t.save}</button></div>
            <div className="cue-list">{cues.map((cue,index)=><div className="cue" key={index}><span className="cue-number">{String(index+1).padStart(2,'0')}</span><div className="time"><time>{timestamp(cue.start)}</time><span>→</span><time>{timestamp(cue.end)}</time></div><textarea aria-label={t.cueLabel(index+1)} value={cue.text} onChange={event=>{setCues(previous=>previous.map((value,n)=>n===index?{...value,text:event.target.value}:value));setDirty(true);setNotice('')}}/></div>)}</div><div className="downloads"><span>{t.download}</span>{['srt','vtt','txt'].map(kind=><button key={kind} disabled={saving} onClick={()=>download(kind)}><Icon name="download"/>{kind.toUpperCase()}</button>)}</div></>}
        </>}</section>
      <section className="history-panel" aria-label={t.historyTitle}><div className="history-heading"><div><h2>{auth.user?t.accountHistory:t.guestHistory}</h2><p>{auth.user?t.accountHistoryHint:t.guestHistoryHint}</p></div><span className="history-count">{t.historyCount(history.length)}</span></div>
        {!history.length?<p className="history-empty">{t.noHistory}</p>:<div className="history-list">{history.map(item=><div className="history-row" key={item.id}><button className="history-open" disabled={active||historyBusy} onClick={()=>openHistory(item)}><strong>{item.title||t.untitled}</strong><span>{date(item.updated_at??item.created_at)} · {t.sources[item.source]??t.stages[item.status]}{item.detected_language?' · '+item.detected_language:''}</span></button><button className="history-delete" aria-label={t.deleteHistoryLabel(item.title||t.untitled)} disabled={active||historyBusy} onClick={()=>deleteHistory(item)}>{t.deleteHistory}</button></div>)}</div>}
      </section><footer><span>≋ {t.footer}</span><span>{t.disclaimer}</span></footer>
    </main></>;
}
createRoot(document.getElementById('root')).render(<App/>);
