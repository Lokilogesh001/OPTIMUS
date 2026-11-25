import QtQuick

Item {
    id: root

    property real missionProgress: (typeof animBridge !== "undefined") ? animBridge.missionProgress : 0.0
    property string missionStage: (typeof animBridge !== "undefined") ? animBridge.missionStage : "Ready"
    property bool isPaused: (typeof animBridge !== "undefined") ? animBridge.isPaused : false
    property bool reducedMotion: (typeof animBridge !== "undefined") ? animBridge.reducedMotion : false

    implicitWidth: 320
    implicitHeight: 90

    Rectangle {
        anchors.fill: parent
        color: (typeof animBridge !== "undefined") ? animBridge.colorBgVoid : "#04070d"
    }

    Column {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 8

        Item {
            width: parent.width
            height: 16

            Text {
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                text: "MISSION: " + root.missionStage.toUpperCase()
                font.family: "Segoe UI"
                font.pixelSize: 10
                font.bold: true
                font.letterSpacing: 1.0
                color: (typeof animBridge !== "undefined") ? animBridge.colorTextPrimary : "#f8fafc"
            }

            Text {
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                text: Math.round(root.missionProgress * 100) + "%"
                font.family: "Consolas"
                font.pixelSize: 10
                font.bold: true
                color: "#10b981"
            }
        }

        // Animated Progress Track
        Rectangle {
            width: parent.width
            height: 10
            radius: 5
            color: "#0f1728"
            clip: true

            Rectangle {
                id: fillBar
                width: parent.width * Math.max(0.02, Math.min(1.0, root.missionProgress))
                height: parent.height
                radius: 5

                gradient: Gradient {
                    orientation: Gradient.Horizontal
                    GradientStop { position: 0.0; color: "#0084ff" }
                    GradientStop { position: 1.0; color: "#00d4ff" }
                }

                Behavior on width {
                    NumberAnimation {
                        duration: root.reducedMotion ? 0 : 350
                        easing.type: Easing.OutCubic
                    }
                }
            }
        }

        // Subtitle status line
        Text {
            text: root.missionProgress >= 1.0 ? "Execution Completed & Verified" : (root.missionProgress > 0.0 ? "Autonomous Execution In Progress" : "Standing By For Objective")
            font.family: "Segoe UI"
            font.pixelSize: 9
            color: "#64748b"
        }
    }
}
