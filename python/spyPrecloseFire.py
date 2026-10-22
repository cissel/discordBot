#!/usr/bin/env python3
"""
spyPrecloseFire.py - second half of the SPY pre-close execution (cron 15:52 ET).

tradeSpy.py --execute --preclose (15:05 run) computes the day's target from the
partial-session feature row and writes it to
outputs/markets/preclose/spy_preclose_<date>.json with pending=True. Alpaca paper
does not fill market-on-close orders, so this script sends a plain DAY market
order for the delta between the CURRENT broker position and that target, about
eight minutes before the close. Idempotent: refuses to fire twice (fired=True),
refuses stale state (not today's), and re-reads the live position so a fill that
already happened cannot be doubled. Posts a one-liner to #test-env.
"""
import os
import sys
import json
import datetime as dt

sys.path.insert(0, os.path.dirname(__file__))
import tradeSpy as T  # noqa: E402


def main():
    today = dt.date.today().isoformat()
    st = T.load_preclose_state(today)
    if not st:
        print(f"no pre-close state for {today}; nothing to fire"); return 0
    if st.get("fired"):
        print(f"already fired at {st.get('fired_at')} (order {st.get('order_id')})"); return 0
    if not st.get("pending"):
        print(f"state not pending (passed_risk={st.get('passed_risk')}, action={st.get('action')}); nothing to fire"); return 0
    e = T.load_env()
    headers = {"APCA-API-KEY-ID": e["APCA_PAPER_API_KEY_ID"], "APCA-API-SECRET-KEY": e["APCA_PAPER_API_SECRET_KEY"],
               "Content-Type": "application/json"}
    pos = T.get_spy_position(headers)
    cur = int(pos["shares"])
    target = int(st["target_shares"])
    delta = target - cur
    msg_prefix = f"SPY pre-close FIRE {today}: target {target} sh, broker has {cur}"
    if abs(delta) < T.MIN_ORDER_SHARES:
        st.update({"fired": True, "fired_at": T.now_et().strftime("%H:%M:%S"), "order_id": "", "fire_note": "already at target"})
        T.save_preclose_state(st)
        msg = msg_prefix + " -> already at target, no order"
    else:
        action = "BUY" if delta > 0 else "SELL"
        try:
            o = T.place_order(action, abs(delta), headers, dry_run=False, time_in_force="day")
            st.update({"fired": True, "fired_at": T.now_et().strftime("%H:%M:%S"), "order_id": o.get("id", ""),
                       "fire_action": action, "fire_shares": abs(delta), "executed": True})
            T.save_preclose_state(st)
            msg = msg_prefix + f" -> {action} {abs(delta)} market (day), order {o.get('id')} status {o.get('status')}"
        except Exception as e:
            st.update({"fire_error": str(e)[:200]})
            T.save_preclose_state(st)
            msg = msg_prefix + f" -> ORDER FAILED: {e}. 16:15 run will trade (next-open fill)."
    print(msg)
    try:
        from discordPost import post
        post("test-env", msg)
    except Exception as e:
        print(f"post failed: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
