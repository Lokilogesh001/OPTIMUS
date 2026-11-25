import QtQuick

Item {
    id: root

    property var activeAgents: (typeof animBridge !== "undefined") ? animBridge.activeAgents : []
    property bool isPaused: (typeof animBridge !== "undefined") ? animBridge.isPaused : false
    property bool reducedMotion: (typeof animBridge !== "undefined") ? animBridge.reducedMotion : false

    implicitWidth: 320
    implicitHeight: 280

    readonly property var defaultSpecialists: [
        { name: "Researcher", role: "researcher", angle: 0 },
        { name: "Developer",  role: "developer",  angle: 72 },
        { name: "Computer",   role: "computer",   angle: 144 },
        { name: "Data Intel", role: "data_intel", angle: 216 },
        { name: "File Agent", role: "file_agent", angle: 288 }
    ]

    Rectangle {
        anchors.fill: parent
        color: (typeof animBridge !== "undefined") ? animBridge.colorBgVoid : "#04070d"
    }

    Item {
        id: meshArea
        anchors.centerIn: parent
        width: Math.min(parent.width, parent.height) - 40
        height: width

        readonly property real cx: width / 2
        readonly property real cy: height / 2
        readonly property real orbitRadius: width * 0.38

        // Connection lines to satellite nodes
        Canvas {
            id: lineCanvas
            anchors.fill: parent
            antialiasing: true

            onPaint: {
                var ctx = getContext("2d");
                ctx.reset();
                ctx.lineWidth = 1;
                ctx.strokeStyle = "rgba(56, 189, 248, 0.25)";
                ctx.setLineDash([4, 4]);

                for (var i = 0; i < root.defaultSpecialists.length; i++) {
                    var rad = (root.defaultSpecialists[i].angle * Math.PI) / 180.0;
                    var sx = meshArea.cx + meshArea.orbitRadius * Math.cos(rad);
                    var sy = meshArea.cy + meshArea.orbitRadius * Math.sin(rad);

                    ctx.beginPath();
                    ctx.moveTo(meshArea.cx, meshArea.cy);
                    ctx.lineTo(sx, sy);
                    ctx.stroke();
                }
            }
        }

        // Central Orchestrator Node
        Rectangle {
            id: centralNode
            anchors.centerIn: parent
            width: 48
            height: 48
            radius: 24
            color: "#0c1322"
            border.color: "#0084ff"
            border.width: 2

            Text {
                anchors.centerIn: parent
                text: "ORCH"
                font.family: "Segoe UI"
                font.pixelSize: 9
                font.bold: true
                color: "#f8fafc"
            }
        }

        // Satellite Agent Nodes
        Repeater {
            model: root.defaultSpecialists

            delegate: Item {
                id: satNode
                readonly property real rad: (modelData.angle * Math.PI) / 180.0
                x: meshArea.cx + meshArea.orbitRadius * Math.cos(rad) - 18
                y: meshArea.cy + meshArea.orbitRadius * Math.sin(rad) - 18
                width: 36
                height: 36

                Rectangle {
                    anchors.fill: parent
                    radius: 18
                    color: "#0f172a"
                    border.color: "#38bdf8"
                    border.width: 1.5

                    Text {
                        anchors.centerIn: parent
                        text: modelData.name.substring(0, 3).toUpperCase()
                        font.family: "Segoe UI"
                        font.pixelSize: 8
                        font.bold: true
                        color: "#94a3b8"
                    }
                }
            }
        }
    }
}
