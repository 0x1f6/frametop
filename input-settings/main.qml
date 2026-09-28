// Frametop Input Settings (Kirigami). Backend: ft_input_settings.py ("backend").
import QtQuick
import QtQuick.Controls as Controls
import QtQuick.Layouts
import org.kde.kirigami as Kirigami

Kirigami.ApplicationWindow {
    id: root
    title: "Frametop Input Settings"
    width: Kirigami.Units.gridUnit * 44
    height: Kirigami.Units.gridUnit * 34

    globalDrawer: Kirigami.GlobalDrawer {
        isMenu: false
        modal: false
        collapsible: true
        collapsed: root.width < Kirigami.Units.gridUnit * 30
        actions: [
            Kirigami.Action { text: "Devices"; icon.name: "input-mouse"; onTriggered: root.show(devicesPage) },
            Kirigami.Action { text: "Buttons"; icon.name: "input-keyboard"; onTriggered: root.show(buttonsPage) },
            Kirigami.Action { text: "Controllers"; icon.name: "input-gamepad"; onTriggered: root.show(controllersPage) },
            Kirigami.Action { text: "Pointer"; icon.name: "transform-move"; onTriggered: root.show(pointerPage) },
            Kirigami.Action { text: "Gaze"; icon.name: "view-visible"; onTriggered: root.show(gazePage) },
            Kirigami.Action { text: "Bluetooth"; icon.name: "preferences-system-bluetooth"; onTriggered: root.show(bluetoothPage) }
        ]
    }

    function show(page) {
        pageStack.clear()
        pageStack.push(page)
    }

    // FT_INPUT_PAGE=buttons|controllers|pointer|gaze|bluetooth opens the app on that page.
    pageStack.initialPage: ({ buttons: buttonsPage, controllers: controllersPage, pointer: pointerPage, gaze: gazePage,
                              bluetooth: bluetoothPage })[startPage] || devicesPage

    Connections {
        target: backend
        function onMessage(text, isError) {
            root.showPassiveNotification(text, isError ? "long" : "short")
        }
    }

    // ---------------------------------------------------------------- Devices
    Component {
        id: devicesPage
        Kirigami.ScrollablePage {
            title: "Devices"

            header: Kirigami.InlineMessage {
                visible: !backend.relayRunning || !backend.pointerMode
                position: Kirigami.InlineMessage.Position.Header
                type: backend.relayRunning ? Kirigami.MessageType.Information : Kirigami.MessageType.Error
                text: !backend.relayRunning
                      ? "The input relay isn't running (frametop-input-relay.service)."
                      : "Pointer mode is off (POINTER=0): pointer devices act as a plain mouse."
            }

            ListView {
                model: backend.devices
                spacing: Kirigami.Units.smallSpacing

                Kirigami.PlaceholderMessage {
                    anchors.centerIn: parent
                    visible: parent.count === 0
                    text: "No USB or Bluetooth mice or keyboards connected"
                    explanation: "Connect or wake one; it appears here within a second."
                }

                delegate: Controls.ItemDelegate {
                    id: row
                    required property var modelData
                    width: ListView.view.width
                    hoverEnabled: false
                    down: false

                    contentItem: RowLayout {
                        spacing: Kirigami.Units.largeSpacing

                        // Activity light: flashes when the device sends input.
                        Rectangle {
                            id: light
                            implicitWidth: Kirigami.Units.gridUnit * 0.8
                            implicitHeight: implicitWidth
                            radius: width / 2
                            color: Kirigami.Theme.disabledTextColor
                            opacity: 0.35
                            Connections {
                                target: backend
                                function onActivity(id) {
                                    if (id === row.modelData.id) {
                                        light.color = Kirigami.Theme.positiveTextColor
                                        light.opacity = 1
                                        fade.restart()
                                    }
                                }
                            }
                            Timer {
                                id: fade
                                interval: 350
                                onTriggered: { light.color = Kirigami.Theme.disabledTextColor; light.opacity = 0.35 }
                            }
                        }

                        Kirigami.Icon {
                            source: row.modelData.kinds.indexOf("mouse") >= 0 ? "input-mouse" : "input-keyboard"
                            implicitWidth: Kirigami.Units.iconSizes.medium
                            implicitHeight: implicitWidth
                        }

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 0
                            Controls.Label {
                                text: row.modelData.name
                                font.bold: true
                                Layout.fillWidth: true
                                elide: Text.ElideRight
                            }
                            Controls.Label {
                                text: row.modelData.connected
                                      ? row.modelData.bus + " · " + row.modelData.kinds.join(" + ") + " · " + row.modelData.id
                                        + (row.modelData.grabbed ? " · grabbed" : "")
                                      : "not connected · saved settings · " + row.modelData.id
                                opacity: 0.7
                                font: Kirigami.Theme.smallFont
                                Layout.fillWidth: true
                                elide: Text.ElideRight
                            }
                        }

                        Controls.ComboBox {
                            model: backend.roles
                            textRole: "text"
                            valueRole: "value"
                            Component.onCompleted: currentIndex = indexOfValue(row.modelData.role)
                            onActivated: backend.setRole(row.modelData.id, currentValue, row.modelData.name)
                        }
                        Controls.Button {
                            visible: !row.modelData.connected
                            text: "Forget"
                            icon.name: "edit-delete-remove"
                            onClicked: backend.forgetDevice(row.modelData.id)
                        }
                        Controls.ToolButton {
                            icon.name: "edit-undo"
                            visible: row.modelData.explicit
                            display: Controls.AbstractButton.IconOnly
                            text: "Use the default role"
                            Controls.ToolTip.text: text
                            Controls.ToolTip.visible: hovered
                            onClicked: backend.resetRole(row.modelData.id)
                        }
                    }
                }
            }

            footer: Controls.Label {
                padding: Kirigami.Units.largeSpacing
                wrapMode: Text.Wrap
                opacity: 0.7
                text: "Move or press a device to see which row it is. 3D pointer: grabbed, drives the SteamVR pointer. "
                      + "Pass through: left alone (a Meta tap toggles the dashboard if META_DASHBOARD=1). Ignore: left alone."
            }
        }
    }

    // ---------------------------------------------------------------- Buttons
    Component {
        id: buttonsPage
        Kirigami.ScrollablePage {
            id: bpage
            title: "Buttons"
            actions: [
                Kirigami.Action {
                    text: "Remove all"
                    icon.name: "edit-clear-all"
                    tooltip: "Remove every binding you added for this device; built-in buttons go back to their defaults"
                    enabled: bpage.rows.some(r => r.custom)
                    onTriggered: backend.clearMappings(bpage.deviceId)
                }
            ]
            property string deviceId: pointerDevices.length > 0 ? pointerDevices[Math.max(0, deviceBox.currentIndex)].id : ""
            property var pointerDevices: backend.devices.filter(d => d.role === "pointer")
            property int capturedCode: -1
            property string capturedName: ""
            property bool capturing: false
            property var rows: deviceId ? backend.mappings(deviceId) : []

            Connections {
                target: backend
                function onCaptured(code, name) { bpage.capturedCode = code; bpage.capturedName = name; bpage.capturing = false }
                function onMappingsChanged() { bpage.rows = bpage.deviceId ? backend.mappings(bpage.deviceId) : [] }
            }

            ColumnLayout {
                spacing: Kirigami.Units.largeSpacing

                Kirigami.PlaceholderMessage {
                    Layout.fillWidth: true
                    visible: bpage.pointerDevices.length === 0
                    text: "No 3D pointer devices"
                    explanation: "Set a device's role to 3D pointer on the Devices page."
                }

                Kirigami.FormLayout {
                    Layout.fillWidth: true
                    visible: bpage.pointerDevices.length > 0

                    Controls.ComboBox {
                        id: deviceBox
                        Kirigami.FormData.label: "Device:"
                        model: bpage.pointerDevices.map(d => ({ name: d.name + (d.connected ? "" : "  (not connected)"), id: d.id }))
                        textRole: "name"
                        onActivated: { bpage.capturedCode = -1; bpage.rows = backend.mappings(bpage.deviceId) }
                    }

                    RowLayout {
                        Kirigami.FormData.label: "New mapping:"
                        enabled: bpage.pointerDevices.length > 0 && bpage.pointerDevices[Math.max(0, deviceBox.currentIndex)].connected
                        Controls.Button {
                            text: bpage.capturing ? "Press a button or key on the device…" : "Capture a button"
                            icon.name: "input-mouse-click-left"
                            highlighted: bpage.capturing
                            onClicked: {
                                if (bpage.capturing) { backend.cancelCapture(); bpage.capturing = false }
                                else { bpage.capturedCode = -1; bpage.capturing = true; backend.startCapture(bpage.deviceId) }
                            }
                        }
                        Controls.Label {
                            visible: bpage.capturedCode >= 0
                            text: bpage.capturedName
                            font.bold: true
                        }
                        Controls.ComboBox {
                            id: newAction
                            visible: bpage.capturedCode >= 0
                            model: backend.actions
                            textRole: "text"
                            valueRole: "value"
                        }
                        Controls.Button {
                            visible: bpage.capturedCode >= 0
                            text: "Map"
                            icon.name: "dialog-ok-apply"
                            onClicked: { backend.setMapping(bpage.deviceId, bpage.capturedCode, newAction.currentValue); bpage.capturedCode = -1 }
                        }
                    }
                }

                Kirigami.Heading {
                    visible: bpage.rows.length > 0
                    level: 3
                    text: "Current mappings"
                }

                Repeater {
                    model: bpage.rows
                    delegate: RowLayout {
                        required property var modelData
                        Layout.fillWidth: true
                        spacing: Kirigami.Units.largeSpacing
                        Controls.Label {
                            text: modelData.name
                            font.family: "monospace"
                            Layout.preferredWidth: Kirigami.Units.gridUnit * 10
                        }
                        Controls.ComboBox {
                            model: backend.actions
                            textRole: "text"
                            valueRole: "value"
                            Layout.preferredWidth: Kirigami.Units.gridUnit * 13
                            Component.onCompleted: currentIndex = indexOfValue(modelData.action)
                            onActivated: backend.setMapping(bpage.deviceId, modelData.code, currentValue)
                        }
                        Controls.Label {
                            text: modelData.custom ? (modelData.isDefault ? "changed" : "custom") : "default"
                            opacity: 0.6
                            Layout.preferredWidth: Kirigami.Units.gridUnit * 4
                        }
                        // Remove: your own binding goes away (the button passes through again).
                        // Reset: a changed built-in binding goes back to its default.
                        // Unbind: an untouched built-in binding is set to "Do nothing".
                        Controls.Button {
                            text: modelData.custom ? (modelData.isDefault ? "Reset" : "Remove") : "Unbind"
                            icon.name: modelData.custom ? (modelData.isDefault ? "edit-undo" : "edit-delete-remove")
                                                        : "list-remove"
                            onClicked: modelData.custom ? backend.removeMapping(bpage.deviceId, modelData.code)
                                                        : backend.unbind(bpage.deviceId, modelData.code)
                        }
                        Item { Layout.fillWidth: true }
                    }
                }

                Controls.Label {
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    opacity: 0.7
                    text: "Buttons without a mapping pass through (mouse buttons as clicks, keys as keys). "
                          + "The Z3's extra buttons show up as keys from its keyboard node. "
                          + "The Frame controllers' buttons are on the Controllers page."
                }
            }
        }
    }

    // ---------------------------------------------------------------- Controllers
    Component {
        id: controllersPage
        Kirigami.ScrollablePage {
            id: cpage
            title: "Controllers"
            actions: [
                Kirigami.Action {
                    text: "Remove all"
                    icon.name: "edit-clear-all"
                    tooltip: "Give every controller button back to games"
                    enabled: backend.controllerMappings.length > 0
                    onTriggered: backend.clearControllerMappings()
                }
            ]
            property string capturedButton: ""
            property string capturedLabel: ""
            property bool capturing: false
            property var status: backend.controllerStatus
            Component.onDestruction: backend.cancelControllerCapture()

            Connections {
                target: backend
                function onCapturedController(button, label) {
                    cpage.capturedButton = button; cpage.capturedLabel = label; cpage.capturing = false
                    buttonBox.currentIndex = buttonBox.indexOfValue(button)
                }
            }
            Timer {
                // The relay takes every button for 30 s while capturing.
                running: cpage.capturing
                interval: 30000
                onTriggered: { backend.cancelControllerCapture(); cpage.capturing = false }
            }

            ColumnLayout {
                spacing: Kirigami.Units.largeSpacing

                Kirigami.InlineMessage {
                    Layout.fillWidth: true
                    visible: !cpage.status.helper
                    type: Kirigami.MessageType.Error
                    text: "The pointer helper isn't answering (frametop-pointer.service, needs SteamVR). "
                          + "It reads the controller buttons."
                }
                Kirigami.InlineMessage {
                    Layout.fillWidth: true
                    visible: cpage.status.helper && !cpage.status.manifest
                    type: Kirigami.MessageType.Error
                    text: "The pointer helper couldn't set up SteamVR input (pointer/helper/actions). See its log."
                }
                Kirigami.InlineMessage {
                    Layout.fillWidth: true
                    visible: cpage.status.helper && !cpage.status.global && backend.controllerMappings.length > 0
                    type: Kirigami.MessageType.Warning
                    text: "Global input is off, so the mapped buttons only reach Frametop when nothing else has "
                          + "focus, if at all."
                }

                Kirigami.FormLayout {
                    Layout.fillWidth: true

                    Controls.Switch {
                        Kirigami.FormData.label: "Global input:"
                        text: "SteamVR's \"Enable global input from overlays (Experimental)\", which mapped buttons need"
                        checked: cpage.status.global
                        enabled: cpage.status.helper
                        onToggled: backend.setGlobalInput(checked)
                    }
                    Controls.Switch {
                        Kirigami.FormData.label: "In games:"
                        text: "Mapped buttons work while a game is running too (the game loses them)"
                        checked: backend.controllerInGames
                        onToggled: backend.setControllerInGames(checked)
                    }
                    RowLayout {
                        Kirigami.FormData.label: "New mapping:"
                        Controls.Button {
                            text: cpage.capturing ? "Press a button on a controller…" : "Capture a button"
                            icon.name: "input-gamepad"
                            highlighted: cpage.capturing
                            enabled: cpage.status.helper
                            onClicked: {
                                if (cpage.capturing) { backend.cancelControllerCapture(); cpage.capturing = false }
                                else { cpage.capturedButton = ""; cpage.capturing = true; backend.startControllerCapture() }
                            }
                        }
                        Controls.Label { text: "or" ; opacity: 0.7 }
                        Controls.ComboBox {
                            id: buttonBox
                            model: backend.controllerButtons
                            textRole: "text"
                            valueRole: "value"
                            Layout.preferredWidth: Kirigami.Units.gridUnit * 10
                        }
                        Controls.ComboBox {
                            id: newControllerAction
                            model: backend.controllerActions
                            textRole: "text"
                            valueRole: "value"
                            Layout.preferredWidth: Kirigami.Units.gridUnit * 13
                        }
                        Controls.Button {
                            text: "Map"
                            icon.name: "dialog-ok-apply"
                            onClicked: { backend.setControllerMapping(buttonBox.currentValue, newControllerAction.currentValue); cpage.capturedButton = "" }
                        }
                    }
                }

                Kirigami.Heading {
                    visible: backend.controllerMappings.length > 0
                    level: 3
                    text: "Current mappings"
                }

                Repeater {
                    model: backend.controllerMappings
                    delegate: RowLayout {
                        id: crow
                        required property var modelData
                        Layout.fillWidth: true
                        spacing: Kirigami.Units.largeSpacing
                        Controls.Label {
                            text: crow.modelData.label
                            Layout.preferredWidth: Kirigami.Units.gridUnit * 10
                        }
                        Controls.ComboBox {
                            model: backend.controllerActions
                            textRole: "text"
                            valueRole: "value"
                            Layout.preferredWidth: Kirigami.Units.gridUnit * 13
                            Component.onCompleted: currentIndex = indexOfValue(crow.modelData.action)
                            onActivated: backend.setControllerMapping(crow.modelData.button, currentValue)
                        }
                        Controls.Label {
                            // Active: SteamVR delivers it to Frametop now (a controller is on).
                            text: cpage.status.active.indexOf(crow.modelData.button) >= 0 ? "active"
                                  : cpage.status.inGame && !backend.controllerInGames ? "game's" : "waiting"
                            opacity: 0.6
                            Layout.preferredWidth: Kirigami.Units.gridUnit * 4
                            Controls.ToolTip.text: text === "active" ? "SteamVR gives this button to Frametop"
                                                   : text === "game's" ? "A game is running: the button is the game's until it quits"
                                                   : "No controller with this button is on, or SteamVR doesn't give it to Frametop"
                            Controls.ToolTip.visible: hover.hovered
                            HoverHandler { id: hover }
                        }
                        Controls.Button {
                            text: "Remove"
                            icon.name: "edit-delete-remove"
                            onClicked: backend.removeControllerMapping(crow.modelData.button)
                        }
                        Item { Layout.fillWidth: true }
                    }
                }

                Controls.Label {
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    opacity: 0.7
                    text: "Outside games, a mapped button is Frametop's; while a game runs it's the game's, unless "
                          + "\"In games\" is on. Unmapped buttons are left alone, and the system button stays SteamVR's. "
                          + "Clicks and scrolling go to the 3D pointer. The trigger and grip also move SteamVR's laser "
                          + "to that controller, so they're better left unmapped."
                }
            }
        }
    }

    // ---------------------------------------------------------------- Pointer
    Component {
        id: pointerPage
        Kirigami.ScrollablePage {
            title: "Pointer"
            actions: [
                Kirigami.Action {
                    text: "Recenter"
                    icon.name: "zoom-fit-best"
                    onTriggered: backend.recenter()
                }
            ]

            Kirigami.FormLayout {
                Controls.Switch {
                    Kirigami.FormData.label: "Head follow:"
                    text: "Pointer follows your head (experimental; leash below, 0° locks it to your view)"
                    checked: backend.pointerFollow
                    onToggled: backend.setPointerFollow(checked)
                }
                Kirigami.InlineMessage {
                    Layout.fillWidth: true
                    Layout.maximumWidth: Kirigami.Units.gridUnit * 30
                    visible: true
                    type: Kirigami.MessageType.Warning
                    text: "Head follow is experimental. It's only lightly tested and not finished: the head "
                          + "follow settings below are a starting point, and polishing how it feels is left open "
                          + "for anyone who wants to take it further."
                }
                Repeater {
                    model: backend.pointerSettings
                    delegate: RowLayout {
                        required property var modelData
                        Kirigami.FormData.label: modelData.label + ":"
                        Controls.Slider {
                            id: slider
                            from: modelData.min
                            to: modelData.max
                            stepSize: modelData.step
                            value: modelData.value
                            Layout.preferredWidth: Kirigami.Units.gridUnit * 14
                            onMoved: backend.setPointerSetting(modelData.key, value)
                        }
                        Controls.Label {
                            text: (modelData.step < 1 ? slider.value.toFixed(modelData.step < 0.01 ? 3 : 2) : Math.round(slider.value))
                                  + (modelData.unit ? " " + modelData.unit : "")
                            Layout.preferredWidth: Kirigami.Units.gridUnit * 5
                        }
                        Controls.ToolButton {
                            icon.name: "edit-undo"
                            display: Controls.AbstractButton.IconOnly
                            text: "Default (" + modelData.default + ")"
                            Controls.ToolTip.text: text
                            Controls.ToolTip.visible: hovered
                            onClicked: { slider.value = modelData.default; backend.setPointerSetting(modelData.key, modelData.default) }
                        }
                    }
                }
            }

            footer: Controls.Label {
                padding: Kirigami.Units.largeSpacing
                wrapMode: Text.Wrap
                opacity: 0.7
                text: "Changes apply live. SteamVR dot shrink: how close to the target the pointer's laser starts; "
                      + "higher makes SteamVR's own blue dot smaller (max 0.98)."
            }
        }
    }

    // ---------------------------------------------------------------- Gaze
    Component {
        id: gazePage
        Kirigami.ScrollablePage {
            id: gpage
            title: "Gaze"
            property var status: backend.gazeStatus
            actions: [
                Kirigami.Action {
                    text: "Calibrate…"
                    icon.name: "crosshairs"
                    tooltip: "Open the gaze probe to calibrate (fullscreen on a Frametop screen)"
                    onTriggered: backend.openGazeProbe()
                },
                Kirigami.Action {
                    text: "Reload calibration"
                    icon.name: "view-refresh"
                    enabled: backend.gazeServiceRunning
                    onTriggered: backend.reloadGazeCalibration()
                },
                Kirigami.Action {
                    text: "Forget nudges"
                    icon.name: "edit-clear-history"
                    enabled: backend.gazeServiceRunning
                    tooltip: "Drop what your mouse nudges taught; the calibration stays"
                    onTriggered: backend.forgetGazeLessons()
                }
            ]

            Kirigami.FormLayout {
                Kirigami.InlineMessage {
                    Layout.fillWidth: true
                    Layout.maximumWidth: Kirigami.Units.gridUnit * 30
                    visible: true
                    type: Kirigami.MessageType.Warning
                    text: "Gaze mode is experimental. The pointer goes where you look and the mouse does the last bit; "
                          + "moving the mouse takes over, looking well away hands it back. A small nudge and a click "
                          + "teach the gaze service where it was off."
                }
                Controls.Switch {
                    Kirigami.FormData.label: "Gaze pointer:"
                    text: backend.gazeMode < 0 ? "Pointer helper not running" : "Pointer goes where you look"
                    enabled: backend.gazeMode >= 0
                    checked: backend.gazeMode > 0
                    onToggled: backend.setGazeMode(checked)
                }
                Controls.Label {
                    visible: backend.gazeMode >= 0 && (backend.gazeMode > 0) !== backend.gazeDefault
                    text: "Toggled by a button; it starts " + (backend.gazeDefault ? "on" : "off") + " after a restart."
                    opacity: 0.7
                    font: Kirigami.Theme.smallFont
                }
                Repeater {
                    model: backend.gazeSettings
                    delegate: RowLayout {
                        required property var modelData
                        Kirigami.FormData.label: modelData.label + ":"
                        Controls.Slider {
                            id: gslider
                            from: modelData.min
                            to: modelData.max
                            stepSize: modelData.step
                            value: modelData.value
                            Layout.preferredWidth: Kirigami.Units.gridUnit * 14
                            onMoved: backend.setPointerSetting(modelData.key, value)
                        }
                        Controls.Label {
                            text: gslider.value.toFixed(modelData.step < 0.1 ? 2 : 1) + " " + modelData.unit
                            Layout.preferredWidth: Kirigami.Units.gridUnit * 5
                        }
                        Controls.ToolButton {
                            icon.name: "edit-undo"
                            display: Controls.AbstractButton.IconOnly
                            text: "Default (" + modelData.default + ")"
                            Controls.ToolTip.text: text
                            Controls.ToolTip.visible: hovered
                            onClicked: { gslider.value = modelData.default; backend.setPointerSetting(modelData.key, modelData.default) }
                        }
                    }
                }

                Kirigami.Separator { Kirigami.FormData.isSection: true; Kirigami.FormData.label: "Gaze service" }

                Controls.Label {
                    Kirigami.FormData.label: "Service:"
                    text: backend.gazeServiceRunning
                          ? (gpage.status.ft_gaze ? "running" : "running, eye tracker reader restarting")
                          : "not running (frametop-gaze.service, starts with SteamVR)"
                    color: backend.gazeServiceRunning ? Kirigami.Theme.textColor : Kirigami.Theme.negativeTextColor
                }
                Controls.Label {
                    visible: backend.gazeServiceRunning
                    Kirigami.FormData.label: "Headset:"
                    text: gpage.status.headset_on ? "on" : "off"
                }
                Controls.Label {
                    visible: backend.gazeServiceRunning
                    Kirigami.FormData.label: "Tracking:"
                    text: gpage.status.rate === undefined || gpage.status.rate === null ? "…"
                          : Math.round(gpage.status.rate) + " samples/s"
                            + (gpage.status.one_eye_share > 0.5 ? " · only one eye tracked" : "")
                    color: gpage.status.one_eye_share > 0.5 ? Kirigami.Theme.neutralTextColor : Kirigami.Theme.textColor
                    Controls.ToolTip.text: "Only one eye tracked: reseat the headset or check the lenses. It still works, "
                                           + "probably less precisely."
                    Controls.ToolTip.visible: gpage.status.one_eye_share > 0.5 && ghover.hovered
                    HoverHandler { id: ghover }
                }
                Controls.Label {
                    visible: backend.gazeServiceRunning
                    Kirigami.FormData.label: "Calibration:"
                    text: gpage.status.calibration_samples > 0
                          ? gpage.status.calibration_samples + " points (" + gpage.status.model + ")"
                          : "none yet: use Calibrate…"
                }
                Controls.Label {
                    visible: backend.gazeServiceRunning
                    Kirigami.FormData.label: "Learned from nudges:"
                    text: gpage.status.lessons + (gpage.status.lessons === 1 ? " nudge" : " nudges")
                          + (gpage.status.refused > 0 ? " (" + gpage.status.refused + " too far off, ignored)" : "")
                }
            }

            footer: Controls.Label {
                padding: Kirigami.Units.largeSpacing
                wrapMode: Text.Wrap
                opacity: 0.7
                text: "Map a mouse button (Buttons) or a controller button (Controllers) to \"Gaze pointer on/off\" "
                      + "to switch it on the fly. A click waits for the release: if the gaze is off, drag onto the target "
                      + "with the button held and let go there. Hold still to drag: hold a press this long without moving "
                      + "to drag something instead. Look away to hand back: how far from the pointer you look before the "
                      + "gaze takes it back from the mouse. Largest nudge to learn: bigger mouse moves before a click "
                      + "are treated as using the mouse, not correcting the gaze."
            }
        }
    }

    // ---------------------------------------------------------------- Bluetooth
    Component {
        id: bluetoothPage
        Kirigami.ScrollablePage {
            title: "Bluetooth"
            actions: [
                Kirigami.Action { text: "Refresh"; icon.name: "view-refresh"; onTriggered: backend.refreshBluetooth() },
                Kirigami.Action {
                    text: "Apply Bluetooth fixes"
                    icon.name: "tools-wizard"
                    tooltip: "Privacy off, and address resolution on bonded LE devices (asks for your password)"
                    onTriggered: backend.applyBluetoothFixes()
                }
            ]

            ListView {
                model: backend.bluetooth
                Kirigami.PlaceholderMessage {
                    anchors.centerIn: parent
                    visible: parent.count === 0
                    text: "No paired Bluetooth devices"
                    explanation: "Pair in Steam: Settings → Bluetooth."
                }
                delegate: Controls.ItemDelegate {
                    required property var modelData
                    width: ListView.view.width
                    contentItem: RowLayout {
                        spacing: Kirigami.Units.largeSpacing
                        Kirigami.Icon {
                            source: "preferences-system-bluetooth"
                            implicitWidth: Kirigami.Units.iconSizes.medium
                            implicitHeight: implicitWidth
                            opacity: modelData.connected ? 1 : 0.4
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 0
                            Controls.Label { text: modelData.name; font.bold: true }
                            Controls.Label {
                                text: modelData.address + " · " + (modelData.connected ? "connected" : "not connected")
                                      + (modelData.battery >= 0 ? " · battery " + modelData.battery + "%" : "")
                                opacity: 0.7
                                font: Kirigami.Theme.smallFont
                            }
                        }
                    }
                }
            }

            footer: Controls.Label {
                padding: Kirigami.Units.largeSpacing
                wrapMode: Text.Wrap
                opacity: 0.7
                text: "Pair new devices in Steam (Settings → Bluetooth). After pairing an LE mouse or keyboard, "
                      + "use Apply Bluetooth fixes once so it reconnects on its own."
            }
        }
    }
}
