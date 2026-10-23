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
  const context=await browser.newContext({viewport:{width:1440,height:1000},locale:'zh-CN'});
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
   }else if(endpoint==='/api/receiver-settings')value={seconds:0};
   else if(endpoint==='/api/capability-duration')value={seconds:0};
   else if(endpoint==='/api/capability-service')value={running:true,connected:true,sharedInteraction:true,enabledOnStart:false,durationSettings:{seconds:0},remainingSeconds:null,syncStatus:'两端已同步'};
   else if(endpoint==='/api/data-settings')value=settings;
   else if(endpoint==='/api/data')value={schemaVersion:1,settings,measurements:{},bridge:connection,measurement:null,heartRate:null};
   else if(endpoint==='/api/history/catalog')value={days:[]};
   await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(value)});
  });
  const url=`http://127.0.0.1:${server.address().port}/`;
  await page.goto(url);await page.waitForFunction(()=>document.querySelector('.page-nav a[aria-current]')?.dataset.page==='overview');
  assert.equal(await page.locator('[data-page-panel]:visible').count(),1);ok('single_overview_on_start');
  await page.screenshot({path:path.join(out,'overview-desktop.png'),fullPage:true});
  const pages=['device','heart-trend','heart-history','reminders','reminder-history','settings'];
  for(const name of pages){
   await page.locator(`.page-nav [data-page="${name}"]`).click();await page.waitForFunction(name=>document.querySelector('.page-nav [aria-current]')?.dataset.page===name,name);
   assert.equal(await page.locator('[data-page-panel]:visible').count(),1);
   assert.equal(await page.locator(`[data-page-panel="${name}"]`).isVisible(),true);ok('independent_page_'+name);
  }
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
  await page.waitForFunction(()=>document.querySelectorAll('.reminder-history-item').length===1);assert.match(await page.locator('#reminderHistoryList').innerText(),/手表已删除/);ok('deleted_status_filter');
  if(!await page.locator('.reminder-history-item').evaluate(element=>element.open))await page.locator('.reminder-history-item summary').click();
  await page.locator('.reminder-history-item button').click();await page.waitForFunction(()=>document.querySelector('#reminderHistoryMessage').textContent.includes('本条核对结果'));
  assert.equal(actions.length,1);assert.equal(actions[0].body.action,'status');ok('explicit_receipt_check_only_no_send_or_control');
  await page.locator('#reminderHistoryQuery').fill('<script>window.__xss=1</script>');await page.locator('#reminderHistoryFilters button[type=submit]').click();
  await page.waitForSelector('.history-empty-state');assert.equal(await page.evaluate(()=>window.__xss),undefined);ok('empty_search_and_plain_text_rendering');
  await page.locator('#reminderHistoryReset').click();await page.waitForSelector('.reminder-history-item');
  await page.locator('.page-nav [data-page="settings"]').click();await page.goBack();assert.equal(new URL(page.url()).hash,'#reminder-history');ok('browser_back');
  await page.reload();await page.waitForSelector('.reminder-history-item');assert.equal(await page.locator('[data-page-panel]:visible').getAttribute('data-page-panel'),'reminder-history');ok('direct_route_refresh');
  await page.goto(url+'#preferences');await page.waitForFunction(()=>document.querySelector('#settingsView').hidden===false);ok('legacy_deep_link');
  await page.setViewportSize({width:390,height:844});
  for(const name of ['overview','device','reminders','reminder-history','settings','heart-trend','heart-history']){
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
