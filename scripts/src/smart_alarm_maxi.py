# SPDX-License-Identifier: MIT
# smart_alarm_maxi.py — noknok Smart Alarm Maxi (Setup 3: USB LEDs + I2C controls + Display)  v1.0.1
#
# The alarm-clock sibling of the Smart Lamp Maxi (same modules: LEDs 16x (or
# 8x), LED Button, Knob, Buzzer, Display). Lamp + clock + kitchen timer + wake-up
# light, with TWO alarms, six alarm tones, a display that can be turned in four
# directions, and a one-press way to arm the alarms (DEV-236).
#
#   Home screen: big clock, info line, bell + "1" / "2" / "1+2" when armed,
#   colour swatch + brightness bar (the one the knob changes has a white frame).
#   Everything stays inside a 150x70 (portrait 70x150) safe area: the panel has
#   rounded corners. Backlight: 60 % with the lamp on, 15 % off, its lowest
#   level after a Sundown (night) until the lamp is switched on again.
#
#   LED Button (home screen)
#     - single press     -> arm the alarms: Alarm 1 -> Alarm 2 -> both -> off
#                           (1 / 2 / 3 short beeps, one low beep for off). A
#                           pending snooze is cancelled instead.
#     - double press     -> Sundown: fade to off over sundown_minutes
#     - hold (1 s)       -> lamp on / off
#     - while ringing    -> timer: stop. Alarm: press = snooze 5 min, hold = stop
#   LED Button (menu)    -> one step back (edit -> menu -> home)
#   Knob (home screen)
#     - turn             -> brightness (or colour, after a tap)
#     - tap              -> switch the knob between brightness and colour;
#                           back to brightness after 5 s without turning
#     - hold (2 s)       -> menu
#   Knob (menu)  Timer · Alarm 1 · Alarm 2 · Tone · Sundown · Display · Reset · Back
#     - turn = choose / adjust, tap = OK, hold 2 s = leave the menu
#   Alarm 1/2  set the time; saving a time also arms that alarm.
#   Tone       six alarm tones, each previewed while you turn.
#   Display    0 / 90 / 180 / 270 degrees (landscape or portrait layout).
#
# Time: the brain syncs UTC over NTP at boot (code.py). The local time comes
# from the `timezone` setting; summer time (EU / US rules) is computed here.
# No WiFi -> "--:--", the timer still works, the alarms wait for the clock.
#
# Factory reset is not this script's job: hold the button (or the knob) while
# plugging in the power (code.py boot-hold). "Reset" in the menu only puts the
# lamp's own settings back to defaults (time zone and display direction kept).
#
# Settings (c.settings, shared with the app): on, brightness, color,
# sundown_minutes, alarm_armed, alarm1_time, alarm2_time, alarm_tone,
# sunrise_minutes, timezone, rotation, and the read-only info field `clock`.
# Persistence is the Conductor's (runtime Store, never a file: DEV-18 / ADR-003).
#
# Display: there is no frame buffer, so everything is repainted only when its
# value changes.

from noknok import (Conductor, BLACK, WHITE, GREY, DARK_GREY,
                    PORTRAIT, LANDSCAPE, PORTRAIT_FLIPPED, LANDSCAPE_FLIPPED)
import time

# ── Palette: 4 whites + 60 hues = 64 (same as Smart Lamp Midi / Maxi) ──────────
WHITES = [
    (255, 175,  95),   # 0  warm white   (~2700 K)
    (255, 205, 150),   # 1  soft white
    (255, 240, 225),   # 2  neutral white
    (200, 220, 255),   # 3  cool white   (~6500 K)
]
WARM_WHITE_INDEX = 0

def _hsv_hue(h):
    """Hue 0-360 at full saturation/brightness -> (r, g, b) 0-255."""
    c = 255
    x = int(255 * (1 - abs((h / 60.0) % 2 - 1)))
    if   h < 60:  return (c, x, 0)
    elif h < 120: return (x, c, 0)
    elif h < 180: return (0, c, x)
    elif h < 240: return (0, x, c)
    elif h < 300: return (x, 0, c)
    else:         return (c, 0, x)

PALETTE = WHITES + [_hsv_hue(i * 6) for i in range(60)]

def rgb_to_hex(rgb):
    return "#%02X%02X%02X" % rgb

def hex_to_rgb(s, fallback=WHITES[0]):
    try:
        s = str(s).lstrip("#")
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except Exception:
        return fallback

def palette_index(rgb):
    try:
        return PALETTE.index(tuple(rgb))
    except ValueError:
        return None

def parse_hhmm(s, fallback=7 * 60):
    """'HH:MM' -> minutes after midnight."""
    try:
        h, m = str(s).split(":")
        h, m = int(h), int(m)
        if 0 <= h < 24 and 0 <= m < 60:
            return h * 60 + m
    except Exception:
        pass
    return fallback

def fmt_hhmm(minutes):
    minutes %= 1440
    return "%02d:%02d" % (minutes // 60, minutes % 60)

def fmt_mmss(seconds):
    seconds = max(0, int(seconds + 0.999))      # round up: never show 0:00 early
    return "%d:%02d" % (seconds // 60, seconds % 60)


# ── Time zones: id -> (standard offset in hours, summer-time rule) ─────────────
# Same ids as the manifest's `timezone` options. "eu" = last Sunday of March
# to last Sunday of October, 01:00 UTC. "us" = second Sunday of March 02:00
# local to first Sunday of November 02:00 local.
ZONES = {
    "utc":  (0,  None),
    "uk":   (0,  "eu"),     # UK, Ireland, Portugal
    "cet":  (1,  "eu"),     # Central Europe (CH, DE, FR, IT, ...)
    "eet":  (2,  "eu"),     # Eastern Europe (FI, GR, RO, ...)
    "us_e": (-5, "us"),
    "us_c": (-6, "us"),
    "us_m": (-7, "us"),
    "us_p": (-8, "us"),
    "jp":   (9,  None),     # Japan / Korea, no summer time
}

def _utc(y, m, d, h=0):
    """UTC epoch seconds of a date/hour. The RTC runs on UTC, so mktime is UTC."""
    return time.mktime((y, m, d, h, 0, 0, 0, -1, -1))

def _sunday_on_or_before(y, m, d):
    wd = time.localtime(_utc(y, m, d)).tm_wday            # Monday = 0, Sunday = 6
    return d - ((wd + 1) % 7)

_dst_cache = {}   # (rule, std, year) -> (start_utc, end_utc)

def _dst_window(rule, std, year):
    key = (rule, std, year)
    if key not in _dst_cache:
        if rule == "eu":
            start = _utc(year, 3,  _sunday_on_or_before(year, 3, 31), 1)
            end   = _utc(year, 10, _sunday_on_or_before(year, 10, 31), 1)
        else:   # "us"
            start = _utc(year, 3,  _sunday_on_or_before(year, 3, 14), 2 - std)
            end   = _utc(year, 11, _sunday_on_or_before(year, 11, 7), 2 - (std + 1))
        _dst_cache[key] = (start, end)
    return _dst_cache[key]

def utc_offset_s(utc_now, zone_id):
    std, rule = ZONES.get(zone_id, ZONES["cet"])
    off = std * 3600
    if rule:
        start, end = _dst_window(rule, std, time.localtime(utc_now).tm_year)
        if start <= utc_now < end:
            off += 3600
    return off


# ── Alarm tones ────────────────────────────────────────────────────────────────
# Each tone is a list of (frequency Hz, note length ms, step ms): the note is
# sent, the next one follows `step` ms later (step > length = a rest after it).
# freq 0 = a rest. The buzzer counts lengths in 100 ms units and any new command
# cuts the running note, so a step shorter than its length simply cuts it.
# The list repeats while the alarm rings; the volume rises over time.
TONES = {
    # The Maxi's original call: rising three notes, pause.
    "classic":  [(784, 200, 300), (988, 200, 300), (1175, 200, 1200)],
    # Soft chime: C-E-G-C, slow, long pause.
    "gentle":   [(523, 300, 400), (659, 300, 400), (784, 300, 400), (1047, 600, 2000)],
    # Mozart, Eine kleine Nachtmusik K. 525 (1787, public domain), opening:
    # G . D G . D | G D G B D --
    "mozart":   [(784, 300, 600), (587, 100, 200), (784, 300, 600), (587, 100, 200),
                 (784, 100, 200), (587, 100, 200), (784, 100, 200), (988, 100, 200),
                 (1175, 700, 2000)],
    # 8-bit "level up" jingle: fast arpeggios, one step up, a held top note.
    "chiptune": [(1047, 100, 110), (1319, 100, 110), (1568, 100, 110), (2093, 100, 110),
                 (1568, 100, 110), (2093, 200, 330),
                 (1175, 100, 110), (1480, 100, 110), (1760, 100, 110), (2349, 300, 1300)],
    # Bird chirps: two quick calls high up.
    "birds":    [(2637, 100, 120), (3136, 100, 120), (2637, 100, 500),
                 (2349, 100, 120), (2794, 100, 120), (3136, 100, 120), (3520, 100, 1500)],
    # A slow major scale climbing like the sunrise.
    "rise":     [(523, 200, 260), (587, 200, 260), (659, 200, 260), (698, 200, 260),
                 (784, 200, 260), (880, 200, 260), (988, 200, 260), (1047, 500, 1800)],
}
TONE_ORDER = ["classic", "gentle", "mozart", "chiptune", "birds", "rise"]
TONE_NAMES = {"classic": "Classic", "gentle": "Gentle", "mozart": "Mozart",
              "chiptune": "Chiptune", "birds": "Birds", "rise": "Sunrise"}
TIMER_TONE = [(1760, 100, 250), (1760, 100, 900)]      # fast double beep

# ── Alarms armed: one setting, four states ────────────────────────────────────
ARMED_ORDER = ["off", "1", "2", "both"]
ARMED_SET   = {"off": (), "1": (1,), "2": (2,), "both": (1, 2)}
ARMED_LABEL = {"off": "", "1": "1", "2": "2", "both": "1+2"}

# ── Display direction: setting value -> noknok.py rotation constant ───────────
# 0 = the module's boot default (landscape). Which way "90" turns depends on how
# the display is mounted; 90 and 270 are the two portrait directions.
ROTATIONS = {"0": LANDSCAPE, "90": PORTRAIT, "180": LANDSCAPE_FLIPPED,
             "270": PORTRAIT_FLIPPED}
ROT_ORDER = ["0", "90", "180", "270"]


# ── Behaviour tuning ───────────────────────────────────────────────────────────
BRIGHT_MIN       = 8       # lowest lamp level: below it colours round to red
LONG_PRESS_S     = 1.0     # button hold -> lamp on/off, alarm stop
DOUBLE_S         = 0.35    # second press within this after a release = double press
KNOB_HOLD_S      = 2.0     # knob hold -> menu
KNOB_MODE_IDLE_S = 5.0     # colour mode falls back to brightness after this
MENU_IDLE_S      = 20.0    # menu closes by itself after this
FLASH_S          = 2.5     # how long "Alarm 1 on" etc. stays on the info line
SNOOZE_S         = 5 * 60
RING_MAX_S       = {"timer": 5 * 60, "alarm": 10 * 60}   # stop ringing by itself
SUNRISE_STEP_S   = 2.0     # how often the wake-up light may change brightness
BACKLIGHT_ON     = 0.6     # display backlight while the lamp is on
BACKLIGHT_OFF    = 0.15    # dimmer while the lamp is off (bedside)
BACKLIGHT_NIGHT  = 3 / 255  # after a Sundown: dimmest real level (a FRACTION:
                            # raw 1-2 are BRIGHTER than 3 on the panel, bench 4 Oct)
STATUS_SHOW_S    = 3.0     # button LED shows a new colour this long, then dark
LOOP_SLEEP_S     = 0.03
LAMP_REFRESH_S   = 10.0    # re-send the full lamp state this often while on
LAMP_COALESCE_S  = 0.12    # knob turns: at most one LEDs update this often
LAMP_SETTLE_S    = 0.5     # one full re-send this long after the LAST toggle (DEV-151)
SAFE             = 5       # px kept free at every panel edge (rounded corners)

DEFAULTS = {
    "on": True,
    "brightness": 120,
    "color": rgb_to_hex(WHITES[WARM_WHITE_INDEX]),
    "sundown_minutes": 30,
    "alarm_armed": "off",
    "alarm1_time": "07:00",
    "alarm2_time": "08:30",
    "alarm_tone": "classic",
    "sunrise_minutes": 15,
    "timezone": "cet",
    "rotation": "0",
}
KEEP_ON_RESET = ("timezone", "rotation")      # "Reset" in the menu keeps these
LAMP_KEYS = [k for k in DEFAULTS if k not in KEEP_ON_RESET]


# ── Setup ──────────────────────────────────────────────────────────────────────
c = Conductor()
c.enumerate_all()      # both buses: I2C controls + display, USB LEDs
c.load_roles()         # no roles: every module is a singleton

button  = c.ledbutton[0] if c.ledbutton else None
knob    = c.knob[0]      if c.knob      else None
buzzer  = c.buzzer[0]    if c.buzzer    else None
display = c.display[0]   if c.display   else None
lamps   = list(c.leds16) + list(c.leds)    # every LEDs module, 16x and 8x

if not lamps:
    print("[alarm] FAILED: no USB LEDs module found")
    raise SystemExit("No USB LEDs module found - check wiring.")
if button is None or knob is None or display is None:
    print("[alarm] WARNING missing: button=%s knob=%s display=%s"
          % (button is not None, knob is not None, display is not None))

s = c.settings
s.defaults(DEFAULTS)

def lamp_on():         return bool(s.get("on"))
def brightness():      return max(BRIGHT_MIN, min(255, int(s.get("brightness", 120))))
def color_rgb():       return hex_to_rgb(s.get("color"), WHITES[WARM_WHITE_INDEX])
def sundown_minutes(): return max(1, min(120, int(s.get("sundown_minutes", 30))))
def sunrise_minutes(): return max(1, min(60, int(s.get("sunrise_minutes", 15))))
def armed():
    a = str(s.get("alarm_armed", "off"))
    return a if a in ARMED_SET else "off"
def alarm_min(n):      return parse_hhmm(s.get("alarm%d_time" % n, DEFAULTS["alarm%d_time" % n]))
def tone_id():
    t = str(s.get("alarm_tone", "classic"))
    return t if t in TONES else "classic"
def rotation_id():
    r = str(s.get("rotation", "0"))
    return r if r in ROTATIONS else "0"

def local_now():
    """Local time as a struct_time, or None while the clock is not set (no NTP
    since power-on: the Pico has no battery-backed clock)."""
    utc = time.time()
    if time.localtime(utc).tm_year < 2025:
        return None
    return time.localtime(utc + utc_offset_s(utc, str(s.get("timezone", "cet"))))

def clock_text():
    t = local_now()
    return "--:--" if t is None else "%02d:%02d" % (t.tm_hour, t.tm_min)

s.info("clock", lambda: (clock_text() if local_now() else "not set - no WiFi"))

print("[alarm] up: %d lamp(s), button=%s knob=%s buzzer=%s display=%s, settings=%s"
      % (len(lamps), button is not None, knob is not None, buzzer is not None,
         display is not None, s.all()))


# ── Runtime state (never persisted: a power cut ends a timer or a fade) ────────
sundown_end   = None    # monotonic time the running Sundown fade reaches off
sundown_start = 0.0     # when the running Sundown (re)started
sundown_level = None    # brightness the Sundown fade is at
sunrise_level = None    # brightness the wake-up light is at, or None
sunrise_skip  = None    # monotonic time until which the wake-up light is cancelled
timer_end     = None    # monotonic time the kitchen timer reaches zero
timer_last    = 10      # minutes, preselected next time the timer is opened
ringing       = None    # None / "timer" / "alarm"
ring_start    = 0.0
snooze_until  = None    # monotonic time a snoozed alarm rings again
alarm_fired   = {}      # alarm number -> (year, yday, minute) it last rang
flash_text    = None    # short message on the info line ("Alarm 1 on" ...)
flash_until   = 0.0

knob_mode     = "bri"   # home screen: "bri" or "col"
lamp_dirty    = False   # a knob turn changed the lamp; sent from the loop
night_dim     = False   # display at night level until the lamp is switched on again
knob_used_at  = 0.0
screen        = "home"  # "home" / "menu" / "edit"
menu_idx      = 0
menu_used_at  = 0.0
edit          = None    # dict while editing a menu item

MENU = ["Timer", "Alarm 1", "Alarm 2", "Tone", "Sundown", "Display", "Reset", "Back"]


# ── Buzzer ─────────────────────────────────────────────────────────────────────
# One small sequencer plays everything longer than a single beep (alarm tones,
# timer ring, tone preview, arming cues) from the main loop, so nothing blocks.
def beep(freq=1000, ms=100, vol=60):
    if buzzer: buzzer.play(freq, ms, vol)
def beep_on():      beep(1000, 100, 60)
def beep_off():     beep(440, 200, 60)
def beep_ok():      beep(1320, 100, 60)
def beep_back():    beep(660, 100, 50)

class Player:
    def __init__(self):
        self.seq = None
    def start(self, seq, loop=False, vol=60):
        self.seq, self.loop, self.vol, self.i, self.next = seq, loop, vol, 0, 0.0
    def cancel(self):
        """Forget the sequence; a note already sent ends by itself."""
        self.seq = None
    def stop(self):
        if self.seq is not None and buzzer:
            buzzer.stop()
        self.seq = None
    def busy(self):
        return self.seq is not None
    def service(self, now, vol=None):
        if self.seq is None or now < self.next:
            return
        if self.i >= len(self.seq):
            if not self.loop:
                self.seq = None
                return
            self.i = 0
        f, length, step = self.seq[self.i]
        if f:
            beep(f, length, self.vol if vol is None else vol)
        self.next = now + step / 1000
        self.i += 1

player = Player()

def cue(seq):
    """A short feedback sequence, unless something is ringing."""
    if ringing is None:
        player.start(seq, loop=False, vol=60)

def beep_sundown():   # descending "good night" motif
    cue([(660, 150, 180), (523, 150, 180), (392, 200, 250)])


# ── Lamp output ────────────────────────────────────────────────────────────────
# The LEDs 16x has a real white die (W). Mixing white from R+G+B turns red when
# dimmed, so on the 16x the shared part of a colour goes to W.
def _rgbw(lamp, r, g, b):
    if hasattr(lamp, "white"):           # NoknokLEDs16
        w = min(r, g, b)
        return (r - w, g - w, b - w, w)
    return (r, g, b)

def lamp_color(lamp, r, g, b):
    lamp.set_all(*_rgbw(lamp, r, g, b))

def lamp_preset(lamp, preset, speed, r, g, b):
    c4 = _rgbw(lamp, r, g, b)
    if len(c4) == 4:
        lamp.play_preset(preset, speed, c4[0], c4[1], c4[2], c4[3])
    else:
        lamp.play_preset(preset, speed, r, g, b)

def apply_output(changed=None):
    """Push the settings to every LEDs module and the status LED."""
    global sundown_end, sundown_start, sundown_level, status_until
    if changed and "color" in changed:   # a new colour from the app: show it
        status_until = time.monotonic() + STATUS_SHOW_S
    if ringing == "timer":               # the pulse owns the lamp until stopped
        paint_status()
        return
    if not lamp_on():
        sundown_end = None
        for lamp in lamps:
            lamp.off()
        paint_status()
        return
    if sundown_end is not None:          # a running Sundown restarts from here
        sundown_start = time.monotonic()
        sundown_end   = sundown_start + sundown_minutes() * 60
        sundown_level = brightness()
    push_lamp(current_level())
    paint_status()

def current_level():
    if sunrise_level is not None:
        return sunrise_level
    if sundown_end is not None and sundown_level is not None:
        return sundown_level
    return brightness()

# Every lamp update sends BOTH brightness and the full colour, and the state is
# re-sent every LAMP_REFRESH_S while on: a garbled USB frame never sticks (DEV-73).
_last_push = 0.0
_settle_at = None    # DEV-151: one re-send LAMP_SETTLE_S after the last toggle

def resend_lamp():
    if ringing == "timer":
        return
    if lamp_on():
        push_lamp(current_level())
    else:
        for lamp in lamps:
            lamp.off()

def push_lamp(level):
    global _last_push
    r, g, b = color_rgb()
    for lamp in lamps:
        lamp.set_brightness(level)
        lamp_color(lamp, r, g, b)
    _last_push = time.monotonic()

# Sundown and the wake-up light are driven from here with set_brightness (one
# rounding, stays white down to 8), NOT with the module's SUNDOWN preset.
_sundown_sent = 0.0

def service_sundown(now):
    global sundown_end, sundown_level, _sundown_sent, night_dim
    if sundown_end is None or ringing == "timer":
        return
    total = sundown_end - sundown_start
    p = min(1.0, (now - sundown_start) / total)
    top = max(BRIGHT_MIN, brightness())
    level = BRIGHT_MIN + int((top - BRIGHT_MIN) * (1.0 - p) * (1.0 - p))
    if p >= 1.0:
        sundown_end = None
        night_dim = True                 # bedtime: display to its lowest level
        s.set("on", False)
        apply_output()
        print("[alarm] sundown finished -> off")
    elif level != sundown_level and now - _sundown_sent >= SUNRISE_STEP_S:
        sundown_level = level
        _sundown_sent = now
        push_lamp(level)

# Button LED: dark by default (bedside). After a colour change it shows the new
# colour for STATUS_SHOW_S, then goes dark again.
status_until = 0.0
_status_shown = None

def show_color_on_button():
    global status_until
    status_until = time.monotonic() + STATUS_SHOW_S
    paint_status()

def paint_status():
    global _status_shown
    if button is None:
        return
    want = color_rgb() if (lamp_on() and time.monotonic() < status_until) else None
    if want == _status_shown:
        return
    _status_shown = want
    if want:
        button.set_color(*want)
    else:
        button.led_off()

def start_sundown():
    global sundown_end
    cancel_sunrise()
    if not lamp_on():
        s.set("on", True)
    sundown_end = time.monotonic()       # marks it running; apply_output sets the end
    beep_sundown()
    apply_output()
    print("[alarm] sundown %d min" % sundown_minutes())

def cancel_sunrise():
    """The user took over: no more wake-up ramp until this alarm has passed.
    Returns True if a ramp was actually running."""
    global sunrise_level, sunrise_skip
    if sunrise_level is None:
        return False
    sunrise_level = None
    sunrise_skip = time.monotonic() + sunrise_minutes() * 60 + 60
    return True


# ── Ringing (timer at zero, alarm time) ────────────────────────────────────────
def start_ring(kind):
    global ringing, ring_start, snooze_until
    ringing, ring_start = kind, time.monotonic()
    snooze_until = None
    if kind == "timer":
        player.start(TIMER_TONE, loop=True, vol=80)
        r, g, b = color_rgb()
        for lamp in lamps:
            lamp.set_brightness(255)
            lamp_preset(lamp, lamp.PRESET_BREATHE, 0, r, g, b)
    else:
        player.start(TONES[tone_id()], loop=True, vol=50)
    print("[alarm] ringing: %s (%s)" % (kind, tone_id() if kind == "alarm" else "timer"))

def stop_ring(snooze=False):
    global ringing, snooze_until
    kind, ringing = ringing, None
    player.stop()
    snooze_until = time.monotonic() + SNOOZE_S if (snooze and kind == "alarm") else None
    apply_output()                       # the lamp back to its settings
    print("[alarm] %s %s" % (kind, "snoozed" if snooze_until else "stopped"))

def service_ring(now):
    if ringing is None:
        player.service(now)              # preview / cues
        return
    if now - ring_start >= RING_MAX_S[ringing]:
        stop_ring()
        return
    vol = 80 if ringing == "timer" else min(100, 50 + int((now - ring_start) / 6))
    player.service(now, vol)


# ── Alarms + wake-up light ─────────────────────────────────────────────────────
_sunrise_sent = 0.0

def next_alarm(t):
    """(alarm number, minute, seconds until it) of the next armed alarm, or None."""
    now_s = t.tm_hour * 3600 + t.tm_min * 60 + t.tm_sec
    best = None
    for n in ARMED_SET[armed()]:
        m = alarm_min(n)
        rem = (m * 60 - now_s) % 86400
        if best is None or rem < best[2]:
            best = (n, m, rem)
    return best

def service_alarm(now):
    """Every loop: fire an armed alarm at its minute, ramp the wake-up light
    before the next one, re-ring after a snooze."""
    global sunrise_level, _sunrise_sent
    if snooze_until is not None and now >= snooze_until and ringing is None:
        start_ring("alarm")
        return
    t = local_now()
    nxt = next_alarm(t) if t is not None else None
    if nxt is None:
        if sunrise_level is not None:
            sunrise_level = None
            apply_output()
        return
    now_min = t.tm_hour * 60 + t.tm_min
    for n in ARMED_SET[armed()]:
        m = alarm_min(n)
        day = (t.tm_year, t.tm_yday, m)
        if now_min == m and alarm_fired.get(n) != day:
            alarm_fired[n] = day
            if sunrise_level is not None:    # end of the ramp: full set brightness
                sunrise_level = None
                apply_output()
            if ringing is None:
                start_ring("alarm")
            print("[alarm] alarm %d at %s" % (n, fmt_hhmm(m)))
            return
    remaining = nxt[2]
    total = sunrise_minutes() * 60
    skipped = sunrise_skip is not None and now < sunrise_skip
    if 0 < remaining <= total and not skipped and ringing is None:
        p = 1.0 - remaining / total
        level = max(BRIGHT_MIN, int(brightness() * p * p))   # eased: slow start
        if sunrise_level is None:
            print("[alarm] wake-up light: %d min to alarm %d" % (remaining // 60, nxt[0]))
            sunrise_level = level
            if not lamp_on():
                s.set("on", True)
            apply_output()
            _sunrise_sent = now
        elif level != sunrise_level and now - _sunrise_sent >= SUNRISE_STEP_S:
            sunrise_level = level
            push_lamp(level)
            _sunrise_sent = now
    elif sunrise_level is not None and remaining > total:
        sunrise_level = None                         # alarm moved / disarmed
        apply_output()

def cycle_armed():
    """Single button press: off -> 1 -> 2 -> both -> off."""
    global flash_text, flash_until
    nxt = ARMED_ORDER[(ARMED_ORDER.index(armed()) + 1) % len(ARMED_ORDER)]
    s.set("alarm_armed", nxt)
    hi = (1319, 100, 180)
    cue({"off": [(440, 200, 250)], "1": [hi], "2": [hi, hi], "both": [hi, hi, hi]}[nxt])
    if L.get("portrait"):
        flash_text = {"off": "Off", "1": "A1 on", "2": "A2 on", "both": "A1+A2"}[nxt]
    else:
        flash_text = {"off": "Alarms off", "1": "Alarm 1 on", "2": "Alarm 2 on",
                      "both": "Both alarms"}[nxt]
    flash_until = time.monotonic() + FLASH_S
    print("[alarm] armed: %s" % nxt)

def arm(n):
    """Saving an alarm time in the menu arms that alarm too."""
    a = set(ARMED_SET[armed()]) | {n}
    s.set("alarm_armed", "both" if len(a) == 2 else str(n))


# ── Display layout ─────────────────────────────────────────────────────────────
# Two layouts, both inside the safe area (SAFE px from every edge).
#
#   Landscape 160x80                      Portrait 80x160
#   y  5-36  clock 32 px                  y  5-44  hours 40 px
#   y 40-55  info | 1+2 | bell            y 46-85  minutes 40 px
#   y 59-74  swatch | brightness bar      y 90-105 bell | 1+2
#                                         y108-123 info
#                                         y127-138 colour swatch
#                                         y142-154 brightness bar
#
# Region = (name, x, y, w, h, size, colour, align).
L = {}

def compute_layout():
    portrait = display.height > display.width
    L.clear()
    L["portrait"] = portrait
    if not portrait:
        L["home"] = [
            ("clock", 5, 5, 150, 32, 32, WHITE, "center"),
            ("info", 5, 40, 106, 16, 16, GREY, "left"),
            ("armed", 113, 40, 24, 16, 16, WHITE, "right"),
            ("bell", 139, 40, 16, 16, 16, WHITE, "left"),
        ]
        L["sw"], L["bar"] = (5, 59, 32, 16), (41, 59, 114, 16)
        L["menu"] = [
            ("m_title", 5, 5, 150, 16, 16, GREY, "center"),
            ("m_value", 5, 28, 150, 24, 24, WHITE, "center"),
            ("m_hint", 5, 59, 150, 16, 16, GREY, "center"),
        ]
    else:
        L["home"] = [
            ("clock_h", 5, 5, 70, 40, 40, WHITE, "center"),
            ("clock_m", 5, 46, 70, 40, 40, WHITE, "center"),
            ("bell", 5, 90, 16, 16, 16, WHITE, "left"),
            ("armed", 23, 90, 52, 16, 16, WHITE, "left"),
            ("info", 5, 108, 70, 16, 16, GREY, "center"),
        ]
        L["sw"], L["bar"] = (5, 127, 70, 12), (5, 142, 70, 13)
        L["menu"] = [
            ("m_title", 5, 5, 70, 16, 16, GREY, "center"),
            ("m_value", 5, 64, 70, 16, 16, WHITE, "center"),
            ("m_hint", 5, 139, 70, 16, 16, GREY, "center"),
        ]

def apply_rotation():
    if not display:
        return
    display.rotation(ROTATIONS[rotation_id()])
    compute_layout()
    print("[alarm] display %s deg (%dx%d)" % (rotation_id(), display.width, display.height))

_drawn = {}       # region name -> what is on the panel now (repaint on change)

def _define(regions):
    display.clear(BLACK)
    for name, x, y, w, h, size, col, align in regions:
        display.region(name, x, y, w, h, size=size, color=col, align=align)
    _drawn.clear()

def _put(name, value, **kw):
    """Repaint a region only if its content changed."""
    if _drawn.get(name) == value:
        return
    _drawn[name] = value
    if value is None or value == "":
        display.set(name)
    elif name == "bell":
        display.set(name, icon=value)
    else:
        display.set(name, text=value, **kw)

def _frame(rect, frame_col, inner_col):
    x, y, w, h = rect
    display.fill_rect(x, y, w, h, frame_col)
    display.fill_rect(x + 2, y + 2, w - 4, h - 4, inner_col)

def _lamp_row():
    on = lamp_on()
    rgb = color_rgb()
    level = sunrise_level if sunrise_level is not None else brightness()
    sw = (on, rgb, knob_mode)
    if _drawn.get("sw") != sw:
        _drawn["sw"] = sw
        _frame(L["sw"], WHITE if knob_mode == "col" else DARK_GREY, rgb if on else BLACK)
    if _drawn.get("frame") != knob_mode:
        _drawn["frame"] = knob_mode
        _frame(L["bar"], WHITE if knob_mode == "bri" else DARK_GREY, BLACK)
        _drawn.pop("bar", None)          # the inside was wiped: repaint the fill
    # Only the difference is painted: wiping the whole bar flickers (bench 3 Oct).
    bx, by, bw, bh = L["bar"]
    x0, y0, inner_w, inner_h = bx + 2, by + 2, bw - 4, bh - 4
    fill = max(1, (inner_w * level) // 255)
    col = WHITE if on else DARK_GREY
    old = _drawn.get("bar")
    if old == (fill, col):
        return
    _drawn["bar"] = (fill, col)
    if old is None or old[1] != col:
        display.fill_rect(x0, y0, fill, inner_h, col)
        display.fill_rect(x0 + fill, y0, inner_w - fill, inner_h, BLACK)
    elif fill > old[0]:
        display.fill_rect(x0 + old[0], y0, fill - old[0], inner_h, col)
    else:
        display.fill_rect(x0 + fill, y0, old[0] - fill, inner_h, BLACK)

def _info_text(now):
    """Info line. Portrait has 8 characters, landscape 13."""
    p = L.get("portrait")
    if ringing == "timer":
        return "TIMER!" if p else "TIMER! press"
    if ringing == "alarm":
        return "ALARM!" if p else "ALARM! snooze"
    if flash_text is not None and now < flash_until:
        return flash_text
    if snooze_until is not None:
        return ("Z " if p else "Snooze ") + fmt_mmss(snooze_until - now)
    if timer_end is not None:
        return ("T " if p else "Timer ") + fmt_mmss(timer_end - now)
    if sundown_end is not None and lamp_on():
        mins = max(1, int((sundown_end - now) / 60 + 0.999))
        return ("Sun %dm" if p else "Sundown %dmin") % mins
    if sunrise_level is not None:
        return "Morning" if p else "Good morning"
    if armed() != "off":
        t = local_now()
        if t is None:
            return "No WiFi"
        nxt = next_alarm(t)
        return ("" if p else "Alarm ") + fmt_hhmm(nxt[1])
    return ""

_backlight = None

def paint_home(now):
    global _backlight, night_dim
    if lamp_on() or ringing:
        night_dim = False
        want = BACKLIGHT_ON
    else:
        want = BACKLIGHT_NIGHT if night_dim else BACKLIGHT_OFF
    if want != _backlight:
        display.backlight(want)
        _backlight = want
    clock = clock_text()
    if L["portrait"]:
        _put("clock_h", clock[:2])
        _put("clock_m", clock[3:])
    else:
        _put("clock", clock)
    _put("info", _info_text(now))
    a = armed()
    _put("armed", ARMED_LABEL[a])
    _put("bell", "bell" if a != "off" else None)
    _lamp_row()

def _hint():
    if L["portrait"]:
        return "tap = OK"
    return "tap=OK  btn=back"

def paint_menu():
    if screen == "menu":
        _put("m_title", "MENU %d/%d" % (menu_idx + 1, len(MENU)))
        _put("m_value", MENU[menu_idx])
    else:
        _put("m_title", edit["title"])
        _put("m_value", edit_value_text())
    _put("m_hint", _hint())

def go_home():
    global screen, edit
    if ringing is None:
        player.cancel()                  # a tone preview ends with its note
    screen, edit = "home", None
    if display:
        _define(L["home"])

def go_menu(idx=0):
    global screen, menu_idx, menu_used_at, edit
    if ringing is None:
        player.cancel()
    screen, menu_idx, menu_used_at, edit = "menu", idx, time.monotonic(), None
    if display:
        _define(L["menu"])

def menu_back():
    """LED Button in the menu: one step back."""
    beep_back()
    if screen == "edit":
        go_menu(menu_idx)
    else:
        go_home()


# ── Menu editing ───────────────────────────────────────────────────────────────
def open_item(name):
    global edit, screen
    if name == "Back":
        go_home()
        return
    if name == "Timer":
        edit = {"title": "Timer", "v": 0 if timer_end is not None else timer_last}
    elif name in ("Alarm 1", "Alarm 2"):
        n = int(name[-1])
        edit = {"title": name, "n": n, "v": alarm_min(n)}
    elif name == "Tone":
        edit = {"title": "Tone", "v": TONE_ORDER.index(tone_id())}
    elif name == "Sundown":
        edit = {"title": "Sundown", "v": sundown_minutes()}
    elif name == "Display":
        edit = {"title": "Display", "v": ROT_ORDER.index(rotation_id())}
    elif name == "Reset":
        edit = {"title": "Reset?", "v": 0}
    edit["item"] = name
    screen = "edit"
    _drawn.clear()

def edit_value_text():
    it, v = edit["item"], edit["v"]
    if it == "Timer":
        return ("Stop" if timer_end is not None else "Off") if v == 0 else "%d min" % v
    if it.startswith("Alarm"):
        return fmt_hhmm(v)
    if it == "Tone":
        return TONE_NAMES[TONE_ORDER[v]]
    if it == "Sundown":
        return "%d min" % v
    if it == "Display":
        return "%s deg" % ROT_ORDER[v]
    return "Yes" if v else "No"

def edit_turn(delta):
    it, v = edit["item"], edit["v"]
    if it == "Timer":                    # 1-min steps up to 10, then 5-min steps
        for _ in range(abs(delta)):
            if delta > 0:
                v = v + 1 if v < 10 else min(120, v + 5)
            else:
                v = v - 1 if v <= 10 else v - 5
            v = max(0, v)
    elif it.startswith("Alarm"):         # fast turn = 15-min steps
        v = (v + (5 if abs(delta) < 3 else 15) * delta) % 1440
    elif it == "Tone":
        v = (v + delta) % len(TONE_ORDER)
        if ringing is None:              # play the new tone once as a preview
            player.start(TONES[TONE_ORDER[v]], loop=False, vol=60)
    elif it == "Sundown":
        v = max(5, min(60, v + 5 * delta))
    elif it == "Display":
        v = (v + delta) % len(ROT_ORDER)
    else:
        v = (v + delta) % 2
    edit["v"] = v

def edit_confirm():
    """Knob tap in an edit screen."""
    global timer_end, timer_last
    it, v = edit["item"], edit["v"]
    if it == "Timer":
        if v == 0:
            timer_end = None
            print("[alarm] timer cleared")
        else:
            timer_last = v
            timer_end = time.monotonic() + v * 60
            print("[alarm] timer %d min" % v)
    elif it.startswith("Alarm"):
        s.set("alarm%d_time" % edit["n"], fmt_hhmm(v))
        arm(edit["n"])
        print("[alarm] alarm %d %s, armed %s" % (edit["n"], fmt_hhmm(v), armed()))
    elif it == "Tone":
        s.set("alarm_tone", TONE_ORDER[v])
    elif it == "Sundown":
        s.set("sundown_minutes", v)
    elif it == "Display":
        if s.set("rotation", ROT_ORDER[v]):
            apply_rotation()             # the app's on_change does the same
    elif v:
        reset_lamp()
        go_home()
        return
    else:
        go_home()
        return
    beep_ok()
    go_home()

def reset_lamp():
    global timer_end, sundown_end, snooze_until, knob_mode
    timer_end = sundown_end = snooze_until = None
    cancel_sunrise()
    knob_mode = "bri"
    for k in LAMP_KEYS:
        s.set(k, DEFAULTS[k])
    beep_ok()
    apply_output()
    print("[alarm] lamp settings reset to defaults")


# ── Controls ───────────────────────────────────────────────────────────────────
def toggle_lamp():
    global _settle_at
    cancel_sunrise()
    if lamp_on():
        s.set("on", False)
        beep_off()
    else:
        s.set("on", True)
        beep_on()
    apply_output()
    _settle_at = time.monotonic() + LAMP_SETTLE_S
    print("[alarm] lamp %s" % ("on" if lamp_on() else "off"))

def button_single():
    """Home screen, single press (after the double-press window)."""
    global snooze_until
    if ringing is not None:
        return
    if snooze_until is not None:
        snooze_until = None              # a pending snooze is cancelled first
        beep_off()
        print("[alarm] snooze cancelled")
        return
    cycle_armed()

def knob_turn_home(delta):
    global knob_mode, lamp_dirty
    if not lamp_on():
        return                           # turning does nothing while off
    changed = cancel_sunrise()
    if knob_mode == "bri":
        level = brightness()
        for _ in range(abs(delta)):      # ~8 % per detent: even feel low and high
            step = max(2, level // 12)
            level = level + step if delta > 0 else level - step
        changed = s.set("brightness", max(BRIGHT_MIN, min(255, level))) or changed
    else:
        idx = palette_index(color_rgb())
        idx = WARM_WHITE_INDEX if idx is None else (idx + delta) % len(PALETTE)
        if s.set("color", rgb_to_hex(PALETTE[idx])):
            changed = True
            show_color_on_button()
    if changed:                          # never fire USB commands for nothing (DEV-78)
        lamp_dirty = True

def on_settings(changed=None):
    """The app changed something."""
    if changed and "rotation" in changed and display:
        apply_rotation()
        if screen == "home":
            go_home()
        else:
            go_menu(menu_idx)
    apply_output(changed)


# ── Main loop ──────────────────────────────────────────────────────────────────
s.on_change(on_settings)
apply_output()
if display:
    # The Pico's 8x16 font is half as wide as the module's square cells.
    display.native_text = False
    apply_rotation()
    go_home()

pb_start = None; pb_long = False; pb_ring = False    # button
pb_single_at = None                                   # release time of a waiting single press
kn_start = None; kn_long = False; kn_was = False     # knob

while True:
    now = time.monotonic()
    pb = button.read() if button else None
    kn = knob.read()   if knob   else None

    # ── Button ──────────────────────────────────────────────────────────────
    if pb is not None:
        if pb.press_event:
            pb_start, pb_long = now, False
            pb_ring = ringing is not None        # this press belongs to the ringing
        if pb.pressed and pb_start is not None and not pb_long and now - pb_start >= LONG_PRESS_S:
            pb_long = True
            if pb_ring:
                if ringing: stop_ring()          # hold while ringing = stop
            elif screen == "home":
                pb_single_at = None              # a hold cancels a waiting single press
                toggle_lamp()                    # hold = lamp on / off
            # in the menu a hold does nothing
        if pb.release_event:
            if pb_start is not None and not pb_long:
                if pb_ring:
                    if ringing: stop_ring(snooze=(ringing == "alarm"))
                elif screen != "home":
                    menu_used_at = now
                    menu_back()                  # menu: one step back, no waiting
                elif pb_single_at is not None:
                    pb_single_at = None          # second press in time = double
                    start_sundown()
                else:
                    pb_single_at = now           # wait: maybe a second press follows
            pb_start = None
    if pb_single_at is not None and pb_start is None and now - pb_single_at >= DOUBLE_S:
        pb_single_at = None
        button_single()

    # ── Knob ────────────────────────────────────────────────────────────────
    if kn is not None:
        if kn.pressed and not kn_was:            # press
            kn_start, kn_long = now, False
            if ringing:
                stop_ring(snooze=(ringing == "alarm"))
                kn_long = True                   # swallow the rest of this press
        elif kn.pressed and kn_start is not None and not kn_long and now - kn_start >= KNOB_HOLD_S:
            kn_long = True                       # hold 2 s: menu in / out
            beep_ok()
            if screen == "home":
                go_menu()
            else:
                go_home()
        elif (not kn.pressed) and kn_was:        # release
            if not kn_long:                      # a tap
                if screen == "home":
                    knob_mode = "col" if knob_mode == "bri" else "bri"
                    knob_used_at = now
                elif screen == "menu":
                    menu_used_at = now
                    open_item(MENU[menu_idx])
                else:
                    menu_used_at = now
                    edit_confirm()
            kn_start = None
        kn_was = kn.pressed

        if kn.delta:
            if screen == "home":
                knob_used_at = now
                knob_turn_home(kn.delta)
            elif screen == "menu":
                menu_used_at = now
                menu_idx = (menu_idx + kn.delta) % len(MENU)
            else:
                menu_used_at = now
                edit_turn(kn.delta)

    # ── Timeouts ────────────────────────────────────────────────────────────
    if screen == "home" and knob_mode == "col" and now - knob_used_at >= KNOB_MODE_IDLE_S:
        knob_mode = "bri"
    if screen != "home" and now - menu_used_at >= MENU_IDLE_S:
        go_home()
    if flash_text is not None and now >= flash_until:
        flash_text = None

    # ── Timer, Sundown, alarm, ringing ──────────────────────────────────────
    if timer_end is not None and now >= timer_end:
        timer_end = None
        start_ring("timer")
    if lamp_dirty and now - _last_push >= LAMP_COALESCE_S:
        lamp_dirty = False
        apply_output()
    elif lamp_on() and ringing != "timer" and now - _last_push >= LAMP_REFRESH_S:
        push_lamp(current_level())               # heal any corrupted frame
    if _settle_at is not None and now >= _settle_at:
        _settle_at = None
        resend_lamp()                            # DEV-151: settle after quick toggles
    if _status_shown is not None and now >= status_until:
        paint_status()
    service_sundown(now)
    service_alarm(now)
    service_ring(now)

    # ── Screen ──────────────────────────────────────────────────────────────
    if display:
        if screen == "home":
            paint_home(now)
        else:
            paint_menu()

    c.sleep(LOOP_SLEEP_S)   # also services the app channel
