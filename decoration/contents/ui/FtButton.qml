/*
    A title bar button drawn the way Breeze draws its own: a glyph in the title's colour,
    a soft circle behind it on hover, and a red circle for Close.

    The float button is KWin's Keep Below button with a glyph of its own: Frametop's KWin
    script (float/frametop-float.js) floats a window when keep-below is set on it and docks
    it when it's cleared, and keeps the flag set on every floating window, so "toggled" here
    means "floating" and the glyph turns into "back to the desktop".

    SPDX-License-Identifier: GPL-2.0-or-later
*/
import QtQuick
import QtQuick.Shapes
import org.kde.kwin.decoration

DecorationButton {
    id: button
    property real size: 20
    property color fg: "white"
    property color bg: "black"
    readonly property bool isClose: buttonType === DecorationOptions.DecorationButtonClose
    readonly property bool isFloat: buttonType === DecorationOptions.DecorationButtonKeepBelow
    // The glyph, as polylines in an 18 x 18 box (Breeze's own sizes).
    readonly property var glyph: {
        switch (buttonType) {
        case DecorationOptions.DecorationButtonClose:
            return [[[5, 5], [13, 13]], [[13, 5], [5, 13]]];
        case DecorationOptions.DecorationButtonMaximizeRestore:
            return decoration.client.maximized ? [[[4.5, 9], [9, 4.5], [13.5, 9], [9, 13.5], [4.5, 9]]]
                                               : [[[4, 11.5], [9, 6.5], [14, 11.5]]];
        case DecorationOptions.DecorationButtonMinimize:
            return [[[4, 7], [9, 12], [14, 7]]];
        case DecorationOptions.DecorationButtonKeepAbove:
            return [[[4, 9], [9, 4], [14, 9]], [[4, 14], [9, 9], [14, 14]]];
        case DecorationOptions.DecorationButtonShade:
            return [[[4, 5], [14, 5]], [[4, 13], [9, 8], [14, 13]]];
        case DecorationOptions.DecorationButtonApplicationMenu:
            return [[[3.5, 5], [14.5, 5]], [[3.5, 9], [14.5, 9]], [[3.5, 13], [14.5, 13]]];
        case DecorationOptions.DecorationButtonKeepBelow:
            // A box with an arrow leaving it (float in VR), or coming back into it (put it back).
            return button.toggled
                ? [[[8, 3.5], [3.5, 3.5], [3.5, 14.5], [14.5, 14.5], [14.5, 10]], [[14.5, 3.5], [8.5, 9.5]],
                   [[8.5, 5], [8.5, 9.5], [13, 9.5]]]
                : [[[8, 3.5], [3.5, 3.5], [3.5, 14.5], [14.5, 14.5], [14.5, 10]], [[8.5, 9.5], [14.5, 3.5]],
                   [[10, 3.5], [14.5, 3.5], [14.5, 8]]];
        }
        return [];
    }
    readonly property string tip: isFloat ? (toggled ? "Back to Desktop" : "Float in VR") : ""

    width: size
    height: size

    Rectangle {
        anchors.fill: parent
        radius: width / 2
        visible: button.hovered || button.pressed || (button.toggled && !button.isFloat)
        color: button.isClose ? (button.pressed ? "#c0392b" : "#da4453")
                              : Qt.rgba(button.fg.r, button.fg.g, button.fg.b,
                                        button.pressed ? 0.35 : button.hovered ? 0.2 : 0.12)
    }

    // On all desktops: a dot, filled while the window is on all of them. Help: a question mark.
    Rectangle {
        visible: button.buttonType === DecorationOptions.DecorationButtonOnAllDesktops
        anchors.centerIn: parent
        width: button.size * 0.3
        height: width
        radius: width / 2
        color: button.toggled ? button.fg : "transparent"
        border.width: Math.max(1, button.size / 18)
        border.color: button.fg
    }
    Text {
        visible: button.buttonType === DecorationOptions.DecorationButtonQuickHelp
        anchors.centerIn: parent
        text: "?"
        color: button.fg
        font.pixelSize: button.size * 0.65
        font.bold: true
    }

    // Up to three strokes per glyph.
    function stroke(i) {
        const k = button.size / 18;
        return i < glyph.length ? glyph[i].map(p => Qt.point(p[0] * k, p[1] * k)) : [];
    }
    readonly property color strokeColor: isClose && (hovered || pressed) ? "white" : fg
    readonly property real strokeWidth: Math.max(1, size / 18 * 1.1)
    // (An inline component can't see the ids around it, so everything comes in as properties.)
    component Stroke: ShapePath {
        property var points: []
        fillColor: "transparent"
        capStyle: ShapePath.RoundCap
        joinStyle: ShapePath.RoundJoin
        PathPolyline { path: points }
    }
    Shape {
        anchors.fill: parent
        preferredRendererType: Shape.CurveRenderer
        Stroke { points: button.stroke(0); strokeColor: button.strokeColor; strokeWidth: button.strokeWidth }
        Stroke { points: button.stroke(1); strokeColor: button.strokeColor; strokeWidth: button.strokeWidth }
        Stroke { points: button.stroke(2); strokeColor: button.strokeColor; strokeWidth: button.strokeWidth }
    }

    onHoveredChanged: {
        if (!tip || typeof decoration.requestShowToolTip !== "function")
            return;
        if (hovered)
            decoration.requestShowToolTip(tip);
        else
            decoration.requestHideToolTip();
    }
    Component.onCompleted: {
        if (buttonType === DecorationOptions.DecorationButtonQuickHelp)
            visible = Qt.binding(() => decoration.client.providesContextHelp);
        if (buttonType === DecorationOptions.DecorationButtonApplicationMenu)
            visible = Qt.binding(() => decoration.client.hasApplicationMenu);
        // Like Breeze: no On All Desktops button with only one virtual desktop.
        if (buttonType === DecorationOptions.DecorationButtonOnAllDesktops)
            visible = Qt.binding(() => decorationSettings.onAllDesktopsAvailable);
    }
}
