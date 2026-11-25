import QtQuick
import "components"

Item {
    id: root

    property string coreState: (typeof animBridge !== "undefined") ? animBridge.coreState : "IDLE"
    property real audioLevel: (typeof animBridge !== "undefined") ? animBridge.audioLevel : 0.0
    property bool isPaused: (typeof animBridge !== "undefined") ? animBridge.isPaused : false
    property bool reducedMotion: (typeof animBridge !== "undefined") ? animBridge.reducedMotion : false

    implicitWidth: 320
    implicitHeight: 460

    Rectangle {
        anchors.fill: parent
        color: (typeof animBridge !== "undefined") ? animBridge.colorBgVoid : "#04070d"
    }

    Column {
        anchors.fill: parent
        spacing: 12

        // 1. Central Core HUD
        OptimusCore {
            width: parent.width
            height: parent.width
        }

        // 2. Real-Time Voice Waveform
        VoiceVisualizer {
            width: parent.width
            height: 80
        }

        // 3. Compact Telemetry
        TelemetryVisualizer {
            width: parent.width
            height: 70
        }
    }
}
