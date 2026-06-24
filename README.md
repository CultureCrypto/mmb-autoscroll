# mmb-autoscroll

**Windows-style middle-button autoscroll for GNOME / Wayland** (and any other
desktop), implemented as a tiny `evdev` → `uinput` daemon that runs *below* the
compositor.

Hold the middle mouse button and move the pointer — the page scrolls, faster
the farther you move, and keeps scrolling while you hold. Let go to stop. A
quick middle-click still works normally (so paste is unaffected).

---

## Background — what I wanted, and what was missing

Windows has a feature many people rely on without thinking about it: press the
middle mouse button and you get **autoscroll** — move the pointer and the view
scrolls, accelerating with distance, until you release. It's great for long
web pages, documents and code, and for mice without a free-spinning wheel. I
wanted exactly that on my Ubuntu desktop.

Linux *can* do this in principle — `libinput` (the input stack under modern
desktops) has an [on-button scrolling](https://wayland.freedesktop.org/libinput/doc/latest/scrolling.html#on-button-scrolling)
mode that turns "button held + motion" into scrolling. But on a stock **Ubuntu
desktop (GNOME on Wayland)** there is no way to switch it on for a normal mouse:

- libinput only enables on-button scrolling **by default for pointing sticks**
  (e.g. ThinkPad TrackPoints) — not for regular mice.
- Turning it on for a mouse requires a **client of libinput to call its config
  API**. On Wayland that client is the **compositor**, and GNOME's **mutter
  exposes no setting for it**: under `org.gnome.desktop.peripherals.mouse` there
  are keys for speed, acceleration, natural-scroll and middle-click emulation,
  but **nothing for scroll method or scroll button**.
- The advice you'll find online — `xinput set-prop … "libinput Button Scrolling
  Button"`, or an `xorg.conf.d` snippet with `Option "ScrollMethod" "button"` —
  only works under **X11**. On a Wayland session it has no effect on native
  apps.

So the realistic choices were: abandon Wayland and run an X11 session just for
this, or fill the gap a different way. `mmb-autoscroll` fills the gap.

## How it works

It sits one layer **below** the compositor, where mutter can't get in the way:

1. exclusively grab the physical mouse at the evdev level (`EVIOCGRAB`),
2. re-emit its events through a virtual `uinput` device the compositor sees
   instead (so the mouse behaves completely normally), and
3. while the **middle button is held**, convert pointer motion into scroll
   wheel events instead of moving the cursor.

Because it works at the evdev/uinput layer, it's **compositor-agnostic** — it
behaves the same in every application regardless of Wayland/mutter/Xwayland.
A quick middle-click that never leaves the deadzone is passed straight through
as a normal middle-click, so middle-click paste and middle-click-to-open keep
working.

## Behaviour / modes

- **`MMB_MODE=rate`** (default) — the Windows autoscroll feel: holding the
  middle button drops an anchor; the farther you move from it the faster it
  scrolls, and it keeps scrolling while held even if you stop moving.
- **`MMB_MODE=position`** — the libinput feel: scrolls only while you are
  actively moving the mouse, roughly 1:1 with motion.

Vertical and (optionally) horizontal, hi-res-wheel output for smooth scrolling,
and every knob — speed, deadzone/anchor size, direction, click-vs-scroll
threshold — is configurable.

## Requirements

- Linux with `python3` and **python3-evdev**.
- Read access to `/dev/input/event*` and write access to `/dev/uinput`. On most
  distributions that means running as **root** (the provided systemd unit does);
  alternatively a `uinput` udev rule plus membership in the `input` group.
- `systemd` for the boot service (optional — you can also just run the script).

## Install

```bash
# 1. dependency
sudo apt install -y python3-evdev        # Debian/Ubuntu; use your distro's pkg

# 2. (optional) headless logic test — no root, no hardware needed
python3 test_engine.py

# 3. quick FOREGROUND trial first (safest; Ctrl-C restores the mouse).
#    Hold the middle button and move; tune MMB_GAIN up/down to taste.
sudo MMB_GAIN=0.020 python3 ./mmb_autoscroll.py

# 4. once it feels right, install as a service
sudo ./install.sh                         # copies binary + unit + default config
sudo systemctl enable --now mmb-autoscroll   # enable at boot
```

## Configure / tune

All tunables live in `/etc/mmb-autoscroll.conf` (see
[`mmb-autoscroll.conf.sample`](mmb-autoscroll.conf.sample)):

```bash
sudoedit /etc/mmb-autoscroll.conf
sudo systemctl restart mmb-autoscroll
journalctl -u mmb-autoscroll -b --no-pager   # which device it grabbed, any errors
```

Common tweaks: `MMB_GAIN` (overall speed), `MMB_DEADZONE` (anchor size / how
easily a hold becomes a scroll vs a click), `MMB_INVERT_V` (flip direction —
handy if you use natural scroll), `MMB_HORIZONTAL=0` (vertical only),
`MMB_MODE` (rate vs position).

## Uninstall / kill switch

```bash
sudo systemctl disable --now mmb-autoscroll
sudo rm -f /usr/local/bin/mmb-autoscroll /etc/systemd/system/mmb-autoscroll.service
sudo systemctl daemon-reload
# /etc/mmb-autoscroll.conf is left in place; remove it if you like.
```

If anything ever feels off, `sudo systemctl stop mmb-autoscroll` (or `Ctrl-C` a
foreground run) releases the grab and restores the plain mouse immediately.
Normal motion and clicks are passed through untouched while it runs.

## Compatibility & alternatives

- **On X11 you don't need this.** Enable libinput on-button scrolling natively:
  an `xorg.conf.d` snippet with `Option "ScrollMethod" "button"` +
  `Option "ScrollButton" "2"`, or per-session `xinput set-prop`.
- **Other Wayland compositors** (KDE Plasma, sway, Hyprland, …) often expose
  on-button scrolling directly — e.g. sway/Hyprland `scroll_method
  on_button_down` + `scroll_button`. Check those first. `mmb-autoscroll` is for
  environments like **GNOME** that don't, but since it works at the evdev/uinput
  layer it'll run anywhere if you'd rather have one tool everywhere.

## How it's built

The middle-button → scroll decision logic lives in a pure `ScrollEngine` class
with no evdev or device dependencies, so it's unit-tested headlessly
([`test_engine.py`](test_engine.py), 12 tests — tap-vs-scroll, the rate curve,
deadzone, direction, speed cap, position mode). The device layer
(`EVIOCGRAB` + `uinput` re-injection) is a thin shell around it. Contributions
and device reports welcome.

## Notes & limits

- No on-screen anchor icon (a global overlay is awkward on Wayland); it's
  hold-and-move only, not click-to-toggle. (`ScrollButtonLock`-style toggling
  could be added.)
- Device selection: by default it grabs devices whose name matches the regex
  `(?i)mouse` and that look like a real relative pointer — this catches the
  mouse but not, say, a USB dock/KVM's phantom pointer. Override with
  `MMB_DEVICE=/dev/input/eventN` or `MMB_MATCH_NAME` if needed.
- If the service fails with a device-access error, comment out the
  `DevicePolicy=` / `DeviceAllow=` lines in the unit and `systemctl
  daemon-reload` (some setups need looser device sandboxing).
