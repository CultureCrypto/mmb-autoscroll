#!/usr/bin/env python3
"""mmb-autoscroll — Windows-style middle-button autoscroll for Wayland.

GNOME/mutter on Wayland does not expose libinput's "on-button scrolling"
for a regular mouse (there is no gsettings key for it, and mutter never
calls the libinput config API for non-trackpoint devices). So we implement
the behaviour one layer lower, where the compositor can't get in the way:

  * grab the physical mouse exclusively at the evdev level (EVIOCGRAB),
  * re-emit all its events through a virtual uinput device (which the
    compositor sees instead), and
  * when the middle button is held, convert pointer motion into scroll
    wheel events instead of forwarding the motion.

Feel (default, "Windows rate-based"): press-and-hold the middle button to
drop an anchor; the further you move the pointer from the anchor, the
faster it scrolls, and it keeps scrolling while held even if you stop
moving. Release to stop. A quick press+release that never leaves the
deadzone passes through as a normal middle-click, so paste still works.

Set MMB_MODE=position for the simpler libinput-style feel (scrolls only
while you are actively moving).

Runs as root (needs /dev/input/event* + /dev/uinput). All tunables are
environment variables — see CONFIG below / the systemd unit.

The pure decision logic lives in ScrollEngine, which has no evdev or device
dependencies so it can be unit-tested headlessly (see test_engine.py).
"""

import math
import os
import sys
import time


# --------------------------------------------------------------------------
# Config (env-driven; defaults are conservative — tune to taste live).
# --------------------------------------------------------------------------
def _envf(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


def _envi(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return int(default)


class Config:
    def __init__(self):
        # "rate" = Windows autoscroll (speed ∝ distance, continuous while held).
        # "position" = libinput on-button (scroll tracks motion 1:1-ish).
        self.mode = os.environ.get("MMB_MODE", "rate").strip().lower()

        # Counts of pointer travel from the anchor before scrolling begins.
        # Doubles as the click-vs-scroll threshold: stay inside it and the
        # press is delivered as a normal middle-click.
        self.deadzone = _envf("MMB_DEADZONE", 18)

        # rate mode: notches/sec = gain * (counts_beyond_deadzone ** exponent),
        # capped at max_speed. Tune gain up for faster scrolling.
        self.gain = _envf("MMB_GAIN", 0.020)
        self.exponent = _envf("MMB_EXPONENT", 1.30)
        self.max_speed = _envf("MMB_MAX_SPEED", 40.0)  # notches/sec cap

        # position mode: notches emitted per count of motion while held.
        self.position_factor = _envf("MMB_POSITION_FACTOR", 0.06)

        self.tick_ms = _envi("MMB_TICK_MS", 15)        # rate-mode scroll cadence
        self.horizontal = _envi("MMB_HORIZONTAL", 1)   # 1 = also scroll sideways
        self.invert_v = _envi("MMB_INVERT_V", 0)       # flip vertical direction
        self.invert_h = _envi("MMB_INVERT_H", 0)       # flip horizontal direction

        # Device selection. MMB_DEVICE pins one explicit /dev/input/eventN
        # (used by the test harness and to override discovery). Otherwise we
        # grab every device whose name matches MMB_MATCH_NAME and that looks
        # like a relative pointer with a middle button.
        self.device = os.environ.get("MMB_DEVICE", "").strip()
        self.match_name = os.environ.get("MMB_MATCH_NAME", "(?i)mouse")
        self.uinput_name = os.environ.get("MMB_UINPUT_NAME", "mmb-autoscroll virtual pointer")


# One wheel "notch" is 120 hi-res units (REL_WHEEL_HI_RES convention).
HIRES_PER_NOTCH = 120


class ScrollEngine:
    """Pure middle-button → scroll state machine for ONE source device.

    No evdev / device I/O. The device layer feeds it button/motion and calls
    tick(); it returns a list of intents the caller turns into uinput writes:

        ("click",)                  -> synthesise a middle-button click
        ("wheel", v_hires, h_hires) -> emit scroll (hi-res units; sign = dir)

    Motion forwarding is decided by held: while held, motion is consumed
    (the cursor anchors) and accumulated into the offset; otherwise the
    device layer forwards it untouched.
    """

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.held = False
        self.moved = False          # has the press left the deadzone yet?
        self.offx = 0.0             # pointer offset from anchor (counts)
        self.offy = 0.0
        self._acc_v = 0.0           # fractional hi-res accumulators
        self._acc_h = 0.0

    # -- middle button ----------------------------------------------------
    def middle_down(self, now):
        self.held = True
        self.moved = False
        self.offx = self.offy = 0.0
        self._acc_v = self._acc_h = 0.0
        return []                    # withhold the click until we know intent

    def middle_up(self, now):
        was_scroll = self.moved
        self.held = False
        self.moved = False
        self.offx = self.offy = 0.0
        self._acc_v = self._acc_h = 0.0
        # Never left the deadzone => it was a click, not a scroll gesture.
        return [] if was_scroll else [("click",)]

    # -- motion -----------------------------------------------------------
    def add_motion(self, dx, dy):
        """Accumulate motion while held. Returns True if consumed (held)."""
        if not self.held:
            return False
        self.offx += dx
        self.offy += dy
        if not self.moved and math.hypot(self.offx, self.offy) > self.cfg.deadzone:
            self.moved = True
        if self.cfg.mode == "position" and self.moved:
            self._emit_position(dx, dy)
        return True

    def _emit_position(self, dx, dy):
        # position mode emits as you move; stash into accumulators, flushed
        # by drain().
        f = self.cfg.position_factor
        self._acc_v += -dy * f * HIRES_PER_NOTCH if not self.cfg.invert_v else dy * f * HIRES_PER_NOTCH
        if self.cfg.horizontal:
            self._acc_h += dx * f * HIRES_PER_NOTCH if not self.cfg.invert_h else -dx * f * HIRES_PER_NOTCH

    # -- periodic tick (rate mode) ---------------------------------------
    def tick(self, dt):
        """Advance time by dt seconds; return [] or [("wheel", v, h)]."""
        if self.cfg.mode != "rate" or not self.held or not self.moved:
            return self._drain()
        self._acc_v += self._velocity(self.offy, self.cfg.invert_v) * dt
        if self.cfg.horizontal:
            self._acc_h += self._velocity(self.offx, self.cfg.invert_h, horiz=True) * dt
        return self._drain()

    def _velocity(self, off, invert, horiz=False):
        """Hi-res units/sec for an axis offset (signed)."""
        eff = abs(off) - self.cfg.deadzone
        if eff <= 0:
            return 0.0
        notches = min(self.cfg.gain * (eff ** self.cfg.exponent), self.cfg.max_speed)
        hires = notches * HIRES_PER_NOTCH
        # Vertical: moving DOWN (off>0 in evdev) scrolls content down = wheel
        # down = negative REL_WHEEL. Horizontal: moving RIGHT scrolls right =
        # positive REL_HWHEEL.
        if horiz:
            sign = 1.0 if off > 0 else -1.0
            if invert:
                sign = -sign
        else:
            sign = -1.0 if off > 0 else 1.0
            if invert:
                sign = -sign
        return sign * hires

    def _drain(self):
        """Emit whole hi-res units from the accumulators; keep the remainder."""
        v = int(self._acc_v)
        h = int(self._acc_h)
        if v == 0 and h == 0:
            return []
        self._acc_v -= v
        self._acc_h -= h
        return [("wheel", v, h)]


# --------------------------------------------------------------------------
# Everything below needs evdev + root and is exercised live, not in the unit
# test. Imported lazily so the engine module loads without evdev present.
# --------------------------------------------------------------------------
def _run():
    import re
    import selectors
    import evdev
    from evdev import ecodes as e

    cfg = Config()
    sel = selectors.DefaultSelector()

    def looks_like_mouse(d):
        caps = d.capabilities()
        rel = caps.get(e.EV_REL, [])
        key = caps.get(e.EV_KEY, [])
        return (e.REL_X in rel and e.REL_Y in rel
                and e.BTN_LEFT in key and e.BTN_MIDDLE in key)

    def discover():
        if cfg.device:
            return [evdev.InputDevice(cfg.device)]
        pat = re.compile(cfg.match_name)
        found = []
        for path in evdev.list_devices():
            try:
                d = evdev.InputDevice(path)
            except OSError:
                continue
            if d.name == cfg.uinput_name:   # never grab our own output
                continue
            if looks_like_mouse(d) and pat.search(d.name or ""):
                found.append(d)
            else:
                d.close()
        return found

    sources = discover()
    if not sources:
        print("mmb-autoscroll: no matching mouse found "
              f"(match_name={cfg.match_name!r}); exiting for systemd restart.",
              file=sys.stderr)
        sys.exit(1)

    # One virtual output device mirroring the source caps, so the compositor
    # sees an ordinary mouse. We forward most events verbatim and only inject
    # wheel events, so the source's own capabilities are what we need.
    # Limitation: a mouse exposing no wheel axis at all would need the wheel
    # codes added explicitly here; every mouse seen so far exposes REL_WHEEL
    # (and usually REL_WHEEL_HI_RES).
    ui = evdev.UInput.from_device(*sources, name=cfg.uinput_name,
                                  filtered_types=(e.EV_SYN, e.EV_FF))

    engines = {}
    for d in sources:
        try:
            d.grab()
        except OSError as ex:
            print(f"mmb-autoscroll: cannot grab {d.path} ({ex}); skipping",
                  file=sys.stderr)
            continue
        engines[d.fd] = (d, ScrollEngine(cfg))
        sel.register(d.fd, selectors.EVENT_READ, d)
        print(f"mmb-autoscroll: grabbed {d.path} '{d.name}'", file=sys.stderr)

    if not engines:
        sys.exit(1)

    tick = cfg.tick_ms / 1000.0

    def emit_wheel(v_hires, h_hires):
        if v_hires:
            ui.write(e.EV_REL, e.REL_WHEEL_HI_RES, v_hires)
            # low-res notch when we cross a 120 boundary (best-effort)
            notch = int(round(v_hires / HIRES_PER_NOTCH))
            if notch:
                ui.write(e.EV_REL, e.REL_WHEEL, notch)
        if h_hires:
            ui.write(e.EV_REL, e.REL_HWHEEL_HI_RES, h_hires)
            notch = int(round(h_hires / HIRES_PER_NOTCH))
            if notch:
                ui.write(e.EV_REL, e.REL_HWHEEL, notch)
        ui.syn()

    def emit_click():
        ui.write(e.EV_KEY, e.BTN_MIDDLE, 1)
        ui.syn()
        ui.write(e.EV_KEY, e.BTN_MIDDLE, 0)
        ui.syn()

    def do_intents(intents):
        for it in intents:
            if it[0] == "click":
                emit_click()
            elif it[0] == "wheel":
                emit_wheel(it[1], it[2])

    last = time.monotonic()
    any_scrolling = lambda: any(eng.held and eng.moved for _, eng in engines.values())

    while True:
        timeout = tick if any_scrolling() else None
        events = sel.select(timeout)
        now = time.monotonic()

        for key, _ in events:
            dev = key.data
            eng = engines[dev.fd][1]
            try:
                batch = list(dev.read())
            except OSError:
                # Device vanished (unplug). Drop it; exit if none left so
                # systemd restarts us clean.
                sel.unregister(dev.fd)
                try:
                    dev.ungrab(); dev.close()
                except OSError:
                    pass
                del engines[dev.fd]
                if not engines:
                    print("mmb-autoscroll: all devices gone; exiting.",
                          file=sys.stderr)
                    sys.exit(1)
                continue

            pending_syn = False
            for ev in batch:
                if ev.type == e.EV_KEY and ev.code == e.BTN_MIDDLE:
                    if ev.value == 1:
                        do_intents(eng.middle_down(now))
                    elif ev.value == 0:
                        do_intents(eng.middle_up(now))
                    # value==2 (autorepeat) ignored
                elif ev.type == e.EV_REL and ev.code in (e.REL_X, e.REL_Y):
                    if eng.held:
                        dx = ev.value if ev.code == e.REL_X else 0
                        dy = ev.value if ev.code == e.REL_Y else 0
                        eng.add_motion(dx, dy)
                        if cfg.mode == "position":
                            do_intents(eng._drain())
                    else:
                        ui.write(ev.type, ev.code, ev.value)
                        pending_syn = True
                elif ev.type == e.EV_SYN:
                    if pending_syn:
                        ui.syn()
                        pending_syn = False
                else:
                    # Any other event (other buttons, real wheel, misc) passes
                    # straight through.
                    ui.write(ev.type, ev.code, ev.value)
                    pending_syn = True
            if pending_syn:
                ui.syn()

        # rate-mode periodic scroll
        if cfg.mode == "rate":
            dt = now - last
            for _, eng in engines.values():
                do_intents(eng.tick(dt))
        last = now


if __name__ == "__main__":
    _run()
