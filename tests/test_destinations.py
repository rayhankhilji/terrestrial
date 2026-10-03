"""Re-route alerts need a settled change, not two fields swapping the lead."""

from live.destinations import CONFIRM_TICKS, REROUTE_COOLDOWN_MS, Destinations


def pred(dest, p):
    return {"dest": dest, "p": p}


def test_flip_flop_between_two_fields_never_alerts():
    d = Destinations(store=None)
    t = 0
    assert d._confirm("a", pred("LLBG", 0.45), t) is None
    for i in range(20):
        t += 10_000
        assert d._confirm("a", pred("LLEK" if i % 2 == 0 else "LLBG", 0.45), t) is None


def test_settled_change_alerts_once_then_cools_down():
    d = Destinations(store=None)
    d._confirm("a", pred("ETAR", 0.6), 0)
    alerts = [d._confirm("a", pred("EDFH", 0.5), k * 10_000) for k in range(1, CONFIRM_TICKS + 1)]
    assert alerts[:-1] == [None] * (CONFIRM_TICKS - 1) and alerts[-1]["dest"] == "ETAR"
    # Back to Ramstein for good, but within the cooldown: no second alert.
    t = CONFIRM_TICKS * 10_000
    assert all(d._confirm("a", pred("ETAR", 0.6), t + k * 10_000) is None for k in range(1, 6))
    # After the cooldown, the settled return to Ramstein is reported (from EDFH).
    assert d._confirm("a", pred("ETAR", 0.6), REROUTE_COOLDOWN_MS + t)["dest"] == "EDFH"
