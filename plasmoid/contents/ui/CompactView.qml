// Symbol in der Leiste bzw. auf der Arbeitsfläche, mit roter Zahl wie beim Tray-Symbol.
import QtQuick
import org.kde.kirigami as Kirigami

MouseArea {
    id: view
    property int count: 0

    Kirigami.Icon {
        id: icon
        anchors.fill: parent
        source: "update-planner"
        fallback: "system-software-update"
    }

    Rectangle {
        visible: view.count > 0
        anchors.top: parent.top
        anchors.right: parent.right
        height: Math.round(Math.min(parent.width, parent.height) * 0.55)
        width: Math.max(height, label.implicitWidth + height * 0.3)
        radius: height / 2
        color: "#e5484d"

        Text {
            id: label
            anchors.centerIn: parent
            text: view.count > 99 ? "99+" : view.count
            color: "white"
            font.bold: true
            font.pixelSize: Math.max(8, Math.round(parent.height * 0.7))
        }
    }
}
