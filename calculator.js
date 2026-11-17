/* Percentages below are points of one weekly allowance, not tokens. */
(function (scope) {
  'use strict';
  const HOUR = 3600;
  const WEEK = 168 * HOUR;
  const EPS = 1e-7;

  function demandCurve(model, start, end, strength) {
    const times = [start];
    const sums = [0];
    let t = start;
    while (t < end - EPS) {
      const local = new Date((t + 8 * HOUR) * 1000);
      const slot = Math.floor(local.getUTCHours() / 3);
      const day = (local.getUTCDay() + 6) % 7; // Monday = 0, matching Python datetime.weekday().
      const boundary = Date.UTC(local.getUTCFullYear(), local.getUTCMonth(), local.getUTCDate(), (slot + 1) * 3) / 1000 - 8 * HOUR;
      const next = Math.min(end, boundary);
      const rate = model.hourlyByThreeHours[day][slot] * strength;
      sums.push(sums[sums.length - 1] + rate * (next - t) / HOUR);
      times.push(next);
      t = next;
    }
    function at(x) {
      if (x <= start) return 0;
      if (x >= end) return sums[sums.length - 1];
      let lo = 0, hi = times.length - 1;
      while (hi - lo > 1) {
        const mid = (lo + hi) >> 1;
        if (times[mid] <= x) lo = mid; else hi = mid;
      }
      return sums[lo] + (sums[hi] - sums[lo]) * (x - times[lo]) / (times[hi] - times[lo]);
    }
    function reaches(a, b, amount) {
      if (amount <= EPS) return a;
      if (at(b) - at(a) < amount - EPS) return null;
      let lo = a, hi = b;
      for (let k = 0; k < 45; k++) {
        const mid = (lo + hi) / 2;
        if (at(mid) - at(a) < amount) lo = mid; else hi = mid;
      }
      return hi;
    }
    return { at, reaches, total: sums[sums.length - 1] };
  }

  function segment(start, end, remaining, refresh, demand, includeEvents = false) {
    let t = start, cap = remaining, clock = refresh, served = 0;
    const events = [];
    while (clock <= start + EPS) clock += WEEK;
    while (t < end - EPS) {
      const next = Math.min(end, clock);
      const wanted = Math.max(0, demand.at(next) - demand.at(t));
      const used = Math.min(cap, wanted);
      if (includeEvents && wanted > cap + EPS) {
        const at = cap > EPS ? demand.reaches(t, next, cap) : t;
        events.push({ type: 'blocked', time: at, end: next });
      }
      cap = Math.max(0, cap - used);
      served += used;
      t = next;
      if (t >= clock - EPS) {
        cap = 100;
        if (includeEvents) events.push({ type: 'natural', time: clock });
        clock += WEEK;
      }
    }
    return { served, remaining: cap, refresh: clock, events };
  }

  function compare(a, b) {
    if (!b) return true;
    if (a.served > b.served + EPS) return true;
    if (a.served < b.served - EPS) return false;
    if (a.count !== b.count) return a.count < b.count;
    if (a.waste < b.waste - EPS) return true;
    if (a.waste > b.waste + EPS) return false;
    return a.time > b.time;
  }

  function optimize(model, options = {}) {
    const start = options.start ?? model.account.capturedAt;
    const end = start + (options.days ?? 28) * 24 * HOUR;
    const strength = options.strength ?? 1;
    const remaining = options.remaining ?? model.account.remaining;
    const refresh = options.refresh ?? model.account.nextRefresh;
    const expiries = (options.expiries ?? model.account.expiries).filter(e => e > start).sort((a,b) => a-b);
    const demand = demandCurve(model, start, end, strength);
    const base = { time: start, remaining, refresh, served: 0, count: 0, waste: 0, prev: null, card: -1 };
    const grid = [start];
    for (const time of options.candidateTimes ?? []) {
      if (Number.isFinite(time) && time >= start && time < end - EPS) grid.push(time);
    }
    for (let t = Math.ceil(start / HOUR) * HOUR; t < end - EPS; t += HOUR) {
      if (t > start + EPS) grid.push(t);
    }
    // Exact current depletion and each deadline supplement the hourly grid.
    const depletion = demand.reaches(start, Math.min(end, refresh), remaining);
    if (depletion !== null && depletion < end - EPS) grid.push(depletion);
    for (const expiry of expiries) if (expiry - HOUR > start && expiry - HOUR < end) grid.push(expiry - HOUR);
    const candidates = [...new Set(grid)].sort((a,b) => a-b);

    function solve(forceNow) {
      const nodes = [];
      let origin = base;
      let firstCard = 0;
      if (forceNow) {
        if (!expiries.length || remaining >= 80 - EPS) return null;
        origin = { time: start, remaining: 100, refresh: start + WEEK, served: 0,
          count: 1, waste: remaining, prev: base, card: 0, before: remaining };
        firstCard = 1;
      }
      nodes.push(origin);
      for (let card = firstCard; card < expiries.length; card++) {
        const previous = nodes.slice();
        const layer = [];
        for (const time of candidates) {
          // One-hour redemption margin avoids advice at an expiry boundary.
          if (time > expiries[card] - HOUR + EPS) continue;
          let best = null;
          for (const node of previous) {
            if (node.card >= card || node.time > time + EPS) continue;
            const travel = segment(node.time, time, node.remaining, node.refresh, demand);
            if (travel.remaining >= 80 - EPS) continue;
            const option = { time, remaining: 100, refresh: time + WEEK,
              served: node.served + travel.served, count: node.count + 1,
              waste: node.waste + travel.remaining, prev: node, card, before: travel.remaining };
            if (compare(option, best)) best = option;
          }
          if (best) layer.push(best);
        }
        nodes.push(...layer);
      }
      let winner = null;
      for (const node of nodes) {
        const tail = segment(node.time, end, node.remaining, node.refresh, demand);
        const score = Object.assign({}, node, { served: node.served + tail.served });
        if (compare(score, winner?.score)) winner = { score, node, tail };
      }
      const schedule = [];
      for (let node = winner.node; node && node.card >= 0; node = node.prev) {
        schedule.unshift({ time: node.time, card: node.card, before: node.before,
          expiry: expiries[node.card], refresh: node.refresh });
      }
      return { served: winner.score.served, count: schedule.length, waste: winner.score.waste,
        schedule, remaining: winner.tail.remaining, refresh: winner.tail.refresh };
    }

    const optimal = solve(false);
    const now = options.skipNow ? null : solve(true);
    const baseline = segment(start, end, remaining, refresh, demand);

    function trace(schedule, until = end) {
      let time = start, cap = remaining, clock = refresh, total = 0;
      const events = [];
      for (const reset of schedule) {
        if (reset.time > until) break;
        const part = segment(time, reset.time, cap, clock, demand, true);
        total += part.served;
        events.push(...part.events, Object.assign({type:'banked'}, reset));
        time = reset.time; cap = 100; clock = reset.time + WEEK;
      }
      const tail = segment(time, until, cap, clock, demand, true);
      return { served: total + tail.served, events: [...events, ...tail.events] };
    }
    const rows = [];
    for (let t = start; t <= end + EPS; t += 24 * HOUR) {
      const day = (t-start)/(24*HOUR);
      rows.push([day, demand.at(t)/100, trace([] , t).served/100, trace(optimal.schedule, t).served/100]);
    }
    return { start, end, demand: demand.total, depletion, optimal, now,
      baseline: baseline.served, rows, events: trace(optimal.schedule).events,
      modelRates: model.dailyRates.map(x => x*strength), expiries };
  }
  function calculate(model, options = {}) {
    const start = options.start ?? model.account.capturedAt;
    const end = start + (options.days ?? 28)*24*HOUR;
    const remaining = options.remaining ?? model.account.remaining;
    const refresh = options.refresh ?? model.account.nextRefresh;
    const unit = 1; // One percentage point of the full weekly allowance.
    const comparable = Object.assign({}, options);
    if (remaining >= unit) {
      const demand = demandCurve(model, start, end, options.strength ?? 1);
      const until = Math.min(end, refresh);
      // Both counterfactuals use the same candidate times. A finer search on
      // only one side would otherwise look like a benefit from using quota.
      comparable.candidateTimes = (options.candidateTimes ?? []).concat(
        [remaining, remaining-unit].map(cap => demand.reaches(start, until, cap)));
    }
    const result = optimize(model, comparable);
    if (remaining < unit) {
      result.marginal = { available: false, unit, reason: 'insufficientQuota' };
      return result;
    }
    const after = optimize(model, Object.assign({}, comparable,
      { remaining: remaining-unit, skipNow: true }));
    const futureGain = after.optimal.served-result.optimal.served;
    function summary(plan, usedNow) {
      return { future: plan.served, total: plan.served+usedNow, count: plan.count,
        firstReset: plan.schedule.length ? plan.schedule[0].time : null };
    }
    result.marginal = {
      available: true, unit,
      before: summary(result.optimal, 0), after: summary(after.optimal, unit),
      futureGain, totalGain: unit+futureGain,
    };
    return result;
  }
  scope.BankResetCore = { demandCurve, segment, optimize, calculate, HOUR, WEEK };
})(typeof globalThis === 'undefined' ? this : globalThis);
