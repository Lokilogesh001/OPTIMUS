import QtQuick

Item {
    id: root

    property real baseRadius: 50
    property color glowColor: "#00d4ff"
    property real minScale: 0.85
    property real maxScale: 1.15
    property int duration: 2400
    property bool running: true
    property bool paused: false
    property bool reducedMotion: false

    width: baseRadius * 2
    height: baseRadius * 2

    Rectangle {
        id: glowCircle
        anchors.centerIn: parent
        width: root.baseRadius * 2
        height: root.baseRadius * 2
        radius: root.baseRadius
        color: "transparent"

        // Outer radial gradient glow simulation with soft layered concentric circles
        Rectangle {
            anchors.centerIn: parent
            width: parent.width * 1.2
            height: parent.height * 1.2
            radius: width / 2
            color: root.glowColor
            opacity: 0.12
        }

        Rectangle {
            anchors.centerIn: parent
            width: parent.width
            height: parent.height
            radius: width / 2
            color: root.glowColor
            opacity: 0.28
        }

        Rectangle {
            anchors.centerIn: parent
            width: parent.width * 0.7
            height: parent.height * 0.7
            radius: width / 2
            color: root.glowColor
            opacity: 0.55
        }

        Rectangle {
            anchors.centerIn: parent
            width: parent.width * 0.35
            height: parent.height * 0.35
            radius: width / 2
            color: "#f8fafc"
            opacity: 0.85
        }
    }

    SequentialAnimation {
        id: breatheAnim
        running: root.running && !root.paused && !root.reducedMotion
        loops: Animation.Infinite

        NumberAnimation {
            target: glowCircle
            property: "scale"
            from: root.minScale
            to: root.maxScale
            duration: root.duration / 2
            easing.type: Easing.InOutSine
        }

        NumberAnimation {
            target: glowCircle
            property: "scale"
            from: root.maxScale
            to: root.minScale
            duration: root.duration / 2
            easing.type: Easing.InOutSine
        }
    }
}
