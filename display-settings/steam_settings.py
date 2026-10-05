"""Steam's sleep settings, read and written through Steam's own UI.

Steam, not systemd, puts the Frame to sleep: after "When Plugged In and Idle -> Sleep after"
(an hour by default) without input, even while it charges. That's a Steam client setting,
`system_idle_suspend_ac_sec` (0 = never), with no file or command line to change it. Steam
on the Frame runs with -cef-enable-debugging, so its UI's JavaScript context
(SharedJSContext) is reachable over the Chrome DevTools Protocol on 127.0.0.1:8080
(steam/steamui.py). There `settingsStore.clientSettings` has the current values, and
`SteamClient.Settings.SetSetting` takes a change as a serialized CMsgClientSettings protobuf,
which is what Steam's own Settings -> Power page sends.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "steam"))
from steamui import SteamUnreachable, evaluate  # noqa: E402,F401  (callers catch SteamUnreachable here)

# CMsgClientSettings field numbers (Steam's UI bundle maps the names to these).
FIELDS = {"system_idle_suspend_ac_sec": 24004, "system_idle_suspend_battery_sec": 24003}


def sleep_settings():
    """{"ac": seconds, "battery": seconds}: when Steam puts the Frame to sleep without input,
    plugged in and on battery (0 = never)."""
    value = evaluate("(() => { const c = settingsStore.clientSettings; "
                     "return {ac: c.system_idle_suspend_ac_sec, battery: c.system_idle_suspend_battery_sec}; })()")
    if not isinstance(value, dict) or not all(isinstance(value.get(k), int) for k in ("ac", "battery")):
        raise SteamUnreachable("Steam's settings don't have the sleep timeouts")
    return value


def set_sleep_setting(name, seconds):
    """Sets one of FIELDS to a whole number of seconds and checks that Steam took it."""
    field, seconds = FIELDS[name], int(seconds)
    if seconds < 0:
        raise ValueError("seconds must be 0 (never) or more")
    ok = evaluate(f"""(async () => {{
        const bytes = [];
        const varint = n => {{ while (n > 127) {{ bytes.push((n & 127) | 128); n = Math.floor(n / 128); }} bytes.push(n); }};
        varint({field} * 8); varint({seconds});
        await SteamClient.Settings.SetSetting(btoa(String.fromCharCode(...bytes)));
        for (let i = 0; i < 40; i++) {{
            if (settingsStore.clientSettings.{name} === {seconds}) return true;
            await new Promise(r => setTimeout(r, 50));
        }}
        return false;
    }})()""")
    if ok is not True:
        raise SteamUnreachable(f"Steam didn't take {name} = {seconds}")
