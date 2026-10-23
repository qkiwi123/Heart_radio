'use strict';
(() => {
 const el=id=>document.getElementById(id),root=el('reminderHistoryView');
 const labels={deleted:'手表已删除',acknowledged:'本人已确认',displayed:'手表已显示',expired:'提醒已过期',cancelled:'效果已取消',issued:'已下发',
  notification_only_dnd:'仅通知 · 系统模式限制',notification_only_system_restricted:'仅通知 · 系统限制',
  outcome_unknown_timeout:'结果不确定 · 超时',outcome_unknown_disconnected:'结果不确定 · 断连',outcome_unknown_after_restart:'结果不确定 · 重启',
  interaction_disabled:'交互未开启',interaction_sync_pending:'交互待同步',watch_offline:'手表离线',sending:'发送中',
  reminder_policy_denied:'超出提醒限制',reminder_policy_sync_pending:'提醒限制待同步',reminder_storage_error:'手表保存异常',
  reminder_ledger_full:'手表记录已满',rate_limited:'发送过于频繁',busy:'手表正忙',delivery_error:'执行未完成'};
 const date=ms=>ms?new Date(ms).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'—';
 const sounds={not_requested:'未请求声音',preparing:'正在准备',started:'已开始播放',completed:'播放已结束',stopped:'已停止',suppressed:'未执行',failed:'播放失败',requested_via_system:'已提交系统播放'};
 const state={page:1,pages:1,request:0,open:new Set(),querying:false,signature:null};
 const node=(tag,content,className)=>{const n=document.createElement(tag);if(content!=null)n.textContent=content;if(className)n.className=className;return n};
 function syncText(s){
  if(!s)return '重连后自动核对回执；需要连接和两端已开启的 Agent 交互。';
  if(s.error)return '回执同步保存失败：'+s.error;
  if(s.status==='waiting')return '等待可用连接与两端已确认的 Agent 交互；不会自动开启开关。';
  if(s.status==='complete')return `本轮回执核对完成：${s.checked}条${s.notFound?`；其中${s.notFound}条已不在手表账本，不能据此判断是否删除`:''}。`;
  if(s.status==='partial')return `已核对${s.checked}/${s.total}条，${s.failed}条暂未核对成功，下次重连再核对。`;
  return `重连回执${s.status==='retrying'?'等待重试':'核对中'}：${s.checked}/${s.total}条，待处理${s.pending}条。只查询状态，不重新提醒。`;
 }
 function item(row){
  const r=row.receipt||{},p=row.params||{},details=node('details',null,'reminder-history-item');
  details.open=state.open.has(row.reminderId);
  details.addEventListener('toggle',()=>{if(details.open)state.open.add(row.reminderId);else state.open.delete(row.reminderId)});
  const summary=node('summary');summary.append(node('span',p.title||'无标题','reminder-history-title'));
  const badge=node('span',labels[row.status]||row.status,'reminder-status');badge.dataset.status=row.status;
  summary.append(badge,node('span',date(row.createdAtMs)+' · '+row.reminderId,'reminder-history-meta'));details.append(summary);
  const body=node('div',null,'reminder-history-detail');body.append(node('p',p.body||'没有保存正文','reminder-history-body'));
  if(row.status==='deleted')body.append(node('p','手表已清除本机正文；电脑按现有策略保留原始发送内容。','history-note'));
  if(row.watchRecordStatus==='not_found')body.append(node('p','最近核对时，手表账本中已无此编号。可能已被容量规则替换，不能据此认定已删除或未执行。','history-note'));
  const fields=[['提醒编号',row.reminderId],['发送时间',date(row.createdAtMs)],['发送有效期至',date(row.pcExpiresAtMs)],
   ['手表页面显示',date(r.displayedAtMs)],['本人确认',date(r.acknowledgedAtMs)],['手表删除',date(r.deletedAtMs)],
   ['最近核对',date(row.watchCheckedAtMs)],['最近回执',date(row.receiptReceivedAtMs)],
   ['请求效果',[p.sound?'声音':null,p.vibrate?'振动':null,p.screen?'亮屏':null].filter(Boolean).join('、')||'仅通知'],
   ['声音回执',sounds[r.soundStatus]||r.soundStatus||'尚无记录']];
  const list=node('dl');for(const [name,value]of fields){const pair=node('div');pair.append(node('dt',name),node('dd',value));list.append(pair)}body.append(list);
  const button=node('button','向手表核对本条回执');button.type='button';button.disabled=state.querying;
  button.addEventListener('click',()=>check(row.reminderId));body.append(button);details.append(body);return details;
 }
 async function check(id){
  if(state.querying)return;state.querying=true;
  el('reminderHistoryList').querySelectorAll('button').forEach(button=>{button.disabled=true});
  el('reminderHistoryMessage').textContent='正在核对此条回执，不会重新发送提醒…';
  let message;
  try{
   const response=await fetch('/api/reminders',{method:'POST',headers:{'Content-Type':'application/json','X-HeartRadio-Action':'reminders'},body:JSON.stringify({action:'status',reminderId:id,fresh:true})});
   const result=await response.json();if(!response.ok)throw Error(result.error||'核对失败');
   message=result.status==='not_found'?'手表账本中已无此编号；电脑原记录保留。':`本条核对结果：${labels[result.status]||result.status}。`;
  }catch(error){message=error.message}
  finally{state.querying=false;state.signature=null;await load();el('reminderHistoryMessage').textContent=message}
 }
 async function load(){
  if(root.hidden||window.heartRadioStopped||state.querying)return;
  const request=++state.request;
  const params=new URLSearchParams({page:String(state.page),pageSize:'20',q:el('reminderHistoryQuery').value.trim(),status:el('reminderHistoryStatus').value});
  try{
   const response=await fetch('/api/reminders/history?'+params,{cache:'no-store'});if(!response.ok)throw Error('无法读取提醒历史，请检查本地服务。');
   const result=await response.json();if(request!==state.request)return;
   state.page=result.page;state.pages=result.pages;
   el('reminderHistoryCapacity').textContent=`当前保存${result.total}/${result.capacity}条 · 仅查询电脑账本，离线也可查看。`;
   el('reminderSyncState').textContent=syncText(result.sync);
   const status=el('reminderHistoryStatus'),selected=status.value;
   const options=['',...new Set([...result.statuses,...(selected?[selected]:[])])];
   if(JSON.stringify([...status.options].map(option=>option.value))!==JSON.stringify(options)){
    status.replaceChildren(...options.map(value=>new Option(value?(labels[value]||value):'全部状态',value)));status.value=selected;
   }
   const signature=JSON.stringify(result.reminders.map(({receiptAgeSeconds,...row})=>row));
   if(signature!==state.signature){
    const content=result.reminders.length?result.reminders.map(item):[node('p',result.total?'没有符合筛选条件的提醒。':'尚无电脑提醒记录。发送后会显示在这里。','history-empty-state')];
    el('reminderHistoryList').replaceChildren(...content);state.signature=signature;
   }
   el('reminderHistoryPage').textContent=`第${result.page}/${result.pages}页 · 共${result.matched}条`;
   el('reminderHistoryPrevious').disabled=result.page<=1;el('reminderHistoryNext').disabled=result.page>=result.pages;
   el('reminderHistoryMessage').textContent=result.storageError||'展开条目查看正文与回执。历史中的内容保留不代表手表仍保留正文。';
  }catch(error){if(request===state.request)el('reminderHistoryMessage').textContent=error.message}
 }
 el('reminderHistoryFilters').addEventListener('submit',event=>{event.preventDefault();state.page=1;load()});
 el('reminderHistoryStatus').addEventListener('change',()=>{state.page=1;load()});
 el('reminderHistoryReset').addEventListener('click',()=>{el('reminderHistoryQuery').value='';el('reminderHistoryStatus').value='';state.page=1;load()});
 el('reminderHistoryRefresh').addEventListener('click',load);
 el('reminderHistoryPrevious').addEventListener('click',()=>{if(state.page>1){state.page--;load()}});
 el('reminderHistoryNext').addEventListener('click',()=>{if(state.page<state.pages){state.page++;load()}});
 window.addEventListener('heartradio-page-change',event=>{if(event.detail.page==='reminder-history')load()});
 const timer=setInterval(load,5000);window.addEventListener('heartradio-session-stopped',()=>clearInterval(timer));
})();
