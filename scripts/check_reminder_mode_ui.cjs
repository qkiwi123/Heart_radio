const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'dashboard/static/reminders.js'), 'utf8').replace(/\r\n/g,'\n');
const startup = "updateInputs();load();const loadTimer=setInterval(()=>{if(!window.heartRadioStopped)load()},2000);\n window.addEventListener('heartradio-session-stopped',()=>clearInterval(loadTimer));";
assert(source.includes(startup), 'Script startup anchor changed');
const nodes = new Map();
const sandbox = {document: {getElementById(id) {if (!nodes.has(id)) nodes.set(id, {textContent:'', disabled:false, addEventListener(){}});return nodes.get(id);}}};
vm.createContext(sandbox);
vm.runInContext(source.replace(startup, 'globalThis.renderForTest=render;'), sandbox);
const cases = [
 ['silent', 'issued', {mode:'silent',modeLabel:'静音模式',effectsAllowed:true,silentCompatibility:true}, {interruptionFilter:4,suppressedByDnd:false}, /静音模式/, /声音保持关闭/],
 ['dnd', 'notification_only_dnd', {mode:'do_not_disturb',modeLabel:'勿扰模式',effectsAllowed:false}, {interruptionFilter:4,suppressedByDnd:true}, /勿扰模式/, /勿扰限制：是/],
 ['normal', 'issued', {mode:'normal',modeLabel:'正常模式',effectsAllowed:true,soundImplemented:true}, {interruptionFilter:1,suppressedByDnd:false,soundRequested:true,soundIssued:true}, /正常模式/, /已提交系统播放/],
 ['volume_zero', 'issued', {mode:'normal',modeLabel:'正常模式',effectsAllowed:true,soundImplemented:true}, {interruptionFilter:1,soundRequested:true,soundIssued:false,soundSuppressedReason:'notification_volume_muted'}, /通知音量为零或已静音/, /声音遵守系统/],
 ['restricted', 'notification_only_system_restricted', {mode:'system_restricted',modeLabel:'系统通知限制',effectsAllowed:false}, {interruptionFilter:4,suppressedByDnd:false}, /系统通知限制/, /勿扰限制：否/],
 ['legacy', 'notification_only_dnd', null, {interruptionFilter:4,suppressedByDnd:true}, /旧版回执/, /未区分静音和勿扰/]
];
const results=[];
for(const [name,status,policy,extra,expected1,expected2] of cases) {
 const receipt={...extra,vibrationIssued:name==='silent',screenLaunchRequested:name==='silent'};
 if(policy) receipt.systemAlertPolicy=policy;
 sandbox.renderForTest({reminderId:'ui_'+name,status,createdAtMs:1,params:{title:'模式提示测试'},receipt});
 const output=nodes.get('reminderMessage').textContent+'\n'+nodes.get('reminderReceipt').textContent;
 assert.match(output, expected1);assert.match(output, expected2);
 if(name==='silent'||name==='legacy') assert.doesNotMatch(output,/勿扰限制：是/);
 results.push({name,passed:true,output});
}
const outputFile=process.argv[2];
if(outputFile) fs.writeFileSync(outputFile,JSON.stringify(results,null,2)+'\n');
console.log(`PASS ${results.length} receipt display cases (actual reminders.js render function)`);
