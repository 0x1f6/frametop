/*
    Frametop's window decoration: Breeze's flat title bar, drawn in QML for KWin's Aurorae
    engine, plus a button left of Close that floats the window in VR (docs/floating-windows.md,
    decision 25). Aurorae loads QML without compiling anything, so this keeps working across
    SteamOS's KWin updates, which a C++ decoration wouldn't.

    The float button is the Keep Below button (FtButton.qml): Frametop's KWin script floats a
    window when keep-below is set and docks it when it's cleared. A Keep Below button in the
    configured button order is left out, since the float button stands in for it.

    SPDX-License-Identifier: GPL-2.0-or-later
*/
import QtQuick
import org.kde.kwin.decoration

Decoration {
    id: root
    alpha: false

    DecorationOptions {
        id: options
        deco: decoration
    }
    TextMetrics {
        id: metrics
        font: options.titleFont
        text: "Mj"
    }

    readonly property bool maximized: decoration.client.maximized
    readonly property int buttonSize: Math.max(16, Math.round(metrics.height * 1.25))
    readonly property int titleHeight: buttonSize + 8
    readonly property int borderSize: decorationSettings.borderSize
    readonly property int side: {
        switch (borderSize) {
        case DecorationOptions.BorderNone:
        case DecorationOptions.BorderNoSides: return 0;
        case DecorationOptions.BorderTiny: return 2;
        case DecorationOptions.BorderLarge: return 6;
        case DecorationOptions.BorderVeryLarge: return 8;
        case DecorationOptions.BorderHuge: return 12;
        case DecorationOptions.BorderVeryHuge: return 18;
        case DecorationOptions.BorderOversized: return 27;
        default: return 4;
        }
    }
    readonly property int bottomBorder: borderSize === DecorationOptions.BorderNone ? 0
                                : borderSize === DecorationOptions.BorderNoSides ? 4 : side
    readonly property color outline: Qt.tint(options.titleBarColor, Qt.rgba(0, 0, 0, 0.35))

    function applyBorders() {
        borders.left = side;
        borders.right = side;
        borders.bottom = bottomBorder;
        borders.top = titleHeight;
        maximizedBorders.top = titleHeight;
        // Without visible side borders, keep a strip to grab for resizing.
        extendedBorders.left = side ? 0 : 4;
        extendedBorders.right = side ? 0 : 4;
        extendedBorders.bottom = bottomBorder ? 0 : 4;
    }
    onTitleHeightChanged: applyBorders()
    onSideChanged: applyBorders()
    onBottomBorderChanged: applyBorders()
    Component.onCompleted: applyBorders()

    // The configured buttons, with the float button left of Close (or first on the right
    // when there's no Close), and no Keep Below of their own.
    function order(list, right) {
        const out = [];
        let placed = false;
        for (let i = 0; i < list.length; ++i) {
            const t = list[i];
            if (t === DecorationOptions.DecorationButtonKeepBelow)
                continue;
            if (t === DecorationOptions.DecorationButtonClose && !placed) {
                if (right) {
                    out.push(DecorationOptions.DecorationButtonKeepBelow, t);
                } else {
                    out.push(t, DecorationOptions.DecorationButtonKeepBelow);
                }
                placed = true;
                continue;
            }
            out.push(t);
        }
        return {buttons: out, placed: placed};
    }
    readonly property var leftOrder: order(options.titleButtonsLeft || [], false)
    readonly property var rightOrder: {
        const r = order(options.titleButtonsRight || [], true);
        if (!r.placed && !leftOrder.placed)
            r.buttons.unshift(DecorationOptions.DecorationButtonKeepBelow);
        return r;
    }

    function componentFor(t) {
        switch (t) {
        case DecorationOptions.DecorationButtonMenu: return menuButton;
        case DecorationOptions.DecorationButtonExplicitSpacer: return spacer;
        case DecorationOptions.DecorationButtonClose: return closeButton;
        case DecorationOptions.DecorationButtonMaximizeRestore: return maximizeButton;
        case DecorationOptions.DecorationButtonMinimize: return minimizeButton;
        case DecorationOptions.DecorationButtonKeepBelow: return floatButton;
        case DecorationOptions.DecorationButtonKeepAbove: return keepAboveButton;
        case DecorationOptions.DecorationButtonShade: return shadeButton;
        case DecorationOptions.DecorationButtonOnAllDesktops: return allDesktopsButton;
        case DecorationOptions.DecorationButtonQuickHelp: return helpButton;
        case DecorationOptions.DecorationButtonApplicationMenu: return appMenuButton;
        }
        return null;
    }

    Rectangle {
        anchors.fill: parent
        color: options.titleBarColor
        border.width: root.maximized ? 0 : 1
        border.color: root.outline
    }

    Item {
        id: titleBar
        x: root.maximized ? 0 : Math.max(root.side, 1)
        y: root.maximized ? 0 : 1
        width: root.width - 2 * x
        height: root.titleHeight - y

        Row {
            id: leftButtons
            anchors.left: parent.left
            anchors.leftMargin: 4
            anchors.verticalCenter: parent.verticalCenter
            spacing: 4
            Repeater {
                model: root.leftOrder.buttons
                delegate: Loader {
                    required property var modelData
                    sourceComponent: root.componentFor(modelData)
                }
            }
        }
        Row {
            id: rightButtons
            anchors.right: parent.right
            anchors.rightMargin: 4
            anchors.verticalCenter: parent.verticalCenter
            spacing: 4
            layoutDirection: Qt.LeftToRight
            Repeater {
                model: root.rightOrder.buttons
                delegate: Loader {
                    required property var modelData
                    sourceComponent: root.componentFor(modelData)
                }
            }
        }
        // Centred over the whole bar, like Breeze, but never under the buttons.
        Text {
            id: caption
            readonly property real free: rightButtons.x - (leftButtons.x + leftButtons.width) - 16
            width: Math.min(implicitWidth, free)
            x: Math.max(leftButtons.x + leftButtons.width + 8,
                        Math.min((parent.width - width) / 2, rightButtons.x - 8 - width))
            anchors.verticalCenter: parent.verticalCenter
            text: decoration.client.caption
            textFormat: Text.PlainText
            font: options.titleFont
            color: options.fontColor
            elide: Text.ElideRight
            renderType: Text.NativeRendering
        }
        Component.onCompleted: decoration.installTitleItem(titleBar)
    }

    Component {
        id: menuButton
        MenuButton {
            width: root.buttonSize
            height: root.buttonSize
        }
    }
    Component {
        id: spacer
        Item {
            width: root.buttonSize
            height: root.buttonSize
        }
    }
    Component {
        id: closeButton
        FtButton {
            buttonType: DecorationOptions.DecorationButtonClose
            size: root.buttonSize
            fg: options.fontColor
            bg: options.titleBarColor
        }
    }
    Component {
        id: maximizeButton
        FtButton {
            buttonType: DecorationOptions.DecorationButtonMaximizeRestore
            size: root.buttonSize
            fg: options.fontColor
            bg: options.titleBarColor
        }
    }
    Component {
        id: minimizeButton
        FtButton {
            buttonType: DecorationOptions.DecorationButtonMinimize
            size: root.buttonSize
            fg: options.fontColor
            bg: options.titleBarColor
        }
    }
    Component {
        id: floatButton
        FtButton {
            buttonType: DecorationOptions.DecorationButtonKeepBelow
            size: root.buttonSize
            fg: options.fontColor
            bg: options.titleBarColor
        }
    }
    Component {
        id: keepAboveButton
        FtButton {
            buttonType: DecorationOptions.DecorationButtonKeepAbove
            size: root.buttonSize
            fg: options.fontColor
            bg: options.titleBarColor
        }
    }
    Component {
        id: shadeButton
        FtButton {
            buttonType: DecorationOptions.DecorationButtonShade
            size: root.buttonSize
            fg: options.fontColor
            bg: options.titleBarColor
        }
    }
    Component {
        id: allDesktopsButton
        FtButton {
            buttonType: DecorationOptions.DecorationButtonOnAllDesktops
            size: root.buttonSize
            fg: options.fontColor
            bg: options.titleBarColor
        }
    }
    Component {
        id: helpButton
        FtButton {
            buttonType: DecorationOptions.DecorationButtonQuickHelp
            size: root.buttonSize
            fg: options.fontColor
            bg: options.titleBarColor
        }
    }
    Component {
        id: appMenuButton
        FtButton {
            buttonType: DecorationOptions.DecorationButtonApplicationMenu
            size: root.buttonSize
            fg: options.fontColor
            bg: options.titleBarColor
        }
    }
}
