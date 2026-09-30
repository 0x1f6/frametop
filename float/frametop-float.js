// frametop-float: the KWin side of floating windows (see docs/floating-windows.md). ft-floatd
// loads it into the desktop's KWin over D-Bus (org.kde.kwin.Scripting) and talks to it:
//   - events go to ft-floatd as JSON strings (org.frametop.Float.Event), for the windows it
//     cares about: floating windows (the ones on a spare output, WL-<screens> and up), their
//     popups and dialogs, new windows, and "Float in VR" requests;
//   - commands come back through a long poll: the script calls NextCommand, ft-floatd
//     answers when it has one (or after a while with nothing), and the script calls again.
// KWin scripts can call D-Bus but can't serve it, hence the poll. Window ids are KWin's
// internalId (a UUID string).

const SERVICE = "org.frametop.Float", PATH = "/Float", IFACE = "org.frametop.Float";
let screens = 0;       // outputs WL-0 .. WL-<screens - 1> are screens; the rest are spares
let polling = false;
const watched = {};    // id -> true once its signals are connected

function send(ev) {
    callDBus(SERVICE, PATH, IFACE, "Event", JSON.stringify(ev));
}

function outputIndex(o) {
    const m = o ? /^WL-(\d+)$/.exec(o.name) : null;
    return m ? parseInt(m[1]) : -1;
}
function isSpare(o) {
    return screens > 0 && outputIndex(o) >= screens;
}
function rect(g) {
    return {x: g.x, y: g.y, w: g.width, h: g.height};
}
function byId(id) {
    const all = workspace.windowList();
    for (let i = 0; i < all.length; ++i)
        if (String(all[i].internalId) === id) return all[i];
    return null;
}
function outputByName(name) {
    const all = workspace.screens;
    for (let i = 0; i < all.length; ++i)
        if (all[i].name === name) return all[i];
    return null;
}
function info(w) {
    const o = w.output;
    return {
        id: String(w.internalId), pid: w.pid, cls: String(w.resourceClass), app: String(w.desktopFileName),
        caption: String(w.caption), output: o ? o.name : "", outputRect: o ? rect(o.geometry) : null,
        frame: rect(w.frameGeometry), client: rect(w.clientGeometry), popup: w.popupWindow,
        transient: w.transient, parent: w.transientFor ? String(w.transientFor.internalId) : "",
        normal: w.normalWindow, dialog: w.dialog, fullScreen: w.fullScreen, minimized: w.minimized,
        onAllDesktops: w.onAllDesktops
    };
}

function report(type, w) {
    const ev = info(w);
    ev.ev = type;
    send(ev);
}

// Floating windows, and popups and dialogs on a spare output: tell ft-floatd about changes.
function watch(w) {
    const id = String(w.internalId);
    if (watched[id]) return;
    watched[id] = true;
    const onSpare = () => isSpare(w.output);
    w.frameGeometryChanged.connect(() => { if (onSpare()) report("geometry", w); });
    w.outputChanged.connect(() => report("output", w));
    w.interactiveMoveResizeStarted.connect(() => {
        if (onSpare()) send({ev: "move-start", id: id, move: w.move, resize: w.resize, frame: rect(w.frameGeometry)});
    });
    w.interactiveMoveResizeFinished.connect(() => { if (onSpare()) report("move-end", w); });
    w.fullScreenChanged.connect(() => { if (onSpare()) report("fullscreen", w); });
    w.minimizedChanged.connect(() => { if (onSpare()) report("minimized", w); });
    w.maximizedChanged.connect(() => {
        // A floating window stays an ordinary window: its output is its size plus a margin.
        if (onSpare() && w.normalWindow && !w.fullScreen) w.setMaximize(false, false);
    });
}

workspace.windowAdded.connect(w => {
    watch(w);
    report("added", w);
});
workspace.windowRemoved.connect(w => {
    send({ev: "removed", id: String(w.internalId)});
    delete watched[String(w.internalId)];
});
workspace.windowActivated.connect(w => {
    if (w && isSpare(w.output)) send({ev: "activated", id: String(w.internalId)});
});
workspace.windowList().forEach(watch);

function requestFloat(w) {
    if (!w || !w.normalWindow || w.popupWindow) return;
    report(isSpare(w.output) ? "dock-request" : "float-request", w);
}

registerUserActionsMenu(w => {
    if (!w.normalWindow || w.popupWindow) return null;
    const floating = isSpare(w.output);
    return {
        text: floating ? "Back to Desktop" : "Float in VR",
        icon: floating ? "window-restore" : "window-new",
        triggered: () => requestFloat(w)
    };
});
registerShortcut("Frametop Float Window", "Frametop: Float Window in VR (or put it back)", "Meta+Shift+F",
                 () => requestFloat(workspace.activeWindow));

function run(c) {
    const w = c.id ? byId(c.id) : null;
    switch (c.cmd) {
        case "config":
            screens = c.screens;
            workspace.windowList().forEach(w => report("window", w));
            break;
        case "place": {  // onto an output, at a frame rectangle (logical, global)
            if (!w) break;
            const o = outputByName(c.output);
            if (!o) break;
            if (w.fullScreen && !c.keepFullScreen) w.fullScreen = false;
            w.setMaximize(false, false);
            workspace.sendClientToScreen(w, o);
            w.frameGeometry = {x: c.x, y: c.y, width: c.w, height: c.h};
            if (c.onAllDesktops !== undefined) w.onAllDesktops = c.onAllDesktops;
            break;
        }
        case "geometry":
            if (w) w.frameGeometry = {x: c.x, y: c.y, width: c.w, height: c.h};
            break;
        case "close":
            if (w) w.closeWindow();
            break;
        case "activate":
            if (w) workspace.activeWindow = w;
            break;
        case "minimize":
            if (w) w.minimized = c.on;
            break;
        case "info":
            if (w) report("window", w);
            break;
        case "request-active":  // ft-float float|dock active: like the shortcut
            requestFloat(workspace.activeWindow);
            break;
    }
}

function poll() {
    if (polling) return;
    polling = true;
    callDBus(SERVICE, PATH, IFACE, "NextCommand", reply => {
        polling = false;
        if (reply) {
            try {
                JSON.parse(reply).forEach(run);
            } catch (e) {
                print("frametop-float: bad command " + reply + ": " + e);
            }
        }
        poll();
    });
}

send({ev: "hello"});
poll();
