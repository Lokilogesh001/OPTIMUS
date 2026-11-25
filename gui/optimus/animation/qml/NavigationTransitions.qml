import QtQuick

Item {
    id: root

    property int activeIndex: 0
    property real indicatorY: 0
    property int transitionDuration: 180
    property bool reducedMotion: (typeof animBridge !== "undefined") ? animBridge.reducedMotion : false

    implicitWidth: 4
    implicitHeight: 600

    Rectangle {
        id: slidingBar
        width: root.width
        height: 36
        y: root.indicatorY
        radius: 2
        color: (typeof animBridge !== "undefined") ? animBridge.colorAccentCyan : "#38bdf8"

        Behavior on y {
            NumberAnimation {
                duration: root.reducedMotion ? 0 : root.transitionDuration
                easing.type: Easing.OutCubic
            }
        }
    }
}
