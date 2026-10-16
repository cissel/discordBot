"""
mm_env.py
=========
Gymnasium environment for training an RL market-making policy for the SOXL
paper-trading `mm` bot (see python/marketMaker.py).

WHY A SIMULATOR (not pure historical replay):
  The real cycle log (outputs/markets/mm_cycle_log_SOXL.csv) only has mid/
  spread snapshots every ~5 min and fill COUNTS, not the underlying NBBO tick
  path or queue dynamics. There's no way to know "would a resting order at
  price X have filled in the next 5 min" from that log alone. So the primary
  training mode is a calibrated synthetic simulator (classic Avellaneda-
  Stoikov style: GBM mid-price + distance-based fill-intensity model), with
  parameters pulled directly from real observed stats so episodes look like
  actual SOXL days. A `historical` mode replays the real mid/spread PATH
  (for eval realism) but still uses the synthetic fill-probability model,
  since real fill granularity doesn't exist. A `mixed` mode (Oct 2026,
  TRAINING default) blends historical-day replay with the synthetic
  simulator during TRAINING itself (not just eval), so the policy sees real
  SOXL idiosyncrasies (whatever intraday structure synthetic GBM misses)
  as part of what it optimizes against, not just as a held-out eval set.

ENV REALISM UPGRADE (Oct 2026, v6): the original synthetic simulator was a
single fixed-vol-regime GBM (ret_std ~ uniform(0.6,1.5) x calibrated vol
every day, independent spread draw). Two realism gaps fixed:
  1. VOL REGIMES + JUMPS: real markets have fat-tailed, regime-dependent
     vol (long calm stretches + occasional violent days), not a tight
     uniform multiplier. _simulate_day now draws a day-level regime from a
     3-state mixture (70% normal, 20% elevated ~1.3-2.2x, 10% high ~2.2-4x)
     AND adds a small per-step jump-diffusion component (2% chance per step
     of a jump 3-6x the base step std, random sign) to fat-tail the
     within-day return distribution too - proxies news/liquidity shocks a
     pure GBM never produces.
  2. VOL-SPREAD COUPLING: spread_bps was sampled independently of the vol
     regime before, which is unrealistic - real spreads widen in high-vol
     conditions (adverse-selection risk goes up for anyone resting a quote).
     Spread mean now scales with sqrt(vol regime multiplier), so high-vol
     synthetic days also show the wider spreads real high-vol days do.
  3. MIXED TRAINING (mode="mixed"): with historical_frac=0.3 (configurable),
     30% of training episodes replay a REAL historical SOXL day's mid/spread
     path (still with the same fill-probability model, since real
     tick-level fill data doesn't exist) instead of a synthetic one. This
     means training itself - not just eval - sees real price-path structure.

CALIBRATION SOURCE (computed from outputs/markets/mm_cycle_log_SOXL.csv,
977 non-halted 5-min cycles across 16 trading days, Sept 22 - Oct 13 2026):
  - spread_bps: mean 3.0, std 1.6, range 0.6-12.7
  - per-5min mid return std: ~0.003-0.009 depending on day, ~0.006 typical
  - fills per active cycle: ~4.0 combined both sides at qty=10/side
    -> i.e. near-certain fill when quoting AT the touch (half_spread ==
       market half-spread), which anchors the fill-intensity curve below.

ACTION SPACE (Box, continuous, matches what a real quoting engine controls):
  [0] half_spread_bps in [0.5, 15.0]  - distance from mid for BOTH quotes
  [1] skew_bps        in [-8.0, 8.0]  - shifts both quotes same direction
                                        (+ = quotes shifted up => sells more
                                        aggressively placed, used to unwind
                                        long inventory, and vice versa)
  Order size is fixed at 10 shares/side (matches marketMaker.py default).

  TRIED AND REVERTED (Oct 2026): a 3rd action dim `size_mult in [0.2, 2.0]`
  (scales qty both sides) was added as a v2 extension. First attempt had no
  fill-probability cost for larger size, so PPO found the degenerate "always
  quote size_mult=2.0" solution (reward scales linearly with size, zero
  downside modeled) - looked like a 2x reward improvement but was pure
  leverage, not skill (size_mult had ZERO variance in the trained policy).
  Added a size-dependent fill-probability penalty (exp decay above
  size_mult=1.0, proxying queue position/impact) to remove the free-money
  solution - but then a 1M-step retrain converged to a policy that
  UNDERPERFORMED the production baseline (42% win rate, -3.3 paired diff,
  vs. the 2-action policy's 61% win rate / +4.9 paired diff). The 3rd action
  dimension made the optimization problem harder without exposing enough
  real edge to outweigh that, at least with this size-penalty calibration.
  REVERTED to 2-action (half_spread + skew only) as the validated baseline.
  Revisit size control only with either (a) real NBBO/queue data to
  calibrate the size-impact penalty properly, or (b) more training budget
  AND a retuned (likely stronger) SIZE_IMPACT_K.

OBSERVATION (Box, 4-dim, all normalized to roughly [-3, 3]):
  [0] inventory / inventory_cap          (-1..1 typically, can exceed slightly)
  [1] time_remaining_frac  (1.0 at open -> 0.0 at close)
  [2] market_spread_bps / 10.0            (current observed market spread)
  [3] unrealized_pnl_frac = inventory * (mid - avg_entry) / equity_ref

  NOTE (Oct 2026 redesign): `last_step_return` and `session_drift` were
  REMOVED from the observation. The synthetic sim is pure GBM (i.i.d.
  returns, zero real autocorrelation/drift signal), but a 1M-step PPO run
  still learned skew_bps positively correlated with inventory (+0.55) AND
  with last-step return (+0.55) - i.e. it exploited those two noise
  features to make directional momentum bets on inventory, got rewarded
  for it via mark-to-market P&L when lucky, and that's why mean reward
  stayed flat (~16) across 20k->1M steps while reward variance roughly
  doubled (std 88->145). There is no real edge to learn from fake
  momentum, so these features were pure overfitting surface and are gone.

REWARD (per step) - redesigned Oct 2026 to stop rewarding directional luck:
  reward = spread_capture + INVENTORY_PNL_WEIGHT * inventory_mark_pnl
           - risk_penalty - holding_cost
  where:
    spread_capture    = dollar edge actually earned THIS step from fills
                         executing favorably vs. mid (buy below mid / sell
                         above mid) - the only genuinely "learnable" signal
                         in a zero-drift synthetic world.
    inventory_mark_pnl = inventory_before_step * (mid_after - mid_before)
                         - the noise/directional-exposure component from
                         carrying existing inventory through a price move.
                         Heavily downweighted (INVENTORY_PNL_WEIGHT=0.15)
                         so it can't dominate the policy gradient the way
                         full delta_networth did before.
    risk_penalty      = RISK_AVERSION * inv_frac^2 * (CALIBRATED sigma_$)^2
                         Uses the FIXED calibrated per-step vol, not the
                         step's realized return, so the policy cannot game
                         the penalty by correlating actions with what
                         actually happened to price this step.
    holding_cost      = HOLDING_COST_COEF * |inv_frac|  - small continuous
                         pull back toward flat inventory, not just a cliff
                         at the cap.
  Terminal step force-flattens remaining inventory at the (worse) touch
  price; that realized flatten cost is added to reward at full weight
  (it's a real transaction cost, not noise) - mirrors marketMaker.py
  --flatten behavior.

VALIDATED RESULT (Oct 2026, this 2-action design, 300k steps,
models/ppo_mm_synthetic_300000.zip = v3_fixedreward_noSize):
  - Win rate vs. production baseline (quote-at-touch, zero skew): 61%
    (synthetic), 59-69% (historical real SOXL price paths)
  - Paired reward diff: +4.9 (synthetic), +3.7-4.2 (historical)
  - skew_bps vs. inventory correlation: -0.95 (strongly mean-reverting,
    i.e. genuinely learned inventory risk management, not noise-chasing)
  - half_spread_bps vs. market_spread_bps correlation: 0.71-0.80 (reacts
    sensibly to market conditions)
  - Training to 1M steps did not improve on the 300k result (same edge,
    same win rate) - this is a converged, plateaued signal at this reward/
    env design, not an undertrained one. Real further improvement likely
    requires env realism upgrades (vol-regime mixing, training on
    historical paths, properly-calibrated size control), not more steps.
"""

from __future__ import annotations

import os
import math
import csv
import datetime
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import gymnasium as gym
from gymnasium import spaces

CYCLE_LOG = os.path.expanduser("~/discordBot/outputs/markets/mm_cycle_log_SOXL.csv")

# ── Calibrated constants (from real mm_cycle_log_SOXL.csv, see docstring) ───
CALIB_SPREAD_BPS_MEAN = 3.0
CALIB_SPREAD_BPS_STD = 1.6
CALIB_SPREAD_BPS_MIN = 0.6
CALIB_RETURN_STD_PER_STEP = 0.006   # ~5-min mid return std, typical day
CALIB_STEPS_PER_DAY = 60            # ~6.5hr session / 5min, matches logged counts (52-65)
CALIB_START_PRICE = 140.0           # roughly mid of observed $117-$169 range

# Fill-intensity model: P(fill in one 5-min step | quote distance d bps from mid)
#   p = exp(-FILL_K * max(d - market_half_spread, 0))
# FILL_K chosen so that quoting ~2x the market half-spread away roughly halves
# fill probability relative to quoting at the touch - a reasonable default,
# NOT independently verified against real NBBO data (flagged as an assumption
# to revisit once we have tick-level fills to calibrate against).
FILL_K = 0.35
FILL_P_AT_TOUCH = 0.97  # matches ~4 fills/cycle observed when quoting at touch


@dataclass
class MMEnvConfig:
    order_qty: float = 10.0
    inventory_cap: float = 30.0           # matches production: QTY(10) * INVENTORY_MULT(3) in marketMaker.py
    risk_aversion: float = 2e-4
    inventory_pnl_weight: float = 0.15    # downweight mark-to-market noise vs. real spread capture
    holding_cost_coef: float = 0.02       # continuous dollar pull back toward flat inventory
    equity_ref: float = 83000.0           # matches real paper account equity scale
    steps_per_day: int = CALIB_STEPS_PER_DAY
    seed: Optional[int] = None
    mode: str = "synthetic"               # "synthetic", "historical", or "mixed"
    historical_frac: float = 0.3          # mode="mixed" only: fraction of episodes using a real historical day


class MarketMakerEnv(gym.Env):
    metadata = {"render_modes": []}

    def __init__(self, config: MMEnvConfig = MMEnvConfig()):
        super().__init__()
        self.cfg = config
        self.action_space = spaces.Box(
            low=np.array([0.5, -8.0], dtype=np.float32),
            high=np.array([15.0, 8.0], dtype=np.float32),
            dtype=np.float32,
        )
        self.observation_space = spaces.Box(
            low=-5.0, high=5.0, shape=(4,), dtype=np.float32
        )
        self._rng = np.random.default_rng(config.seed)
        self._historical_days = None
        if config.mode in ("historical", "mixed"):
            self._historical_days = _load_historical_days(CYCLE_LOG)

        self._reset_episode_state()

    # ── gym API ──────────────────────────────────────────────────────────
    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        if seed is not None:
            self._rng = np.random.default_rng(seed)
        self._reset_episode_state()
        return self._obs(), {}

    def step(self, action):
        half_spread_bps = float(np.clip(action[0], 0.5, 15.0))
        skew_bps = float(np.clip(action[1], -8.0, 8.0))

        mid_before = self.mid
        self.inventory_before = self.inventory  # for reward: mark-to-market on pre-fill inventory only
        market_spread_bps = self.spread_path[self.t]

        # quote prices (half_spread applied symmetrically, then skewed)
        buy_dist_bps = half_spread_bps - skew_bps     # distance of bid below mid
        sell_dist_bps = half_spread_bps + skew_bps    # distance of ask above mid
        buy_dist_bps = max(buy_dist_bps, 0.01)
        sell_dist_bps = max(sell_dist_bps, 0.01)

        buy_price = mid_before * (1 - buy_dist_bps / 10000.0)
        sell_price = mid_before * (1 + sell_dist_bps / 10000.0)

        # inventory caps: don't add past cap (mirrors marketMaker.py inventory control)
        can_buy = self.inventory < self.cfg.inventory_cap
        can_sell = self.inventory > -self.cfg.inventory_cap

        market_half_spread_bps = market_spread_bps / 2.0

        filled_buy = can_buy and self._rng.random() < _fill_prob(buy_dist_bps, market_half_spread_bps)
        filled_sell = can_sell and self._rng.random() < _fill_prob(sell_dist_bps, market_half_spread_bps)

        realized_pnl = 0.0
        qty = self.cfg.order_qty
        if filled_buy:
            cost = buy_price * qty
            self.cash -= cost
            if self.inventory >= 0:
                self.avg_entry = (
                    (self.avg_entry * self.inventory + buy_price * qty) / (self.inventory + qty)
                    if (self.inventory + qty) > 0 else buy_price
                )
            else:
                # covering a short: realize pnl on the covered portion
                cover_qty = min(qty, -self.inventory)
                realized_pnl += (self.avg_entry - buy_price) * cover_qty
            self.inventory += qty

        if filled_sell:
            proceeds = sell_price * qty
            self.cash += proceeds
            if self.inventory <= 0:
                self.avg_entry = (
                    (self.avg_entry * (-self.inventory) + sell_price * qty) / (-self.inventory + qty)
                    if (-self.inventory + qty) > 0 else sell_price
                )
            else:
                close_qty = min(qty, self.inventory)
                realized_pnl += (sell_price - self.avg_entry) * close_qty
            self.inventory -= qty

        # advance the price path
        self.t += 1
        terminated = False
        truncated = False
        if self.t >= len(self.mid_path):
            self.t = len(self.mid_path) - 1
            terminated = True
        mid_after = self.mid_path[self.t]
        self.mid = mid_after

        # ── reward decomposition (Oct 2026 redesign) ──────────────────────
        # 1) spread_capture: real dollar edge earned on fills THIS step,
        #    measured vs. the mid at fill time (mid_before) - this is the
        #    only genuinely learnable signal in a zero-drift synthetic world.
        spread_capture = 0.0
        if filled_buy:
            spread_capture += (mid_before - buy_price) * qty      # bought below mid = positive edge
        if filled_sell:
            spread_capture += (sell_price - mid_before) * qty     # sold above mid = positive edge

        # 2) inventory_mark_pnl: mark-to-market P&L on inventory HELD BEFORE
        #    this step's fills, from pure price movement - this is noise in
        #    the synthetic GBM world (zero real drift) and is what the old
        #    reward let the policy exploit via skew. Heavily downweighted.
        inventory_mark_pnl = self.inventory_before * (mid_after - mid_before)

        # 3) risk_penalty: uses the FIXED calibrated per-step vol, not the
        #    step's realized return, so the policy can't game the penalty
        #    by correlating actions with what actually happened to price.
        inv_frac = self.inventory / self.cfg.inventory_cap
        calibrated_sigma_dollars = CALIB_RETURN_STD_PER_STEP * mid_before
        risk_penalty = self.cfg.risk_aversion * (inv_frac ** 2) * (calibrated_sigma_dollars ** 2)

        # 4) holding_cost: small continuous pull back toward flat inventory.
        holding_cost = self.cfg.holding_cost_coef * abs(inv_frac) * mid_before / 1000.0

        reward = (
            spread_capture
            + self.cfg.inventory_pnl_weight * inventory_mark_pnl
            - risk_penalty
            - holding_cost
        )

        # true cash-based networth for logging/terminal flatten math
        networth = self.cash + self.inventory * mid_after

        flatten_cost = 0.0
        if terminated and abs(self.inventory) > 1e-9:
            # EOD force-flatten at worse-of-touch (mirrors --flatten in marketMaker.py)
            market_half_spread_bps = self.spread_path[self.t] / 2.0
            exit_price = mid_after * (1 - market_half_spread_bps / 10000.0) if self.inventory > 0 \
                else mid_after * (1 + market_half_spread_bps / 10000.0)
            flatten_cost = self.inventory * (mid_after - exit_price) if self.inventory > 0 \
                else -self.inventory * (exit_price - mid_after)
            self.cash += self.inventory * exit_price
            self.inventory = 0.0
            networth = self.cash
            # flatten_cost is a REAL transaction cost (not noise) - charge it at full weight
            reward -= abs(flatten_cost)

        self.prev_networth = networth
        self.day_realized_pnl += realized_pnl

        info = {
            "mid": self.mid,
            "inventory": self.inventory,
            "networth": networth,
            "filled_buy": filled_buy,
            "filled_sell": filled_sell,
            "flatten_cost": flatten_cost,
            "spread_capture": spread_capture,
            "inventory_mark_pnl": inventory_mark_pnl,
        }
        return self._obs(), float(reward), terminated, truncated, info

    # ── internals ────────────────────────────────────────────────────────
    def _reset_episode_state(self):
        n = self.cfg.steps_per_day
        use_historical = False
        if self.cfg.mode == "historical" and self._historical_days:
            use_historical = True
        elif self.cfg.mode == "mixed" and self._historical_days:
            use_historical = self._rng.random() < self.cfg.historical_frac

        if use_historical:
            day = self._historical_days[self._rng.integers(0, len(self._historical_days))]
            self.mid_path = day["mid"]
            self.spread_path = day["spread"]
            n = len(self.mid_path)
        else:
            self.mid_path, self.spread_path = _simulate_day(self._rng, n, CALIB_START_PRICE)

        self.t = 0
        self.mid = self.mid_path[0]
        self.day_open_mid = self.mid
        self.inventory = 0.0
        self.inventory_before = 0.0
        self.avg_entry = self.mid
        self.cash = 0.0
        self.prev_networth = 0.0
        self.day_realized_pnl = 0.0
        self._n_steps = n

    def _obs(self):
        time_remaining = 1.0 - self.t / max(self._n_steps - 1, 1)
        inv_norm = self.inventory / self.cfg.inventory_cap
        spread_norm = self.spread_path[self.t] / 10.0
        unrl_pnl_frac = (self.inventory * (self.mid - self.avg_entry)) / self.cfg.equity_ref
        obs = np.array([
            inv_norm,
            time_remaining,
            spread_norm,
            unrl_pnl_frac * 100.0,
        ], dtype=np.float32)
        return np.clip(obs, -5.0, 5.0)


def _fill_prob(distance_bps: float, market_half_spread_bps: float) -> float:
    excess = max(distance_bps - market_half_spread_bps, 0.0)
    p_at_touch = FILL_P_AT_TOUCH
    return float(p_at_touch * math.exp(-FILL_K * excess))


def _simulate_day(rng: np.random.Generator, n: int, start_price: float):
    """GBM mid-price path + spread path, calibrated to observed SOXL stats,
    with (Oct 2026 v6) regime-dependent vol, jump-diffusion fat tails, and
    vol-coupled spread widening - see module docstring ENV REALISM UPGRADE.
    """
    # ── day-level vol regime (3-state mixture, not a tight uniform range) ──
    regime_roll = rng.random()
    if regime_roll < 0.70:
        vol_mult = rng.uniform(0.6, 1.1)      # normal day
    elif regime_roll < 0.90:
        vol_mult = rng.uniform(1.3, 2.2)      # elevated vol day
    else:
        vol_mult = rng.uniform(2.2, 4.0)      # high-vol / shock day

    ret_std = CALIB_RETURN_STD_PER_STEP * vol_mult
    rets = rng.normal(0.0, ret_std, size=n)

    # ── jump-diffusion component: occasional larger moves within the day,
    #    on top of the regular GBM steps, to fat-tail the return distribution
    #    (proxies news/liquidity shocks a pure GBM never produces). ──
    jump_mask = rng.random(n) < 0.02   # ~2% of steps get a jump
    jump_sizes = rng.normal(0.0, ret_std * rng.uniform(3.0, 6.0), size=n)
    rets = rets + jump_mask * jump_sizes

    mid = start_price * np.exp(np.cumsum(rets))

    # ── spread widens with vol regime (sqrt scaling - adverse-selection risk
    #    rises with vol, so anyone resting a quote demands more compensation). ──
    spread_mean = CALIB_SPREAD_BPS_MEAN * math.sqrt(max(vol_mult, 1.0))
    spread = np.clip(
        rng.normal(spread_mean, CALIB_SPREAD_BPS_STD, size=n),
        CALIB_SPREAD_BPS_MIN, 25.0,   # raised cap from 15 since high-vol days can spread wider
    )
    return mid.astype(np.float64), spread.astype(np.float64)


def _load_historical_days(path: str):
    """Group real logged (non-halted) cycles into per-day mid/spread arrays."""
    if not os.path.exists(path):
        return []
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            if r.get("halted") == "True":
                continue
            mid = r.get("mid", "")
            spread = r.get("spread_bps", "")
            if not mid or not spread:
                continue
            ts = r["timestamp"]
            rows.append((ts[:10], float(mid), float(spread)))
    days = {}
    for date, mid, spread in rows:
        days.setdefault(date, {"mid": [], "spread": []})
        days[date]["mid"].append(mid)
        days[date]["spread"].append(spread)
    out = []
    for d, v in days.items():
        if len(v["mid"]) >= 10:
            out.append({"date": d, "mid": np.array(v["mid"]), "spread": np.array(v["spread"])})
    return out


if __name__ == "__main__":
    # quick smoke test
    env = MarketMakerEnv(MMEnvConfig(seed=0))
    obs, _ = env.reset()
    total_r = 0.0
    for _ in range(env._n_steps - 1):
        action = env.action_space.sample()
        obs, r, term, trunc, info = env.step(action)
        total_r += r
        if term or trunc:
            break
    print("smoke test ok. steps:", env.t, "total_reward:", round(total_r, 4),
          "final_inventory:", info["inventory"], "networth:", round(info["networth"], 2))
