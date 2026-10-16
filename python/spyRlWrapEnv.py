#!/usr/bin/env python3
"""
spyRlWrapEnv.py
================
Gymnasium environment for RL-learned MULTIPLIER on top of the existing
hand-coded vol-targeted SPY position sizing (spyVolTargetWFCV.py).

Built after the full-replace spike (spyRlSizingEnv.py / spyRlIntradayEnv.py)
went net negative in both daily and 5-min variants -- collapsed to all-flat
on one fold, actively destabilizing on others (Oct'26). James's call: try
wrapping instead of replacing, since full replace threw away every existing
safety rail and went risk-off entirely.

Wrap semantics:
  final_pos[t] = clip(vt_pos[t] * action[t], 0.0, 1.0)
where vt_pos[t] is the EXISTING vol-targeted position from
spyVolTargetWFCV.py (already gated: kill switch, chop-VIX gate, bear
halving all already applied). action in [0, 2]:
  0   = go flat regardless of what the rule says
  1   = defer entirely to the existing rule (no-op)
  2   = double the rule's raw position (still hard-capped at 1.0)

Critical property: on any day the kill switch / chop-VIX gate has already
zeroed vt_pos, action * 0 == 0 no matter what the agent outputs. The RL
agent CANNOT override the existing risk rails -- it can only scale
confidence up or down on days the rule already allows a nonzero position.
This is the "isolates RL's marginal value, lower risk" framing from the
original scope discussion.

Reward = net daily return of final_pos after one-way transaction cost on
turnover of final_pos (same accounting as spyVolTargetWFCV.backtest_positions,
directly comparable Sharpe).

State = static features (today's p, regime one-hot, vix/gex context,
realized vol, vol_scalar, AND vt_pos itself -- the agent needs to see what
it's scaling) + a rolling WINDOW of the agent's own (action, net_ret)
history, so it can condition on recent realized performance.
"""
import numpy as np
import gymnasium as gym
from gymnasium import spaces

TX_COST = 0.0005
ANNUAL = 252
WINDOW = 10
REGIMES = ["bull", "bear", "chop"]
ACTION_LOW = 0.0
ACTION_HIGH = 2.0


def build_wrap_state_frame(oos_df, vt_pos, target_ann_vol):
    """Static feature matrix including vt_pos (the baseline position being
    scaled) as an explicit input. Returns (dates, next_ret_1d, vt_pos,
    static_features[N, F], feature_names)."""
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
        regime_oh,
        vix_level / 30.0,
        vix_z21,
        gex_sign,
        realized_vol,
        vol_scalar,
        vt_pos,                 # the thing the agent is scaling -- must see it
    ])
    feature_names = (["p"] + [f"regime_{r}" for r in REGIMES] +
                      ["vix_level_n", "vix_z21", "gex_sign", "realized_vol_21d",
                       "vol_scalar", "vt_pos"])
    return (df["date"].values, df["next_ret_1d"].values, np.asarray(vt_pos, dtype=np.float32),
            static.astype(np.float32), feature_names)


class SpyWrapEnv(gym.Env):
    """One episode = one contiguous slice through next_ret_1d/vt_pos/static.
    Action = multiplier in [0, 2] applied to that day's vt_pos (the
    EXISTING hand-coded vol-targeted position), clipped to [0, 1] after
    scaling. Same day-ahead convention as spyRlSizingEnv: position decided
    "today" (post-scaling) earns next_ret_1d, tx cost charged on turnover
    of the SCALED position.
    """
    metadata = {"render_modes": []}

    def __init__(self, next_ret_1d, vt_pos, static_features, tx_cost=TX_COST,
                 episode_len=None, random_start=True, seed=None):
        super().__init__()
        self.next_ret_1d = np.asarray(next_ret_1d, dtype=np.float32)
        self.vt_pos = np.asarray(vt_pos, dtype=np.float32)
        self.static_features = np.asarray(static_features, dtype=np.float32)
        self.n_total = len(self.next_ret_1d)
        self.tx_cost = tx_cost
        self.episode_len = episode_len or self.n_total
        self.random_start = random_start
        self.n_static = self.static_features.shape[1]

        obs_dim = self.n_static + 2 * WINDOW
        self.observation_space = spaces.Box(low=-np.inf, high=np.inf,
                                             shape=(obs_dim,), dtype=np.float32)
        self.action_space = spaces.Box(low=ACTION_LOW, high=ACTION_HIGH,
                                        shape=(1,), dtype=np.float32)
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
        mult = float(np.clip(action[0], ACTION_LOW, ACTION_HIGH))
        base_pos = float(self.vt_pos[self._ptr])
        pos = float(np.clip(base_pos * mult, 0.0, 1.0))
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
        info = {"position": pos, "net_ret": reward, "raw_ret": ret,
                "mult": mult, "base_pos": base_pos}
        return obs, reward, terminated, truncated, info
