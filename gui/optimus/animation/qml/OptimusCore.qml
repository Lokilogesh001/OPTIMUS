import QtQuick
import "components"

Item {
    id: root

    // Reference properties from animBridge if injected, with safe defaults
    property string coreState: (typeof animBridge !== "undefined") ? animBridge.coreState : "IDLE"
    property real audioLevel: (typeof animBridge !== "undefined") ? animBridge.audioLevel : 0.0
    property bool isPaused: (typeof animBridge !== "undefined") ? animBridge.isPaused : false
    property bool reducedMotion: (typeof animBridge !== "undefined") ? animBridge.reducedMotion : false
    property int loadShedding: (typeof animBridge !== "undefined") ? animBridge.loadSheddingLevel : 0

    implicitWidth: 320
    implicitHeight: 320

    // State-dependent color resolution
    readonly property color statePrimaryColor: {
        switch (root.coreState) {
            case "LISTENING": return "#00d4ff";
            case "THINKING":  return "#0084ff";
            case "PROCESSING":return "#38bdf8";
            case "EXECUTING": return "#f59e0b";
            case "SPEAKING":  return "#00d4ff";
            case "ERROR":     return "#ef4444";
            case "OFFLINE":   return "#64748b";
            case "IDLE":
            default:          return "#0084ff";
        }
    }

    readonly property color stateSecondaryColor: {
        switch (root.coreState) {
            case "LISTENING": return "#38bdf8";
            case "THINKING":  return "#00d4ff";
            case "PROCESSING":return "#0084ff";
            case "EXECUTING": return "#fbbf24";
            case "SPEAKING":  return "#60a5fa";
            case "ERROR":     return "#f43f5e";
            case "OFFLINE":   return "#475569";
            case "IDLE":
            default:          return "#38bdf8";
        }
    }

    // Dynamic rotation speeds based on state
    readonly property int speedMultiplier: {
        if (root.coreState === "THINKING" || root.coreState === "PROCESSING") return 3;
        if (root.coreState === "EXECUTING") return 2;
        if (root.coreState === "OFFLINE") return 0;
        return 1;
    }

    readonly property real minDimension: Math.min(width, height)
    readonly property real baseRadius: minDimension * 0.42

    // Background Void Rectangle
    Rectangle {
        anchors.fill: parent
        color: (typeof animBridge !== "undefined") ? animBridge.colorBgVoid : "#04070d"
    }

    // Subtle CRT Scanline overlay (shed when load >= 1 or reduced motion)
    ScanLine {
        id: scanLine
        lineColor: root.stateSecondaryColor
        visible: root.loadShedding < 1 && !root.reducedMotion
        paused: root.isPaused
        reducedMotion: root.reducedMotion
    }

    // Central Breathing Holographic Glow Core
    PulseGlow {
        id: coreGlow
        anchors.centerIn: parent
        baseRadius: Math.max(20, (root.baseRadius * 0.32) + (root.audioLevel * 30))
        glowColor: root.statePrimaryColor
        duration: root.coreState === "THINKING" ? 1200 : 2600
        paused: root.isPaused
        reducedMotion: root.reducedMotion
    }

    // Inner Concentric Ring
    HoloRing {
        id: ringInner
        anchors.centerIn: parent
        radius: Math.max(15, root.baseRadius * 0.50)
        strokeWidth: 1.5
        strokeColor: root.stateSecondaryColor
        dashPattern: [8, 6]
        rotationDuration: root.speedMultiplier > 0 ? (12000 / root.speedMultiplier) : 999999
        clockwise: true
        paused: root.isPaused || root.speedMultiplier === 0
        reducedMotion: root.reducedMotion
    }

    // Middle Concentric Counter-Rotating Ring
    HoloRing {
        id: ringMid
        anchors.centerIn: parent
        radius: Math.max(25, root.baseRadius * 0.72)
        strokeWidth: 2.0
        strokeColor: root.statePrimaryColor
        dashPattern: [16, 10, 4, 10]
        rotationDuration: root.speedMultiplier > 0 ? (18000 / root.speedMultiplier) : 999999
        clockwise: false
        paused: root.isPaused || root.speedMultiplier === 0
        reducedMotion: root.reducedMotion
    }

    // Outer Concentric Segmented Ring (shed when load >= 2)
    HoloRing {
        id: ringOuter
        anchors.centerIn: parent
        radius: Math.max(35, root.baseRadius * 0.92)
        strokeWidth: 2.5
        strokeColor: root.stateSecondaryColor
        dashPattern: [24, 16]
        rotationDuration: root.speedMultiplier > 0 ? (24000 / root.speedMultiplier) : 999999
        clockwise: true
        visible: root.loadShedding < 2
        paused: root.isPaused || root.speedMultiplier === 0
        reducedMotion: root.reducedMotion
    }

    // Central Status Labels
    Column {
        anchors.centerIn: parent
        spacing: 4

        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: "OPTIMUS"
            font.family: "Segoe UI"
            font.pixelSize: Math.max(11, root.minDimension * 0.045)
            font.bold: true
            font.letterSpacing: 2.0
            color: (typeof animBridge !== "undefined") ? animBridge.colorTextPrimary : "#f8fafc"
        }

        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            text: root.coreState
            font.family: "Segoe UI"
            font.pixelSize: Math.max(9, root.minDimension * 0.035)
            font.weight: Font.Medium
            font.letterSpacing: 1.5
            color: root.stateSecondaryColor
        }
    }
}
