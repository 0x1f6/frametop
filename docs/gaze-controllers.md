# Gaze with the controllers: a dead end

Gaze mode is a mouse and keyboard feature. On 2026-09-30 we tried to make the Frame controllers its buttons: with gaze mode on and no game running, either controller's trigger would click where you look (a tap clicks, moving the hand steers the pointer, holding still drags), the controllers' lasers would be muted, and SteamVR's dashboard would follow the gaze too. It can't be done cleanly, for the reasons below. The work wasn't merged, and it's kept outside the published history.

## What worked

- Our `ft_pointer` device can hold SteamVR's laser without a hand role, in the treadmill role. It has to hint that role when SteamVR activates it; a hint changed later never gets the `/user/treadmill` path.
- A trimmed copy of the Frame controller's compositor binding mutes the controllers' laser buttons. It's chosen with `POST /input/selectconfig.action` on vrserver's port 27062 (a JSON body; a form-encoded one gets "Parse failed"). The helper's global action sets can't do it, because SteamVR marks them inactive while its laser mouse has focus.
- vrserver's web socket on 127.0.0.1:27062 reports every controller component without taking it from anyone.
- Steam's UI can be kept from acting on the controllers by wrapping its gamepad input source (webpack module 17900) through Steam's CEF debugger.
- Our device takes the laser back 10 to 15 ms after a controller takes it.

## What broke it

- Steam reads the Frame controllers itself. They aren't devices on the host: vrserver owns their radio and passes their raw reports to Steam through SteamVR's private Steam interface. Steam's client library turns them into a virtual device ("SteamFrameVirtual", at `/steamvr/virtual`), outside every SteamVR binding. Steam's own SteamVR action manifest asks only for haptics.
- Every press and every release that Steam sees takes SteamVR's dashboard, and Frametop's panels, out of laser mode about 40 ms later. That happens whatever the bindings say and whatever Steam's UI does with the event. None of these stopped it: dropping the events in Steam's UI, removing the controller's `dualanalog` bindings, binding every button to a harmless compositor action, or setting `dashboard.modalGamepadAndLaser` to false.
- Taking the laser back after each switch leaves a gap of 20 to 40 ms, and panels treat it as the pointer leaving, so clicks and drags break.
- Steam can't be told to ignore the controllers. It won't save a controller layout for the virtual controller ("Saved Binding Selection Failed - No Identity"), and its menus don't go through the layout anyway: a live preview of the empty layout for Steam's UI (app 769) changed nothing.
- SteamVR hands the controllers to a VR app instead of Steam only while that app has the input focus, as games do. A dashboard overlay with overlay flag `1 << 4` is given the focus only in gamepad mode, and only for gamepad input.

## Options not taken

- Frametop as a transparent VR app (a scene application using OpenXR's alpha blend mode) whenever gaze mode is on. That would cut Steam off while the dashboard is closed, but Steam's dashboard pages would still take the controllers, and it costs a scene layer all the time.
- Patching Steam's running process, with an eBPF probe that writes its memory or an injected hook, to drop the controller reports while gaze mode is on. It would cover everything, but it changes Valve's software, needs Steam's client library reverse-engineered, and breaks with Steam updates.

## What was built

The attempt had a plan and a test log, probes for the laser, input focus, and SteamVR settings, a reader for vrserver's web socket, a filter for Steam's UI, and controller code in the relay and the helper. None of it is in this repo. Only the gaze dot setting (`POINTER_GAZE_DOT`) came over.
