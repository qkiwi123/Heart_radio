/* Isolated Chromium regression: actual static files, synthetic API data, no watch or user writes. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const http=require('node:http');
const {createRequire}=require('node:module');
const load=process.env.HEARTRADIO_NODE_MODULES?createRequire(path.join(process.env.HEARTRADIO_NODE_MODULES,'_entry.cjs')):require;
const {chromium}=load('playwright');
const root=path.resolve(__dirname,'..'),out=path.resolve(process.argv[2]||path.join(root,'runs/dashboard-pages-20261023/browser'));
fs.mkdirSync(out,{recursive:true});
const now=Date.now(),errors=[],actions=[],results=[];
const rows=Array.from({length:45},(_,i)=>({reminderId:'fixture_'+String(i).padStart(2,'0'),createdAtMs:now-i*100000,pcExpiresAtMs:now-i*100000+60000,
 status:i===0?'deleted':i%3===0?'acknowledged':i%3===1?'displayed':'issued',attemptStatus:'issued',
 params:{title:i===0?'已删除的提醒 · 电脑原文保留':i===1?'搜索目标 · 喝水提醒':`日常提醒 ${i+1}`,body:i===0?'这条正文已在手表清除，电脑仍保留。':`第${i+1}条提醒正文。\n用于核对分页、中文搜索和历史详情。`,vibrate:true,screen:true,sound:false},
 receipt:{reminderId:'fixture_'+String(i).padStart(2,'0'),...(i===0?{status:'deleted',deletedAtMs:now-10000}:{}),...(i%3===0?{acknowledgedAtMs:now-5000}:{}),displayedAtMs:now-15000},watchCheckedAtMs:now-2000,receiptReceivedAtMs:now-1000}));
const limits={vibrationEnabled:true,wakeEnabled:true,soundEnabled:false,maxVibrationMs:6000,maxScreenSeconds:30};
const policy={limits,peerConfirmed:true,syncStatus:'两端已同步',storageError:null};
const connection={connected:true,connectionRunning:true,transport:'rfcomm',configuredMode:'auto_bluetooth',automaticRouting:true,agentEnabled:true,
 hostConnectivity:{network:{state:'connected',kind:'wifi',name:'测试 <Wi-Fi>'},bluetooth:'off'},
 status:'蓝牙已连接',heartReceiveEnabled:true,agentInteraction:{peerConfirmed:true,pending:false,syncStatus:'两端已同步'},reminderPolicy:policy,
 heartBroadcast:{enabled:true,running:true,pending:false,syncStatus:'两端已同步'}};
const templates=Object.fromEntries(['strong','standard','light','silent','notification_only'].map(name=>[name,{vibrate:!['silent','notification_only'].includes(name),screen:name!=='notification_only',sound:false,vibration:{amplitude:255,pulseMs:360,gapMs:220,count:2}}]));
const settings={measurementSeconds:4,measurementCooldownSeconds:0,heartMaxAgeSeconds:30,deviceStatusCacheSeconds:15,sensorCacheSeconds:0,allowedMetrics:['heart_rate','device_status','pressure','accelerometer','gyroscope']};
const server=http.createServer((req,res)=>{
 const file=req.url==='/'?'index.html':req.url.slice(1).split('?')[0];
 if(!/^[\w.-]+\.(html|css|js)$/.test(file)){res.writeHead(404);res.end();return;}
 const target=path.join(root,'dashboard/static',file);if(!fs.existsSync(target)){res.writeHead(404);res.end();return;}
 res.writeHead(200,{'Content-Type':file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html; charset=utf-8'});res.end(fs.readFileSync(target));
});
const ok=(name,extra={})=>results.push({name,passed:true,...extra});
async function main(){
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const browser=await chromium.launch({headless:true,...(process.env.HEARTRADIO_BROWSER?{executablePath:process.env.HEARTRADIO_BROWSER}:{})});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1000},locale:'zh-CN',reducedMotion:'reduce'});
  const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/api/**',async route=>{
   const request=route.request(),url=new URL(request.url()),endpoint=url.pathname;let value={};
   if(request.method()==='POST')actions.push({endpoint,body:request.postDataJSON()});
   if(endpoint==='/api/state')value={nowMs:Date.now(),valid:true,bpm:76,connected:true,status:'正在接收心率',accuracy:3,eventAgeSeconds:1,packetAgeSeconds:1,validCount:96,packetCount:100,sequenceGaps:0,connections:1,disconnects:0,minimum:65,maximum:88,elapsedSeconds:600,transport:'rfcomm',points:[{timeMs:now-60000,bpm:72,segment:1},{timeMs:now,bpm:76,segment:1}],widgets:{},receiverSettings:{seconds:0}};
   else if(endpoint==='/api/connection')value=connection;
   else if(endpoint==='/api/reminder-policy')value=policy;
   else if(endpoint==='/api/reminders/history'){
    const q=(url.searchParams.get('q')||'').toLowerCase(),status=url.searchParams.get('status')||'';
    const found=rows.filter(row=>(!status||row.status===status)&&(!q||[row.reminderId,row.params.title,row.params.body].join('\n').toLowerCase().includes(q)));
    const size=20,pages=Math.max(1,Math.ceil(found.length/size)),number=Math.min(pages,Number(url.searchParams.get('page')||1));
    value={reminders:found.slice((number-1)*size,number*size),total:rows.length,capacity:128,matched:found.length,page:number,pages,statuses:[...new Set(rows.map(row=>row.status))],
      sync:{status:'complete',checked:44,total:44,pending:0,notFound:0,failed:0}};
   }else if(endpoint==='/api/reminders'){
    if(request.method()==='POST'){const body=request.postDataJSON();assert.equal(body.action,'status');assert.equal(body.fresh,true);value=rows.find(row=>row.reminderId===body.reminderId);}
    else value={templates,reminders:rows.slice(0,20),total:45,capacity:128};
   }else if(endpoint==='/api/health-timeline' || endpoint==='/api/health-history'){
    const date=url.searchParams.get('date') || url.searchParams.get('start_date');
    const start=Date.parse(date+'T00:00:00+08:00'), noon=start+12*3600000;
    value=endpoint==='/api/health-timeline' ? {
     endDate:date,startAtMs:start,endAtMs:start+86400000,deviceId:'fixture',availableDevices:['fixture'],
     heart:[[noon,70],[noon+120000,72]],heartRadio:[[noon+60000,80,1]],
     heartRadioMinutes:[[noon+60000,noon+60000,80,80,80,1]],activity:[],activityMinutes:[],sleep:[],
     stress:[[noon+60000+21000,36],[noon+180000+45000,48]]
    } : {startDate:date,results:{heart_rate:{status:'ok',days:[{date,samples:[
     {atMs:noon+17000,bpm:70,nativeQuality:6}]}]},
     stress:{status:'observations',days:[{date,recordedSamples:2,minimumScore:36,maximumScore:48,
     observations:[{observedAtMs:noon+81000,score:36},{observedAtMs:noon+225000,score:48}]}]}}};
   }else if(endpoint==='/api/receiver-settings')value={seconds:0};
   else if(endpoint==='/api/capability-duration')value={seconds:0};
   else if(endpoint==='/api/capability-service')value={running:true,connected:true,sharedInteraction:true,enabledOnStart:false,durationSettings:{seconds:0},remainingSeconds:null,syncStatus:'两端已同步'};
   else if(endpoint==='/api/data-settings')value=settings;
   else if(endpoint==='/api/data')value={schemaVersion:1,settings,measurements:{},bridge:connection,measurement:null,heartRate:null,latestStress:{score:36,observedAtMs:now}};
   else if(endpoint==='/api/history/catalog')value={days:[]};
   await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(value)});
  });
  const url=`http://127.0.0.1:${server.address().port}/`;
  await page.goto(url);await page.waitForFunction(()=>document.querySelector('.page-nav a[aria-current]')?.dataset.page==='overview');
  assert.equal(await page.locator('[data-page-panel]:visible').count(),1);ok('single_overview_on_start');
  await page.waitForFunction(()=>document.getElementById('hostNetworkName').textContent==='Wi-Fi · 测试 <Wi-Fi>');
  assert.equal(await page.locator('#hostBluetoothState').innerText(),'已关闭');
  assert.equal(await page.locator('#connectionBadge').innerText(),'已连接');ok('host_indicators_render_independently_of_watch_route');
  await page.screenshot({path:path.join(out,'overview-desktop.png'),fullPage:true});
  const pages=['device','heart-trend','health-history','reminders','reminder-history','settings'];
  for(const name of pages){
   await page.locator(`.page-nav [data-page="${name}"]`).click();await page.waitForFunction(name=>document.querySelector('.page-nav [aria-current]')?.dataset.page===name,name);
   assert.equal(await page.locator('[data-page-panel]:visible').count(),1);
   assert.equal(await page.locator(`[data-page-panel="${name}"]`).isVisible(),true);ok('independent_page_'+name);
  }
  await page.locator('.page-nav [data-page="health-history"]').click();
  await page.locator('#healthHeartLegend .series-radio').waitFor();
  const heartCanvas=page.locator('#healthHeartPlot');
  const sourceSelect=page.locator('#healthHeartSource');
  await heartCanvas.scrollIntoViewIfNeeded();
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  const nativePriority=await heartCanvas.evaluate(canvas=>canvas.toDataURL());
  await sourceSelect.selectOption('both');
  assert.equal(await heartCanvas.evaluate(canvas=>canvas.toDataURL()),nativePriority);
  await sourceSelect.selectOption('native');
  ok('heart_source_modes_share_the_same_linking_rule');
  const sourceHelp=page.locator('#healthHeartSourceHelp');
  const sourceTooltip=page.locator('#healthHeartSourceTooltip');
  assert.equal(await sourceTooltip.isVisible(),false);
  await sourceHelp.hover();
  await sourceTooltip.waitFor({state:'visible'});
  assert.match(await sourceTooltip.innerText(),/原厂优先.*HeartRadio 采集/s);
  assert.match(await sourceTooltip.innerText(),/两路对照/);
  await page.mouse.move(0,0);
  assert.equal(await sourceTooltip.isVisible(),false);
  await sourceHelp.focus();
  assert.equal(await sourceTooltip.isVisible(),true);
  await sourceHelp.blur();
  ok('heart_source_help_shows_on_hover_and_keyboard_focus');
  await page.locator('#healthTimelinePlots').scrollIntoViewIfNeeded();
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  const tracks=await page.locator('.health-chart-window').evaluateAll(items=>items.map(el=>el.getBoundingClientRect().toJSON()));
  for(let i=0;i<2;i++){
   await page.mouse.move(tracks[0].left+tracks[0].width*721.5/1440,(tracks[i].bottom+tracks[i+1].top)/2);
   assert.match(await page.locator('#healthCursorHeart').innerText(),/HeartRadio 80 BPM/);
   assert.match(await page.locator('#healthCursorTime').innerText(),/12:01/);
   assert.match(await page.locator('#healthCursorStress').innerText(),/36 分.*12:01:21/);
   assert.ok(await page.locator('#healthTimelineCursor').isVisible());
  }
  ok('native_priority_fills_missing_minute_in_chart_and_cursor_across_both_gaps');
  assert.match(await page.locator('#healthStressCaption').innerText(),/2 次点测/);
  await page.locator('#healthStressPlot').scrollIntoViewIfNeeded();
  assert.equal(await page.locator('#healthStressPlot').isVisible(),true);
  ok('pressure_points_share_the_health_cursor');
  await page.locator('#healthArchiveDetails > summary').click();
  await page.locator('#healthHistoryRows button').click();
  await page.locator('#healthDetailsMetric').selectOption('heart_rate');
  await page.locator('#healthDetailsRows td').first().waitFor();
  assert.match(await page.locator('#healthDetailsRows td').first().innerText(),/12:00:17/);
  await page.locator('#healthDetailsMetric').selectOption('stress');
  await page.waitForFunction(()=>document.querySelector('#healthDetailsRows').textContent.includes('36 分'));
  ok('pressure_detail_lists_original_points');
  ok('native_sample_detail_shows_beijing_time_with_seconds');
  await page.locator('.page-nav [data-page="reminders"]').click();await page.locator('#reminderBody').fill('切换页面后保留这段未发送内容');
  await page.locator('.page-nav [data-page="settings"]').click();await page.locator('details:has(#dataSettingsForm) > summary').click();await page.locator('#dataSeconds').fill('7');
  await page.locator('.page-nav [data-page="device"]').click();await page.locator('.page-nav [data-page="settings"]').click();
  assert.equal(await page.locator('#dataSeconds').inputValue(),'7');
  await page.locator('.page-nav [data-page="reminders"]').click();assert.equal(await page.locator('#reminderBody').inputValue(),'切换页面后保留这段未发送内容');ok('unsaved_form_values_survive_navigation');
  await page.locator('.page-nav [data-page="reminder-history"]').click();await page.waitForFunction(()=>document.querySelectorAll('.reminder-history-item').length===20);
  assert.match(await page.locator('#reminderHistoryCapacity').innerText(),/45\/128/);
  await page.locator('.reminder-history-item').first().locator('summary').click();
  assert.match(await page.locator('.reminder-history-item').first().innerText(),/电脑仍保留/);
  await page.screenshot({path:path.join(out,'reminder-history-desktop.png'),fullPage:true});ok('deleted_content_and_timestamp_visible');
  await page.locator('#reminderHistoryNext').click();await page.waitForFunction(()=>document.querySelector('#reminderHistoryPage').textContent.includes('第2/3页'));
  await page.locator('#reminderHistoryNext').click();await page.waitForFunction(()=>document.querySelectorAll('.reminder-history-item').length===5);ok('pagination_reaches_all_45_rows');
  await page.locator('#reminderHistoryQuery').fill('搜索目标');await page.locator('#reminderHistoryFilters button[type=submit]').click();
  await page.waitForFunction(()=>document.querySelectorAll('.reminder-history-item').length===1);assert.match(await page.locator('#reminderHistoryList').innerText(),/搜索目标/);ok('unicode_search');
  await page.locator('#reminderHistoryReset').click();await page.locator('#reminderHistoryStatus').selectOption('deleted');
  await page.waitForFunction(()=>document.querySelectorAll('.reminder-history-item').length===1 &&
   document.querySelector('.reminder-history-item summary').textContent.includes('手表已删除'));
  ok('deleted_status_filter');
  if(!await page.locator('.reminder-history-item').evaluate(element=>element.open))await page.locator('.reminder-history-item summary').click();
  await page.locator('.reminder-history-item button').click();await page.waitForFunction(()=>document.querySelector('#reminderHistoryMessage').textContent.includes('本条核对结果'));
  assert.equal(actions.length,1);assert.equal(actions[0].body.action,'status');ok('explicit_receipt_check_only_no_send_or_control');
  await page.locator('#reminderHistoryQuery').fill('<script>window.__xss=1</script>');await page.locator('#reminderHistoryFilters button[type=submit]').click();
  await page.waitForSelector('.history-empty-state');assert.equal(await page.evaluate(()=>window.__xss),undefined);ok('empty_search_and_plain_text_rendering');
  await page.locator('#reminderHistoryReset').click();await page.waitForSelector('.reminder-history-item');
  await page.locator('.page-nav [data-page="settings"]').click();await page.goBack();assert.equal(new URL(page.url()).hash,'#reminder-history');ok('browser_back');
  await page.reload();await page.waitForSelector('.reminder-history-item');assert.equal(await page.locator('[data-page-panel]:visible').getAttribute('data-page-panel'),'reminder-history');ok('direct_route_refresh');
  await page.goto(url+'#preferences');await page.waitForFunction(()=>document.querySelector('#settingsView').hidden===false);ok('legacy_deep_link');
  await page.locator('.page-nav [data-page="device"]').click();
  await page.locator('#dataFresh').check();
  await page.waitForFunction(()=>document.querySelector('#dataStressArchive').textContent.includes('36 分'));
  const stressButton=page.getByRole('button',{name:'测一次压力',exact:true});
  const stressReplies=[
   {ok:true,status:'ok',score:47,observedAtMs:now},
   {ok:false,status:'stock_no_valid_reading',message:'原厂提示“未测得有效值”，本次测量失败。'},
   {ok:false,status:'no_fresh_result',message:'本次测量失败，等待 65 秒仍未取得新分数。'},
   {ok:false,status:'accessibility_disabled'},
  ];
  const stressRequests=[];
  await page.route('**/api/tools',async route=>{
   stressRequests.push(route.request().postDataJSON());
   await route.fulfill({json:{stress:stressReplies.shift()}});
  });
  for(const [score,fragment] of [['47 分','准确度'],['—','未测得有效值'],['—','65 秒'],['—','无障碍设置']]){
   await stressButton.click();
   await page.waitForFunction(text=>document.querySelector('#dataResult_stress').textContent.includes(text),fragment);
   assert.equal(await page.locator('#dataStress').innerText(),score);
  }
  assert.deepEqual(stressRequests,Array.from({length:4},()=>({name:'get_user_state',arguments:{metrics:['stress']}})));
  ok('stress_fresh_score_timeout_stock_failure_permission_and_no_replay');
  await page.setViewportSize({width:390,height:844});
  for(const name of ['overview','device','reminders','reminder-history','settings','heart-trend','health-history']){
   await page.locator(`.page-nav [data-page="${name}"]`).click();
   const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth);
   assert.equal(overflow,false,'Horizontal overflow on '+name);ok('narrow_layout_'+name);
  }
  await page.locator('.page-nav [data-page="reminder-history"]').click();await page.waitForSelector('.reminder-history-item');
  await page.screenshot({path:path.join(out,'reminder-history-mobile.png'),fullPage:true});
  assert.deepEqual(errors,[]);ok('no_browser_exceptions');
  fs.writeFileSync(path.join(out,'validation.json'),JSON.stringify({scope:'actual static UI with synthetic APIs; no real watch or user writes',results,actions,errors},null,2)+'\n');
  console.log(`PASS ${results.length} browser checks; ${actions.length} explicit read-only receipt query`);
 }finally{await browser.close();}
}
main().catch(error=>{fs.writeFileSync(path.join(out,'failure.txt'),error.stack);console.error(error);process.exitCode=1}).finally(()=>server.close());
