'use strict';
const historyUi={catalog:null,selection:{year:'',month:'',day:'',run:''},result:null,request:0};
const beijingTime=ms=>new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(ms));
const beijingDateTime=ms=>new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(ms));
const historyNumber=n=>n==null?'—':Math.round(n*10)/10;
const historyRange=s=>s.minimum==null?'—':`${historyNumber(s.minimum)}–${historyNumber(s.maximum)}`;
function historyOptions(id,values,chosen,empty,label){
 const element=$(id);element.replaceChildren();
 if(empty!=null)element.append(new Option(empty,''));
 for(const value of values)element.append(new Option(label(value),value));
 element.value=chosen;element.disabled=values.length===0;
}
function historyFilters(){
 const dates=historyUi.catalog?.days.map(d=>d.date)||[],s=historyUi.selection;
 const years=[...new Set(dates.map(d=>d.slice(0,4)))];
 if(!years.includes(s.year)){const latest=dates[0]||'';s.year=latest.slice(0,4);s.month=latest.slice(5,7);s.day=latest.slice(8,10);s.run='';}
 const months=[...new Set(dates.filter(d=>d.startsWith(s.year+'-')).map(d=>d.slice(5,7)))];
 if(s.month&&!months.includes(s.month)){s.month='';s.day='';}
 const days=s.month?[...new Set(dates.filter(d=>d.startsWith(`${s.year}-${s.month}-`)).map(d=>d.slice(8,10)))]:[];
 if(s.day&&!days.includes(s.day))s.day='';
 historyOptions('historyYear',years,s.year,null,v=>`${v} 年`);
 historyOptions('historyMonth',months,s.month,'全年',v=>`${Number(v)} 月`);
 historyOptions('historyDay',days,s.day,s.month?'全月':'请先选择月份',v=>`${Number(v)} 日`);
}
function historyParams(){const params=new URLSearchParams();for(const[key,value]of Object.entries(historyUi.selection))if(value)params.set(key,value);return params.toString();}
async function loadHistoryCatalog(){
 $('historyRefresh').disabled=true;text('historyMessage','正在整理本地历史记录…');
 try{
  const response=await fetch('/api/history/catalog',{cache:'no-store'});if(!response.ok)throw Error('无法读取历史目录');
  historyUi.catalog=await response.json();historyFilters();
  if(!historyUi.catalog.days.length){historyUi.request++;historyUi.result=null;$('historyResults').hidden=true;$('historyExport').hidden=true;text('historyMessage','暂无历史记录。采集后的记录会自动保留，届时点击“刷新记录”即可查看。');return;}
  await loadHistory();
 }catch(error){historyUi.result=null;$('historyResults').hidden=true;text('historyMessage',error.message);$('historyExport').hidden=true;}
 finally{$('historyRefresh').disabled=false;}
}
async function loadHistory(){
 if(!historyUi.selection.year)return;
 const request=++historyUi.request,params=historyParams();
 text('historyMessage','正在读取所选记录…');$('historyExport').hidden=true;$('historyResults').hidden=true;
 try{
  const response=await fetch('/api/history?'+params,{cache:'no-store'});if(!response.ok)throw Error('所选记录读取失败，请刷新后重试');
  const result=await response.json();if(request!==historyUi.request)return;
  historyUi.result=result;renderHistory(result);
  $('historyExport').href='/api/history/export.csv?'+params;$('historyExport').hidden=false;
 }catch(error){if(request===historyUi.request){historyUi.result=null;text('historyMessage',error.message);}}
}
function historyCell(row,value){const cell=document.createElement('td');cell.textContent=value;row.append(cell);return cell;}
function renderHistory(result){
 const s=result.statistics;$('historyResults').hidden=false;
 text('historyMessage',result.warnings.length?result.warnings.join('；'):`${result.period} · 北京时间 · 按电脑收到事件的日期归类`);
 text('historyDays',s.dayCount.toLocaleString());text('historyRuns',s.runCount.toLocaleString());text('historyCount',s.validCount.toLocaleString());text('historyRange',historyRange(s));
 const runSelect=$('historyRun');runSelect.replaceChildren(new Option('全部轮次',''));
 for(const run of result.runs)runSelect.append(new Option(`${beijingDateTime(run.firstMs)} · ${run.validCount} 条有效`,run.runId));
 runSelect.value=result.selectedRun||'';runSelect.disabled=result.runs.length===0;
 text('historyChartTitle',`${result.period} · ${result.level==='day'?'日内心率曲线':result.level==='month'?'每日心率范围':'每月心率范围'}`);
 text('historyChartCaption',result.level==='day'?'只绘制有效事件，跨轮次、断连和无效区间断开。':'红线表示最低到最高心率，圆点表示有效读数均值；不同日期不连线。');
 text('historyBucketCaption',result.level==='year'?'按月份汇总':'按日期汇总');
 const body=$('historyBuckets');body.replaceChildren();
 for(const bucket of result.buckets){
  const row=document.createElement('tr'),cell=historyCell(row,'');
  if(result.level!=='day'){
   const button=document.createElement('button');button.type='button';button.textContent=bucket.label;button.title='查看这一'+(result.level==='year'?'月':'天');
   button.addEventListener('click',()=>{const parts=bucket.label.split('-');historyUi.selection={year:parts[0],month:parts[1],day:parts[2]||'',run:historyUi.selection.run};historyFilters();loadHistory();});cell.append(button);
  }else cell.textContent=bucket.label;
  historyCell(row,bucket.validCount.toLocaleString());historyCell(row,historyRange(bucket));historyCell(row,historyNumber(bucket.eventMean));body.append(row);
 }
 if(!result.buckets.length){const row=document.createElement('tr'),cell=historyCell(row,'此范围没有心率事件');cell.colSpan=4;body.append(row);}
 const runs=$('historyRunRows');runs.replaceChildren();
 for(const run of result.runs.slice(0,100)){
  const row=document.createElement('tr');if(run.runId===result.selectedRun)row.classList.add('selected-run');
  historyCell(row,run.runId);historyCell(row,`${beijingDateTime(run.firstMs)} — ${beijingDateTime(run.lastMs)}`);historyCell(row,`${run.validCount.toLocaleString()} / ${run.eventCount.toLocaleString()}`);
  const cell=historyCell(row,''),button=document.createElement('button');button.type='button';button.textContent=run.runId===result.selectedRun?'正在查看':'查看本轮';button.disabled=run.runId===result.selectedRun;
  button.addEventListener('click',()=>{historyUi.selection.run=run.runId;loadHistory();});cell.append(button);runs.append(row);
 }
 text('historyRunLimit',result.runs.length>100?`列表显示前100轮，共${result.runs.length}轮；可缩小日期范围或使用“采集轮次”选择。统计和导出包含全部匹配记录。`:'');
 drawHistory(result);
}
function drawHistory(result){
 const root=$('historyChart');root.replaceChildren();
 const items=result.level==='day'?result.points:result.buckets.filter(b=>b.validCount>0);
 $('historyEmptyChart').hidden=items.length>0;
 const width=Math.max(280,root.clientWidth),left=40,right=width-12;
 root.setAttribute('viewBox',`0 0 ${width} 235`);
 let min=result.statistics.minimum==null?50:Math.floor((result.statistics.minimum-8)/10)*10;
 let max=result.statistics.maximum==null?110:Math.ceil((result.statistics.maximum+8)/10)*10;
 if(max-min<30){min-=10;max+=10;}
 const y=v=>190-(v-min)/(max-min)*164;
 for(let i=0;i<=4;i++){const yy=26+i*41;root.append(svg('line',{x1:left,x2:right,y1:yy,y2:yy,class:'gridline'}),svg('text',{x:left-8,y:yy+3,'text-anchor':'end'},Math.round(max-(max-min)*i/4)));}
 if(result.level==='day'&&items.length){
  const start=items[0].timeMs,end=Math.max(start+60000,items[items.length-1].timeMs),x=t=>left+(t-start)/(end-start)*(right-left);
  const ticks=width<500?2:4;for(let i=0;i<=ticks;i++){const t=start+(end-start)*i/ticks;root.append(svg('text',{x:x(t),y:219,'text-anchor':i===0?'start':i===ticks?'end':'middle'},beijingTime(t).slice(0,5)));}
  let group=[];const flush=()=>{if(group.length>1)root.append(svg('polyline',{points:group.map(p=>`${x(p.timeMs)},${y(p.bpm)}`).join(' '),class:'trend'}));group=[];};
  let previous=null;for(const p of items){if(previous&&previous.segment!==p.segment)flush();group.push(p);previous=p;
   const point=svg('circle',{cx:x(p.timeMs),cy:y(p.bpm),r:items.length>1500?1.7:2.6,class:'point'});point.append(svg('title',{},`${beijingTime(p.timeMs)} · ${historyNumber(p.bpm)} bpm`));root.append(point);
  }flush();
 }else if(items.length){
  const x=i=>left+(i+.5)/items.length*(right-left),stride=Math.max(1,Math.ceil(items.length/(width<500?6:12)));
  items.forEach((b,i)=>{
   const bar=svg('line',{x1:x(i),x2:x(i),y1:y(b.minimum),y2:y(b.maximum),class:'history-range-bar'});
   const title=`${b.label} · ${b.validCount} 条有效 · ${historyRange(b)} bpm · 读数均值 ${historyNumber(b.eventMean)}`;
   bar.append(svg('title',{},title));const point=svg('circle',{cx:x(i),cy:y(b.eventMean),r:4,class:'history-mean'});point.append(svg('title',{},title));root.append(bar,point);
   if(i%stride===0||i===items.length-1)root.append(svg('text',{x:x(i),y:219,'text-anchor':'middle'},result.level==='year'?`${Number(b.label.slice(5))}月`:`${Number(b.label.slice(8))}日`));
  });
 }
 text('historyChartNote',result.level==='day'?`${result.statistics.validCount.toLocaleString()} 条有效记录${result.downsampled?`，图中保留 ${result.displayedPoints.toLocaleString()} 个首末/极值点；统计与CSV保留全部事件`:'，图中展示全部有效事件'}。`:'可点击下方汇总表中的月份或日期继续查看；没有记录的日期不作为零值绘制。');
}
for(const[id,key]of [['historyYear','year'],['historyMonth','month'],['historyDay','day']])$(id).addEventListener('change',()=>{
 historyUi.selection[key]=$(id).value;if(key==='year')historyUi.selection.month='';if(key!=='day')historyUi.selection.day='';historyUi.selection.run='';historyFilters();loadHistory();
});
$('historyRun').addEventListener('change',()=>{historyUi.selection.run=$('historyRun').value;loadHistory();});
$('historyRefresh').addEventListener('click',loadHistoryCatalog);
let historyResizeTimer;window.addEventListener('resize',()=>{clearTimeout(historyResizeTimer);historyResizeTimer=setTimeout(()=>{if(!$('historyView').hidden&&historyUi.result)drawHistory(historyUi.result);},120);});
