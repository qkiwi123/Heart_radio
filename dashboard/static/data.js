'use strict';
(() => {
 const byId=id=>document.getElementById(id);
 const ago=seconds=>seconds>=3600?`${Math.floor(seconds/3600)}小时${Math.floor(seconds%3600/60)}分钟前`:seconds>=60?`${Math.floor(seconds/60)}分钟前`:`${Math.floor(seconds)}秒前`;
 const age=value=>!value?'尚未读取':`${ago(value.cacheAgeSeconds)}收到${value.cacheReusable?' · 可复用缓存':' · 下次重新读取'}`;
 const errors={interaction_disabled:'Agent 交互已关闭，可在电脑或手表任一端开启。',interaction_sync_pending:'交互设置等待另一端确认，确认后才能执行新的设备操作。',watch_offline:'手表尚未连接，请检查独立的连接开关。',bridge_unavailable:'电脑连接服务尚不可用，请检查连接状态。',busy:'本项已有不同采集时长的请求，其他指标仍可读取。',rate_limited:'本项需要等待设置的间隔，其他指标仍可读取。',window_complete:'测量窗口结束，接口没有返回数据。',request_timeout:'请求超时，没有获得新数据。',disconnected:'接口连接已断开，没有获得新数据。',no_valid_samples:'返回的样本无效。',disabled_by_settings:'此指标已在读取设置中关闭。',cancelled:'本次测量已取消；采集中取得的部分样本不作为新结果。',cleanup_unconfirmed:'手表未确认资源释放，本次不显示测量值。',no_active_measurement:'当前没有对应的进行中测量。',measurement_already_finished:'对应测量已经结束。'};
 Object.assign(errors,{timeout:'本次等待结束，未取得有效心率。',no_contact:'本次未取得接触有效的心率，请确认佩戴。',no_valid_data:'收到回调但没有通过有效性检查，本次未返回数值。',registration_failed:'心率监听注册失败。'});
 const names={heart_rate:'心率',device_status:'电量与状态',pressure:'气压',accelerometer:'加速度',gyroscope:'陀螺仪'};
 const pendingMetrics=new Set();
 const cancellingRequests=new Set();
 const activeRows=new Map();
 const resultTimes=new Map();
 let readPollTimer=null;
 let servicePending=false,preferenceDirty=false,settingsDirty=false,cancelPending=false,activeMeasurement=null,settingsRevision=0;
 let durationDirty=false,durationRevision=0,savedDuration=null;
 let serviceSnapshot=null,serviceSnapshotAt=0;
 let lastSettings={measurementSeconds:4,measurementCooldownSeconds:0,allowedMetrics:['heart_rate','device_status','pressure','accelerometer','gyroscope']};
 const fields={measurementSeconds:'dataSeconds',measurementCooldownSeconds:'dataCooldown',heartMaxAgeSeconds:'dataHeartAge',deviceStatusCacheSeconds:'dataStatusCache',sensorCacheSeconds:'dataSensorCache'};
 function renderButtons(){
  const active=activeMeasurement?(activeMeasurement.requests||[activeMeasurement]):[];
  const preparing=[...pendingMetrics].filter(metric=>!active.some(row=>row.metric===metric)).length;
  const count=active.length+preparing;
  byId('dataActiveState').textContent=count?`${count}项正在读取，各项独立结束${preparing?`（${preparing}项准备中）`:''}`:'当前没有测量';
  for(const button of document.querySelectorAll('[data-tool]')){
   const metric=button.dataset.kind||(button.dataset.tool==='measure_heart_rate'?'heart_rate':button.dataset.tool==='measure_pressure'?'pressure':'device_status');
   const reading=pendingMetrics.has(metric)||(activeMeasurement?.requests||[]).some(row=>row.metric===metric);
   button.disabled=reading||!lastSettings.allowedMetrics.includes(metric);
   button.setAttribute('aria-busy',String(reading));
   if(!button.dataset.idleLabel)button.dataset.idleLabel=button.textContent;
   button.textContent=reading?`${names[metric]}读取中…`:button.dataset.kind?`采集${lastSettings.measurementSeconds}秒${names[metric]}`:button.dataset.idleLabel;
   const result=byId('dataResult_'+metric);result.closest('article').dataset.state=reading?'reading':'idle';
  }
  byId('dataCancel').disabled=cancelPending||!activeMeasurement||(activeMeasurement.requests||[activeMeasurement]).every(row=>row.cancelRequested);
 }
 function renderSettings(value){
  lastSettings=value;
  byId('dataSummary').textContent=`不同指标可同时读取，依次点击按钮即可。默认采集${value.measurementSeconds}秒，${value.measurementCooldownSeconds===0?'同项结束即可再读':`同项结束后间隔${value.measurementCooldownSeconds}秒`}；心率最多等待15秒。`;
  if(!settingsDirty){
   for(const [key,id] of Object.entries(fields))byId(id).value=value[key];
   for(const input of document.querySelectorAll('[data-metric]'))input.checked=value.allowedMetrics.includes(input.dataset.metric);
  }
  renderButtons();
 }
 function renderActive(value){
  activeMeasurement=value;
  const rows=value?(value.requests||[value]):[];
  const holder=byId('dataActiveRequests'),ids=new Set(rows.map(row=>row.requestId));
  for(const [id,item] of activeRows)if(!ids.has(id)){item.line.remove();activeRows.delete(id)}
  for(const row of rows){
   let item=activeRows.get(row.requestId);
   if(!item){
    const line=document.createElement('div');line.className='data-active';
    const label=document.createElement('span'),cancel=document.createElement('button');cancel.type='button';cancel.dataset.cancelRequest=row.requestId;
    line.append(label,cancel);holder.append(line);item={line,label,cancel};activeRows.set(row.requestId,item);
   }
   item.label.textContent=`${names[row.metric]} · ${row.cancelRequested?'正在取消，等待释放':row.phase==='preparing'?'正在准备':`采集中，最多${row.seconds}秒`}`;
   item.cancel.textContent=`取消${names[row.metric]}`;item.cancel.disabled=row.cancelRequested||cancellingRequests.has(row.requestId);
  }
  renderButtons();
 }
 const pcStates={stopped:'电脑按需服务未运行',expired:'电脑按需服务已到期，请重新开启',exited:'电脑按需服务已退出，请重新开启',port_conflict:'另一个接口服务占用了端口，请先按原方式停止',ready:'电脑按需服务已就绪',starting:'电脑按需服务正在准备或退出',network_wait:'电脑按需服务等待原网卡的可用局域网'};
 function renderService(value){
  serviceSnapshot=value;serviceSnapshotAt=performance.now();
  byId('capabilityPcState').textContent=pcStates[value.state]||'电脑服务状态暂时无法确认';
  byId('capabilityPcTime').textContent=value.running?(value.durationApplied!==true?'正在核对当前电脑服务时长…':value.durationSeconds===0?'电脑本次不限时 · 重复启动不重新计时':`电脑本次约剩${Math.ceil(value.remainingSeconds/60)}分钟 · 重复启动不重新计时`):'';
  byId('capabilityStart').disabled=servicePending||value.running;
  byId('capabilityStop').disabled=servicePending||!value.running;
  if(!preferenceDirty)byId('capabilityAutoStart').checked=value.enabledOnStart;
  const incoming=value.durationSettings;
  const newer=!savedDuration||(typeof incoming?.savedMonotonic==='number'&&typeof savedDuration.savedMonotonic==='number'?incoming.savedMonotonic>=savedDuration.savedMonotonic:(incoming?.savedMs||0)>=(savedDuration.savedMs||0));
  if(incoming&&newer&&incoming.revision!==savedDuration?.revision){savedDuration=incoming;if(!durationDirty)showDuration(savedDuration)}
  renderDurationStatus(value);
 }
 function renderDurationStatus(value,elapsed=0){
  const incoming=value.durationSettings;
  if(value.sharedInteraction){
   const left=value.remainingSeconds;
   byId('capabilityLimitStatus').textContent=!value.running?`交互已关闭 · 本端下次开启：${savedDuration?.seconds===0?'不限时':`${(savedDuration?.seconds||0)/60} 分钟`}`:
    `当前交互：${left==null?'不限时':`剩余 ${Math.max(0,Math.ceil(left-elapsed))} 秒`} · ${value.syncStatus||'待确认'}`;
   return;
  }
  if(!value.running){byId('capabilityLimitStatus').textContent=savedDuration?`下次启动：${savedDuration.seconds===0?'不限时':`${savedDuration.seconds/60} 分钟`} · 当前电脑按需服务未运行`:'当前电脑按需服务未运行';return}
  if(value.durationApplied!==true||incoming?.revision!==savedDuration?.revision){byId('capabilityLimitStatus').textContent=value.durationSupported===false?'当前版本需重新启动电脑按需服务后才能应用时长设置。':'已保存，等待电脑服务应用…';return}
  const remaining=Math.max(0,Math.floor((value.remainingSeconds||0)-elapsed));
  byId('capabilityLimitStatus').textContent=value.durationSeconds===0?'当前电脑按需服务：不限时':`当前电脑按需服务：剩余 ${String(Math.floor(remaining/60)).padStart(2,'0')}:${String(remaining%60).padStart(2,'0')} · 预计 ${new Date(value.expiresAtMs).toLocaleString('zh-CN',{hour12:false})} 停止`;
 }
 function durationInputs(){const unlimited=byId('capabilityUnlimited').checked;byId('capabilityAmount').disabled=unlimited;byId('capabilityUnit').disabled=unlimited}
 function showDuration(value){
  byId('capabilityUnlimited').checked=value.seconds===0;
  if(value.seconds>0){const hours=value.seconds%3600===0;byId('capabilityUnit').value=hours?'hours':'minutes';byId('capabilityAmount').value=value.seconds/(hours?3600:60)}
  durationInputs();
 }
 async function serviceRequest(path,value,action){
  const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-HeartRadio-Action':action},body:JSON.stringify(value)});
  const result=await response.json();if(!response.ok)throw Error(result.error||'电脑服务操作失败');return result;
 }
 async function refreshService(){
  try{const response=await fetch('/api/capability-service',{cache:'no-store'});if(!response.ok)throw Error('电脑服务状态暂时无法确认');const value=await response.json();if(!servicePending)renderService(value)}
  catch(error){if(!servicePending)byId('capabilityPcState').textContent=error.message}
 }
 async function controlService(action){
  if(servicePending)return;servicePending=true;
  byId('capabilityStart').disabled=true;byId('capabilityStop').disabled=true;
  byId('capabilityMessage').textContent=action==='start'?'正在准备电脑按需服务…':'正在关闭电脑按需服务…';
  try{
   const value=await serviceRequest('/api/capability-service',{action},'capability-service');
   byId('capabilityMessage').textContent=action==='start'?(value.action==='reused'?'已复用电脑服务，原期限不变。':'电脑服务已就绪；请在手表开启待命通道。'):
    value.watchStopStatus==='stop_sent'?'电脑服务已停止，已向手表发送关闭待命请求；心率广播独立运行。':'电脑服务已停止；未确认手表收到关闭请求，请在手表关闭待命。不限时没有自动到期。';
  }catch(error){byId('capabilityMessage').textContent=error.message}
  finally{servicePending=false;await refreshService();await refreshCache()}
 }
 byId('capabilityStart').addEventListener('click',()=>controlService('start'));
 byId('capabilityStop').addEventListener('click',()=>controlService('stop'));
 byId('capabilityAutoStart').addEventListener('change',()=>{preferenceDirty=true});
 for(const event of ['input','change'])byId('capabilityDurationForm').addEventListener(event,()=>{durationDirty=true;durationRevision++;durationInputs()});
 byId('capabilityDurationForm').addEventListener('submit',async event=>{
  event.preventDefault();byId('capabilityDurationSave').disabled=true;const revision=durationRevision;
  try{
   savedDuration=await serviceRequest('/api/capability-duration',{unlimited:byId('capabilityUnlimited').checked,amount:Number(byId('capabilityAmount').value),unit:byId('capabilityUnit').value},'capability-duration');
   durationDirty=revision!==durationRevision;
   if(!durationDirty)showDuration(savedDuration);
   byId('capabilityDurationMessage').textContent=durationDirty?'刚才提交的时长已保存，后续修改尚未保存。':savedDuration.seconds===0?'已保存：不限时。':'已保存：限时从本次保存开始计算；下次启动按完整时长重新计时。';
   await refreshService();setTimeout(refreshService,350);
  }catch(error){byId('capabilityDurationMessage').textContent=error.message}finally{byId('capabilityDurationSave').disabled=false}
 });
 async function loadDuration(){
  try{const response=await fetch('/api/capability-duration',{cache:'no-store'});if(!response.ok)throw Error('读取电脑服务时长失败，请刷新页面重试');savedDuration=await response.json();if(!durationDirty)showDuration(savedDuration);byId('capabilityDurationSave').disabled=false;await refreshService()}
  catch(error){byId('capabilityDurationMessage').textContent=error.message}
 }
 byId('capabilitySettings').addEventListener('submit',async event=>{
  event.preventDefault();const button=event.currentTarget.querySelector('button');button.disabled=true;
  const choice=byId('capabilityAutoStart').checked;
  try{
   await serviceRequest('/api/capability-settings',{enabledOnStart:choice},'capability-settings');
   preferenceDirty=byId('capabilityAutoStart').checked!==choice;
   byId('capabilityMessage').textContent=choice?'已保存：下次01会准备按需服务。当前运行状态不变。':'已保存：下次01保留仅心率方式。当前运行状态不变。';
   await refreshService();
  }catch(error){byId('capabilityMessage').textContent=error.message}finally{button.disabled=false}
 });
 byId('dataSettingsForm').addEventListener('input',()=>{settingsDirty=true;settingsRevision++;byId('dataSettingsMessage').textContent='有未保存的修改。'});
 byId('dataSettingsForm').addEventListener('submit',async event=>{
  event.preventDefault();byId('dataSettingsSave').disabled=true;
  const revision=settingsRevision,value={};for(const [key,id] of Object.entries(fields))value[key]=Number(byId(id).value);
  value.allowedMetrics=[...document.querySelectorAll('[data-metric]:checked')].map(input=>input.dataset.metric);
  try{
   const result=await serviceRequest('/api/data-settings',value,'data-settings');settingsDirty=settingsRevision!==revision;renderSettings(result);
   byId('dataSettingsMessage').textContent=settingsDirty?'刚才提交的设置已保存，后续修改尚未保存。':'已保存，网页和Agent共用这些读取设置。当前采集不重新计时。';
   await refreshCache();
  }catch(error){byId('dataSettingsMessage').textContent=error.message}finally{byId('dataSettingsSave').disabled=false}
 });
 async function cancelRead(requestId){
  if(cancelPending||cancellingRequests.has(requestId)||!activeMeasurement)return;
  if(requestId)cancellingRequests.add(requestId);else cancelPending=true;
  renderButtons();
  try{
   const result=await serviceRequest('/api/tools',{name:'cancel_measurement',arguments:requestId?{request_id:requestId}:{}},'tools');
   // The measurement response can finish before the cancellation acknowledgement.
   // Preserve its final message instead of replacing it with a waiting message.
   await refreshCache();
   if(result.status!=='cancel_requested')byId('dataMessage').textContent=errors[result.status]||`取消未完成：${result.status}`;
   else byId('dataMessage').textContent=requestId?'已请求取消这一项，其余读取继续运行。':'已请求取消调用时正在进行的全部采集。';
  }catch(error){byId('dataMessage').textContent=error.message}finally{if(requestId)cancellingRequests.delete(requestId);else cancelPending=false;await refreshCache()}
 }
 byId('dataCancel').addEventListener('click',()=>cancelRead());
 byId('dataActiveRequests').addEventListener('click',event=>{const button=event.target.closest('[data-cancel-request]');if(button)cancelRead(button.dataset.cancelRequest)});
 function render(data){
  if(data.settings)renderSettings(data.settings);renderActive(data.measurement||null);
  const bridge=data.bridge||{}, report=bridge.watchLifecycle, elapsed=bridge.watchReportAgeSeconds;
  const left=report?.leaseRemainingMs!=null?Math.max(0,report.leaseRemainingMs/1000-(elapsed||0)):null;
  const sync=bridge.watchDurationSync;
  const syncText=sync?.requested?(sync.applied?' · 时长跟随电脑（已同步）':' · 等待手表确认电脑时长'):' · 手表独立计时';
  byId('dataConnection').textContent=bridge.connected?`手表待命已连接 · 本轮第${bridge.connectionSerial}次连接${report?.leaseUnlimited?' · 手表本次不限时':left!=null?` · 手表本次约剩${Math.ceil(left/60)}分钟`:''}${syncText}`:bridge.status==='bridge_unavailable'?'请先开启电脑按需服务，再在手表开启待命通道。':'电脑服务就绪，手表待命尚未连接；请在手表开启，已开启且尚未到期时会自动重试。';
  byId('dataConnection').textContent=bridge.automaticRouting?(bridge.connected?`当前使用${bridge.transport==='rfcomm'?'蓝牙':'Wi-Fi'} · Agent 交互${bridge.agentEnabled?'已开启':'已关闭'} · ${bridge.agentInteraction?.syncStatus||'等待确认'}`:'基础连接尚未建立；交互改动会保存待同步，心率仍独立控制。'):'当前仅查看本地记录；如需新设备读取，请到“总览与连接”开启连接并设置Agent交互。';
  const values=data.measurements||{}, battery=values.device_status, pressure=values.pressure;
  for(const metric of ['heart_rate','device_status','pressure','accelerometer','gyroscope']){
   const saved=values[metric==='heart_rate'?'heart_rate_once':metric];
   if(saved&&!pendingMetrics.has(metric)&&resultTimes.get(metric)!==saved.receivedAtMs){
    byId('dataResult_'+metric).textContent=saved.ok?(metric==='device_status'?'最近一次状态读取成功。':'最近一次采集已完成。'):(errors[saved.status]||`最近一次没有可用数据：${saved.status}`);
    byId('dataResult_'+metric).dataset.tone=saved.ok?'success':'error';
    resultTimes.set(metric,saved.receivedAtMs);
   }
  }
  const once=values.heart_rate_once;
  byId('dataOnceHeart').textContent=once?.ok&&once.value!=null?Math.round(once.value):'—';
  byId('dataOnceHeartAge').textContent=once?`${age(once)} · ${once.ok?'按需测量结果，广播开关独立':errors[once.status]||once.status}`:'尚未测量';
  byId('dataBattery').textContent=battery?.ok?`${battery.data.batteryPct??'—'}% · ${battery.data.plugged>0?'已接电源':'未充电'}`:'—';
  byId('dataBatteryAge').textContent=age(battery);
  byId('dataPressure').textContent=pressure?.ok&&pressure.value!=null?pressure.value.toFixed(2):'—';
  byId('dataPressureAge').textContent=age(pressure);
  for(const [metric,prefix] of [['accelerometer','dataAccelerometer'],['gyroscope','dataGyroscope']]){
   const motion=values[metric],axes=motion?.ok?motion.samples?.at(-1)?.values:null;
   byId(prefix).textContent=motion?.ok?`${motion.callbacks}次回调`:'—';
   byId(prefix+'Values').textContent=axes?`X / Y / Z：${axes.map(value=>value.toFixed(3)).join(' / ')} ${motion.unit}`:'手表 X / Y / Z 轴';
   byId(prefix+'Age').textContent=motion?`${age(motion)} · 保存${motion.retainedSamples??0}条样本`:'尚未采集';
  }
 }
 async function refreshCache(){try{const response=await fetch('/api/data',{cache:'no-store'});if(response.ok)render(await response.json())}catch{}}
 function pollWhileReading(){
  if(readPollTimer!==null)return;
  const poll=async()=>{if(window.heartRadioStopped)return;await refreshCache();readPollTimer=!window.heartRadioStopped&&pendingMetrics.size?setTimeout(poll,500):null};
  readPollTimer=setTimeout(poll,200);
 }
 async function refresh(){if(window.heartRadioStopped)return;try{await Promise.all([refreshCache(),refreshService()])}finally{if(!window.heartRadioStopped)setTimeout(refresh,5000)}}
 for(const button of document.querySelectorAll('[data-tool]'))button.addEventListener('click',async()=>{
  const name=button.dataset.tool,metric=button.dataset.kind||(name==='measure_heart_rate'?'heart_rate':name==='measure_pressure'?'pressure':'device_status');
  if(pendingMetrics.has(metric))return;
  pendingMetrics.add(metric);renderButtons();
  pollWhileReading();
  const argumentsValue=byId('dataFresh').checked&&name!=='measure_heart_rate'?{fresh:true}:{};
  if(button.dataset.kind)argumentsValue.kind=button.dataset.kind;
  byId('dataResult_'+metric).textContent=name==='get_device_status'?'正在查询…':name==='measure_heart_rate'?'正在读取，最多15秒；其他按钮仍可使用…':`正在采集${lastSettings.measurementSeconds}秒…`;
  byId('dataResult_'+metric).dataset.tone='progress';
  try{
   const response=await fetch('/api/tools',{method:'POST',headers:{'Content-Type':'application/json','X-HeartRadio-Action':'tools'},body:JSON.stringify({name,arguments:argumentsValue})});
   const result=await response.json();if(!response.ok)throw Error(result.error||'本地请求失败');
   byId('dataResult_'+metric).textContent=result.ok?(result.cached?`复用了约${Math.floor(result.cacheAgeSeconds)}秒前的结果。`:result.sharedInFlight?'共用了正在进行的采集，已完成。':name==='get_device_status'?'状态已读取；电池温度不是体温。':'采集完成，已释放本项监听。'):(errors[result.status]||`没有可用新数据：${result.status}`)+(result.retryAfterSeconds?` 本项约${result.retryAfterSeconds}秒后可重试。`:'');
   byId('dataResult_'+metric).dataset.tone=result.ok?'success':'error';
   const cache=await fetch('/api/data',{cache:'no-store'});if(cache.ok)render(await cache.json());
  }catch(error){byId('dataResult_'+metric).textContent=error.message;byId('dataResult_'+metric).dataset.tone='error'}
  finally{pendingMetrics.delete(metric);renderButtons()}
 });
 const durationTimer=setInterval(()=>{if(serviceSnapshot)renderDurationStatus(serviceSnapshot,(performance.now()-serviceSnapshotAt)/1000)},1000);
 window.addEventListener('heartradio-session-stopped',()=>{clearInterval(durationTimer);clearTimeout(readPollTimer)});
 loadDuration();refresh();
})();
