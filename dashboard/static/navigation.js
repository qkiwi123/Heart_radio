'use strict';
(() => {
 const pages={
  overview:['总览与连接','查看当前心率，独立控制连接、连续心率和 Agent 交互。'],
  device:['按需读取手表','各项数据独立读取；进入页面不会启动新的测量。'],
  'heart-trend':['心率趋势','查看本次采集的有效心率，空档不补值。'],
  'heart-history':['心率历史','按北京时间的年月日与采集轮次回看记录。'],
  reminders:['发送手表提醒','设置本条内容与效果，查看最近一次发送的回执。'],
  'reminder-history':['提醒历史','浏览电脑保留的全部提醒，核对手表上的处理状态。'],
  settings:['设置与桌面小窗','管理接收时长、交互时长、数据读取与桌面显示。']
 };
 const aliases={liveView:'overview',deviceData:'device',heartTrend:'heart-trend',historyView:'heart-history',sendReminder:'reminders',preferences:'settings'};
 const positions=new Map();let current=null;
 function navigate(focus=false){
  const hash=location.hash.slice(1),page=aliases[hash]||hash;
  if(hash==='pageTitle'&&current)return;
  const next=Object.hasOwn(pages,page)?page:'overview';
  if(next===current)return;
  if(current)positions.set(current,window.scrollY);
  current=next;
  document.querySelectorAll('[data-page-panel]').forEach(panel=>{panel.hidden=panel.dataset.pagePanel!==next});
  document.querySelectorAll('.page-nav [data-page]').forEach(link=>{
   if(link.dataset.page===next)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');
  });
  $('pageTitle').textContent=pages[next][0];$('pageSubtitle').textContent=pages[next][1];
  document.title=pages[next][0]+' · HeartRadio';
  $('liveExport').hidden=!['overview','heart-trend'].includes(next);
  if(next==='heart-history'){
   if(!historyUi.catalog)loadHistoryCatalog();else if(historyUi.result)drawHistory(historyUi.result);
  }
  if(next==='heart-trend'&&lastState)chart(lastState);
  window.dispatchEvent(new CustomEvent('heartradio-page-change',{detail:{page:next}}));
  if(focus)$('pageTitle').focus({preventScroll:true});
  window.scrollTo({top:positions.get(next)||0,behavior:'instant'});
 }
 // Hash routes survive refresh and support browser back/forward without resetting forms.
 window.addEventListener('hashchange',()=>navigate(true));
 navigate();
})();
