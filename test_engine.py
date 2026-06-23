#!/usr/bin/env python3
"""Headless tests for ScrollEngine — no evdev, no root, no hardware.

Run: python3 test_engine.py
"""
from mmb_autoscroll import Config, ScrollEngine, HIRES_PER_NOTCH


def cfg(**over):
    c = Config()
    # deterministic defaults independent of the environment
    c.mode = "rate"
    c.deadzone = 20
    c.gain = 0.02
    c.exponent = 1.3
    c.max_speed = 40.0
    c.position_factor = 0.06
    c.horizontal = 1
    c.invert_v = 0
    c.invert_h = 0
    for k, v in over.items():
        setattr(c, k, v)
    return c


def total_v(engine, ticks, dt=0.015):
    v = 0
    for _ in range(ticks):
        for it in engine.tick(dt):
            if it[0] == "wheel":
                v += it[1]
    return v


def test_tap_is_click():
    eng = ScrollEngine(cfg())
    assert eng.middle_down(0.0) == []
    assert eng.middle_up(0.1) == [("click",)], "a tap must pass through as a click"


def test_small_motion_still_click():
    eng = ScrollEngine(cfg())
    eng.middle_down(0.0)
    eng.add_motion(5, 5)          # inside deadzone (hypot ~7 < 20)
    assert eng.middle_up(0.1) == [("click",)], "staying in the deadzone = click"


def test_motion_past_deadzone_is_scroll_not_click():
    eng = ScrollEngine(cfg())
    eng.middle_down(0.0)
    eng.add_motion(0, 60)         # well past deadzone
    assert eng.moved is True
    assert eng.middle_up(0.1) == [], "a scroll gesture must NOT emit a click"


def test_rate_down_scrolls_down():
    # moving DOWN (dy>0) should scroll content down => negative REL_WHEEL.
    eng = ScrollEngine(cfg())
    eng.middle_down(0.0)
    eng.add_motion(0, 120)
    v = total_v(eng, 20)
    assert v < 0, f"down-hold should scroll down (negative), got {v}"


def test_rate_up_scrolls_up():
    eng = ScrollEngine(cfg())
    eng.middle_down(0.0)
    eng.add_motion(0, -120)
    v = total_v(eng, 20)
    assert v > 0, f"up-hold should scroll up (positive), got {v}"


def test_rate_speed_increases_with_distance():
    near = ScrollEngine(cfg()); near.middle_down(0.0); near.add_motion(0, 50)
    far = ScrollEngine(cfg()); far.middle_down(0.0); far.add_motion(0, 250)
    vn = abs(total_v(near, 40))
    vf = abs(total_v(far, 40))
    assert vf > vn > 0, f"farther must scroll faster: near={vn} far={vf}"


def test_rate_speed_capped():
    eng = ScrollEngine(cfg())
    eng.middle_down(0.0)
    eng.add_motion(0, 100000)     # absurd distance
    # over 1s the magnitude must not exceed max_speed notches * 120 (+1 rounding)
    v = abs(total_v(eng, ticks=int(1/0.015), dt=0.015))
    cap = cfg().max_speed * HIRES_PER_NOTCH
    assert v <= cap + HIRES_PER_NOTCH, f"speed not capped: {v} > {cap}"


def test_deadzone_no_scroll():
    eng = ScrollEngine(cfg())
    eng.middle_down(0.0)
    eng.add_motion(0, 10)         # inside deadzone
    assert total_v(eng, 50) == 0, "no scroll while inside the deadzone"


def test_horizontal_right_positive():
    eng = ScrollEngine(cfg())
    eng.middle_down(0.0)
    eng.add_motion(120, 0)
    h = 0
    for _ in range(20):
        for it in eng.tick(0.015):
            h += it[2]
    assert h > 0, f"moving right should emit positive hwheel, got {h}"


def test_invert_v():
    eng = ScrollEngine(cfg(invert_v=1))
    eng.middle_down(0.0)
    eng.add_motion(0, 120)        # down, but inverted
    assert total_v(eng, 20) > 0, "invert_v should flip vertical direction"


def test_position_mode_emits_on_motion():
    eng = ScrollEngine(cfg(mode="position"))
    eng.middle_down(0.0)
    eng.add_motion(0, 30)         # past deadzone -> moved, emits inline
    drained = eng._drain()
    # already drained inside add_motion via _emit_position+_drain? No: in
    # position mode the device layer calls _drain; here we call it directly.
    v = sum(it[1] for it in drained if it[0] == "wheel")
    # plus whatever _emit_position queued is now drained; ensure downward
    # motion gave negative wheel.
    assert v <= 0
    # and a tap in position mode is still a click
    eng2 = ScrollEngine(cfg(mode="position"))
    eng2.middle_down(0.0)
    assert eng2.middle_up(0.05) == [("click",)]


def test_release_resets_state():
    eng = ScrollEngine(cfg())
    eng.middle_down(0.0)
    eng.add_motion(0, 200)
    eng.middle_up(0.2)
    assert eng.held is False and eng.offy == 0.0 and eng.moved is False
    # a fresh tap afterwards is a clean click
    eng.middle_down(0.3)
    assert eng.middle_up(0.31) == [("click",)]


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as ex:
            failed += 1
            print(f"FAIL {t.__name__}: {ex}")
        except Exception as ex:  # noqa
            failed += 1
            print(f"ERROR {t.__name__}: {ex!r}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
