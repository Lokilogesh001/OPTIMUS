import QtQuick
import QtQuick.Shapes

Item {
    id: root

    property real radius: 80
    property real strokeWidth: 2
    property color strokeColor: "#0084ff"
    property var dashPattern: [12, 8]
    property int rotationDuration: 12000
    property bool clockwise: true
    property bool running: true
    property bool paused: false
    property bool reducedMotion: false

    width: radius * 2
    height: radius * 2

    Canvas {
        id: ringCanvas
        anchors.fill: parent
        antialiasing: true

        onPaint: {
            var ctx = getContext("2d");
            ctx.reset();
            var cx = width / 2;
            var cy = height / 2;
            var r = root.radius - (root.strokeWidth / 2);
            if (r <= 0) return;

            ctx.lineWidth = root.strokeWidth;
            ctx.strokeStyle = root.strokeColor.toString();
            if (root.dashPattern && root.dashPattern.length > 0) {
                ctx.setLineDash(root.dashPattern);
            }

            ctx.beginPath();
            ctx.arc(cx, cy, r, 0, Math.PI * 2, false);
            ctx.stroke();
        }

        onWidthChanged: requestPaint()
        onHeightChanged: requestPaint()
    }

    Connections {
        target: root
        function onStrokeColorChanged() { ringCanvas.requestPaint(); }
        function onRadiusChanged() { ringCanvas.requestPaint(); }
        function onStrokeWidthChanged() { ringCanvas.requestPaint(); }
    }

    NumberAnimation {
        id: rotateAnim
        target: root
        property: "rotation"
        from: root.clockwise ? 0 : 360
        to: root.clockwise ? 360 : 0
        duration: Math.max(500, root.rotationDuration)
        loops: Animation.Infinite
        running: root.running && !root.paused && !root.reducedMotion
    }
}
