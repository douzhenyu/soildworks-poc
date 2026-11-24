const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const names=['标注在图框内','多余尺寸','重复特征数量','单位与英文','定位尺寸齐全','特征尺寸齐全','封闭尺寸链','明细栏必填信息','签名与日期','新图框检查','受控水印','引用标准版本'];
const subtitles=['标注与有效图框边界','悬空关联与引线指向','孔、槽、倒角等重复特征','单位大小写及英文拼写','从基准追踪到特征，核对各方向定位','核对外形和特征的直接尺寸或完整尺寸链','核实同一方向、同一对端点的重复约束','材料、机型、名称、图号','指定区域应无签名日期','模板编号及公司名称','指定受控文件印章','识别标准引用，版本待核验'];
let job=null,currentFile=null,page=1,zoom=1,filter='all',activeIssue=null,timer=null,polling=false;
let activeRule=null;
const verdictLabels={problem:'有问题',pass:'没问题',manual:'需要人工审核',missing:'缺少判断依据'};
const verdictOrder=['problem','missing','manual','pass'];
let selectedRules=new Set(JSON.parse(localStorage.getItem('drawingRules')||'[1,2,3,4,5,6,7,8,9,10,11,12]'));
const labels={queued:'等待处理',extracting:'正在解压',converting:'正在转换',awaiting_confirmation:'待确认',reviewing:'正在审核',completed:'初审完成',failed:'处理失败',ready:'已转换'};
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function toast(s){$('#toast').textContent=s;$('#toast').hidden=false;clearTimeout(timer);timer=setTimeout(()=>$('#toast').hidden=true,4500);}
async function api(path,options){let r=await fetch(path,options);let d=await r.json();if(!r.ok)throw Error(d.error||'请求失败');return d;}
function displayName(s){try{return decodeURIComponent(s)}catch{return s}}
function rulesUI(){
 $('#rulesList').innerHTML=names.map((n,i)=>`<label class="rule-option"><input type="checkbox" value="${i+1}" ${selectedRules.has(i+1)?'checked':''}><span><strong><span style="color:#94a187">R${String(i+1).padStart(2,'0')}</span>　${n}</strong><small>${subtitles[i]}</small></span></label>`).join('');
 $('#rulesSelected').textContent=`已选择 ${selectedRules.size} / 12 项`;
 $$('#rulesList input').forEach(c=>c.onchange=()=>{if(c.checked)selectedRules.add(+c.value);else selectedRules.delete(+c.value);localStorage.setItem('drawingRules',JSON.stringify([...selectedRules]));$('#rulesSelected').textContent=`已选择 ${selectedRules.size} / 12 项`;});
}
function showRules(){rulesUI();$('#rulesDialog').showModal();}
$('#rulesButton').onclick=$('#settingsButton').onclick=showRules;
$$('.close-dialog').forEach(b=>b.onclick=()=>b.closest('dialog').close());
function choose(){if(job&&['extracting','converting','queued','reviewing'].includes(job.status))return toast('当前任务正在处理，可从“最近任务”查看进度。');$('#fileInput').click();}
$('#newTask').onclick=$('#chooseFile').onclick=e=>{e.stopPropagation();choose()};
$('#dropZone').onclick=choose;$('#dropZone').onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();choose()}};
['dragenter','dragover'].forEach(t=>$('#dropZone').addEventListener(t,e=>{e.preventDefault();$('#dropZone').classList.add('dragging')}));
['dragleave','drop'].forEach(t=>$('#dropZone').addEventListener(t,e=>{e.preventDefault();$('#dropZone').classList.remove('dragging')}));
$('#dropZone').ondrop=e=>{e.preventDefault();upload(e.dataTransfer.files[0])};
$('#fileInput').onchange=e=>{upload(e.target.files[0]);e.target.value=''};
async function upload(file){
 if(!file)return;if(!file.name.toLowerCase().endsWith('.zip'))return toast('请选择ZIP格式压缩包。');if(file.size>500*1024**2)return toast('文件超过500MB上限。');
 showWorkspace();$('#progressArea').hidden=false;$('#progressMessage').textContent='正在上传压缩包…';$('#progressValue').textContent='0%';$('#batchName').textContent=file.name;$('#confirmBar').hidden=true;$('#downloadAll').hidden=true;$('#fileSelect').replaceChildren();$('#issueList').innerHTML='<div class="issue-empty">上传中，请稍候</div>';$('#sheet').hidden=true;$('#viewerPlaceholder').hidden=false;$('#viewerPlaceholder').textContent='上传完成后自动解压并转换图纸…';$('#errorBanner').hidden=true;
 try{
 const j=await new Promise((resolve,reject)=>{const x=new XMLHttpRequest();x.open('POST','/api/jobs');x.setRequestHeader('Content-Type','application/zip');x.setRequestHeader('X-File-Name',encodeURIComponent(file.name));x.upload.onprogress=e=>{if(e.lengthComputable){let v=Math.round(e.loaded/e.total*100);$('#progressValue').textContent=v+'%';$('#progressFill').style.width=v+'%';}};x.onload=()=>{try{let d=JSON.parse(x.responseText);if(x.status>=400)reject(Error(d.error));else resolve(d)}catch{reject(Error('服务响应异常'))}};x.onerror=()=>reject(Error('上传失败，请检查本地服务'));x.send(file)});
 currentFile=null;activeIssue=null;activeRule=null;page=1;job=j;localStorage.setItem('drawingJob',j.id);render();poll();
 }catch(e){toast(e.message);$('#progressArea').hidden=true;$('#errorBanner').hidden=false;$('#errorBanner').textContent=e.message;}
}
async function poll(){if(polling)return;polling=true;try{while(job&&['queued','extracting','converting','reviewing'].includes(job.status)){await new Promise(r=>setTimeout(r,1200));const id=job.id;const next=await api('/api/jobs/'+id);if(job?.id===id){job=next;render();}}}catch(e){toast('进度更新失败：'+e.message)}finally{polling=false}}
async function openJob(id){job=await api('/api/jobs/'+id);currentFile=null;activeIssue=null;activeRule=null;page=1;zoom=1;localStorage.setItem('drawingJob',id);render();poll();}
$('#historyButton').onclick=async()=>{try{let jobs=await api('/api/jobs');$('#historyList').innerHTML=jobs.length?jobs.map(j=>`<button class="history-item" data-job="${esc(j.id)}"><strong>${esc(displayName(j.name))}</strong><small>${esc(labels[j.status]||j.status)} · ${j.files.length} 份图纸 · ${new Date(j.created*1000).toLocaleString('zh-CN')}</small></button>`).join(''):'<div class="issue-empty">还没有审图任务</div>';$$('[data-job]').forEach(b=>b.onclick=()=>{$('#historyDialog').close();openJob(b.dataset.job)});$('#historyDialog').showModal()}catch(e){toast(e.message)}};
$('#workbench').onclick=()=>{if(job)render();else $('#empty').hidden=false};
function showWorkspace(){
 $('#empty').hidden=true;$('#workspace').hidden=false;document.body.classList.add('review-mode');
}
$('#fileSelect').onchange=event=>{
 currentFile=event.target.value;page=1;zoom=1;activeIssue=null;renderFile();
};
function render(){
 if(!job)return;showWorkspace();
 const running=['queued','extracting','converting','reviewing'].includes(job.status),files=job.files||[],ready=files.filter(f=>f.status==='ready');
 const step=job.status==='awaiting_confirmation'?2:job.status==='reviewing'||job.status==='completed'?3:job.status==='extracting'||job.status==='converting'||job.status==='queued'?1:0;
 $$('.workflow>div').forEach(d=>{d.classList.toggle('current',+d.dataset.step===step);d.classList.toggle('done',+d.dataset.step<step)});
 $('#batchName').textContent=displayName(job.name);$('#batchSummary').textContent=`${files.length} 份图纸 · ${ready.reduce((a,f)=>a+f.pages,0)} 页 · ${labels[job.status]||job.status}`;
 $('#progressArea').hidden=!running;$('#progressMessage').textContent=job.message;$('#progressValue').textContent=job.progress+'%';$('#progressFill').style.width=job.progress+'%';$('#errorBanner').hidden=!job.error;$('#errorBanner').textContent=job.error||'';
 $('#confirmBar').hidden=job.status!=='awaiting_confirmation';$('#confirmText').textContent=`${ready.length}/${files.length} 份可审核。请确认PDF图框、文字、尺寸线和页数正确；失败文件不进入审核。`;$('#startReview').disabled=!ready.length;
 $('#downloadAll').hidden=job.status!=='completed';$('#downloadAll').href='/api/jobs/'+job.id+'/bundle';$('#downloadAll').download='初审标注.zip';$('#fileCount').textContent=files.length;
 $('#retryFiles').hidden=running||!files.some(f=>f.status!=='ready');$('#reReview').hidden=job.status!=='completed';
 if(!currentFile||!files.some(f=>f.id===currentFile))currentFile=(ready[0]||files[0])?.id;
 $('#fileSelect').innerHTML=files.map(f=>`<option value="${esc(f.id)}">${esc(f.name)}${f.status==='failed'?' · 转换失败':f.pages?' · '+f.pages+' 页':''}</option>`).join('');
 $('#fileSelect').value=currentFile||'';
 $('#fileSelect').disabled=!files.length;
 renderFile();
}
let imageKey='';
function resultsFor(file){
 if(!file?.reviewed)return [];
 if(file.manualNotes)return [...(file.issues||[]),...file.manualNotes];
 if(file.reviewResults)return file.reviewResults;
 const results=(file.issues||[]).map(issue=>({...issue,status:issue.status||{error:'problem',warning:'manual',pending:'missing'}[issue.severity]}));
 for(const row of file.coverage||[]){
  if(row.status==='checked'&&results.some(result=>result.rule===row.rule))continue;
  const status=row.status==='checked'?'pass':row.status==='pending'||row.rule===12?'missing':'manual';
  if(!results.some(result=>result.rule===row.rule&&result.status===status)){
   results.push({rule:row.rule,status,title:verdictLabels[status],reason:row.reason,method:'规则引擎'});
  }
 }
 return results;
}
function groupExplanations(results){
 const groups=[],byExplanation=new Map();
 for(const row of results){
  const key=row.method?.endsWith('审图 Agent')&&JSON.stringify(
   row.findingId!=null?[row.fileId,row.findingId]:[row.fileId,row.page,row.rule,row.status,row.method,row.view,row.title,row.reason]);
  let group=key&&byExplanation.get(key);
  if(!group){
   group={...row,locations:[]};
   groups.push(group);
   if(key)byExplanation.set(key,group);
  }
  if(row.id&&row.page&&row.box)group.locations.push(row);
 }
 return groups;
}
function renderRules(file){
 const results=groupExplanations(resultsFor(file));
 const batch=(job.files||[]).flatMap(file=>groupExplanations(resultsFor(file)));
 const rows=names.map((name,i)=>{
  const rule=i+1;
  const resultState=verdictOrder.find(status=>results.some(item=>item.rule===rule&&item.status===status));
  const summary=(file.ruleReviews||file.agentRuleReviews)?.find(item=>item.rule===rule);
  const state=(resultState==='problem'?'problem':summary?.status||resultState)||'idle';
  const total=batch.filter(item=>item.rule===rule&&item.status==='problem').length;
  const located=results.filter(item=>item.rule===rule&&item.locations.length);
  const passed=located.filter(item=>item.status==='pass').length;
  const pending=located.filter(item=>['manual','missing'].includes(item.status)).length;
  const detail=[passed?`已确认 ${passed} 处`:'',pending?`待核对 ${pending} 处`:''].filter(Boolean).join(' · ');
  return {rule,name,state,label:verdictLabels[state]||'未审核',total,detail};
 });
 const states=[...Object.entries(verdictLabels),['idle','未审核']];
 const counts=states.map(([state,label])=>({state,label,count:rows.filter(row=>row.state===state).length}));
 $('#ruleProgress').innerHTML=`<div class="rule-meter">${counts.filter(item=>item.count).map(item=>`<span class="${item.state}" style="flex:${item.count}"></span>`).join('')}</div><div class="rule-totals">${counts.filter(item=>item.count).map(item=>`<span class="${item.state}">${item.label} ${item.count}</span>`).join('')}</div>`;
 const query=$('#ruleSearch').value.trim().toLowerCase();
 const visible=rows.filter(row=>`r${String(row.rule).padStart(2,'0')} ${row.rule} ${row.name}`.toLowerCase().includes(query));
 $('#reviewRuleList').innerHTML=(!query?'<button class="review-rule all-rules" data-rule="" type="button" aria-pressed="'+(activeRule===null)+'">全部审核证据</button>':'')+
  (visible.length?visible.map(row=>`<button class="review-rule" data-rule="${row.rule}" type="button" aria-pressed="${row.rule===activeRule}"><span class="rule-code">R${String(row.rule).padStart(2,'0')}</span><span class="rule-name">${row.name}${row.detail?`<small>${row.detail}</small>`:''}${row.total?`<small class="batch-problems">整批 ${row.total} 处问题</small>`:''}</span><span class="rule-state ${row.state}">${row.label}</span></button>`).join(''):'<p class="issue-empty">没有匹配的规则</p>');
 return rows.find(row=>row.rule===activeRule);
}
function showPanel(id,visible){
 const panel=$('#'+id),toggle=$('#'+(id==='rulePanel'?'toggleRules':'toggleEvidence'));
 panel.hidden=!visible;toggle.setAttribute('aria-expanded',String(visible));
}
$('#toggleRules').onclick=()=>showPanel('rulePanel',$('#rulePanel').hidden);
$('#toggleEvidence').onclick=()=>showPanel('issuePanel',$('#issuePanel').hidden);
$('#ruleSearch').oninput=()=>{
 const file=job?.files.find(item=>item.id===currentFile);if(file)renderRules(file);
};
$('#evidenceScope').onchange=()=>renderFile();
$('#reviewRuleList').onclick=event=>{
 const row=event.target.closest('[data-rule]');if(!row)return;
 activeRule=Number(row.dataset.rule)||null;activeIssue=null;setFilter('all');showPanel('issuePanel',true);renderFile();
 $('#reviewRuleList').querySelector(`[data-rule="${row.dataset.rule}"]`)?.focus({preventScroll:true});
};
function setFilter(value){
 filter=value;$$('[data-filter]').forEach(button=>{
  const selected=button.dataset.filter===value;
  button.classList.toggle('selected',selected);button.setAttribute('aria-pressed',String(selected));
 });
}
function renderFile(){
 const f=job?.files.find(x=>x.id===currentFile);if(!f){$('#sheet').hidden=true;$('#viewerPlaceholder').hidden=false;return}
 $('#drawingState').textContent=f.reviewed?'初审完成':labels[f.status];
 $('#viewerPlaceholder').hidden=f.status==='ready';$('#sheet').hidden=f.status!=='ready';
 if(f.status==='failed'){$('#viewerPlaceholder').innerHTML=`<span>${esc(f.error)}</span><br><a href="/api/jobs/${job.id}/${f.id}/log" target="_blank">查看转换日志 ↗</a>`}
 else $('#viewerPlaceholder').textContent='SolidWorks 正在准备图纸预览…';
 const pages=f.pages||0;
 page=Math.min(page,Math.max(1,pages));$('#pageCounter').textContent=`${pages?page:0} / ${pages}`;$('#prevPage').disabled=page<=1;$('#nextPage').disabled=page>=pages;
 const key=job.id+'/'+f.id+'/'+page;if(f.status==='ready'&&key!==imageKey){imageKey=key;$('#pageImage').src=`/api/jobs/${job.id}/${f.id}/page?n=${page}`;}
 $('#pageImage').onerror=()=>toast('预览加载失败，请刷新页面或下载PDF检查。');
 $('#sheet').style.width=(zoom*100)+'%';$('#zoomLabel').textContent=Math.round(zoom*100)+'%';
 $('#pdfLink').hidden=f.status!=='ready';$('#pdfLink').href=`/api/jobs/${job.id}/${f.id}/pdf${f.reviewed?'?review=1':''}`;$('#pdfLink').download=f.name.replace(/\.slddrw$/i,'')+(f.reviewed?'-初审':'')+'.pdf';
 $('#evidenceLink').hidden=f.status!=='ready';$('#evidenceLink').href=`/api/jobs/${job.id}/${f.id}/evidence`;$('#evidenceLink').download=f.name.replace(/\.slddrw$/i,'')+'-证据.json';
 const size=f.sizes?.[page-1];
 $('#pageSize').textContent=size?size.map(x=>Math.round(x/72*25.4)).join(' × ')+' mm':'原纸张比例';
 const results=resultsFor(f);
 const rule=renderRules(f);
 const inBatch=$('#evidenceScope').value==='batch';
 const files=inBatch?job.files:[f];
 const scope=groupExplanations(files.flatMap(file=>resultsFor(file).filter(row=>activeRule===null||row.rule===activeRule)
  .map(row=>({...row,fileId:file.id,fileName:file.name}))));
 const summaries=files.flatMap(file=>(file.ruleReviews||file.agentRuleReviews||[])
  .filter(row=>(activeRule===null||row.rule===activeRule)&&(filter==='all'||row.status===filter))
  .map(row=>({...row,fileName:file.name})));
 $('#evidenceTitle').textContent=rule?`R${String(rule.rule).padStart(2,'0')} · ${rule.name}`:'审核证据';
 const problems=scope.filter(a=>a.status==='problem');
 const manual=scope.filter(a=>['manual','missing'].includes(a.status)&&(filter==='all'||filter===a.status));
 const shown=filter==='all'||filter==='problem'?problems:[];
 $('#issueCount').textContent=f.reviewed?problems.length+' 条问题':'—';
 const manualOpen=$('#manualNotes')?.open;
 const renderCard=a=>{
  const single=a.locations.length===1?a.locations[0]:null;
  const selected=a.fileId===currentFile&&a.locations.some(location=>location.id===activeIssue);
  const position=[inBatch?a.fileName:'',a.page?`第 ${a.page} 页`:''].filter(Boolean).join(' · ');
  const locator=location=>`data-issue="${esc(location.id)}" data-result-file="${esc(a.fileId)}"`;
  const links=a.locations.length>1?`<div class="issue-locations" role="group" aria-label="图纸定位">${a.locations.map((location,i)=>
   `<button type="button" ${locator(location)} aria-pressed="${selected&&location.id===activeIssue}">位置 ${i+1} ↗</button>`).join('')}</div>`:'';
  return `<article class="issue-card ${a.status} ${selected?'selected':''}" ${single?`${locator(single)} tabindex="0" role="button" aria-pressed="${selected}"`:''}><div class="issue-meta"><span class="badge ${a.status}">${verdictLabels[a.status]}</span><span>R${String(a.rule).padStart(2,'0')}</span><small title="${esc(position)}">${esc(position)}</small></div><h3>${esc(a.title)}</h3><p>${esc(a.reason)}</p><div class="issue-foot"><span>${esc(a.method)}</span><span>${single?(a.highlightBoxes?.length?`${1+a.highlightBoxes.length} 个定位点 ↗`:'定位到图纸 ↗'):a.locations.length?`${a.locations.length} 个定位点`:'图纸级结论'}</span></div>${links}</article>`;
 };
 if(f.reviewed||inBatch&&scope.length){
 $('#issueList').innerHTML=shown.length?shown.map(renderCard).join(''):'<div class="issue-empty"><b>当前筛选没有正式问题</b></div>';
 if(manual.length)$('#issueList').insertAdjacentHTML('beforeend',`<details id="manualNotes" ${manualOpen?'open':''}><summary>人工复核提醒（${manual.length}）</summary>${manual.map(renderCard).join('')}</details>`);
 if(summaries.length){
  const cards=summaries.map(a=>`<article class="issue-card ${a.status}"><div class="issue-meta"><span class="badge ${a.status}">${verdictLabels[a.status]}</span><span>R${String(a.rule).padStart(2,'0')}</span><small>${inBatch?esc(a.fileName):''}</small></div><h3>规则综合结论</h3><p>${esc(a.reason)}</p>${a.passedChecks?.length?`<details><summary>通过项（${a.passedChecks.length}）</summary>${a.passedChecks.map(check=>`<p><strong>${esc(check.title)}</strong> · ${esc(check.reason)}</p>`).join('')}</details>`:''}</article>`).join('');
  $('#issueList').insertAdjacentHTML('beforeend',`<details><summary>规则检查摘要（${summaries.length}）</summary>${cards}</details>`);
 }
 $('#coverage').textContent=inBatch?'点击带位置的证据可切换图纸并定位。':(f.vision||'选择规则查看对应的审核结论。');}
 else{$('#issueList').innerHTML=`<div class="issue-empty"><b>${job.status==='reviewing'?'正在进行审核':'审核尚未开始'}</b>${job.status==='awaiting_confirmation'?'确认图纸后，点击下方按钮开始审图。':'转换后需由你确认，<br>系统才会开始检查。'}</div>`;$('#coverage').textContent='规则与视觉结果将分开记录，数据不足的项目不会自动判为合格。';}
 $$('[data-issue]').forEach(el=>{
  el.onclick=()=>selectIssue(el.dataset.issue,false,el.dataset.resultFile);
  if(el.tagName!=='BUTTON')el.onkeydown=event=>{
   if(event.key!=='Enter'&&event.key!==' ')return;
   event.preventDefault();el.click();
  };
 });
 const markers=results.filter(a=>a.box&&a.page===page&&(activeRule===null||activeRule===a.rule)
  &&(a.id===activeIssue||a.status==='problem'&&(filter==='all'||filter==='problem')))
  .flatMap(a=>a.id===activeIssue?[a,...(a.highlightBoxes||[]).map(box=>({...a,box}))]:[a]);
 $('#annotations').innerHTML=markers.map(a=>`<button class="annotation ${a.status} ${a.id===activeIssue?'active':''}" data-box="${esc(a.id)}" style="left:${a.box[0]*100}%;top:${a.box[1]*100}%;width:${(a.box[2]-a.box[0])*100}%;height:${(a.box[3]-a.box[1])*100}%;${a.id===activeIssue?'z-index:1':''}" title="${esc(a.title)}" aria-label="${esc(a.title)}"><span>R${a.rule} · ${verdictLabels[a.status]}</span></button>`).join('');
 $$('[data-box]').forEach(el=>el.onclick=()=>{selectIssue(el.dataset.box,true);document.querySelector(`[data-issue="${CSS.escape(el.dataset.box)}"]`)?.closest('.issue-card')?.scrollIntoView({block:'nearest',behavior:'smooth'})});
}
function revealAnnotation(){
 const box=$('.annotation.active');
 if(!box||!$('#pageImage').naturalWidth)return;
 const viewer=$('#viewer'),viewport=viewer.getBoundingClientRect(),mark=box.getBoundingClientRect();
 viewer.scrollBy({left:mark.left+mark.width/2-viewport.left-viewport.width/2,
                  top:mark.top+mark.height/2-viewport.top-viewport.height/2});
}
$('#pageImage').addEventListener('load',revealAnnotation);
function selectIssue(id,fromDrawing=false,fileId=currentFile){
 const issue=resultsFor(job.files.find(f=>f.id===fileId)).find(item=>item.id===id);
 if(!issue)return;
 if(fileId!==currentFile){currentFile=fileId;$('#fileSelect').value=fileId;zoom=1;}
 if(fromDrawing){
  if(activeRule!==null)activeRule=issue.rule;
  if(filter!=='all'&&filter!==issue.status)setFilter('all');
  showPanel('issuePanel',true);
 }
 activeIssue=id;page=issue.page;renderFile();
 if($('#pageImage').complete)revealAnnotation();
 if(!fromDrawing){
  if(matchMedia('(max-width: 860px)').matches)$('.pdf-panel').scrollIntoView({block:'start'});
  $('.annotation.active')?.focus({preventScroll:true});
 }
}
$('#prevPage').onclick=()=>{page--;renderFile()};$('#nextPage').onclick=()=>{page++;renderFile()};$('#zoomIn').onclick=()=>{zoom=Math.min(3,zoom+.25);renderFile()};$('#zoomOut').onclick=()=>{zoom=Math.max(.5,zoom-.25);renderFile()};$('#zoomLabel').onclick=()=>{zoom=1;renderFile()};
$$('[data-filter]').forEach(b=>b.onclick=()=>{activeIssue=null;setFilter(b.dataset.filter);renderFile()});
setFilter('all');
$('#startReview').onclick=async()=>{if(!selectedRules.size)return toast('请至少选择一项审核规则。');$('#startReview').disabled=true;try{job=await api('/api/jobs/'+job.id+'/review',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({rules:[...selectedRules]})});render();poll()}catch(e){toast(e.message);$('#startReview').disabled=false}};
$('#reReview').onclick=()=>$('#startReview').onclick();
$('#retryFiles').onclick=async()=>{try{job=await api('/api/jobs/'+job.id+'/retry',{method:'POST'});imageKey='';render();poll()}catch(e){toast(e.message)}};
async function refreshModelStatus(){
 const config=await api('/api/config');
 const source=config.visionProvider==='custom'?'自定义模型':'Codex CLI';
 $('#visionStatus').title=config.visionConfigured?source+' · '+config.visionModel:'请配置视觉审核模型';
}
function showCustomModelFields(){
 const custom=$('#modelProvider').value==='custom';
 $('#customModelFields').hidden=!custom;
 $$('#customModelFields input').forEach(input=>input.disabled=!custom);
 $('#modelBaseUrl').required=custom;
 $('#modelName').required=custom;
}
$('#modelProvider').onchange=showCustomModelFields;
$('#visionStatus').onclick=async()=>{
 try{
  const settings=await api('/api/model-settings');
  $('#modelProvider').value=settings.provider;
  $('#modelBaseUrl').value=settings.baseUrl;
  $('#modelName').value=settings.model;
  $('#modelApiKey').value='';
  $('#modelApiKey').placeholder=settings.hasApiKey?'留空保留同一地址的已保存密钥':'服务无需鉴权时可留空';
  $('#clearModelKey').checked=false;
  $('#clearModelKeyRow').hidden=!settings.hasApiKey;
  $('#modelSettingsError').hidden=true;
  showCustomModelFields();
  $('#modelDialog').showModal();
 }catch(e){toast(e.message)}
};
$('#modelDialog').addEventListener('close',()=>{$('#modelApiKey').value=''});
$('#modelForm').onsubmit=async event=>{
 event.preventDefault();
 const button=$('#saveModelSettings');
 const error=$('#modelSettingsError');
 button.disabled=true;
 error.hidden=true;
 try{
  await api('/api/model-settings',{
   method:'POST',
   headers:{'Content-Type':'application/json'},
   body:JSON.stringify({
    provider:$('#modelProvider').value,
    baseUrl:$('#modelBaseUrl').value,
    model:$('#modelName').value,
    apiKey:$('#modelApiKey').value,
    clearApiKey:$('#clearModelKey').checked
   })
  });
  $('#modelDialog').close();
  toast('模型设置已保存');
  await refreshModelStatus();
 }catch(e){
  if($('#modelDialog').open){
   error.textContent=e.message;
   error.hidden=false;
  }else toast(e.message);
 }finally{button.disabled=false}
};
(async()=>{
 try{
  await refreshModelStatus();
  const id=localStorage.getItem('drawingJob');
  if(id)await openJob(id);
 }catch(e){toast(e.message)}
})();

