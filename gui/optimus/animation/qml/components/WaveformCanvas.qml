import QtQuick

Item {
    id: root

    property real audioLevel: (typeof animBridge !== "undefined") ? animBridge.audioLevel : 0.0
    property var audioWaveform: (typeof animBridge !== "undefined") ? animBridge.audioWaveform : []
    property color barColor: (typeof animBridge !== "undefined") ? animBridge.colorAccentCyan : "#38bdf8"
    property int bandCount: 16
    property bool running: true
    property bool paused: (typeof animBridge !== "undefined") ? animBridge.isPaused : false
    property bool reducedMotion: (typeof animBridge !== "undefined") ? animBridge.reducedMotion : false
    property bool hasActiveDecay: false
    property int decayFramesLeft: 0

    implicitWidth: 260
    implicitHeight: 64

    Canvas {
        id: canvas
        anchors.fill: parent
        antialiasing: true

        onPaint: {
            var ctx = getContext("2d");
            ctx.reset();

            var w = width;
            var h = height;
            var bands = root.bandCount;
            var barWidth = (w - (bands - 1) * 3) / bands;
            var level = root.audioLevel;

            if (barWidth < 2) barWidth = 2;

            for (var i = 0; i < bands; i++) {
                // Procedural synthetic harmonic formula derived from RMS amplitude (B-02)
                var freq = 1.0 + (i * 0.4);
                var damping = 1.0 - (i / (bands * 1.3));
                var timeOffset = Date.now() * 0.005;
                var harmonic = (Math.sin(timeOffset * freq + i * 0.6) + 1.0) * 0.5;
                
                // If raw waveform is present and populated, blend with it
                var sample = 0.0;
                if (root.audioWaveform && root.audioWaveform.length > i) {
                    sample = Math.abs(root.audioWaveform[i]);
                }

                var heightRatio = (level * 0.75 + sample * 0.25) * harmonic * damping;
                heightRatio = Math.max(0.04, Math.min(1.0, heightRatio));

                var barHeight = Math.max(3, h * heightRatio);
                var x = i * (barWidth + 3);
                var y = h - barHeight;

                // Gradient fill for each bar
                var grad = ctx.createLinearGradient(x, y, x, h);
                grad.addColorStop(0.0, root.barColor.toString());
                grad.addColorStop(1.0, "#0084ff");

                ctx.fillStyle = grad;
                ctx.fillRect(x, y, barWidth, barHeight);
            }
        }
    }

    Timer {
        id: redrawTimer
        interval: 33  // ~30 FPS canvas redraw
        running: root.running && !root.paused && !root.reducedMotion && root.visible && (root.audioLevel > 0.001 || root.hasActiveDecay)
        repeat: true
        onTriggered: {
            if (root.audioLevel <= 0.001 && root.hasActiveDecay) {
                root.decayFramesLeft -= 1;
                if (root.decayFramesLeft <= 0) {
                    root.hasActiveDecay = false;
                }
            }
            canvas.requestPaint();
        }
    }

    Connections {
        target: root
        function onAudioLevelChanged() {
            if (root.audioLevel > 0.001) {
                root.hasActiveDecay = true;
                root.decayFramesLeft = 10;
            }
            if (!root.reducedMotion && !redrawTimer.running && !root.paused && root.visible && (root.audioLevel > 0.005 || root.hasActiveDecay)) {
                canvas.requestPaint();
            }
        }
    }
}
