// Plasma-6-Miniprogramm des Update Planners. Die Prüfung selbst macht `update-planner-tray --status`;
// hier wird nur angezeigt. Die Befehle sind fest – es wandern keine Daten in eine Shell.
import QtQuick
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.plasmoid
import org.kde.plasma.plasma5support as P5Support

PlasmoidItem {
    id: root

    readonly property string statusCommand: '"$HOME/.local/bin/update-planner-tray" --status'
    readonly property string forceCommand: statusCommand + ' --force'
    readonly property string openCommand: 'kstart --application update-planner'
    readonly property bool german: Qt.locale().name.startsWith("de")

    property var repo: []
    property var aur: []
    property var flatpak: []
    property double checked: 0          // Unix-Zeit der letzten erfolgreichen Prüfung, 0 = noch keine
    property string error: ""
    property bool busy: false

    readonly property int count: repo.length + aur.length + flatpak.length
    readonly property string title: {
        if (checked === 0)
            return busy ? tr("Prüfe auf Updates …", "Checking for updates …")
                        : error ? tr("Prüfung fehlgeschlagen", "Check failed") : "Update Planner"
        if (count === 0)
            return tr("Keine Updates", "No updates")
        return count === 1 ? tr("1 Update verfügbar", "1 update available")
                           : tr(count + " Updates verfügbar", count + " updates available")
    }

    function tr(de, en) {
        return german ? de : en
    }

    function refresh(force) {
        if (busy)
            return
        busy = true
        runner.connectSource(force ? forceCommand : statusCommand)
    }

    function openPlanner() {
        runner.connectSource(openCommand)
        root.expanded = false
    }

    function handle(exitCode, stdout, stderr) {
        busy = false
        if (exitCode === 127) {
            error = tr("Update Planner ist nicht installiert (packaging/install-desktop.sh).",
                       "Update Planner is not installed (packaging/install-desktop.sh).")
            return
        }
        try {
            const data = JSON.parse(stdout)
            repo = data.repo
            aur = data.aur
            flatpak = data.flatpak
            checked = data.checked
            error = data.error || ""
        } catch (e) {
            error = stderr.trim().slice(-200) || tr("Unlesbare Antwort", "Unreadable answer")
        }
    }

    P5Support.DataSource {
        id: runner
        engine: "executable"
        connectedSources: []
        onNewData: (source, data) => {
            disconnectSource(source)    // einmalig ausführen
            if (source !== root.openCommand)
                root.handle(data["exit code"], data.stdout, data.stderr)
        }
    }

    // Häufig nachfragen ist billig: geprüft wird nur, wenn der Stand veraltet ist oder pacman etwas getan hat
    Timer {
        interval: 2 * 60 * 1000
        running: true
        repeat: true
        triggeredOnStart: true
        onTriggered: root.refresh(false)
    }

    onExpandedChanged: if (root.expanded) refresh(false)

    Plasmoid.icon: "update-planner"
    toolTipMainText: "Update Planner"
    toolTipSubText: error && checked !== 0 ? title + "\n" + tr("Letzte Prüfung fehlgeschlagen", "Last check failed") : title

    Plasmoid.contextualActions: [
        PlasmaCore.Action {
            text: root.tr("Update Planner öffnen", "Open Update Planner")
            icon.name: "update-planner"
            onTriggered: root.openPlanner()
        },
        PlasmaCore.Action {
            text: root.tr("Jetzt prüfen", "Check now")
            icon.name: "view-refresh"
            enabled: !root.busy
            onTriggered: root.refresh(true)
        }
    ]

    compactRepresentation: CompactView {
        count: root.count
        onClicked: root.expanded = !root.expanded
    }

    fullRepresentation: FullView {
        title: root.title
        repo: root.repo
        aur: root.aur
        flatpak: root.flatpak
        checked: root.checked
        error: root.error
        busy: root.busy
        german: root.german
        onCheckRequested: root.refresh(true)
        onOpenRequested: root.openPlanner()
    }
}
