'use strict';
(() => {
 const byId = id => document.getElementById(id);
 const names = {activity:'累计活动',wear:'佩戴状态',current_sleep:'当前睡眠',historical_sleep:'历史睡眠',user_state:'综合状态'};
 const statuses = {
  watch_offline:'手表未连接',interaction_disabled:'Agent 交互已关闭',
  interaction_sync_pending:'双端交互尚未同步',disabled_by_settings:'该指标的实时读取已关闭',
  busy:'手表正在读取其他健康数据',request_timeout:'本次读取超时',outcome_unknown_timeout:'本次读取超时',
  connection_changed:'连接已切换',outcome_unknown_disconnected:'读取期间连接断开',
  watch_update_required:'需要升级手表应用',unverified_firmware:'手表固件尚未适配',
  no_data:'本次没有可用记录',state_unverified:'已收到原厂回包，状态语义待验证',
  insufficient_evidence:'本次观测证据不足',invalid_result:'回包未通过校验',
  listener_unavailable:'原厂睡眠监听未运行',partial:'已返回可用项目',
  off_wrist:'手表已离腕，未进行状态判断',
 };
 const displayStatus = status => statuses[status] || '本次未取得可用结果';
 const wearLabel = value => ({worn:'佩戴中',off_wrist:'已离腕'})[value] || '未知';
 const sleepLabel = value => ({sleeping:'原厂报告睡眠中',not_sleeping:'原厂最近报告非睡眠'})[value] || '未知';
 const number = value => Number.isFinite(value) ? value.toLocaleString('zh-CN') : '—';
 const time = new Intl.DateTimeFormat('zh-CN', {
  timeZone:'Asia/Shanghai', month:'2-digit', day:'2-digit',
  hour:'2-digit', minute:'2-digit', second:'2-digit', hourCycle:'h23',
 });
 const formatTime = ms => Number.isFinite(ms) ? time.format(ms) : '—';
 const duration = minutes => minutes >= 60 ? `${Math.floor(minutes/60)} 小时 ${minutes%60} 分钟` : `${minutes} 分钟`;
 const rawSleep = sample => !Number.isInteger(sample?.rawCode) ? null
  : `原厂码 ${sample.rawCode}（${sample.fieldPresent ? '字段存在' : '字段缺省'}）`;

 async function localJson(path, options) {
  const response = await fetch(path, {cache:'no-store', ...options});
  if (!response.ok) {
   if (response.status === 400) throw new Error('查询条件无效，请检查日期或筛选项。');
   throw new Error('电脑服务暂不可用，请稍后重试。');
  }
  return response.json();
 }
 function callTool(name, args) {
  return localJson('/api/tools', {
   method:'POST',
   headers:{'Content-Type':'application/json','X-HeartRadio-Action':'tools'},
   body:JSON.stringify({name,arguments:args}),
  });
 }

 const liveCards = {activity:'healthLiveActivity',wear:'healthLiveWear',current_sleep:'healthLiveSleep',user_state:'healthLiveState'};
 const readButtons = [...document.querySelectorAll('[data-health-read]')];
 const message = byId('healthLiveMessage');

 function renderReading(metric, sample) {
  const card = liveCards[metric];
  const observed = Number.isFinite(sample?.observedAtMs) ? `设备读取 ${formatTime(sample.observedAtMs)}` : '';
  let value = '—';
  let detail = displayStatus(sample?.status);
  if (metric === 'activity' && sample?.status === 'ok' && Number.isFinite(sample.value)) {
   value = `${number(sample.value)} 步`;
   detail = Number.isFinite(sample.sourceAtMs) ? `原厂记录 ${formatTime(sample.sourceAtMs)}` : '原厂记录时间未提供';
  } else if (metric === 'wear' && sample?.status === 'ok') {
   value = wearLabel(sample.value);
   detail = '原厂佩戴观测';
  } else if (metric === 'current_sleep') {
   value = sleepLabel(sample?.value);
   detail = sample?.messageId === 490 && sample.status === 'ok'
    ? `最近原厂事件 ${formatTime(sample.sourceAtMs)}`
    : sample?.sleepEventStatus === 'awaiting_event' ? '等待本轮监听的首条原厂推送'
    : [rawSleep(sample), displayStatus(sample?.status)].filter(Boolean).join(' · ');
  }
  byId(card).textContent = value;
  byId(card+'Meta').textContent = [detail, observed, sample?.archiveStatus === 'storage_error' ? '电脑保存失败' : '']
   .filter(Boolean).join(' · ');
 }

 for (const button of readButtons) button.addEventListener('click', async () => {
  const metric = button.dataset.healthRead;
  const card = liveCards[metric];
  for (const control of readButtons) control.disabled = true;
  button.setAttribute('aria-busy','true');
  byId(card).textContent = '读取中…';
  byId(card+'Meta').textContent = '';
  message.textContent = metric === 'user_state' ? '正在核对佩戴和用户状态，运动观测约需 30 秒。' : `正在读取${names[metric]}…`;
  try {
   if (metric === 'user_state') {
    const metrics=['sleep','heart_rate','accelerometer','gyroscope'];
    if(byId('includeStressInState').checked)metrics.push('stress');
    const result = await callTool('get_user_state',{metrics});
    if(result.stress)window.dispatchEvent(new CustomEvent('heartradio-stress-result',{detail:result.stress}));
    byId(card).textContent = result.label || '暂不判断';
    const stateLabel = result.assessmentStatus === 'conflicting_evidence' ? '原厂睡眠与当次运动均已记录'
     : result.sleep?.value === 'sleeping' || result.sleep?.value === 'not_sleeping' ? '含原厂最近睡眠报告'
     : Number.isInteger(result.tier) ? '手表观测的粗略判断' : displayStatus(result.status);
    byId(card+'Meta').textContent = Number.isFinite(result.completedAtMs)
     ? `${stateLabel} · 本次完成于 ${formatTime(result.completedAtMs)}` : stateLabel;
    if (result.wear) renderReading('wear', result.wear);
    if (result.sleep) renderReading('current_sleep', result.sleep);
    message.textContent = result.status === 'off_wrist' ? displayStatus(result.status) : '综合状态和独立睡眠观测已返回。';
   } else {
    const result = await callTool('read_watch_health_now',{metrics:[metric]});
    const sample = result.results?.[metric];
    renderReading(metric, sample);
    message.textContent = sample?.status === 'ok' ? `${names[metric]}读取完成。` : displayStatus(sample?.status);
   }
  } catch (error) {
   byId(card).textContent = '—';
   byId(card+'Meta').textContent = error.message;
   message.textContent = error.message;
  } finally {
   button.removeAttribute('aria-busy');
   for (const control of readButtons) control.disabled = false;
  }
 });

 const archive = byId('healthArchiveDetails');
 const historyMessage = byId('healthHistoryMessage');
 const historyResults = byId('healthHistoryResults');
 const fillStages = byId('healthHistoryFillStages');
 const details = byId('healthHistoryDetails');
 const detailsMetric = byId('healthDetailsMetric');
 const detailsMessage = byId('healthDetailsMessage');
 const activityHours = byId('healthActivityHours');
 const sleepTimeline = byId('healthSleepTimeline');
 const pagination = byId('healthDetailsPagination');
 const prev = byId('healthDetailsPrev');
 const next = byId('healthDetailsNext');
 let loaded = false;
 let archiveVersion = 0;
 let filling = false;
 let selectedDate = '';
 let selectedDevice = '';
 let detailDay = null;
 let detailOffset = 0;
 let nextOffset = null;
 let detailVersion = 0;

 function historyRequest(parameters) {
  return localJson('/api/health-history?' + new URLSearchParams(parameters));
 }
 function rowFor(table, cells) {
  const row = table.insertRow();
  for (const cell of cells) row.insertCell().textContent = cell;
  return row;
 }
 function activityText(day) {
  const lines = [];
  if (Number.isFinite(day.stepsFromRecords)) {
   const coverage = day.completeWindow ? '整日范围已核对' : '部分日期覆盖';
   lines.push(`${number(day.stepsFromRecords)} 步 · ${number(day.recordedMinutes)} 分钟有记录 · ${coverage}`);
  }
  if (Number.isFinite(day.latestSnapshot?.value)) lines.push(`累计快照 ${number(day.latestSnapshot.value)} 步`);
  if (!lines.length && day.windowStatuses?.includes('no_data')) return '无活动分钟记录';
  return lines.join('\n') || '—';
 }
 function wearText(day) {
  const counts = day.observationCounts || {};
  const labels = [['worn','佩戴'],['off_wrist','离腕'],['unknown','未知']];
  return labels.filter(([key]) => counts[key]).map(([key,label]) => `${label} ${number(counts[key])} 次`).join(' · ') || '—';
 }
 function sleepText(day) {
  const counts = day.observationCounts || {};
  return ['sleeping','not_sleeping','unknown'].filter(key => counts[key])
   .map(key => `${sleepLabel(key)} ${number(counts[key])} 次`).join(' · ') || '—';
 }
 function nightText(day) {
  const night = day.value;
  if (night) return `${duration(night.sleepMinutes)}\n${formatTime(night.startAtMs)} 至 ${formatTime(night.endAtMs)}`;
  return day.windowStatuses?.includes('no_data') ? '无可用睡眠记录' : '—';
 }
 function renderHistory(result) {
  const days = new Map();
  for (const [metric, group] of Object.entries(result.results)) {
   for (const row of group.days || []) {
    if (!days.has(row.date)) days.set(row.date, {});
    days.get(row.date)[metric] = row;
   }
  }
  const rows = byId('healthHistoryRows');
  rows.replaceChildren();
  for (const [day, values] of [...days].sort(([a],[b]) => b.localeCompare(a))) {
   const heart = values.heart_rate;
   const heartText = heart?.recordedSamples
    ? `${heart.minimumBpm}–${heart.maximumBpm} BPM · ${number(heart.recordedSamples)} 条` : '—';
   const row = rowFor(rows, [day, activityText(values.activity || {}), heartText, wearText(values.wear || {}),
    sleepText(values.current_sleep || {}), nightText(values.historical_sleep || {}),
    values.stress?.recordedSamples?`${values.stress.minimumScore}–${values.stress.maximumScore} 分 · ${values.stress.recordedSamples} 次`:'—']);
   const cell = row.insertCell();
   const button = document.createElement('button');
   button.type = 'button';
   button.textContent = '明细';
   button.setAttribute('aria-label', `查看 ${day} 明细`);
   button.addEventListener('click', () => {
    detailDay = day;
    if (values.historical_sleep?.value) detailsMetric.value = 'historical_sleep';
    details.hidden = false;
    const title = byId('healthDetailsTitle');
    title.textContent = `${day} · 单日明细`;
    title.focus();
    loadDetails(0);
   });
   cell.append(button);
  }
  historyResults.hidden = !days.size;
  const hasData = Object.values(result.results).some(group => group.status !== 'no_data');
  historyMessage.textContent = hasData ? `${result.startDate} · 已读取电脑归档`
   : '所选日期暂无已归档的健康记录。';
 }
 async function loadHistory() {
  if (!selectedDate) return;
  const request = ++archiveVersion;
  historyMessage.textContent = '正在读取电脑历史…';
  const parameters = {start_date:selectedDate, end_date:selectedDate};
  if (selectedDevice) parameters.device_id = selectedDevice;
  try {
   const result = await historyRequest(parameters);
   if (request !== archiveVersion) return;
   renderHistory(result);
   loaded = true;
  } catch (error) {
   if (request === archiveVersion) historyMessage.textContent = error.message;
  }
 }
 const stages = [['awake','清醒'],['rem','REM'],['light','浅睡'],['deep','深睡'],['gap','片段间隔']];
 function renderSleepTimeline(night) {
  sleepTimeline.replaceChildren();
  if (!night) return;
  sleepTimeline.hidden = false;
  const heading = document.createElement('h4');
  heading.textContent = '睡眠分期时间轴';
  sleepTimeline.append(heading);
  if (!night.segments?.length) {
   const message = document.createElement('p');
   message.textContent = '这晚已有摘要。连接手表后，可用上方「补齐近期睡眠分期」读取一周内仍可获取的分期。';
   sleepTimeline.append(message);
   return;
  }
  const legend = document.createElement('ul');
  for (const [stage, label] of stages) {
   if (stage === 'gap' && !night.stageMinutes.gap) continue;
   const item = document.createElement('li');
   item.dataset.stage = stage;
   item.textContent = `${label} ${duration(night.stageMinutes[stage])}`;
   legend.append(item);
  }
  const namespace = 'http://www.w3.org/2000/svg';
  const chart = document.createElementNS(namespace, 'svg');
  chart.setAttribute('viewBox', '0 0 1000 120');
  chart.setAttribute('preserveAspectRatio', 'none');
  chart.setAttribute('role', 'img');
  chart.setAttribute('aria-label', '按时间绘制的清醒、REM、浅睡和深睡片段');
  const start = night.segments[0].startAtMs;
  const end = night.segments.at(-1).endAtMs;
  const x = ms => (ms-start)/(end-start)*1000;
  for (const segment of night.segments) {
   const bar = document.createElementNS(namespace, 'rect');
   const index = stages.findIndex(([key]) => key === segment.stage);
   bar.setAttribute('data-stage', segment.stage);
   bar.setAttribute('x', x(segment.startAtMs));
   bar.setAttribute('y', segment.stage === 'gap' ? '0' : String(index*30+2));
   bar.setAttribute('width', x(segment.endAtMs)-x(segment.startAtMs));
   bar.setAttribute('height', segment.stage === 'gap' ? '120' : '26');
   const title = document.createElementNS(namespace, 'title');
   title.textContent = `${stages[index][1]} · ${formatTime(segment.startAtMs)} 至 ${formatTime(segment.endAtMs-60000)} · ${duration((segment.endAtMs-segment.startAtMs)/60000)}`;
   bar.append(title);
   chart.append(bar);
  }
  const axis = document.createElement('div');
  axis.className = 'health-sleep-axis';
  for (const [at, caption] of [[start, '开始'], [night.endAtMs, '最后记录']]) {
   const label = document.createElement('span');
   label.textContent = `${caption} ${formatTime(at)}`;
   axis.append(label);
  }
  sleepTimeline.append(legend, chart, axis);
 }

 function renderActivityHours(hours) {
  const chart = byId('healthActivityHourChart');
  const reading = byId('healthActivityHourReading');
  const highest = Math.max(1, ...hours.map(hour => hour.steps ?? 0));
  const clock = hour => `${String(hour).padStart(2, '0')}:00`;
  let selected = null;
  chart.replaceChildren();
  hours.forEach((hour, index) => {
   const button = document.createElement('button');
   const plot = document.createElement('span');
   const bar = document.createElement('span');
   const tick = document.createElement('span');
   const steps = hour.steps === null ? '无记录' : `${number(hour.steps)} 步 · ${hour.recordedMinutes} / 60 分钟有记录`;
   const label = `${clock(index)}–${clock(index+1)} · ${steps}`;
   button.type = 'button';
   button.className = 'health-hour';
   button.setAttribute('aria-label', label);
   button.setAttribute('aria-pressed', 'false');
   button.title = label;
   button.dataset.value = hour.steps === null ? 'missing' : hour.steps === 0 ? 'zero' : 'positive';
   plot.className = 'health-hour-plot';
   bar.className = 'health-hour-bar';
   bar.style.setProperty('--height', `${(hour.steps ?? 0)/highest*100}%`);
   tick.className = 'health-hour-tick';
   tick.textContent = index % 6 === 0 || index === 23 ? clock(index).slice(0, 2) : '';
   plot.append(bar);
   button.append(plot, tick);
   button.addEventListener('click', () => {
    selected?.setAttribute('aria-pressed', 'false');
    selected = button;
    selected.setAttribute('aria-pressed', 'true');
    reading.textContent = label;
   });
   chart.append(button);
  });
  activityHours.hidden = false;
  chart.children[hours.findLastIndex(hour => hour.recordedMinutes > 0)].click();
 }

 function detailRows(metric, day) {
  if (metric === 'heart_rate') {
   return (day.samples || []).map(item => [formatTime(item.atMs),`${item.bpm} BPM`,`原厂质量码 ${item.nativeQuality}`]);
  }
  if (metric === 'historical_sleep') {
   const night = day.value;
   return night ? [[`${formatTime(night.startAtMs)} 至 ${formatTime(night.endAtMs)}`,
    duration(night.sleepMinutes), '原厂按晚摘要']] : [];
  }
  const records = [];
  for (const item of day.observations || []) {
   const at = item.sourceAtMs ?? item.observedAtMs;
   if (metric === 'wear') records.push([at,wearLabel(item.value),'原厂佩戴观测']);
   if (metric === 'current_sleep') records.push([at,sleepLabel(item.value),rawSleep(item) || '原厂码未提供']);
   if (metric === 'stress') records.push([at,`${item.score} 分`,'HeartRadio 发起的原厂点测']);
  }
  return records.sort(([a],[b]) => a-b).map(([at,value,note]) => [formatTime(at),value,note]);
 }
 async function loadDetails(offset) {
  const request = ++detailVersion;
  const metric = detailsMetric.value;
  detailsMessage.textContent = '正在读取单日记录…';
  const results = byId('healthDetailsResults');
  const page = byId('healthDetailsPage');
  prev.disabled = next.disabled = true;
  results.hidden = true;
  activityHours.hidden = true;
  sleepTimeline.hidden = true;
  pagination.hidden = true;
  page.textContent = '';
  try {
   const result = await historyRequest({start_date:detailDay,end_date:detailDay,device_id:selectedDevice,
    metric,view:'timeline',offset:String(offset)});
   if (request !== detailVersion) return;
   const day = result.results[metric]?.days?.[0] || {};
   if (metric === 'activity') {
    const hasSteps = day.hours?.some(hour => hour.recordedMinutes > 0);
    if (hasSteps) renderActivityHours(day.hours);
    detailsMessage.textContent = hasSteps ? '' : '这一天没有已归档的分钟步数。';
    return;
   }
   if (metric === 'historical_sleep') renderSleepTimeline(day.value);
   const rows = byId('healthDetailsRows');
   rows.replaceChildren();
   const values = detailRows(metric, day);
   values.forEach(cells => rowFor(rows,cells));
   results.hidden = !values.length;
   detailOffset = offset;
   nextOffset = day.nextSampleOffset ?? day.nextObservationOffset ?? null;
   pagination.hidden = !offset && nextOffset === null;
   prev.disabled = offset === 0;
   next.disabled = nextOffset === null;
   page.textContent = values.length || offset ? `第 ${offset/100+1} 页` : '';
   detailsMessage.textContent = values.length ? `电脑已归档的${metric === 'heart_rate' ? '原厂心率' : metric === 'stress' ? '压力点测' : names[metric]}记录` : '这一天没有该指标的可用记录。';
  } catch (error) {
   if (request === detailVersion) detailsMessage.textContent = error.message;
  }
 }

 window.addEventListener('heartradio-health-date', ({detail}) => {
  archiveVersion++;
  detailVersion++;
  loaded = false;
  selectedDate = detail?.date || '';
  selectedDevice = detail?.deviceId || '';
  detailDay = null;
  details.hidden = true;
  historyResults.hidden = true;
  historyMessage.textContent = '等待所选日期的电脑归档。';
  fillStages.disabled = filling || !selectedDevice;
  if (archive.open && selectedDate) loadHistory();
 });
 fillStages.addEventListener('click', async () => {
  if (filling || !selectedDevice) return;
  filling = true;
  fillStages.disabled = true;
  const stageMessage = byId('healthStagesMessage');
  stageMessage.textContent = '正在从手表补齐近期睡眠分期…';
  let message;
  let updated = 0;
  try {
   const result = await localJson('/api/health-sleep-stages', {
    method:'POST', headers:{'Content-Type':'application/json','X-HeartRadio-Action':'health-sleep-stages'},
    body:JSON.stringify({device_id:selectedDevice}),
   });
   updated = result.days.filter(day => day.status === 'updated').length;
   const incomplete = result.days.filter(day => !['updated','already_complete'].includes(day.status));
   const unavailable = result.status === 'different_device' ? '当前连接的手表与所选归档设备不同' : displayStatus(result.status);
   const remaining = incomplete.length ? `仍只有摘要：${incomplete.map(day => day.date).join('、')}` : '';
   message = [updated ? `已补齐 ${updated} 晚睡眠分期` : '',
    result.status === 'complete' ? remaining || (!updated ? '近期没有待补的睡眠分期。' : '') : unavailable]
    .filter(Boolean).join(' · ');
  } catch (error) {
   message = error.message;
  } finally {
   filling = false;
   fillStages.disabled = !selectedDevice;
  }
  stageMessage.textContent = message;
  if (updated) window.dispatchEvent(new Event('heartradio-health-refresh'));
 });
 detailsMetric.addEventListener('change', () => loadDetails(0));
 prev.addEventListener('click', () => loadDetails(Math.max(0,detailOffset-100)));
 next.addEventListener('click', () => { if (nextOffset !== null) loadDetails(nextOffset); });
 archive.addEventListener('toggle', event => {
  if (event.target.open && !loaded) loadHistory();
 });
})();
