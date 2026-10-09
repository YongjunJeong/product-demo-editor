'use strict';
const $ = id => document.getElementById(id);
const token = location.hash.slice(1) || sessionStorage.getItem('video-token') || '';
if (token) sessionStorage.setItem('video-token', token);
history.replaceState(null, '', '/');
let state = {}, dirty = true, renderedPlan = '', renderedExchange = '', pending = false, translationDirty = false, cueDirty = false, previewError = '';
function node(tag, text, cls) { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if(cls) n.className = cls; return n; }
function media(id, key) { const el = $(id); const url = key ? `/media/${key}?token=${encodeURIComponent(token)}` : ''; if(el.dataset.key !== (key || '')) { el.dataset.key = key || ''; if(url) el.src = url; else { el.removeAttribute('src'); el.load(); } } el.hidden = !key; }
function changed() { dirty = true; controls(); $('review-badge').textContent = '변경됨 · 미리보기 필요'; }
function previewFeedback() {
  const error = previewError || state.error;
  const active = state.busy && state.last_action?.action === 'preview';
  const elapsed = state.started_at ? Math.max(0, Math.floor((Date.now()-Date.parse(state.started_at))/1000)) : 0;
  $('preview').textContent = active ? '미리보기 생성 중…' : '선택한 편집 미리보기';
  $('preview-status').hidden = !(active || error || state.review);
  $('preview-status').textContent = error || (active ? `${state.message} · ${elapsed}초 경과. 완료되면 아래 검토 영상이 표시됩니다.` : state.review ? '미리보기 완료. 아래 검토 영상에서 재생하세요.' : '');
}
function controls() {
  previewFeedback();
  document.querySelectorAll('button').forEach(b => b.disabled = !!state.busy || pending);
  $('approve').disabled ||= dirty || !state.review;
  $('render').disabled ||= dirty || !state.approved;
  $('export').disabled ||= dirty || !state.rendered;
  $('speed').disabled ||= cueDirty || translationDirty;
  $('preview').disabled ||= translationDirty;
  document.querySelectorAll('input, textarea, select').forEach(el => el.disabled = !!state.busy || pending);
}
function choices(kind, ops, plan) {
  const target = $(kind); target.replaceChildren();
  if(!ops.length) target.append(node('p', '후보가 없습니다.', 'hint'));
  for(const op of ops) {
    const row = node('div', undefined, 'option'), label = node('label', undefined, 'check');
    const check = document.createElement('input'); check.type='checkbox'; check.value=op.id;
    check.checked=(state.draft?.[kind] || state.review?.[kind] || []).includes(op.id); check.addEventListener('change', changed);
    const description = node('span', `${(op.start_frame/plan.fps).toFixed(2)}–${(op.end_frame/plan.fps).toFixed(2)}초${kind==='speeds' ? ` · ${op.rate}× · ${op.audio==='mute'?'무음':'음높이 유지'}`:''}`);
    label.append(check, description);
    const note = plan.agent_notes?.[`${kind==='speeds'?'speed':'cut'}:${op.id}`];
    if(note) description.append(node('span',note,'hint'));
    const seek = node('button','구간 보기','secondary'); seek.onclick=()=> { $('source-video').currentTime=op.start_frame/plan.fps; }; row.append(label,seek); target.append(row);
  }
}
function display(s) {
  const projectChanged = state.project_id !== s.project_id;
  if(projectChanged) { renderedPlan=''; renderedExchange=''; previewError=''; }
  $('projects').replaceChildren();
  for(const project of s.projects || []) {
    const row=node('div',undefined,'option');
    row.append(node('span',project.title || '이름 없는 작업'),node('span',project.error?'확인 필요':project.message,'hint'));
    const open=node('button',project.project_id===s.project_id?'현재 작업':'이어서 열기','secondary');
    open.onclick=()=>action('open-project',{id:project.project_id}); row.append(open); $('projects').append(row);
  }
  $('retry').hidden=!s.retry;

  state=s; $('status').textContent=s.message; $('error').hidden=!s.error; $('error').textContent=s.error || '';
  $('editor').hidden=!s.plan;
  if(s.plan && (renderedPlan !== s.plan_path || projectChanged)) {
    renderedPlan=s.plan_path; dirty=!s.review; cueDirty=false; translationDirty=false;
    const plan=s.plan; $('source-duration').textContent=`${(plan.source_duration_ms/1000).toFixed(2)}초`;
    $('source-name').textContent=plan.source; $('plan-path').textContent=`현재 계획: ${s.plan_path}`;
    $('speech-note').textContent=plan.speech_protection_available ? '음성 구간 보호 적용 · 컷과 배속의 음성 겹침을 검사합니다.' : '음성 보호 정보 없음 · 화면 조작과 발화가 잘리지 않는지 직접 확인하세요.';
    choices('cuts',plan.cuts,plan); choices('speeds',plan.speeds,plan);
    $('cues').replaceChildren();
    if(!plan.cues.length) $('cues').append(node('p','자막이 없습니다. 전사가 있다면 영어 번역을 입력해 초안을 만드세요.','hint'));
    for(const cue of plan.cues) {
      const row=node('div',undefined,'cue'), label=node('label',`#${cue.id} · ${(cue.start_ms/1000).toFixed(2)}–${(cue.end_ms/1000).toFixed(2)}초`), text=document.createElement('textarea'); text.value=s.draft?.texts?.[plan.cues.indexOf(cue)] ?? cue.text; text.setAttribute('aria-label',`자막 ${cue.id}`); text.oninput=()=>{cueDirty=true;changed();}; row.append(label,text); $('cues').append(row);
    }
  }
  $('translation-panel').hidden=!s.exchange;
  if(s.exchange && renderedExchange!==s.exchange) {
    renderedExchange=s.exchange; $('translations').replaceChildren();
    for(const segment of s.transcript.segments) { const row=node('div',undefined,'translation'); const ko=node('p',`${segment.start.toFixed(2)}–${segment.end.toFixed(2)}초 · ${segment.text}`); const en=document.createElement('textarea'); en.dataset.id=segment.id; en.value=(s.draft?.segments || s.translations || []).find(t=>t.id===segment.id)?.text || '';  en.placeholder='영어 번역을 입력하세요'; en.setAttribute('aria-label',`발화 ${segment.id} 영어 번역`); en.oninput=()=>{translationDirty=true;changed();$('status').textContent='영어 번역을 수정했습니다. 초안을 저장한 뒤 미리보기를 만드세요.';}; row.append(ko,en); $('translations').append(row); }
  }
  if(s.draft && (projectChanged || !dirty)) {dirty=true; translationDirty=!!s.draft.translationDirty; cueDirty=!!s.draft.cueDirty;}
  media('source-video',s.source_media); media('preview-video',s.preview_media); media('final-video',s.final_media);
  $('review-badge').textContent=s.approved && !dirty ? '승인 완료' : s.review && !dirty ? '검토 대기' : '미리보기 필요';
  $('review-summary').replaceChildren();
  if(s.review) { const r=s.review.summary; $('review-summary').append(node('p',`${r.source_seconds.toFixed(2)}초 → ${r.output_seconds.toFixed(2)}초`,'metric'),node('p',`컷 ${s.review.cuts.length}개 · 배속 ${s.review.speeds.length}개 · 자막 ${r.subtitle_cues}개`)); for(const w of r.caption_warnings) $('review-summary').append(node('p',`자막 ${w.cue_id}: 길이·읽기 속도를 확인하세요.`,'hint')); }
  $('output').textContent=s.bundle ? `CapCut 파일 폴더: ${s.bundle}` : s.rendered ? `최종 렌더 폴더: ${s.rendered}` : '';
  controls();
}
async function refresh(){ const response=await fetch('/api/state',{headers:{'X-Video-Token':token}}); if(!response.ok) throw new Error('터미널에 표시된 전체 주소(# 뒤의 세션 키 포함)로 접속하세요.'); display(await response.json()); }
async function action(name, data){
  pending=true; controls(); $('error').hidden=true;
  if(name==='preview') { previewError=''; $('preview-status').hidden=false; $('preview-status').textContent='미리보기 생성 요청 중…'; }
  try { const response=await fetch(`/api/${name}`,{method:'POST',headers:{'Content-Type':'application/json','X-Video-Token':token},body:JSON.stringify(data)}); const body=await response.json(); if(!response.ok) throw new Error(body.error); await refresh(); while(state.busy){ await new Promise(resolve=>setTimeout(resolve,700)); await refresh(); } }
  catch(error){ $('error').hidden=false; $('error').textContent=error.message; if(name==='preview') { previewError=error.message; } }
  finally{pending=false;controls();}
}
$('analyze').onclick=()=>action('analyze',{source:$('source').value,transcribe:$('transcribe').checked});
for(const name of ['load-run','load-plan']) $(name).onclick=()=>action(name,{path:$('existing').value});
$('translate').onclick=()=>action('translate',{segments:[...document.querySelectorAll('#translations textarea')].map(el=>({id:Number(el.dataset.id),text:el.value}))});
$('speed').onclick=()=>action('speed',{start:Number($('speed-start').value),end:Number($('speed-end').value),rate:Number($('speed-rate').value),audio:$('speed-audio').value});
$('preview').onclick=()=>action('preview',{cuts:[...document.querySelectorAll('#cuts input:checked')].map(el=>Number(el.value)),speeds:[...document.querySelectorAll('#speeds input:checked')].map(el=>Number(el.value)),texts:[...document.querySelectorAll('#cues textarea')].map(el=>el.value)});
$('approve').onclick=()=>action('approve',{review_id:state.review.id});
$('render').onclick=()=>action('render',{}); $('export').onclick=()=>action('export',{});
refresh().then(async()=>{while(state.busy){await new Promise(resolve=>setTimeout(resolve,700));await refresh();}}).catch(error=>{$('status').textContent=error.message;});

function draftData(){return {cuts:[...document.querySelectorAll('#cuts input:checked')].map(el=>Number(el.value)),speeds:[...document.querySelectorAll('#speeds input:checked')].map(el=>Number(el.value)),texts:[...document.querySelectorAll('#cues textarea')].map(el=>el.value),segments:[...document.querySelectorAll('#translations textarea')].map(el=>({id:Number(el.dataset.id),text:el.value})),translationDirty,cueDirty};}
$('save-draft').onclick=()=>action('save-draft',draftData());
$('retry').onclick=()=>action('retry',{});
let parentFolder='';
async function files(path=''){
  try {const response=await fetch('/api/files'+(path?'?path='+encodeURIComponent(path):''),{headers:{'X-Video-Token':token}}); const data=await response.json(); if(!response.ok)throw new Error(data.error);
    $('file-picker').hidden=false; $('folder-path').value=data.path; parentFolder=data.parent; $('file-list').replaceChildren();
    for(const item of data.items){const button=node('button',(item.directory?'📁 ':'▶ ')+item.name,'secondary'); button.onclick=()=>{if(item.directory)files(item.path);else{$('source').value=item.path;$('file-picker').hidden=true;}};$('file-list').append(button);}
  }catch(error){$('error').hidden=false;$('error').textContent=error.message;}
}
$('choose-file').onclick=()=>files(); $('folder-go').onclick=()=>files($('folder-path').value); $('folder-up').onclick=()=>files(parentFolder); $('folder-close').onclick=()=>{$('file-picker').hidden=true;};
setInterval(()=>{previewFeedback();$('elapsed').textContent=state.busy && state.started_at ? `진행 중 · ${Math.max(0,Math.floor((Date.now()-Date.parse(state.started_at))/1000))}초 경과 · 단계별 상태이며 완료 예상 시간은 아닙니다.`:'';},1000);
