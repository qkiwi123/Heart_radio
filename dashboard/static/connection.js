'use strict';
(() => {
 const el=id=>document.getElementById(id);
 let busy=false,dirty=false,unavailable=false;
 const refreshTimer=setInterval(()=>{if(!window.heartRadioStopped)refresh()},1500);
 function badge(id,label,tone){const node=el(id);node.textContent=label;node.className='badge'+(tone?' '+tone:'')}
 function showHostConnection(host){
  const network=host?.network;
  let label='暂无法读取';
  if(network?.state==='connected'){
   const medium={wifi:'Wi-Fi',ethernet:'有线网络'}[network.kind];
   label=[medium,network.name||'名称未提供'].filter(Boolean).join(' · ');
  }else if(network?.state==='disconnected')label='未连接';
  else if(network?.state==='loading')label='读取中…';
  el('hostNetworkName').textContent=label;
  const radios={on:'已开启',off:'已关闭',disabled:'已禁用',unavailable:'未检测到蓝牙无线电',loading:'读取中…'};
  el('hostBluetoothState').textContent=radios[host?.bluetooth]||'暂无法读取';
 }
 async function readConnection(){
  try{
   const r=await fetch('/api/connection',{cache:'no-store'});if(!r.ok)throw Error();
   return await r.json();
  }catch{throw Error('电脑服务暂不可用。请用 01 启动 HeartRadio，再点开启连接。')}
 }
 el('connectionMode').addEventListener('change',()=>dirty=true);
 async function refresh(){
  try{
   const s=await readConnection();unavailable=false;
   showHostConnection(s.hostConnectivity);
   if(s.projectSession?.shutdownPending){
    showHostConnection(null);
    window.heartRadioStopped=true;clearInterval(refreshTimer);
    el('stageConnectionState').textContent='未连接满 10 分钟，电脑服务已退出或正在退出。请用 01 重新启动。';
    el('stageConnectionMessage').textContent='重新启动电脑服务后，可在这里检查并开启连接。';
    badge('connectionBadge','超时已停止','');
    badge('heartBadge','已暂停','');badge('agentBadge','已暂停','');
    el('stageHeartState').textContent=`广播偏好保留为${s.heartBroadcast?.desiredEnabled?'开启':'关闭'}；电脑接收已停止，手表状态离线待确认。`;
    el('stageAgentState').textContent='本次交互：已暂停';
    for(const button of document.querySelectorAll('button'))button.disabled=true;
    el('stageConnect').textContent='检查并开启连接';el('stageConnect').disabled=busy;
    window.dispatchEvent(new Event('heartradio-session-stopped'));return;
   }
   document.querySelector('.eyebrow').textContent=`OPPO WATCH 2 / ${s.transport==='rfcomm'?'蓝牙':s.transport==='wifi'?'WI-FI':'等待连接'}`;
   document.querySelector('.data-access > .capability-controls').hidden=true;
   if(!dirty)el('connectionMode').value=s.configuredMode||'auto_bluetooth';
   const suspended=s.projectSession?.suspended===true;
   el('stageConnectionState').textContent=(suspended?'电脑服务已启动 · 请手动开启连接':s.connected?(s.status||'已连接'):s.connectionRunning?`等待手表连接，正在自动重试 · 连续未连接10分钟后退出全部服务${s.projectSession?.remainingSeconds!=null?'（约剩'+Math.ceil(s.projectSession.remainingSeconds)+'秒）':''}`:'连接已关闭')+(s.backingError?` · ${s.backingError}`:'');
   badge('connectionBadge',s.connected?'已连接':s.connectionRunning?'重连中':'已关闭',s.connected?'live':s.connectionRunning?'waiting':'');
   el('stageConnect').textContent='开启连接';el('stageConnect').disabled=busy||s.connectionRunning;el('stageDisconnect').disabled=busy||!s.connectionRunning;
   const heart=s.heartBroadcast||{};
   badge('heartBadge',heart.pending?'待同步':heart.enabled?'已开启':'已关闭',heart.pending?'waiting':heart.enabled?'live':'');
   el('stageHeartToggle').setAttribute('aria-pressed',String(!!heart.enabled));
   el('stageHeartState').textContent=`心率广播：${heart.enabled?'已开启':'已关闭'} · ${heart.syncStatus||'等待载入'} · 手表采集：${heart.running===true?'运行中':heart.running===false?'已停止':'待确认'} · 电脑接收：${s.heartReceiveEnabled===false?'已暂停':'已准备'}`;
   el('stageHeartToggle').textContent=heart.storageError?'重试关闭心率广播':heart.enabled?'关闭心率广播':'开启心率广播';
   el('stageHeartToggle').dataset.action=heart.storageError?'heart_broadcast_off':heart.enabled?'heart_broadcast_off':'heart_broadcast_on';
   el('stageHeartToggle').disabled=busy||suspended;
   const gate=s.agentInteraction||{};
   badge('agentBadge',gate.pending?'待同步':s.agentEnabled?'已开启':'已关闭',gate.pending?'waiting':s.agentEnabled?'live':'');
   el('stageAgentToggle').setAttribute('aria-pressed',String(!!s.agentEnabled));
   el('stageAgentState').textContent=`${agentDurationText(s.agentEnabled,s.agentRemainingSeconds)} · ${gate.syncStatus||'待确认'}${gate.syncError?`（${gate.syncError}）`:''}`;
   el('stageAgentToggle').textContent=gate.storageError?'重试关闭交互':s.agentEnabled?'关闭 Agent 交互':'开启 Agent 交互';
   el('stageAgentToggle').dataset.action=gate.storageError?'agent_off':s.agentEnabled?'agent_off':'agent_on';
   el('stageReceiveToggle').textContent=s.heartReceiveEnabled===false?'仅恢复电脑心率接收':'仅暂停电脑心率接收';
   el('stageReceiveToggle').dataset.action=s.heartReceiveEnabled===false?'heart_receive_on':'heart_receive_off';
   el('stageAgentToggle').disabled=busy||suspended;el('stageReceiveToggle').disabled=busy||suspended||s.automaticRouting!==true;
  }catch(e){
   unavailable=true;el('stageConnectionState').textContent=e.message;
   showHostConnection(null);
   for(const id of ['connectionBadge','heartBadge','agentBadge'])badge(id,'待确认','waiting');
   for(const id of ['stageDisconnect','stageHeartToggle','stageAgentToggle','stageReceiveToggle'])el(id).disabled=true;
   el('stageConnect').textContent='检查并开启连接';el('stageConnect').disabled=busy;
  }
 }
 async function act(value){
  if(busy)return;busy=true;
  for(const button of document.querySelectorAll('[data-connection-action],#connectionModeForm button'))button.disabled=true;
  el('stageConnectionMessage').textContent='正在应用设置…';
  try{
   if(value.action==='start'&&(window.heartRadioStopped||unavailable)){
    const state=await readConnection();
    if(state.projectSession?.shutdownPending)throw Error('电脑服务正在退出，请用 01 重新启动后再试。');
    if(state.connectionRunning){window.location.reload();return}
   }
   const r=await fetch('/api/connection',{method:'POST',headers:{'Content-Type':'application/json','X-HeartRadio-Action':'connection'},body:JSON.stringify(value)});
   const s=await r.json();if(!r.ok)throw Error(s.error||'操作失败');
   if(window.heartRadioStopped){window.location.reload();return}
   if(value.action==='start'){
    el('stageConnectionMessage').textContent=s.connected?'手表已连接。':'电脑已开始连接手表，请确认手表端连接也已开启。';return;
   }
   const shared=value.action.startsWith('heart_broadcast_')?s.heartBroadcast:s.agentInteraction;
   el('stageConnectionMessage').textContent=shared?.pending?'本端已保存，等待另一端确认。连接关闭时不会为同步自动重连。':'设置已应用。心率广播与 Agent 交互各自在两端同步，两个业务开关互不联动。';
   if(value.action==='set_mode')dirty=false;
  }catch(e){el('stageConnectionMessage').textContent=e.message}
  finally{
   busy=false;
   if(window.heartRadioStopped)el('stageConnect').disabled=false;
   else{el('connectionModeForm').querySelector('button').disabled=false;await refresh()}
  }
 }
 for(const b of document.querySelectorAll('[data-connection-action]'))b.addEventListener('click',()=>act({action:b.dataset.action||b.dataset.connectionAction}));
 el('connectionModeForm').addEventListener('submit',e=>{e.preventDefault();act({action:'set_mode',mode:el('connectionMode').value})});
 refresh();
})();
