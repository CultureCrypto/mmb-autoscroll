# mmb-autoscroll

Windows-style **middle-button autoscroll** for **GNOME / Wayland**, where the
compositor doesn't offer it.

## Why this exists

libinput supports
[on-button scrolling](https://wayland.freedesktop.org/libinput/doc/latest/scrolling.html#on-button-scrolling),
but on Wayland the **compositor** must opt in and expose it. GNOME's mutter
does **not** do this for a regular mouse — `org.gnome.desktop.peripherals.mouse`
has no scroll-method/scroll-button key, and libinput only enables on-button
scrolling by default for trackpoints. So the usual `xorg.conf.d` / `xinput`
recipe from the docs doesn't apply on a GNOME-Wayland session.

This daemon implements the behaviour one layer **below** the compositor:

1. exclusively grab the physical mouse at the evdev level (`EVIOCGRAB`),
2. re-emit its events through a virtual `uinput` device the compositor sees,
3. while the **middle button is held**, turn pointer motion into scroll
   wheel events instead of moving the cursor.

Because it's below the compositor, it works in every app regardless of
Wayland/mutter. A quick middle-click (one that never leaves the deadzone)
still passes through as a normal middle-click, so paste keeps working.

## Behaviour

- **Default (`MMB_MODE=rate`)** — Windows autoscroll: hold the middle button
  to drop an anchor; the farther you move from it the faster it scrolls, and
  it keeps scrolling while held even if you stop moving. Release to stop.
- **`MMB_MODE=position`** — the libinput feel: scrolls only while you're
  actively moving.

All tunables (sensitivity, deadzone, direction, horizontal on/off) live in
`/etc/mmb-autoscroll.conf` — see `mmb-autoscroll.conf.sample`.

## Install

```bash
# 1. dependency
sudo apt install -y python3-evdev

# 2. (optional) headless logic test — no root needed
python3 test_engine.py

# 3. quick FOREGROUND trial first (safest; Ctrl-C restores the mouse).
#    Hold the middle button and move; tune MMB_GAIN up/down to taste.
sudo MMB_GAIN=0.020 python3 ./mmb_autoscroll.py

# 4. once it feels right, install as a service
sudo ./install.sh            # copies binary + unit + default config
sudo systemctl start mmb-autoscroll      # trial run
sudo systemctl enable --now mmb-autoscroll   # enable at boot
```

## Tune

```bash
sudoedit /etc/mmb-autoscroll.conf
sudo systemctl restart mmb-autoscroll
journalctl -u mmb-autoscroll -b --no-pager   # which device it grabbed, errors
```

Common tweaks: `MMB_GAIN` (overall speed), `MMB_DEADZONE` (anchor size /
click sensitivity), `MMB_INVERT_V`, `MMB_HORIZONTAL=0` (vertical only).

## Uninstall / kill switch

```bash
sudo systemctl disable --now mmb-autoscroll
sudo rm -f /usr/local/bin/mmb-autoscroll /etc/systemd/system/mmb-autoscroll.service
sudo systemctl daemon-reload
# /etc/mmb-autoscroll.conf is left in place; remove if you want.
```

If a foreground trial ever leaves the mouse feeling grabbed, just `Ctrl-C`
the process (or `sudo systemctl stop mmb-autoscroll`) — releasing the grab
restores the normal mouse immediately.

## Notes / limits

- Runs as root: it needs `/dev/input/event*` and `/dev/uinput` (you're not in
  the `input` group and `/dev/uinput` is root-only).
- No on-screen anchor icon (Wayland makes a global overlay awkward); it's
  hold-and-move only.
- Device pick: by default it grabs devices whose name matches `(?i)mouse`
  and that look like a real relative pointer — this catches the mouse but
  not the dock's phantom pointer. Override with `MMB_DEVICE` /
  `MMB_MATCH_NAME` if needed.
- If the service fails with a device-access error, comment out the
  `DevicePolicy=`/`DeviceAllow=` lines in the unit and `daemon-reload`.
