"""Steam's sleep settings, read and written through Steam's own UI.

Steam, not systemd, puts the Frame to sleep: after "When Plugged In and Idle -> Sleep after"
(an hour by default) without input, even while it charges. That's a Steam client setting,
`system_idle_suspend_ac_sec` (0 = never), with no file or command line to change it. Steam
on the Frame runs with -cef-enable-debugging, so its UI's JavaScript context
(SharedJSContext) is reachable over the Chrome DevTools Protocol on 127.0.0.1:8080. There
`settingsStore.clientSettings` has the current values, and `SteamClient.Settings.SetSetting`
takes a change as a serialized CMsgClientSettings protobuf, which is what Steam's own
Settings -> Power page sends. Standard library only (a minimal WebSocket client).
"""
import base64
import json
import os
import socket
import struct
import urllib.request

CDP_PORT = 8080
# CMsgClientSettings field numbers (Steam's UI bundle maps the names to these).
FIELDS = {"system_idle_suspend_ac_sec": 24004, "system_idle_suspend_battery_sec": 24003}


class SteamUnreachable(Exception):
    pass


class _WebSocket:
    def __init__(self, url, timeout=5):
        host_port, path = url[len("ws://"):].split("/", 1)
        host, port = host_port.rsplit(":", 1)
        self.sock = socket.create_connection((host, int(port)), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET /{path} HTTP/1.1\r\nHost: {host_port}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise SteamUnreachable("Steam closed the connection")
            head += chunk
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise SteamUnreachable(head.split(b"\r\n", 1)[0].decode(errors="replace"))
        self.buf = head.split(b"\r\n\r\n", 1)[1]

    def send(self, text):
        data, mask = text.encode(), os.urandom(4)
        n = len(data)
        if n < 126:
            head = struct.pack(">BB", 0x81, 0x80 | n)
        elif n < 65536:
            head = struct.pack(">BBH", 0x81, 0x80 | 126, n)
        else:
            head = struct.pack(">BBQ", 0x81, 0x80 | 127, n)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _take(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise SteamUnreachable("Steam closed the connection")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def recv(self):
        message = b""
        while True:
            b0, b1 = self._take(2)
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._take(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._take(8))[0]
            message += self._take(n)
            if b0 & 0x80:
                return message.decode(errors="replace")

    def close(self):
        self.sock.close()


def _evaluate(expression):
    """Runs JavaScript in Steam's SharedJSContext and returns its (awaited) value."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json", timeout=3) as r:
            targets = json.load(r)
    except OSError as e:
        raise SteamUnreachable(f"Steam isn't reachable on port {CDP_PORT} ({e})") from e
    url = next((t["webSocketDebuggerUrl"] for t in targets if t.get("title") == "SharedJSContext"), None)
    if not url:
        raise SteamUnreachable("Steam's UI isn't running")
    try:
        ws = _WebSocket(url)
        try:
            ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                                "params": {"expression": expression, "awaitPromise": True, "returnByValue": True}}))
            while True:
                reply = json.loads(ws.recv())
                if reply.get("id") == 1:
                    break
        finally:
            ws.close()
    except OSError as e:
        raise SteamUnreachable(str(e)) from e
    result = reply.get("result", {})
    if "exceptionDetails" in result:
        details = result["exceptionDetails"]
        raise SteamUnreachable(details.get("exception", {}).get("description") or details.get("text", "error"))
    return result.get("result", {}).get("value")


def sleep_settings():
    """{"ac": seconds, "battery": seconds}: when Steam puts the Frame to sleep without input,
    plugged in and on battery (0 = never)."""
    value = _evaluate("(() => { const c = settingsStore.clientSettings; "
                      "return {ac: c.system_idle_suspend_ac_sec, battery: c.system_idle_suspend_battery_sec}; })()")
    if not isinstance(value, dict) or not all(isinstance(value.get(k), int) for k in ("ac", "battery")):
        raise SteamUnreachable("Steam's settings don't have the sleep timeouts")
    return value


def set_sleep_setting(name, seconds):
    """Sets one of FIELDS to a whole number of seconds and checks that Steam took it."""
    field, seconds = FIELDS[name], int(seconds)
    if seconds < 0:
        raise ValueError("seconds must be 0 (never) or more")
    ok = _evaluate(f"""(async () => {{
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
