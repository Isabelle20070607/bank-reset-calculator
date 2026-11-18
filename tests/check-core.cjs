const assert=require('node:assert/strict');
require('../calculator.js');
const {optimize,demandCurve,segment,HOUR,WEEK}=BankResetCore;
const start=Date.UTC(2026,10,16)/1000;
const model={cycleDays:7,slotHours:3,hourlyRates:Array.from({length:7},()=>Array(8).fill(100/24)),dailyRates:Array(7).fill(100),
  account:{capturedAt:start,remaining:0,nextRefresh:start+12*HOUR,expiries:[start+10*24*HOUR]}};
const changedClock=optimize(model,{days:1});
assert(Math.abs(changedClock.baseline-50)<1e-6);
assert(Math.abs(changedClock.now.served-100)<1e-6);
assert.equal(changedClock.now.schedule[0].refresh,start+WEEK);
for(const strength of [0,.25,.5,1,2,3])for(const days of [14,28,42]){
  const result=optimize(model,{strength,days});
  assert(result.optimal.served<=result.demand+1e-6);
  assert(result.optimal.served>=result.baseline-1e-6);
  if(result.now)assert(result.optimal.served>=result.now.served-1e-6);
  assert(result.rows.every(row=>row.every(Number.isFinite)));
  result.optimal.schedule.forEach(step=>{assert(step.time<=step.expiry-HOUR+1e-6);assert(step.before<80);});
}
assert.equal(optimize(model,{expiries:[]}).optimal.count,0);
assert.equal(optimize(model,{strength:0}).optimal.count,0);
const small={...model,account:{...model.account,nextRefresh:start+40*HOUR,expiries:[start+12*HOUR,start+20*HOUR]}};
const demand=demandCurve(small,start,start+24*HOUR,1);let best=0;
function visit(card,time,cap,refresh,used){best=Math.max(best,used+segment(time,start+24*HOUR,cap,refresh,demand).served);if(card>=2)return;visit(card+1,time,cap,refresh,used);for(let t=time;t<=small.account.expiries[card]-HOUR;t+=HOUR){const part=segment(time,t,cap,refresh,demand);if(part.remaining<80)visit(card+1,t,100,t+WEEK,used+part.served);}}
visit(0,start,0,start+40*HOUR,0);
assert(Math.abs(optimize(small,{days:1}).optimal.served-best)<1e-6);
// Monday-first Python profile indices must match Beijing dates, including Sunday wrap.
const monday=Date.UTC(2026,10,15,16)/1000;
const weekly={cycleDays:7,slotHours:3,hourlyRates:Array.from({length:7},(_,day)=>Array(8).fill((day+1)/24))};
const curve=demandCurve(weekly,monday,monday+7*24*HOUR,1);
for(let day=0;day<7;day++)assert(Math.abs(curve.at(monday+(day+1)*24*HOUR)-curve.at(monday+day*24*HOUR)-(day+1))<1e-6);
assert(Math.abs(curve.total-28)<1e-6);
const rollover=demandCurve(weekly,monday-HOUR,monday+HOUR,1);
assert(Math.abs(rollover.total-8/24)<1e-6);
console.log('Passed: seven distinct weekdays, Beijing midnight/Sunday wrap, weekly-clock changes, expiry, 18 consumption scenarios, and exhaustive two-reset comparison.');

const anchor=4*24*HOUR-8*HOUR; // Beijing Monday 1970-01-05.
const fortnight={cycleDays:14,slotHours:3,hourlyRates:Array.from({length:14},(_,day)=>Array(8).fill((day+1)/24))};
const twoWeeks=demandCurve(fortnight,anchor,anchor+15*24*HOUR,1);
for(let day=0;day<15;day++) assert(Math.abs(twoWeeks.at(anchor+(day+1)*24*HOUR)-twoWeeks.at(anchor+day*24*HOUR)-(day%14+1))<1e-6);
const holiday={cycleDays:1,slotHours:1,hourlyRates:[Array.from({length:24},(_,hour)=>hour+1)]};
const dayCurve=demandCurve(holiday,monday,monday+25*HOUR,1);
assert(Math.abs(dayCurve.at(monday+24*HOUR)-300)<1e-6);
assert(Math.abs(dayCurve.total-301)<1e-6);
const transition={cycleDays:1,slotHours:1,hourlyRates:[Array(24).fill(2)],switchAt:monday+6.5*HOUR,
  normalAfter:{cycleDays:7,slotHours:3,hourlyRates:Array.from({length:7},()=>Array(8).fill(.5))}};
assert(Math.abs(demandCurve(transition,monday,monday+24*HOUR,1).total-21.75)<1e-6);
assert(Math.abs(demandCurve({...transition,normalAfter:null},monday,monday+6*HOUR,1).total-12)<1e-6);
assert.throws(()=>demandCurve({...transition,normalAfter:null},monday,monday+7*HOUR,1),/恢复常规模式后的样本不足/);
console.log('Passed: fourteen-day phases, hourly daily holiday repetition, and exact scheduled return across different slot sizes.');
