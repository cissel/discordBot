#!/usr/bin/env python3
"""
spyRlIntradayEnv.py
=====================
Gymnasium environment for RL-learned SPY sizing/exit at 5-min bar
resolution (intraday follow-up to the daily spike in spyRlSizingEnv.py,
Oct'26). Built because the daily-bar spike starved PPO of data (504-756
training transitions) and either collapsed to flat or went unstable.

Episode = ONE trading day (78 x 5-min bars, 9:30-4pm ET regular session
only). Agent is forced flat at the last bar of each day (no overnight
carry -- matches "no overnight gap risk the agent never trained on").
Action = position fraction in [0,1] (long-only, no leverage, same
convention as the daily spike -- the live bot doesn't short SPY).
Reward = net bar return after one-way transaction cost on turnover,
same TX_COST as the daily spike and spyVolTargetWFCV.py for comparability.

State = today's intraday features (momentum/vol/vwap-dev/time-of-day) +
daily regime/vix/gex context (same-day, known as of prior close) + a
rolling WINDOW of the agent's own (position, realized bar return) within
the current day, reset at the start of each episode (day) since the
agent never carries risk overnight.
"""
import numpy as np
import gymnasium as gym
from gymnasium import spaces

TX_COST = 0.0005
ANNUAL_BARS = 78 * 252   # 5-min bars per year, regular session only
WINDOW = 10

STATIC_COLS = [
    "ret_1", "cum_ret_since_open", "dist_from_high_so_far", "dist_from_low_so_far",
    "vwap_dev", "rv_12bar", "rv_36bar", "vol_z_36bar", "mom_12bar", "tod_frac",
    "regime_bull", "regime_bear", "regime_chop",
    "vix_level", "vix_z21", "gex_sign", "vix_rv_spread",
]


def build_day_index(df):
    """Group row indices by trading_date, in chronological order, dropping
    the last-bar-of-day row from the *tradable* index (its next_ret_5min is
    NaN -- the agent can't act on a bar with no forward return to earn)."""
    df = df.reset_index(drop=True)
    days = []
    for day, g in df.groupby("trading_date", sort=True):
        g = g[g["next_ret_5min"].notna()]
        if len(g) >= WINDOW + 5:
            days.append(g.index.values)
    return days


def normalize_static(df):
    static = df[STATIC_COLS].copy()
    static["vix_level"] = static["vix_level"] / 30.0
    static["rv_12bar"] = static["rv_12bar"].clip(0, 2.0)
    static["rv_36bar"] = static["rv_36bar"].clip(0, 2.0)
    static["vol_z_36bar"] = static["vol_z_36bar"].clip(-5, 5)
    static["gex_sign"] = static["gex_sign"].fillna(1.0)
    static["vix_rv_spread"] = static["vix_rv_spread"].fillna(0.0)
    return static.fillna(0.0).values.astype(np.float32)


class SpyIntradayEnv(gym.Env):
    """One episode = one trading day's sequence of 5-min bars."""
    metadata = {"render_modes": []}

    def __init__(self, df, day_indices, tx_cost=TX_COST, random_day=True, seed=None):
        super().__init__()
        self.next_ret = df["next_ret_5min"].values.astype(np.float32)
        self.static = normalize_static(df)
        self.day_indices = day_indices
        self.tx_cost = tx_cost
        self.random_day = random_day
        self.n_static = self.static.shape[1]

        obs_dim = self.n_static + 2 * WINDOW
        self.observation_space = spaces.Box(low=-np.inf, high=np.inf,
                                             shape=(obs_dim,), dtype=np.float32)
        self.action_space = spaces.Box(low=0.0, high=1.0, shape=(1,), dtype=np.float32)
        self._rng = np.random.default_rng(seed)

        self._day_ptr = 0
        self._row_ptr = 0
        self._cur_day = None
        self._pos_hist = np.zeros(WINDOW, dtype=np.float32)
        self._ret_hist = np.zeros(WINDOW, dtype=np.float32)
        self._prev_pos = 0.0
        self._step_in_day = 0

    def _obs(self):
        row = self._cur_day[self._step_in_day]
        static = self.static[row]
        return np.concatenate([static, self._pos_hist, self._ret_hist]).astype(np.float32)

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        if self.random_day:
            self._day_ptr = int(self._rng.integers(0, len(self.day_indices)))
        else:
            self._day_ptr = self._day_ptr % len(self.day_indices) if hasattr(self, "_eval_order") else self._day_ptr
        self._cur_day = self.day_indices[self._day_ptr]
        self._step_in_day = 0
        self._pos_hist = np.zeros(WINDOW, dtype=np.float32)
        self._ret_hist = np.zeros(WINDOW, dtype=np.float32)
        self._prev_pos = 0.0
        return self._obs(), {}

    def set_day(self, day_ptr):
        """For deterministic sequential eval: force the next reset() to use
        a specific day index."""
        self.random_day = False
        self._day_ptr = day_ptr

    def step(self, action):
        pos = float(np.clip(action[0], 0.0, 1.0))
        row = self._cur_day[self._step_in_day]
        ret = float(self.next_ret[row])
        turnover = abs(pos - self._prev_pos)
        reward = pos * ret - turnover * self.tx_cost

        self._pos_hist = np.roll(self._pos_hist, -1)
        self._pos_hist[-1] = pos
        self._ret_hist = np.roll(self._ret_hist, -1)
        self._ret_hist[-1] = reward
        self._prev_pos = pos

        self._step_in_day += 1
        terminated = False
        truncated = self._step_in_day >= len(self._cur_day)
        # forced flat at day end (no overnight carry) -- no extra penalty needed
        # since the day's last tradable bar already has nowhere left to hold.
        obs = self._obs() if not truncated else np.zeros(self.observation_space.shape, dtype=np.float32)
        info = {"position": pos, "net_ret": reward, "raw_ret": ret,
                "trading_date": None}
        return obs, reward, terminated, truncated, info
