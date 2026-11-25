import QtQuick
import "components"

Item {
    id: root

    property string coreState: (typeof animBridge !== "undefined") ? animBridge.coreState : "IDLE"
    property real audioLevel: (typeof animBridge !== "undefined") ? animBridge.audioLevel : 0.0
    property bool isPaused: (typeof animBridge !== "undefined") ? animBridge.isPaused : false
    property bool reducedMotion: (typeof animBridge !== "undefined") ? animBridge.reducedMotion : false

    implicitWidth: 320
    implicitHeight: 140

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

            Row {
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                spacing: 8

                Rectangle {
                    width: 8
                    height: 8
                    radius: 4
                    anchors.verticalCenter: parent.verticalCenter
                    color: root.coreState === "SPEAKING" ? "#00d4ff" : (root.coreState === "LISTENING" ? "#10b981" : "#64748b")
                }

                Text {
                    text: root.coreState === "SPEAKING" ? "VOICE OUTPUT (TTS)" : (root.coreState === "LISTENING" ? "MIC INPUT (STT)" : "AUDIO MONITOR")
                    font.family: "Segoe UI"
                    font.pixelSize: 10
                    font.bold: true
                    font.letterSpacing: 1.0
                    color: (typeof animBridge !== "undefined") ? animBridge.colorTextPrimary : "#f8fafc"
                }
            }

            Text {
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                text: Math.round(root.audioLevel * 100) + "%"
                font.family: "Consolas"
                font.pixelSize: 10
                color: "#38bdf8"
            }
        }

        // Real-Time 16-Band Procedural Waveform Canvas (B-02)
        WaveformCanvas {
            width: parent.width
            height: parent.height - 32
            bandCount: 16
            audioLevel: root.audioLevel
            barColor: root.coreState === "SPEAKING" ? "#00d4ff" : "#38bdf8"
            paused: root.isPaused
            reducedMotion: root.reducedMotion
        }
    }
}
