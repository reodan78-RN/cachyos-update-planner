// Aufklappfenster: Stand, Liste der anstehenden Updates und die zwei Aktionen.
// Paketnamen kommen von außen (AUR!) und werden deshalb immer als reiner Text angezeigt.
import QtQuick
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.plasma.components as PlasmaComponents

Item {
    id: view

    property string title: ""
    property var repo: []
    property var aur: []
    property var flatpak: []
    property double checked: 0
    property string error: ""
    property bool busy: false
    property bool german: true

    signal checkRequested()
    signal openRequested()

    // QMLs JavaScript kennt kein flatMap
    readonly property var rows: {
        const out = []
        for (const [source, items] of [["Repo", repo], ["AUR", aur], ["Flatpak", flatpak]])
            for (let i = 0; i < items.length; i++)    // die erste Zeile je Quelle trägt die Überschrift
                out.push({header: i === 0 ? source : "", name: items[i][0], version: items[i][1]})
        return out
    }

    implicitWidth: Kirigami.Units.gridUnit * 20
    implicitHeight: Kirigami.Units.gridUnit * 22
    Layout.minimumWidth: Kirigami.Units.gridUnit * 16
    Layout.minimumHeight: Kirigami.Units.gridUnit * 14

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Kirigami.Units.smallSpacing
        spacing: Kirigami.Units.smallSpacing

        RowLayout {
            Layout.fillWidth: true

            Kirigami.Heading {
                Layout.fillWidth: true
                level: 3
                text: view.title
                textFormat: Text.PlainText
                elide: Text.ElideRight
            }
            PlasmaComponents.BusyIndicator {
                visible: view.busy
                running: view.busy
                Layout.preferredWidth: Kirigami.Units.iconSizes.smallMedium
                Layout.preferredHeight: Kirigami.Units.iconSizes.smallMedium
            }
        }

        PlasmaComponents.Label {
            Layout.fillWidth: true
            visible: view.checked > 0
            opacity: 0.7
            textFormat: Text.PlainText
            text: (view.german ? "Zuletzt geprüft: " : "Last checked: ")
                  + new Date(view.checked * 1000).toLocaleString(Qt.locale(), Locale.ShortFormat)
        }

        PlasmaComponents.Label {
            Layout.fillWidth: true
            visible: view.error !== ""
            color: Kirigami.Theme.negativeTextColor
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            maximumLineCount: 4
            elide: Text.ElideRight
            text: view.error
        }

        PlasmaComponents.ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true

            ListView {
                clip: true
                model: view.rows
                delegate: ColumnLayout {
                    id: row
                    required property var modelData
                    width: ListView.view.width
                    spacing: 0

                    Kirigami.Heading {
                        visible: row.modelData.header !== ""
                        level: 5
                        text: row.modelData.header
                        textFormat: Text.PlainText
                        topPadding: Kirigami.Units.smallSpacing
                    }
                    RowLayout {
                        Layout.fillWidth: true

                        PlasmaComponents.Label {
                            Layout.fillWidth: true
                            text: row.modelData.name
                            textFormat: Text.PlainText
                            elide: Text.ElideRight
                        }
                        PlasmaComponents.Label {
                            Layout.maximumWidth: row.width * 0.45
                            text: row.modelData.version
                            textFormat: Text.PlainText
                            elide: Text.ElideRight
                            opacity: 0.7
                        }
                    }
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true

            PlasmaComponents.Button {
                icon.name: "view-refresh"
                text: view.german ? "Jetzt prüfen" : "Check now"
                enabled: !view.busy
                onClicked: view.checkRequested()
            }
            Item {
                Layout.fillWidth: true
            }
            PlasmaComponents.Button {
                icon.name: "update-planner"
                text: view.german ? "Planer öffnen" : "Open planner"
                onClicked: view.openRequested()
            }
        }
    }
}
