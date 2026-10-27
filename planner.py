"""Day planner: which open jobs to fly, in what order, and when, so all spraying falls in usable weather.
An orienteering problem with time windows: greedy insertion, then local search under a time budget.
See SPEC.md "Planner formulation"."""
import bisect, math, random, time
from geo import haversine_m, M_PER_MILE

SLOT_MIN = 5
SLOTS_PER_DAY = 24 * 60 // SLOT_MIN
PRIORITY_WEIGHT = {1: 1.0, 2: 2.0, 3: 4.0}     # normal, high, urgent

DEFAULT_CREW = {
    "base_lat": None, "base_lon": None, "start": "06:00", "end": "19:00",
    "acres_per_hour": 40.0, "setup_min": 20.0, "road_mph": 45.0, "detour": 1.3,
    "allow_pause": True, "allow_caution": False,
}


def hhmm_to_min(s):
    h, m = (s or "0:0").split(":")[:2]
    return int(h) * 60 + int(m)


def min_to_hhmm(m):
    m = int(round(m))
    return f"{m // 60:02d}:{m % 60:02d}"


class Plan:
    def __init__(self, jobs, usable, crew):
        """jobs: [{id, name, lat, lon, acres, priority}]; usable: {job_id: [bool] * SLOTS_PER_DAY}; crew: DEFAULT_CREW-like."""
        self.crew = {**DEFAULT_CREW, **{k: v for k, v in (crew or {}).items() if v is not None}}
        c = self.crew
        self.jobs = {j["id"]: j for j in jobs}
        self.ids = [j["id"] for j in jobs]
        self.start = hhmm_to_min(c["start"])
        self.end = hhmm_to_min(c["end"])
        self.end_slot = self.end // SLOT_MIN
        base = (c["base_lat"], c["base_lon"])
        if base[0] is None or base[1] is None:   # no base set: start at the first job's field
            base = (jobs[0]["lat"], jobs[0]["lon"]) if jobs else (0, 0)
        self.base = base
        self.rate = max(1e-6, float(c["acres_per_hour"]))
        self.setup = float(c["setup_min"])
        self.speed = max(1e-6, float(c["road_mph"]))
        self.detour = float(c["detour"])
        self.pause = bool(c["allow_pause"])
        self.k = {}       # spray slots needed
        self.usable_pos = {}
        self.runs = {}
        for jid in self.ids:
            j = self.jobs[jid]
            self.k[jid] = max(1, math.ceil(j["acres"] / self.rate * 60 / SLOT_MIN))
            u = list(usable.get(jid) or [False] * SLOTS_PER_DAY)[:SLOTS_PER_DAY]
            u += [False] * (SLOTS_PER_DAY - len(u))
            for s in range(self.end_slot, SLOTS_PER_DAY):
                u[s] = False
            self.usable_pos[jid] = [s for s, ok in enumerate(u) if ok]
            run = [0] * (SLOTS_PER_DAY + 1)
            for s in range(SLOTS_PER_DAY - 1, -1, -1):
                run[s] = run[s + 1] + 1 if u[s] else 0
            self.runs[jid] = run
        self._tt = {}

    # ----- travel -----
    def travel_min(self, a, b):
        key = (a, b)
        if key not in self._tt:
            pa = self.base if a is None else (self.jobs[a]["lat"], self.jobs[a]["lon"])
            pb = self.base if b is None else (self.jobs[b]["lat"], self.jobs[b]["lon"])
            if pa == pb:
                self._tt[key] = 0.0
            else:
                miles = haversine_m(pa[0], pa[1], pb[0], pb[1]) / M_PER_MILE * self.detour
                self._tt[key] = miles / self.speed * 60
        return self._tt[key]

    # ----- spraying at one field -----
    def spray(self, jid, ready_min):
        """Earliest (start_slot, finish_slot) spraying job jid when the crew is set up at ready_min, or None."""
        t = math.ceil(ready_min / SLOT_MIN - 1e-9)
        k = self.k[jid]
        if self.pause:
            pos = self.usable_pos[jid]
            i = bisect.bisect_left(pos, t)
            if i + k - 1 >= len(pos):
                return None
            return pos[i], pos[i + k - 1] + 1
        run = self.runs[jid]
        for s in range(t, self.end_slot - k + 1):
            if run[s] >= k:
                return s, s + k
        return None

    # ----- route simulation -----
    def simulate(self, route):
        """Returns (cost_tuple, stops) or (None, None) when the route is infeasible."""
        t = float(self.start)
        prev = None
        travel = 0.0
        value = 0.0
        stops = []
        for jid in route:
            tr = self.travel_min(prev, jid)
            arrive = t + tr
            ready = arrive + self.setup
            sp = self.spray(jid, ready)
            if sp is None:
                return None, None
            s, f = sp
            if f * SLOT_MIN > self.end:
                return None, None
            travel += tr
            j = self.jobs[jid]
            value += PRIORITY_WEIGHT.get(int(j.get("priority") or 1), 1.0) * j["acres"]
            stops.append({"job_id": jid, "arrive": arrive, "ready": ready, "start": s * SLOT_MIN, "finish": f * SLOT_MIN,
                          "travel": tr, "wait": s * SLOT_MIN - ready, "paused": (f - s - self.k[jid]) * SLOT_MIN})
            t = f * SLOT_MIN
            prev = jid
        back = self.travel_min(prev, None) if route else 0.0
        return (-round(value, 6), round(travel + back, 3), t), stops

    def cost(self, route):
        return self.simulate(route)[0]

    # ----- search -----
    def greedy(self, route=None, pool=None):
        route = list(route or [])
        pool = [j for j in (pool if pool is not None else self.ids) if j not in route]
        cur = self.cost(route)
        while pool:
            best = None
            for jid in pool:
                j = self.jobs[jid]
                gain = PRIORITY_WEIGHT.get(int(j.get("priority") or 1), 1.0) * j["acres"]
                for p in range(len(route) + 1):
                    r = route[:p] + [jid] + route[p:]
                    c = self.cost(r)
                    if c is None:
                        continue
                    added = max(1.0, (c[2] - cur[2]) + (c[1] - cur[1]) + 1.0)
                    key = (gain / added, -c[1], -c[2])
                    if best is None or key > best[0]:
                        best = (key, r, c, jid)
            if best is None:
                break
            _, route, cur, jid = best
            pool.remove(jid)
        return route

    def solve(self, budget_s=0.8, seed=1):
        rng = random.Random(seed)
        best = self.greedy()
        best_c = self.cost(best)
        cur, cur_c = list(best), best_c
        deadline = time.time() + budget_s
        n_iter = 0
        while time.time() < deadline and n_iter < 20000 and self.ids:
            n_iter += 1
            out = [j for j in self.ids if j not in cur]
            move = rng.random()
            cand = list(cur)
            if move < 0.25 and len(cand) >= 2:              # relocate
                i, j = rng.randrange(len(cand)), rng.randrange(len(cand))
                cand.insert(j, cand.pop(i))
            elif move < 0.45 and len(cand) >= 2:            # swap
                i, j = rng.sample(range(len(cand)), 2)
                cand[i], cand[j] = cand[j], cand[i]
            elif move < 0.6 and len(cand) >= 3:             # 2-opt
                i, j = sorted(rng.sample(range(len(cand)), 2))
                cand[i:j + 1] = reversed(cand[i:j + 1])
            elif move < 0.75 and out:                       # insert or replace with an unscheduled job
                u = rng.choice(out)
                if cand and rng.random() < 0.5:
                    cand[rng.randrange(len(cand))] = u
                else:
                    cand.insert(rng.randrange(len(cand) + 1), u)
            else:                                           # ruin and recreate
                for _ in range(min(len(cand), rng.randint(1, 3))):
                    cand.pop(rng.randrange(len(cand)))
                cand = self.greedy(cand)
            c = self.cost(cand)
            if c is None:
                continue
            if c <= cur_c:
                cur, cur_c = cand, c
                if c < best_c:
                    best, best_c = list(cand), c
            elif rng.random() < 0.02:                       # occasional restart from the best
                cur, cur_c = list(best), best_c
        return best

    def why_not(self, jid):
        k = self.k[jid]
        if k * SLOT_MIN > self.end - self.start:
            return f"Needs {k * SLOT_MIN / 60:.1f} h of spraying, longer than the crew day"
        if self.pause:
            avail = len([s for s in self.usable_pos[jid] if s * SLOT_MIN >= self.start])
            if avail < k:
                return f"Only {avail * SLOT_MIN / 60:.1f} h of usable weather in crew hours; needs {k * SLOT_MIN / 60:.1f} h"
        else:
            longest = max([self.runs[jid][s] for s in range(self.start // SLOT_MIN, self.end_slot)] or [0])
            if longest < k:
                return f"Longest usable window {longest * SLOT_MIN / 60:.1f} h; needs {k * SLOT_MIN / 60:.1f} h unbroken"
        alone = self.cost([jid])
        if alone is None:
            return "Usable weather comes too late to finish after driving there"
        return "Doesn't fit around higher-value jobs today"

    def result(self, route):
        c, stops = self.simulate(route)
        for s in stops:
            j = self.jobs[s["job_id"]]
            s.update({"name": j.get("name"), "acres": j["acres"], "priority": j.get("priority", 1), "lat": j["lat"], "lon": j["lon"],
                      "arrive_txt": min_to_hhmm(s["arrive"]), "start_txt": min_to_hhmm(s["start"]), "finish_txt": min_to_hhmm(s["finish"])})
        back = self.travel_min(route[-1], None) if route else 0.0
        unscheduled = [{"job_id": j, "name": self.jobs[j].get("name"), "acres": self.jobs[j]["acres"], "reason": self.why_not(j)}
                       for j in self.ids if j not in route]
        total_acres = sum(s["acres"] for s in stops)
        return {
            "stops": stops, "unscheduled": unscheduled,
            "acres": round(total_acres, 1), "of_acres": round(sum(j["acres"] for j in self.jobs.values()), 1),
            "travel_min": round(c[1], 1) if c else 0, "return_min": round(back, 1),
            "finish_txt": min_to_hhmm(stops[-1]["finish"]) if stops else None,
            "base": {"lat": self.base[0], "lon": self.base[1]},
        }


def plan_day(jobs, usable, crew, budget_s=0.8, seed=1):
    p = Plan(jobs, usable, crew)
    route = p.solve(budget_s=budget_s, seed=seed) if jobs else []
    return p.result(route)
