import QtQuick

Item {
    id: root

    property color lineColor: "#38bdf8"
    property real lineHeight: 2
    property int duration: 4000
    property bool running: true
    property bool paused: false
    property bool reducedMotion: false

    anchors.fill: parent
    clip: true

    Rectangle {
        id: line
        width: root.width
        height: root.lineHeight
        y: 0
        opacity: 0.18

        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0.0; color: "transparent" }
            GradientStop { position: 0.3; color: root.lineColor }
            GradientStop { position: 0.7; color: root.lineColor }
            GradientStop { position: 1.0; color: "transparent" }
        }
    }

    NumberAnimation {
        id: scanAnim
        target: line
        property: "y"
        from: -root.lineHeight
        to: root.height + root.lineHeight
        duration: root.duration
        loops: Animation.Infinite
        running: root.running && !root.paused && !root.reducedMotion && root.visible
    }
}
