'use strict';
(() => {
 const get = id => document.getElementById(id);
 const form = get('healthTimelineForm');
 const date = get('healthTimelineDate');
 const device = get('healthTimelineDevice');
 const source = get('healthHeartSource');
 const plots = get('healthTimelinePlots');
 const cursor = get('healthTimelineCursor');
 const tooltip = get('healthTimelineTooltip');
 const tracks = [...plots.querySelectorAll('.health-chart-window')];
 const status = get('healthTimelineStatus');
 const previous = get('healthTimelinePrevious');
 const next = get('healthTimelineNext');
 const today = get('healthTimelineToday');
 const minute = 60000;
 const hour = 60*minute;
 const day = 24*hour;
 const beijing = new Intl.DateTimeFormat('sv-SE', {timeZone:'Asia/Shanghai', year:'numeric', month:'2-digit', day:'2-digit'});
 const clock = new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai', hour:'2-digit', minute:'2-digit', hourCycle:'h23'});
 const seconds = new Intl.DateTimeFormat('zh-CN', {timeZone:'Asia/Shanghai', hour:'2-digit', minute:'2-digit', second:'2-digit', hourCycle:'h23'});
 const number = new Intl.NumberFormat('zh-CN');
 const colors = {native:'#ff829a', radio:'#7ad6d5', deep:'#8c94f1', light:'#a3b8ff',
  rem:'#b3a0f5', awake:'#dcb889', summary:'#8896ad', steps:'#68b4a3'};
 const stageNames = {awake:'清醒', rem:'REM', light:'浅睡', deep:'深睡', gap:'分期空档', summary:'睡眠摘要'};
 let result = null;
 let detail = null;
 let deviceFilter = '';
 let cursorMinute = null;
 let version = 0;

 function shift(value, delta) {
  return new Date(Date.parse(value + 'T00:00:00Z') + delta*day).toISOString().slice(0,10);
 }
 function changeDate(delta) {
  if (!date.value) return;
  date.value = shift(date.value,delta);
  load();
 }
 function updateNavigation() {
  date.max = beijing.format(new Date());
  previous.disabled = !date.value || date.value <= date.min;
  next.disabled = !date.value || date.value >= date.max;
  today.disabled = date.value === date.max;
 }
 function selectDate(value) {
  window.dispatchEvent(new CustomEvent('heartradio-health-date', {detail:value}));
 }
 async function load() {
  updateNavigation();
  if (!form.reportValidity()) return;
  const token = ++version;
  hideCursor();
  selectDate(null);
  status.textContent = '正在读取电脑归档…';
  result = detail = null;
  plots.hidden = true;
  const params = new URLSearchParams({date:date.value});
  if (deviceFilter) params.set('device_id',deviceFilter);
  try {
   const reply = await fetch('/api/health-timeline?' + params, {cache:'no-store'});
   if (!reply.ok) throw new Error('读取健康历史失败，请刷新重试。');
   const value = await reply.json();
   if (token !== version) return;
   result = value;
   detail = prepareDetails(value);
   device.replaceChildren(...value.availableDevices.map(id => new Option('手表 · ' + id.slice(-8),id)));
   device.value = value.deviceId || '';
   get('healthTimelineDeviceControl').hidden = value.availableDevices.length < 2;
   status.textContent = '';
   plots.hidden = false;
   draw();
   selectDate({date:value.endDate,deviceId:value.deviceId});
  } catch (error) {
   if (token === version) status.textContent = '读取健康历史失败，请刷新重试。';
  }
 }

 function setup(canvas) {
  const width = canvas.clientWidth;
  const height = canvas.clientHeight;
  if (!width || !height) return null;
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.round(width*ratio);
  canvas.height = Math.round(height*ratio);
  const ctx = canvas.getContext('2d');
  ctx.scale(ratio,ratio);
  const x = time => (time-result.startAtMs)/day*width;
  ctx.strokeStyle = '#a4b1c029';
  ctx.beginPath();
  for (let t = result.startAtMs+4*hour; t < result.endAtMs; t += 4*hour) {
   ctx.moveTo(x(t),0);
   ctx.lineTo(x(t),height);
  }
  ctx.stroke();
  return {ctx,width,height,x};
 }
 function drawAxis() {
  const axis = get('healthTimelineAxis');
  if (axis.childElementCount) return;
  for (let h = 0; h <= 24; h += 4) {
   const tick = document.createElement('span');
   tick.style.left = (h/24*100) + '%';
   tick.textContent = String(h).padStart(2,'0') + ':00';
   if (h === 0) tick.className = 'first';
   if (h === 24) tick.className = 'last';
   axis.append(tick);
  }
 }
 function scale(id, values) {
  const labels = values.map(value => {
   const label = document.createElement('span');
   label.textContent = number.format(value);
   return label;
  });
  get(id).replaceChildren(...labels);
 }
 function horizontalGrid({ctx,width}, positions) {
  ctx.strokeStyle = '#a4b1c023';
  ctx.lineWidth = 1;
  ctx.beginPath();
  for (const y of positions) {
   ctx.moveTo(0,y);
   ctx.lineTo(width,y);
  }
  ctx.stroke();
 }
 function nativeSegments(points) {
  const segments = [];
  for (const point of points) {
   let section = segments.at(-1);
   if (!section || point[0]-section.at(-1)[0] > 180000) {
    section = [];
    segments.push(section);
   }
   section.push(point);
  }
  return segments;
 }
 function legacyChartPoints() {
  if (source.value !== 'native') return result.heartRadio;
  const points = [];
  const nativeMinutes = [...detail.nativeMinutes.keys()];
  let index = 0;
  for (const [at,bpm,segment] of result.heartRadio) {
   const start = Math.floor(at/minute)*minute;
   while (index < nativeMinutes.length && nativeMinutes[index] < start) index++;
   if (!detail.nativeMinutes.has(start)) {
    points.push([at,bpm,segment + ':' + index]);
   }
  }
  return points;
 }
 function drawHeart() {
  const sections = detail.nativeSegments;
  const native = source.value === 'radio' ? [] : sections.flatMap((section,index) =>
   section.map(([at,bpm]) => [at,bpm,index]));
  const radio = legacyChartPoints();
  const caption = get('healthHeartCaption');
  const legend = get('healthHeartLegend');
  legend.children[0].hidden = !native.length;
  legend.children[1].hidden = !radio.length;
  legend.hidden = !native.length && !radio.length;
  const points = [...native,...radio];
  const canvas = get('healthHeartPlot');
  const chart = setup(canvas);
  if (!points.length) {
   caption.textContent = '当天暂无心率记录';
   canvas.setAttribute('aria-label',caption.textContent);
   scale('healthHeartScale',[]);
   return;
  }
  let minimum = Infinity;
  let maximum = -Infinity;
  for (const [,bpm] of points) {
   minimum = Math.min(bpm,minimum);
   maximum = Math.max(bpm,maximum);
  }
  caption.textContent = number.format(minimum) + '–' + number.format(maximum) + ' BPM';
  canvas.setAttribute('aria-label','心率历史 ' + caption.textContent);
  const lower = Math.max(0,Math.floor((minimum-2)/10)*10);
  const upper = Math.max(lower+20,Math.ceil((maximum+2)/10)*10);
  const ticks = [upper,(upper+lower)/2,lower];
  scale('healthHeartScale',ticks);
  if (!chart) return;
  const {ctx,height,x} = chart;
  const y = bpm => height-8-(bpm-lower)/(upper-lower)*(height-16);
  horizontalGrid(chart,ticks.map(y));
  for (const [series,color,dash] of [[native,colors.native,[]],[radio,colors.radio,[4,3]]]) {
   let prior = null;
   ctx.beginPath();
   ctx.lineWidth = 1.6;
   ctx.lineCap = 'round';
   ctx.strokeStyle = color;
   ctx.setLineDash(dash);
   for (const [at,bpm,segment] of series) {
    const px = x(at), py = y(bpm);
    if (segment !== prior) {
     ctx.moveTo(px-0.5,py);
     ctx.lineTo(px+0.5,py);
     prior = segment;
    } else {
     ctx.lineTo(px,py);
    }
   }
   ctx.stroke();
  }
 }
 function drawSleep() {
  const canvas = get('healthSleepPlot');
  const caption = get('healthSleepCaption');
  caption.textContent = result.sleep.length ? result.sleep.length + ' 晚有记录' : '当天暂无睡眠记录';
  canvas.setAttribute('aria-label',caption.textContent);
  get('healthSleepLegend').hidden = !detail.sleep.some(segment => segment.stage === 'summary');
  const chart = setup(canvas);
  if (!chart) return;
  const {ctx,height,x} = chart;
  const lanes = {awake:0, rem:1, light:2, deep:3, summary:2};
  horizontalGrid(chart,[height/4,height/2,height*3/4]);
  for (const segment of detail.sleep) {
   const lo = Math.max(segment.startAtMs,result.startAtMs);
   const hi = Math.min(segment.endAtMs,result.endAtMs);
   if (segment.stage === 'gap' || hi <= lo) continue;
   ctx.fillStyle = colors[segment.stage];
   ctx.fillRect(x(lo),3+lanes[segment.stage]*height/4,Math.max(1,x(hi)-x(lo)),height/4-6);
  }
 }
 function drawSteps() {
  const canvas = get('healthStepsPlot');
  const caption = get('healthStepsCaption');
  const total = result.activity.reduce((sum,bucket) => sum+bucket.steps,0);
  const covered = detail.steps.size;
  caption.textContent = covered ? number.format(total) + ' 步 · ' + number.format(covered) + ' 分钟有记录'
   : '当天暂无步数记录';
  canvas.setAttribute('aria-label','每小时步数，' + caption.textContent);
  const max = Math.max(0,...result.activity.map(bucket => bucket.steps));
  scale('healthStepsScale',max > 0 ? [max,0] : [0]);
  const chart = setup(canvas);
  if (!chart || !max) return;
  const {ctx,height,x} = chart;
  horizontalGrid(chart,[8,height/2,height-1]);
  ctx.fillStyle = colors.steps;
  for (const bucket of result.activity) {
   if (bucket.steps <= 0) continue;
   const left = x(bucket.startAtMs);
   const right = x(bucket.startAtMs+hour);
   const bar = Math.max(1,bucket.steps/max*(height-8));
   ctx.fillRect(left+2,height-bar,Math.max(1,right-left-4),bar);
  }
 }
 function drawStress() {
  const canvas=get('healthStressPlot'),caption=get('healthStressCaption');
  const points=result.stress||[];
  caption.textContent=points.length?`${points.length} 次点测 · 未测时段留空`:'当天暂无压力点测记录';
  canvas.setAttribute('aria-label',caption.textContent);
  scale('healthStressScale',[100,50,0]);
  const chart=setup(canvas);
  if(!chart)return;
  const {ctx,height,x}=chart;
  const y=score=>height-6-score/100*(height-12);
  horizontalGrid(chart,[100,50,0].map(y));
  ctx.fillStyle='#b3a0f5';
  for(const [at,score] of points){
   ctx.beginPath();
   ctx.arc(x(at),y(score),3.5,0,Math.PI*2);
   ctx.fill();
  }
 }
 function draw() {
  if (!result || plots.hidden) return;
  drawAxis();
  drawHeart();
  drawSleep();
  drawSteps();
  drawStress();
 }

 function prepareDetails(value) {
  const nativeMinutes = new Map();
  for (const [at,bpm] of value.heart) {
   const start = Math.floor(at/minute)*minute;
   const sample = nativeMinutes.get(start) || {count:0, min:bpm, max:bpm};
   sample.at = at;
   sample.bpm = bpm;
   sample.count++;
   sample.min = Math.min(sample.min,bpm);
   sample.max = Math.max(sample.max,bpm);
   nativeMinutes.set(start,sample);
  }
  const stressMinutes=new Map();
  for(const [at,score] of value.stress||[]){
   const start=Math.floor(at/minute)*minute;
   if(!stressMinutes.has(start))stressMinutes.set(start,[]);
   stressMinutes.get(start).push([at,score]);
  }
  return {
   nativeMinutes,
   nativeSegments:nativeSegments(value.heart),
   radioMinutes:new Map(value.heartRadioMinutes.map(([start,at,bpm,min,max,count]) =>
    [start,{at,bpm,min,max,count}])),
   sleep:value.sleep.flatMap(night => night.segments || [
    {stage:'summary', startAtMs:night.startAtMs, endAtMs:night.endAtMs+minute}]),
   steps:new Map(value.activityMinutes),
   hours:new Map(value.activity.map(entry => [entry.startAtMs,entry])),
   stressMinutes,
  };
 }
 function heartReading(name,sample) {
  const value = name + ' ' + number.format(sample.bpm) + ' BPM · ' + seconds.format(sample.at);
  return sample.count > 1 ? value + '（本分钟 ' + sample.count + ' 条，' +
   number.format(sample.min) + '–' + number.format(sample.max) + ' BPM）' : value;
 }
 function readingsAt(at) {
  const heart = [];
  const native = detail.nativeMinutes.get(at);
  const radio = detail.radioMinutes.get(at);
  if (native && source.value !== 'radio') heart.push(heartReading('原厂',native));
  if (radio && (source.value !== 'native' || !native)) heart.push(heartReading('HeartRadio',radio));
  const sleep = detail.sleep.find(segment => segment.startAtMs <= at && at < segment.endAtMs);
  const sleepText = sleep ? stageNames[sleep.stage] + ' · ' +
   clock.format(sleep.startAtMs) + '–' + clock.format(sleep.endAtMs) +
   (sleep.stage === 'summary' ? '（无分期）' : '') : '该分钟无记录';
  const steps = detail.steps.get(at);
  const bucket = detail.hours.get(Math.floor(at/hour)*hour);
  const stepText = (steps === undefined ? '该分钟无记录' : '该分钟 ' + number.format(steps) + ' 步') +
   (bucket ? ' · 该小时 ' + number.format(bucket.steps) + ' 步（' + bucket.recordedMinutes + '/60 分钟有记录）' : '');
  const stressText=(detail.stressMinutes.get(at)||[]).map(([time,score])=>`${score} 分 · ${seconds.format(time)}`).join('\n');
  return [heart.join('\n') || '该分钟无记录',sleepText,stepText,stressText||'该分钟无记录'];
 }
 function hideCursor() {
  cursorMinute = null;
  tooltip.hidden = true;
  cursor.hidden = true;
  plots.classList.remove('has-cursor');
 }
 function cursorBounds() {
  const first = tracks[0].getBoundingClientRect();
  const last = tracks.at(-1).getBoundingClientRect();
  return {left:first.left, right:first.right, top:first.top, bottom:last.bottom, width:first.width};
 }
 function showCursor(index,clientX,clientY,bounds,announce=false) {
  cursorMinute = Math.max(0,Math.min(1439,index));
  const at = result.startAtMs+cursorMinute*minute;
  const values = readingsAt(at);
  const label = result.endDate + ' · ' + clock.format(at);
  const title = get('healthCursorTime');
  title.textContent = label;
  title.dateTime = new Date(at).toISOString();
  ['healthCursorHeart','healthCursorSleep','healthCursorSteps','healthCursorStress'].forEach((id,index) =>
   get(id).textContent = values[index]);
  if (announce) get('healthCursorAnnounce').textContent = [label,...values].join('。');
  const area = plots.getBoundingClientRect();
  cursor.style.left = (bounds.left-area.left + cursorMinute/1440*bounds.width) + 'px';
  cursor.style.top = (bounds.top-area.top) + 'px';
  cursor.style.height = (bounds.bottom-bounds.top) + 'px';
  cursor.hidden = false;
  plots.classList.add('has-cursor');
  tooltip.hidden = false;
  const width = tooltip.offsetWidth;
  const height = tooltip.offsetHeight;
  const left = clientX+16+width < window.innerWidth-8 ? clientX+16 : clientX-width-16;
  const top = clientY+16+height < window.innerHeight-8 ? clientY+16 : clientY-height-16;
  tooltip.style.left = Math.max(8,Math.min(window.innerWidth-width-8,left)) + 'px';
  tooltip.style.top = Math.max(8,Math.min(window.innerHeight-height-8,top)) + 'px';
 }
 function usePointer(event) {
  if (!result) return;
  const {clientX,clientY} = event;
  const bounds = cursorBounds();
  if (!bounds.width || clientX < bounds.left || clientX >= bounds.right ||
      clientY < bounds.top || clientY >= bounds.bottom) {
   hideCursor();
   return;
  }
  const index = Math.floor((clientX-bounds.left)/bounds.width*1440);
  showCursor(index,clientX,clientY,bounds);
 }
 plots.addEventListener('pointermove',event => {if (event.pointerType !== 'touch') usePointer(event);});
 plots.addEventListener('pointerdown',event => {if (event.pointerType === 'touch') usePointer(event);});
 plots.addEventListener('pointerleave',event => {if (event.pointerType !== 'touch') hideCursor();});
 for (const track of tracks) {
  track.addEventListener('blur',hideCursor);
  track.addEventListener('keydown',event => {
   if (event.key === 'Escape') hideCursor();
   if (!result || !['ArrowLeft','ArrowRight'].includes(event.key)) return;
   event.preventDefault();
   const delta = (event.key === 'ArrowLeft' ? -1 : 1)*(event.shiftKey ? 15 : 1);
   const index = Math.max(0,Math.min(1439,(cursorMinute ?? -delta)+delta));
   const rect = track.getBoundingClientRect();
   const bounds = cursorBounds();
   showCursor(index,bounds.left+index/1440*bounds.width,rect.top+rect.height/2,bounds,true);
  });
 }
 document.addEventListener('pointerdown',event => {
  if (event.pointerType === 'touch' && !plots.contains(event.target)) hideCursor();
 });
 window.addEventListener('scroll',hideCursor,{passive:true});
 window.addEventListener('resize',hideCursor);
 form.addEventListener('submit',event => {event.preventDefault(); load();});
 previous.addEventListener('click',() => changeDate(-1));
 next.addEventListener('click',() => changeDate(1));
 today.addEventListener('click',() => {date.value = beijing.format(new Date()); load();});
 date.addEventListener('change',load);
 device.addEventListener('change',() => {deviceFilter = device.value; load();});
 source.addEventListener('change',() => {hideCursor(); if (result) drawHeart();});
 window.addEventListener('heartradio-health-refresh',load);
 new ResizeObserver(draw).observe(plots);
 window.addEventListener('heartradio-page-change',event => {
  hideCursor();
  if (event.detail.page === 'health-history') {
   if(!date.value)date.value = beijing.format(new Date());
   load();
  }
 });
})();
