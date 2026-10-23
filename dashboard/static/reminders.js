'use strict';
(() => {
 const el=id=>document.getElementById(id);
 let templates={},lastId=null,busy=false,stickyMessage=false,canSend=false;
 let limits=null,policyDirty=false,policyBusy=false,policyEpoch=0;
 const vibrationFields=['reminderPulse','reminderCount','reminderGap','reminderAmplitude'];
 const soundReasons={not_requested:'本条未请求声音',system_mode_restricted:'静音、勿扰或系统模式限制',audio_state_unknown:'无法确认系统音频状态',notification_volume_muted:'通知音量为零或已静音',app_notifications_disabled:'系统已关闭本应用通知',sound_channel_muted:'系统已关闭该声音渠道',system_default_tone_silent:'系统默认提示音设为无声',sound_channel_unavailable:'声音渠道不可用',sound_setup_error:'系统声音准备失败',audio_focus_denied:'系统未允许占用声音输出',audio_focus_lost:'声音输出被其他应用接管',reminder_no_longer_active:'提醒已结束',prepare_timeout:'声音准备超时',duration_limit:'已达到3秒上限',effects_cancelled:'提醒效果已取消',acknowledged:'本人已确认',expired:'提醒已过期',superseded:'被新提醒替换'};
 const messages={issued:'已下发提醒指令；页面显示与本人确认以回执为准。',notification_only_dnd:'已保存通知；未执行振动或自动亮屏，详情见模式回执。',
  notification_only_system_restricted:'已保存通知；系统限制了振动和自动亮屏，详情见模式回执。',
   deleted:'手表已删除这条提醒；电脑仍保留原始发送记录。',displayed:'手表已报告页面显示，尚未点“知道了”。',acknowledged:'已收到“知道了”的确认回执。',cancelled:'交互关闭，提醒效果已取消。',expired:'提醒已过期。',
  interaction_disabled:'Agent 交互已关闭，可在任一端开启。',interaction_sync_pending:'交互设置尚待对端确认。',watch_offline:'手表尚未连接。',
  outcome_unknown_timeout:'发送结果不确定，未自动重发。可以核对回执。',outcome_unknown_disconnected:'连接中断，发送结果不确定，未自动重发。',
  outcome_unknown_after_restart:'上次发送未完成记录，结果不确定，未自动重发。',rate_limited:'提醒过于频繁，请稍后发送。',busy:'上一组振动尚未结束。',
  reminder_id_conflict:'提醒编号已经用于其他内容。',reminder_storage_error:'手表提醒记录保存异常，未确认执行。',delivery_error:'提醒未完整执行，请查看回执。',
  reminder_policy_denied:'提醒超过你保存的效果限制，未执行。请查看限制并调整本条参数。',reminder_policy_sync_pending:'提醒限制尚未得到手表确认，请等待同步。',reminder_policy_storage_error:'提醒设置存储异常，已暂停新提醒。'};
 function soundSummary(r){
  const reason=soundReasons[r.soundSuppressedReason||r.soundStopReason]||r.soundSuppressedReason||r.soundStopReason;
  if(r.soundStatus==='failed')return `播放失败${reason?'：'+reason:''}`;
  if(r.soundStatus==='stopped')return `已停止${reason?'：'+reason:''}`;
  if(r.soundCompletedAtMs)return '系统报告播放完成（实际听到需本人确认）';
  if(r.soundStartedAtMs)return '播放器已启动（实际听到需本人确认）';
  if(r.soundIssued)return '已提交系统播放（实际听到需本人确认）';
  if(r.soundStatus==='preparing')return '正在准备系统提示音';
  if(r.soundRequested)return reason||'尚未确认启动';
  return r.soundStatus==='not_requested'?'本条未请求声音':'旧版未提供声音回执';
 }
 function render(row){
  if(!row)return;
  lastId=row.reminderId;el('reminderRefresh').disabled=busy||!lastId;
  const receipt=row.receipt||{},v=receipt.appliedVibration,p=receipt.systemAlertPolicy;
  const message=row.status==='notification_only_dnd'?p?.mode==='do_not_disturb'?'已保存通知，勿扰模式下未振动或自动亮屏。':'已保存通知；旧版回执仅记录系统过滤，未区分静音和勿扰。':messages[row.status]||`状态 ${row.status}`;
  if(!stickyMessage)el('reminderMessage').textContent='最近一次提醒：'+message;
  el('reminderReceipt').textContent=[`编号：${lastId}`,
   `发送时间：${row.createdAtMs?new Date(row.createdAtMs).toLocaleString('zh-CN',{hour12:false}):'—'}`,
   `标题：${row.params?.title||receipt.title||'—'}`,
    `手表删除：${receipt.deletedAtMs?new Date(receipt.deletedAtMs).toLocaleString('zh-CN',{hour12:false}):row.status==='deleted'?'已删除':'尚无删除回执'}`,
   `振动指令：${row.params?.vibrate===false?'本条不振动':receipt.vibrationIssued?'已下发':'未确认下发'}`+(v?` · ${v.count}次 × ${v.pulseMs}毫秒 · 间隔${v.gapMs}毫秒 · 实际振幅${v.amplitude??'系统默认'}`:''),
   `声音：${soundSummary(receipt)}${receipt.soundRequested?' · 系统提示音':''}`,
   `亮屏页面：${receipt.screenLaunchRequested?'已请求':'未请求或未确认'}`,
   `页面显示回执：${receipt.displayedAtMs?'已收到':'未收到'} · 本人确认：${receipt.acknowledgedAtMs?'已点知道了':'未收到'}`,
   p?`手表模式：${p.modeLabel} · 系统对振动/亮屏：${p.effectsAllowed?'允许，仍遵守本条设置':'已限制'}`:receipt.interruptionFilter!=null?'手表模式：旧版回执，未区分静音与勿扰':'手表模式：尚无设备回执',
   p?.silentCompatibility?'静音兼容：已启用，所选振动和亮屏可执行，声音保持关闭。':p?.mode==='silent'?'静音模式：所选振动和亮屏可执行，声音保持关闭。':p?.mode==='do_not_disturb'?'勿扰模式：本条不振动、不自动亮屏。':p?.soundImplemented?'声音遵守系统通知音量与模式。':'声音提示：旧版未启用。',
   `勿扰限制：${p?(receipt.suppressedByDnd?'是':'否'):receipt.interruptionFilter!=null?'旧版未区分':'尚无回执'} · 锁屏限制：${receipt.screenSuppressedByLock?'是':'否或尚未取得回执'}`,
   row.receiptAgeSeconds!=null?`最近回执在${Math.floor(row.receiptAgeSeconds)}秒前收到`:'尚无设备回执'].join('\n');
 }
 function templateEffects(){const name=el('reminderTemplate').value;return {vibrate:!['silent','notification_only'].includes(name),screen:name!=='notification_only',sound:false}}
 function validateRequest(){
  el('reminderCount').setCustomValidity('');
  const vibrating=!el('reminderVibrate').disabled&&el('reminderVibrate').checked;
  const errors=[];
  for(const id of [...vibrationFields,'reminderScreenSeconds','reminderTtl']){
   const input=el(id);if(!input.disabled&&!input.validity.valid)errors.push(`${input.parentElement.firstChild.textContent.trim()}：请填写范围内的整数。`);
  }
  if(vibrating&&vibrationFields.every(id=>el(id).validity.valid)){
   const total=Number(el('reminderPulse').value)*Number(el('reminderCount').value)+Number(el('reminderGap').value)*(Number(el('reminderCount').value)-1);
   const max=limits?.maxVibrationMs??6000;
   el('reminderPatternStatus').textContent=`本组振动共${total}毫秒（${total/1000}秒），你保存的上限为${max/1000}秒。`;
   if(total>max){const error=`整组振动${total/1000}秒超过已保存上限${max/1000}秒，请减少时长、次数或间隔。`;el('reminderCount').setCustomValidity(error);errors.push(error)}
  }else el('reminderPatternStatus').textContent=vibrating?'填写完整且符合范围后，将显示整组振动时长。':'本条不振动，振动参数不参与校验或发送。';
  el('reminderValidation').textContent=errors.join(' ');
 }
 function updateInputs(){
  for(const id of vibrationFields)el(id).disabled=el('reminderVibrate').disabled||!el('reminderVibrate').checked;
  el('reminderScreenSeconds').disabled=el('reminderScreen').disabled||!el('reminderScreen').checked;
  validateRequest();
 }
 function updatePolicyInputs(){
  el('policyVibrationSeconds').disabled=!el('policyVibrate').checked;
  el('policyScreenSeconds').disabled=!el('policyWake').checked;
  el('policyValidation').textContent=['policyVibrationSeconds','policyScreenSeconds'].filter(key=>!el(key).disabled&&!el(key).validity.valid).map(key=>key==='policyVibrationSeconds'?'整组上限须为1～10秒，以0.1秒调整。':'页面上限须为5～30的整数秒。').join(' ');
 }
 function applyPolicy(value){
  if(!value?.limits)return;
  const old=limits;limits=value.limits;
  const defaults=templateEffects();
  for(const [id,key,effect] of [['reminderVibrate','vibrationEnabled','vibrate'],['reminderScreen','wakeEnabled','screen'],['reminderSound','soundEnabled','sound']]){
   const input=el(id);input.disabled=!limits[key];
   if(!limits[key])input.checked=false;
   else if(!old||old[key]!==limits[key])input.checked=defaults[effect];
  }
  el('reminderScreenSeconds').max=String(limits.maxScreenSeconds);
  if((!old||old.maxScreenSeconds!==limits.maxScreenSeconds)&&Number(el('reminderScreenSeconds').value)>limits.maxScreenSeconds)el('reminderScreenSeconds').value=String(limits.maxScreenSeconds);
  el('reminderScreenRange').textContent=(limits.maxScreenSeconds===5?'当前只能设5秒。':`当前可设5～${limits.maxScreenSeconds}秒。`)+'默认15秒，上限更小时随之降低。';
  el('reminderEffects').textContent=[limits.vibrationEnabled?`允许振动，整组最多${limits.maxVibrationMs/1000}秒`:'你已禁止振动，本条已取消勾选',limits.wakeEnabled?`允许亮屏，页面最多${limits.maxScreenSeconds}秒`:'你已禁止自动亮屏，本条已取消勾选',limits.soundEnabled?'允许系统提示音，须勾选本条声音':'你已禁止声音，本条已取消勾选'].join('；')+'。';
  el('policyStatus').textContent=value.storageError||value.syncStatus||'等待同步';
  if(!policyDirty&&!policyBusy){
   el('policyVibrate').checked=limits.vibrationEnabled;el('policyWake').checked=limits.wakeEnabled;el('policySound').checked=limits.soundEnabled===true;
   el('policyVibrationSeconds').value=limits.maxVibrationMs/1000;el('policyScreenSeconds').value=limits.maxScreenSeconds;
   updatePolicyInputs();
  }
  updateInputs();
 }
 function useTemplate(){const t=templates[el('reminderTemplate').value];if(!t)return;
  el('reminderVibrate').checked=t.vibrate&&limits?.vibrationEnabled===true;el('reminderScreen').checked=t.screen&&limits?.wakeEnabled===true;
  el('reminderSound').checked=t.sound===true&&limits?.soundEnabled===true;
  for(const [id,key] of [['reminderPulse','pulseMs'],['reminderCount','count'],['reminderGap','gapMs'],['reminderAmplitude','amplitude']])el(id).value=t.vibration[key];
  updateInputs();
 }
 for(const [id,limit] of [['reminderHeading',60],['reminderBody',500]])el(id).addEventListener('input',()=>el(id).setCustomValidity([...el(id).value].length>limit?`最多输入${limit}字` : ''));
 async function post(value){
  const response=await fetch('/api/reminders',{method:'POST',headers:{'Content-Type':'application/json','X-HeartRadio-Action':'reminders'},body:JSON.stringify(value)});
  const result=await response.json();if(!response.ok)throw Error(result.error||'提醒操作失败');return result;
 }
 async function readiness(){
  const epoch=policyEpoch;
  const response=await fetch('/api/connection',{cache:'no-store'});if(!response.ok)throw Error('无法读取连接状态，请确认电脑服务已启动。');
  const s=await response.json();
  if(!s.reminderPolicy){
   const response=await fetch('/api/reminder-policy',{cache:'no-store'});if(!response.ok)throw Error('无法读取已保存的提醒限制。');
   s.reminderPolicy=await response.json();
  }
  if(epoch!==policyEpoch)return '提醒限制已变化，请等待当前设置确认。';
  if(!policyBusy)applyPolicy(s.reminderPolicy);
  const reason=policyBusy?'正在保存提醒限制，请稍候。':!s.connected?'手表尚未连接，请先在电脑和手表开启连接。':!s.agentEnabled?'Agent 交互已关闭，请在任一端开启。':!s.agentInteraction?.peerConfirmed?'Agent 交互尚待手表确认，请等待同步。':s.reminderPolicy?.storageError?'提醒限制保存异常，请先修复设置。':!s.reminderPolicy?.peerConfirmed?'提醒限制尚待手表确认，请等待同步。':'';
  canSend=!reason;el('reminderReady').textContent=reason||'连接、交互和提醒限制已确认，可以发送。';el('reminderReady').dataset.ready=String(canSend);
  el('reminderSend').disabled=busy||!canSend;return reason;
 }
 async function load(){try{
  await readiness();
  const response=await fetch('/api/reminders',{cache:'no-store'});if(!response.ok)return;
  const value=await response.json();templates=value.templates||{};
  if(value.storageError)el('reminderMessage').textContent=value.storageError;
  const row=lastId?value.reminders.find(x=>x.reminderId===lastId):value.reminders[0];if(!busy&&row)render(row);
 }catch(e){canSend=false;el('reminderReady').textContent=e.message;el('reminderReady').dataset.ready='false';el('reminderSend').disabled=true}}
 el('reminderTemplate').addEventListener('change',useTemplate);
 for(const id of ['reminderVibrate','reminderScreen','reminderSound'])el(id).addEventListener('change',updateInputs);
 for(const id of [...vibrationFields,'reminderScreenSeconds','reminderTtl'])el(id).addEventListener('input',validateRequest);
 el('reminderForm').addEventListener('submit',async event=>{
  event.preventDefault();if(busy)return;busy=true;el('reminderSend').disabled=true;el('reminderRefresh').disabled=true;
  try{
   const reason=await readiness();if(reason)throw Error(reason);
   updateInputs();if(!el('reminderForm').reportValidity())return;
   const reminder={title:el('reminderHeading').value,body:el('reminderBody').value,template:el('reminderTemplate').value,
    sound:!el('reminderSound').disabled&&el('reminderSound').checked,soundSource:'system',
    vibrate:!el('reminderVibrate').disabled&&el('reminderVibrate').checked,screen:!el('reminderScreen').disabled&&el('reminderScreen').checked,ttlMs:Number(el('reminderTtl').value)*1000};
   if(reminder.vibrate)reminder.vibration={pulseMs:Number(el('reminderPulse').value),count:Number(el('reminderCount').value),gapMs:Number(el('reminderGap').value),amplitude:Number(el('reminderAmplitude').value)};
   if(reminder.screen)reminder.screenSeconds=Number(el('reminderScreenSeconds').value);
   reminder.reminderId=crypto.randomUUID().replaceAll('-','');lastId=reminder.reminderId;
   stickyMessage=false;render(await post({action:'send',reminder}));
  }catch(e){stickyMessage=true;el('reminderMessage').textContent=e.message}
  finally{busy=false;el('reminderSend').disabled=!canSend;el('reminderRefresh').disabled=!lastId}
 });
 el('reminderRefresh').addEventListener('click',async()=>{
  if(busy||!lastId)return;busy=true;el('reminderRefresh').disabled=true;
  try{stickyMessage=false;render(await post({action:'status',reminderId:lastId,fresh:true}))}catch(e){stickyMessage=true;el('reminderMessage').textContent=e.message}
  finally{busy=false;el('reminderRefresh').disabled=!lastId}
 });
 for(const id of ['policyVibrate','policyWake','policySound','policyVibrationSeconds','policyScreenSeconds'])el(id).addEventListener('input',()=>{
  policyDirty=true;updatePolicyInputs();
 });
 el('reminderPolicyForm').addEventListener('submit',async event=>{
  event.preventDefault();if(policyBusy)return;policyBusy=true;policyEpoch++;canSend=false;el('policySave').disabled=true;el('reminderSend').disabled=true;
  el('reminderReady').textContent='正在保存提醒限制，请稍候。';el('reminderReady').dataset.ready='false';
  const settings={vibrationEnabled:el('policyVibrate').checked,wakeEnabled:el('policyWake').checked,soundEnabled:el('policySound').checked,
   maxVibrationMs:el('policyVibrate').checked?Math.round(Number(el('policyVibrationSeconds').value)*1000):(limits?.maxVibrationMs??6000),
   maxScreenSeconds:el('policyWake').checked?Number(el('policyScreenSeconds').value):(limits?.maxScreenSeconds??30)};
  try{
   const r=await fetch('/api/reminder-policy',{method:'POST',headers:{'Content-Type':'application/json','X-HeartRadio-Action':'reminder-policy'},body:JSON.stringify({settings})});
   const value=await r.json();if(!r.ok)throw Error(value.error||'保存失败');policyDirty=false;policyBusy=false;applyPolicy(value);
  }catch(error){el('policyStatus').textContent=error.message}finally{policyEpoch++;policyBusy=false;el('policySave').disabled=false;await load()}
 });
 for(const id of ['reminderVibrate','reminderScreen','reminderSound'])el(id).disabled=true;
 updateInputs();load();const loadTimer=setInterval(()=>{if(!window.heartRadioStopped)load()},2000);
 window.addEventListener('heartradio-session-stopped',()=>clearInterval(loadTimer));
})();
