import QtQuick

Item {
    id: root

    property real cpuUsage: (typeof animBridge !== "undefined") ? animBridge.cpuUsage : 0.0
    property real ramUsage: (typeof animBridge !== "undefined") ? animBridge.ramUsage : 0.0
    property real gpuUsage: (typeof animBridge !== "undefined") ? animBridge.gpuUsage : 0.0
    property real vramUsage: (typeof animBridge !== "undefined") ? animBridge.vramUsage : 0.0
    property bool isPaused: (typeof animBridge !== "undefined") ? animBridge.isPaused : false
    property bool reducedMotion: (typeof animBridge !== "undefined") ? animBridge.reducedMotion : false

    implicitWidth: 320
    implicitHeight: 120

    Rectangle {
        anchors.fill: parent
        color: (typeof animBridge !== "undefined") ? animBridge.colorBgVoid : "#04070d"
    }

    Column {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 10

        Text {
            text: "SYSTEM HARDWARE TELEMETRY"
            font.family: "Segoe UI"
            font.pixelSize: 10
            font.bold: true
            font.letterSpacing: 1.2
            color: (typeof animBridge !== "undefined") ? animBridge.colorTextPrimary : "#f8fafc"
        }

        // CPU Bar
        Row {
            width: parent.width
            spacing: 8

            Text {
                width: 38
                text: "CPU"
                font.family: "Consolas"
                font.pixelSize: 9
                color: "#94a3b8"
            }

            Rectangle {
                width: parent.width - 85
                height: 8
                radius: 4
                color: "#0f1728"
                clip: true

                Rectangle {
                    width: parent.width * Math.min(1.0, root.cpuUsage / 100.0)
                    height: parent.height
                    radius: 4
                    color: root.cpuUsage > 90 ? "#ef4444" : (root.cpuUsage > 80 ? "#f59e0b" : "#38bdf8")

                    Behavior on width {
                        NumberAnimation { duration: root.reducedMotion ? 0 : 250 }
                    }
                }
            }

            Text {
                width: 32
                text: Math.round(root.cpuUsage) + "%"
                font.family: "Consolas"
                font.pixelSize: 9
                color: "#f8fafc"
            }
        }

        // RAM Bar
        Row {
            width: parent.width
            spacing: 8

            Text {
                width: 38
                text: "RAM"
                font.family: "Consolas"
                font.pixelSize: 9
                color: "#94a3b8"
            }

            Rectangle {
                width: parent.width - 85
                height: 8
                radius: 4
                color: "#0f1728"
                clip: true

                Rectangle {
                    width: parent.width * Math.min(1.0, root.ramUsage / 100.0)
                    height: parent.height
                    radius: 4
                    color: root.ramUsage > 85 ? "#f59e0b" : "#0084ff"

                    Behavior on width {
                        NumberAnimation { duration: root.reducedMotion ? 0 : 250 }
                    }
                }
            }

            Text {
                width: 32
                text: Math.round(root.ramUsage) + "%"
                font.family: "Consolas"
                font.pixelSize: 9
                color: "#f8fafc"
            }
        }
    }
}
