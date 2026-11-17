const assert=require('node:assert/strict');
require('../calculator.js');
const {optimize,calculate,demandCurve,segment,HOUR,WEEK}=BankResetCore;
const start=Date.UTC(2026,10,16)/1000;
const model={hourlyByThreeHours:Array.from({length:7},()=>Array(8).fill(100/24)),dailyRates:Array(7).fill(100),
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
const weekly={hourlyByThreeHours:Array.from({length:7},(_,day)=>Array(8).fill((day+1)/24))};
const curve=demandCurve(weekly,monday,monday+7*24*HOUR,1);
for(let day=0;day<7;day++)assert(Math.abs(curve.at(monday+(day+1)*24*HOUR)-curve.at(monday+day*24*HOUR)-(day+1))<1e-6);
assert(Math.abs(curve.total-28)<1e-6);
const rollover=demandCurve(weekly,monday-HOUR,monday+HOUR,1);
assert(Math.abs(rollover.total-8/24)<1e-6);
console.log('Passed: seven distinct weekdays, Beijing midnight/Sunday wrap, weekly-clock changes, expiry, 18 consumption scenarios, and exhaustive two-reset comparison.');

// Variable extra usage is counted once, with future demand held unchanged.
const allowance={hourlyByThreeHours:Array.from({length:7},()=>Array(8).fill(1/24)),
  dailyRates:Array(7).fill(1),account:{capturedAt:start,remaining:90,nextRefresh:start+WEEK,expiries:[]}};
const spare=calculate(allowance,{days:1,remaining:20});
assert.equal(spare.marginal.availableUsage,20);
assert.equal(spare.marginal.maxUsage,19);
assert.equal(spare.marginal.points[0].usage,0);
assert.equal(spare.marginal.points.at(-1).usage,19);
assert.equal(spare.marginal.limit,'futureZero');
assert(Math.abs(spare.marginal.points.at(-1).futureGain)<1e-6);
const five=spare.marginal.points.find(point=>point.usage===5);
assert(Math.abs(five.totalGain-5)<1e-6);
assert(Math.abs(five.futureGain)<1e-6);
assert(Math.abs(spare.marginal.points.at(-1).totalGain-19)<1e-6);
assert(Math.abs(spare.demand-1)<1e-6);
const tight=calculate(allowance,{days:1,remaining:.5});
assert(tight.marginal.available);
assert(tight.marginal.points.every(point=>Math.abs(point.totalGain)<1e-6));
assert.equal(tight.marginal.maxUsage,0);
assert.equal(tight.marginal.availableUsage,.5);
assert.equal(tight.marginal.points[0].futureGain,0);
assert.equal(calculate(allowance,{remaining:0}).marginal.available,false);
const deadline={hourlyByThreeHours:Array.from({length:7},(_,day)=>Array(8).fill(day===0?0:100/24)),
  dailyRates:[0,100,100,100,100,100,100],
  account:{capturedAt:start,remaining:80.5,nextRefresh:start+WEEK,expiries:[start+2*HOUR]}};
const unlocked=calculate(deadline,{days:3});
assert.equal(unlocked.marginal.before.count,0);
assert.equal(unlocked.marginal.limit,'quota');
const boundary=unlocked.marginal.points.find(point=>point.usage===.5);
const unlockedPoint=unlocked.marginal.points.find(point=>point.usage===.51);
assert.equal(boundary.after.count,0);
assert.equal(unlockedPoint.after.count,1);
assert(Math.abs(boundary.totalGain)<1e-6);
assert(Math.abs(unlockedPoint.totalGain-20.01)<1e-6);
for(const cap of [1,20,79.5,80,94,100]) {
  const result=calculate(model,{remaining:cap,days:14});
  const marginal=result.marginal;
  let previous=-Infinity;
  for(const point of marginal.points) {
    assert(point.usage>=0 && point.usage<=cap);
    assert(point.totalGain>=-1e-6);
    assert(point.totalGain>=previous-1e-6);
    assert(Math.abs(point.after.total-marginal.before.total-point.totalGain)<1e-6);
    assert(Math.abs(point.futureGain+point.usage-point.totalGain)<1e-6);
    previous=point.totalGain;
  }
  assert.equal(marginal.before.total,result.optimal.served);
}
// The last nonnegative point is refined beyond the coarse scan, with one grid.
const partial=calculate(allowance,{days:1,remaining:19.43});
assert.equal(partial.marginal.limit,'futureZero');
assert(Math.abs(partial.marginal.maxUsage-18.43)<.001);
assert(Math.abs(partial.marginal.points.at(-1).futureGain)<1e-6);
console.log('Passed: net-return curves ending at zero future change, fixed future demand, no double count, zero-gain plateaus, sub-one-percent quota, strict deadline unlock, and shared-candidate monotonicity.');
