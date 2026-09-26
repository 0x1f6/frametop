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
            Kirigami.Action { text: "Pointer"; icon.name: "transform-move"; onTriggered: root.show(pointerPage) },
            Kirigami.Action { text: "Bluetooth"; icon.name: "preferences-system-bluetooth"; onTriggered: root.show(bluetoothPage) }
        ]
    }

    function show(page) {
        pageStack.clear()
        pageStack.push(page)
    }

    // FT_INPUT_PAGE=buttons|pointer|bluetooth opens the app on that page.
    pageStack.initialPage: ({ buttons: buttonsPage, pointer: pointerPage, bluetooth: bluetoothPage })[startPage] || devicesPage

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
                      + "Pass through: left alone (a Meta tap still toggles the dashboard). Ignore: left alone."
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
                          + "The Z3's extra buttons show up as keys from its keyboard node."
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
