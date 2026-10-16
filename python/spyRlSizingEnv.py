#!/usr/bin/env python3
"""
spyRlSizingEnv.py
==================
Gymnasium environment for RL-learned SPY position sizing/exit.

Scope (per Oct'26 RL spike, agreed with James):
  - RL agent fully owns position sizing/exit (NOT a wrapper/multiplier on
    top of predictSpy.py's hand-coded Kelly/regime/kill-switch rules). The
    agent sees regime/vix/gex/vol features directly and must learn its own
    risk-off behavior (there is no hard-coded kill switch in this env).
  - Long-only, no leverage: action in [0, 1] (0 = flat/exit, 1 = full size).
    This matches the live bot's long-only ETF sizing; shorting SPY is out
    of scope for this spike (the live autopilot doesn't short).
  - State = today's model probability `p` (from the same walk-forward
    logistic used in spyVolTargetWFCV.py) + regime one-hot + vix/gex/vol
    features + a rolling window of the AGENT's OWN recent (position,
    realized strategy return) pairs, so it can condition on its own recent
    performance/drawdown — this is the one thing the static classifier
    can't do and is the actual hypothesis being tested.
  - Reward = net daily strategy return after one-way transaction cost,
    identical accounting to spyVolTargetWFCV.backtest_positions() so
    Sharpe numbers are directly comparable to the existing baseline.

This module only defines the env. Training/eval driver is
spyRlSizingTrain.py.
"""
import numpy as np
import gymnasium as gym
from gymnasium import spaces

TX_COST = 0.0005   # one-way, same as spyVolTargetWFCV.py
ANNUAL = 252
WINDOW = 10         # rolling window length of (position, realized_ret) history
REGIMES = ["bull", "bear", "chop"]


def build_state_frame(oos_df, target_ann_vol):
    """Turn a pooled-OOS dataframe (output of spyVolTargetWFCV.run_wfcv +
    attach_realized_vol) into a plain numpy feature matrix (static part of
    the state, i.e. everything except the rolling position/return window,
    which is tracked online by the env). Returns (dates, next_ret_1d,
    static_features[N, F], feature_names).
    """
    df = oos_df.reset_index(drop=True).copy()
    vol_scalar = target_ann_vol / df["realized_vol_21d"].values
    vol_scalar = np.nan_to_num(vol_scalar, nan=1.0, posinf=1.0, neginf=1.0)
    vol_scalar = np.clip(vol_scalar, 0.1, 5.0)

    regime_oh = np.zeros((len(df), len(REGIMES)))
    for i, r in enumerate(REGIMES):
        regime_oh[:, i] = (df["regime"].values == r).astype(float)

    gex_sign = df["gex_sign"].fillna(1.0).values
    vix_level = df["vix_level"].fillna(df["vix_level"].median()).values
    vix_z21 = df["vix_z21"].fillna(0.0).values
    realized_vol = df["realized_vol_21d"].fillna(df["realized_vol_21d"].median()).values
    p = df["p"].values

    static = np.column_stack([
        p,
        regime_oh,                 # 3 cols
        vix_level / 30.0,          # rough scale normalization
        vix_z21,
        gex_sign,
        realized_vol,
        vol_scalar,
    ])
    feature_names = (["p"] + [f"regime_{r}" for r in REGIMES] +
                      ["vix_level_n", "vix_z21", "gex_sign", "realized_vol_21d", "vol_scalar"])
    return (df["date"].values, df["next_ret_1d"].values, static.astype(np.float32),
            feature_names)


class SpySizingEnv(gym.Env):
    """One episode = one contiguous run through `next_ret_1d`/static features,
    either a random-start/random-length slice (training) or the full fixed
    slice (deterministic eval). Action = position fraction in [0,1] applied
    to that day's next_ret_1d (same convention as the existing backtest:
    position decided "today" earns next_ret_1d, cost charged on turnover).
    """
    metadata = {"render_modes": []}

    def __init__(self, next_ret_1d, static_features, tx_cost=TX_COST,
                 episode_len=None, random_start=True, seed=None):
        super().__init__()
        self.next_ret_1d = np.asarray(next_ret_1d, dtype=np.float32)
        self.static_features = np.asarray(static_features, dtype=np.float32)
        self.n_total = len(self.next_ret_1d)
        self.tx_cost = tx_cost
        self.episode_len = episode_len or self.n_total
        self.random_start = random_start
        self.n_static = self.static_features.shape[1]

        obs_dim = self.n_static + 2 * WINDOW
        self.observation_space = spaces.Box(low=-np.inf, high=np.inf,
                                             shape=(obs_dim,), dtype=np.float32)
        self.action_space = spaces.Box(low=0.0, high=1.0, shape=(1,), dtype=np.float32)
        self._rng = np.random.default_rng(seed)

        self._ptr = 0
        self._end = 0
        self._pos_hist = np.zeros(WINDOW, dtype=np.float32)
        self._ret_hist = np.zeros(WINDOW, dtype=np.float32)
        self._prev_pos = 0.0

    def _obs(self):
        static = self.static_features[self._ptr]
        return np.concatenate([static, self._pos_hist, self._ret_hist]).astype(np.float32)

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        max_start = max(0, self.n_total - self.episode_len)
        if self.random_start and max_start > 0:
            self._ptr = int(self._rng.integers(0, max_start + 1))
        else:
            self._ptr = 0
        self._end = min(self._ptr + self.episode_len, self.n_total)
        self._pos_hist = np.zeros(WINDOW, dtype=np.float32)
        self._ret_hist = np.zeros(WINDOW, dtype=np.float32)
        self._prev_pos = 0.0
        return self._obs(), {}

    def step(self, action):
        pos = float(np.clip(action[0], 0.0, 1.0))
        ret = float(self.next_ret_1d[self._ptr])
        turnover = abs(pos - self._prev_pos)
        reward = pos * ret - turnover * self.tx_cost

        self._pos_hist = np.roll(self._pos_hist, -1)
        self._pos_hist[-1] = pos
        self._ret_hist = np.roll(self._ret_hist, -1)
        self._ret_hist[-1] = reward
        self._prev_pos = pos

        self._ptr += 1
        terminated = False
        truncated = self._ptr >= self._end or self._ptr >= self.n_total
        obs = self._obs() if not truncated else np.zeros(self.observation_space.shape, dtype=np.float32)
        info = {"position": pos, "net_ret": reward, "raw_ret": ret}
        return obs, reward, terminated, truncated, info
