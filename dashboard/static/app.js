'use strict';
const $=id=>document.getElementById(id);let windowMode='10',lastState=null;
const clock=ms=>new Date(ms).toLocaleTimeString('zh-CN',{hour12:false});
const age=v=>v==null?'—':v<1?'刚刚':`${Math.floor(v)} 秒前`;
const duration=s=>{s=Math.max(0,Math.floor(s));return `${Math.floor(s/60).toString().padStart(2,'0')}:${(s%60).toString().padStart(2,'0')}`};
function text(id,value){$(id).textContent=value}
function svg(name,attrs={},content=''){const e=document.createElementNS('http://www.w3.org/2000/svg',name);for(const[k,v]of Object.entries(attrs))e.setAttribute(k,v);e.textContent=content;return e}
function chart(state){
 const root=$('chart');root.replaceChildren();const now=state.endedMs??state.nowMs;
 const all=state.points||[];const start=windowMode==='10'?now-600000:(all[0]?.timeMs??now-600000);
 const end=Math.max(now,start+60000);const points=all.filter(p=>p.timeMs>=start);
 const width=Math.max(280,root.clientWidth),left=36,right=width-8;root.setAttribute('viewBox',`0 0 ${width} 235`);
 $('emptyChart').hidden=points.length>0;
 const values=points.map(p=>p.bpm);let min=values.length?Math.floor((Math.min(...values)-8)/10)*10:50,max=values.length?Math.ceil((Math.max(...values)+8)/10)*10:110;
 if(max-min<30){min-=10;max+=10}const x=t=>left+(t-start)/(end-start)*(right-left),y=v=>190-(v-min)/(max-min)*164;
 for(let i=0;i<=4;i++){const yy=26+i*41,val=max-(max-min)*i/4;root.append(svg('line',{x1:left,x2:right,y1:yy,y2:yy,class:'gridline'}),svg('text',{x:left-8,y:yy+3,'text-anchor':'end'},Math.round(val)))}
 const ticks=width<500?2:4;for(let i=0;i<=ticks;i++){const t=start+(end-start)*i/ticks;root.append(svg('text',{x:x(t),y:219,'text-anchor':i===0?'start':i===ticks?'end':'middle'},clock(t).slice(0,5)))}
 let segments=[],segment=[],previous=null;for(const p of points){if(previous&&(p.segment!==previous.segment||p.timeMs-previous.timeMs>30000)){segments.push(segment);segment=[]}segment.push(p);previous=p}if(segment.length)segments.push(segment);
 for(const group of segments){if(group.length>1)root.append(svg('polyline',{points:group.map(p=>`${x(p.timeMs)},${y(p.bpm)}`).join(' '),class:'trend'}));for(const p of group){const dot=svg('circle',{cx:x(p.timeMs),cy:y(p.bpm),r:2.6,class:'point'});dot.append(svg('title',{},`${clock(p.timeMs)} 收到 · ${p.bpm} bpm\n手表近似时间 ${clock(p.measurementMs)}`));root.append(dot)}}
 text('chartNote',`电脑接收时间 · 当前视图 ${points.length} 条有效事件`);
}
function render(s){lastState=s;renderWidgets(s.widgets);renderReceiverLimit(s);text('clock',clock(s.nowMs));text('bpm',s.valid?Math.round(s.bpm):'—');text('status',s.status);
 document.querySelector('.current').classList.toggle('unavailable',!s.valid);text('liveBadge',s.valid?'持续更新':s.connected?'已连接':'未连接');$('liveBadge').classList.toggle('live',s.valid);
 text('age',s.eventAgeSeconds==null?'尚无心率事件':`上次心率事件：${age(s.eventAgeSeconds)}`);text('accuracy',`精度状态：${({'-1':'无接触',0:'不可靠',1:'低',2:'中',3:'高'})[s.accuracy]??'—'}`);
 const obs=s.observation;const elapsed=s.elapsedSeconds??0;text('duration',duration(elapsed));$('testProgress').hidden=!obs;text('timerLabel',obs?'自然使用计时':'接收运行时长');text('timerStatus',s.receiverStopped?'已结束':'已运行');
 if(obs){const remain=obs.plannedSeconds-elapsed;$('progressFill').style.width=`${Math.min(100,Math.max(0,elapsed/obs.plannedSeconds*100))}%`;text('remaining',remain>0?`剩余 ${duration(remain)}`:'计划时间已到')}
 text('validCount',s.validCount.toLocaleString());text('range',s.minimum==null?'—':`${Math.round(s.minimum)}–${Math.round(s.maximum)}`);
 $('connectionDot').classList.toggle('live',s.connected);text('link',s.connected?'Wi-Fi · 已收到数据':s.receiverStopped?'接收已结束':'等待数据');text('packetAge',age(s.packetAgeSeconds));text('packets',`${s.packetCount.toLocaleString()} / ${s.sequenceGaps}`);text('connections',`${s.connections} / ${s.disconnects}`);
 text('network',s.host?`${s.watchAddress||'等待已配对手表'} → ${s.host} · 加密传输`:'等待局域网地址或接收器启动');text('runId',s.runId??'尚无会话');text('serviceNote',s.parseErrors?`有 ${s.parseErrors} 行日志未解析`:'每秒读取本地记录');if(!$('liveView').hidden)chart(s);
}
async function poll(){try{const response=await fetch('/api/state',{cache:'no-store'});if(!response.ok)throw Error('读取失败');render(await response.json())}catch{document.querySelector('.current').classList.add('unavailable');text('bpm','—');text('status','本地仪表盘服务暂不可用');text('liveBadge','未连接');$('liveBadge').classList.remove('live');$('connectionDot').classList.remove('live');text('serviceNote','等待本地服务恢复')}finally{setTimeout(poll,1000)}}
for(const[button,mode]of [['window10','10'],['windowAll','all']])$(button).addEventListener('click',()=>{windowMode=mode;for(const id of ['window10','windowAll']){$(id).classList.toggle('selected',id===button);$(id).setAttribute('aria-pressed',id===button?'true':'false')}if(lastState)chart(lastState)});
poll();

function renderWidgets(w){for(const[id,lockId,key,label]of [['widgetCurrent','widgetCurrentLock','current','心率'],['widgetTrend','widgetTrendLock','trend','曲线']]){const on=!!w?.[key],locked=!!w?.locked?.[key];$(id).setAttribute('aria-pressed',on?'true':'false');$(id).textContent=`${on?'关闭':'显示'}桌面${label}`;$(lockId).setAttribute('aria-pressed',locked?'true':'false');$(lockId).textContent=`${locked?'解除固定':'固定'}${label}窗`;$(lockId).title=locked?'解除后可移动、缩放和操作小窗':'固定后整窗点击穿透'}}
for(const[id,widget]of [['widgetCurrent','current'],['widgetTrend','trend']])$(id).addEventListener('click',async()=>{const visible=$(id).getAttribute('aria-pressed')!=='true';$(id).disabled=true;text('widgetMessage','');try{const response=await fetch('/api/widgets',{method:'POST',headers:{'Content-Type':'application/json','X-HeartRadio-Action':'widgets'},body:JSON.stringify({widget,visible})});if(!response.ok){let message='桌面小窗操作失败，请检查本地服务';try{message=(await response.json()).error??message}catch{}throw Error(message)}renderWidgets(await response.json())}catch(error){text('widgetMessage',error.message)}finally{$(id).disabled=false}});
for(const[id,widget]of [['widgetCurrentLock','current'],['widgetTrendLock','trend']])$(id).addEventListener('click',async()=>{const locked=$(id).getAttribute('aria-pressed')!=='true';$(id).disabled=true;text('widgetMessage','');try{const response=await fetch('/api/widgets',{method:'POST',headers:{'Content-Type':'application/json','X-HeartRadio-Action':'widgets'},body:JSON.stringify({widget,locked})});if(!response.ok){let message='小窗固定操作失败，请检查本地服务';try{message=(await response.json()).error??message}catch{}throw Error(message)}renderWidgets(await response.json())}catch(error){text('widgetMessage',error.message)}finally{$(id).disabled=false}});

let savedReceiverSettings=null,receiverDirty=false;
$('receiverSettingsForm').addEventListener('input',()=>{receiverDirty=true});
$('receiverSettingsForm').addEventListener('change',()=>{receiverDirty=true});
function showReceiverSettings(value){$('receiverUnlimited').checked=value.seconds===0;if(value.seconds>0){const hours=value.seconds%3600===0;$('receiverUnit').value=hours?'hours':'minutes';$('receiverAmount').value=value.seconds/(hours?3600:60)}receiverInputs()}
function receiverInputs(){const unlimited=$('receiverUnlimited').checked;$('receiverAmount').disabled=unlimited;$('receiverUnit').disabled=unlimited}
$('receiverUnlimited').addEventListener('change',receiverInputs);
function renderReceiverLimit(s){
 if(s.receiverSettings&&(!savedReceiverSettings||(s.receiverSettings.savedMs??0)>(savedReceiverSettings.savedMs??0))){savedReceiverSettings=s.receiverSettings;if(!receiverDirty)showReceiverSettings(savedReceiverSettings)}
 const limit=s.receiverLimit;
 if(!s.startedMs||s.receiverStopped){text('receiverLimitStatus',savedReceiverSettings?`下次启动：${savedReceiverSettings.seconds===0?'不限时':`${savedReceiverSettings.seconds/60} 分钟`} · 当前接收器未运行`:'当前接收器未运行');return}
 if(savedReceiverSettings&&limit?.revision!==savedReceiverSettings.revision&&savedReceiverSettings.revision!=='default'){text('receiverLimitStatus','设置已保存，等待接收器应用；若长时间未生效，请重新启动电脑端');return}
 if(!limit){text('receiverLimitStatus','当前接收器为旧版，请重新启动电脑端以应用时长设置');return}
 text('receiverLimitStatus',limit.seconds===0?'当前接收：不限时':`当前接收：剩余 ${duration((limit.endsMs-s.nowMs)/1000)} · 预计 ${new Date(limit.endsMs).toLocaleString('zh-CN',{hour12:false})} 停止`);
}
async function loadReceiverSettings(){try{const response=await fetch('/api/receiver-settings',{cache:'no-store'});if(!response.ok)throw Error('读取接收设置失败，请刷新页面重试');savedReceiverSettings=await response.json();if(!receiverDirty)showReceiverSettings(savedReceiverSettings);$('receiverSave').disabled=false;if(lastState)renderReceiverLimit(lastState)}catch(error){text('receiverSettingsMessage',error.message)}}
$('receiverSettingsForm').addEventListener('submit',async event=>{event.preventDefault();$('receiverSave').disabled=true;try{const request={unlimited:$('receiverUnlimited').checked,amount:Number($('receiverAmount').value),unit:$('receiverUnit').value};const response=await fetch('/api/receiver-settings',{method:'POST',headers:{'Content-Type':'application/json','X-HeartRadio-Action':'receiver-settings'},body:JSON.stringify(request)});const result=await response.json();if(!response.ok)throw Error(result.error||'保存失败');savedReceiverSettings=result;receiverDirty=false;text('receiverSettingsMessage',result.seconds===0?'已保存：不限时。':'已保存：限时从本次保存开始计算；下次启动按完整时长重新计时。');if(lastState)renderReceiverLimit(lastState)}catch(error){text('receiverSettingsMessage',error.message)}finally{$('receiverSave').disabled=false}});
loadReceiverSettings();
