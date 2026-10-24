pragma ComponentBehavior: Bound
import QtQuick
import qs.Commons
import "../js/Model.js" as Model
import "../js/Contrast.js" as Contrast

// The card: the Glance (hover) and Usage · Cleanup · Trend · setup (click, D40).
// The Iron Rule: every string is a whole engine field (docs/card-contract.md); the only sums are pixels.
Item {
    id: hc
    // tabular figures, so numbers line up
    readonly property var tnum: ({
            "tnum": 1
        })

    property var doc: null                 // the engine's card, or null
    property string fallbackText: ""       // one line, only while there is no card
    property color foreground: Color.foreground
    property color surface: Color.popups.background   // what the card is painted on
    property string fontFamily: Style.font.family
    property int cardWidth: Style.space(408)   // + the popup's insets = 440 on screen
    property string page: "glance"         // glance (quick view) | trend | reclaim | used | setup | panel (full view)
    property bool full: false
    // Epoch seconds now and whether the Service has stalled (D10, D67), from
    // the owner's one clock
    property real nowSec: Date.now() / 1000
    property bool stalled: false

    readonly property bool live: doc !== null && doc.state === "ok"
    readonly property var glance: live ? doc.glance : null
    readonly property var level: live ? doc.level : null
    readonly property var trendLine: live && doc.trend ? doc.trend.line : null
    readonly property var trendDoc: live ? doc.trend : null
    readonly property var reclaim: live ? doc.reclaim : null
    readonly property var used: live ? doc.used : null
    // Service is scanning: the Reclaim and Used views say so instead
    property bool scanning: false
    // the Service's setup (D80): idle · running · cancelled · needs-admin ·
    // terminal · failed
    property string setupState: "idle"
    // What the card may show exactly (D76, D77), from the owner: the whole
    // card, or only what is exact with one line, or the setup panel.
    property var exactness: null
    readonly property var panel: exactness ? exactness.panel : null
    readonly property bool showTabs: exactness === null || exactness.full === true
    readonly property var themePalette: live ? doc.palette : null
    // Every warning, gravest first (D119), and the shared words (Back, Copy)
    readonly property var warnings: live && doc.warnings ? doc.warnings : []
    readonly property var words: live && doc.strings ? doc.strings : ({
            "back": "",
            "copy": "",
            "copied": ""
        })
    // A What now panel (D120): its id, and the page Back returns to
    property string panelId: ""
    property string backPage: "used"
    readonly property var panelDoc: live && doc.panels && panelId !== "" ? (doc.panels[panelId] || null) : null
    // The step whose command was just copied reads "Copied" for a moment
    property int copiedStep: -1
    Timer {
        id: copiedTimer
        interval: 1600
        onTriggered: hc.copiedStep = -1
    }
    // A warning's link: a panel opens here; anything else is the owner's
    function follow(link) {
        if (link.verb === "panel") {
            if (hc.page !== "panel")
                hc.backPage = hc.page;
            hc.panelId = link.panel;
            hc.copiedStep = -1;
            hc.page = "panel";
            return;
        }
        hc.actionRequested(link.verb, link.argv);
    }
    // Where the chart is on screen (IPC status: lets a test pointer hover it).
    function chartRect() {
        var p = trendChart.mapToGlobal(0, 0);
        return {
            "x": p.x,
            "y": p.y,
            "w": trendChart.width,
            "h": trendChart.height
        };
    }
    // The stalled line's words: the last step whose time has passed (D67:
    // "not updated since 14:54", then "…since yesterday 14:54", then a date).
    // A comparison of two engine epochs, as stale_after already is.
    readonly property string staleText: {
        let steps = glance ? glance.stale_steps : [];
        let text = "";
        for (let i = 0; i < steps.length; i++)
            if (nowSec >= steps[i].after)
                text = steps[i].text;
        return text;
    }

    // Secondary text: the dimmest mix of the text toward the surface that still
    // reads at 4.5:1 (Kanso Screen Time's audit C2: a plain darker shade was
    // louder than the text on light themes).
    readonly property color muted: hc.readable(foreground, 4.5, 0.4)
    // The Glance bar's free part: a neutral fill at 1.9:1 on the surface, so
    // it reads as "free", not as an empty track (D49).
    readonly property color freeFill: hc.readable(foreground, 1.9, 0.1)
    readonly property int rowHeight: Math.ceil(bodyMetrics.height) + Style.space(7)
    // The Trend rows' label column: as wide as the longest label (pixels).
    readonly property real trendLabelWidth: {
        let rows = trendDoc ? trendDoc.rows : [];
        let w = 0;
        for (let i = 0; i < rows.length; i++)
            w = Math.max(w, bodyMetrics.advanceWidth(rows[i].label));
        return Math.ceil(w);
    }

    function role(name) {
        return Model.colourOf(name, Color, hc.themePalette);
    }
    // A role for text: "muted" text takes the contrast-checked dim colour,
    // since the theme's muted token is meant for graphics and is too faint.
    function textRole(name) {
        return name === "muted" ? hc.muted : hc.role(name);
    }
    function faint(c, alpha) {
        return Qt.rgba(c.r, c.g, c.b, alpha);
    }
    // a colour, not data: the arithmetic ban is about sizes and time
    function opaque(c) {
        return Qt.rgba(c.r, c.g, c.b, 1);
    }
    function readable(fg, minRatio, from) {
        return hc.opaque(Contrast.readableMix(fg, hc.surface, minRatio, from));
    }

    width: parent ? parent.width : cardWidth
    implicitWidth: cardWidth
    // The full view is as tall as its tallest page plus the pinned bottom, so
    // switching tabs moves nothing.
    readonly property real pagesHeight: Math.max(trend_.implicitHeight, reclaim_.implicitHeight, used_.implicitHeight, panel_.implicitHeight)
    // the setup panel is as tall as itself, with its own footer (D80)
    implicitHeight: !full ? glance_.implicitHeight : page === "setup" ? setup_.implicitHeight : !showTabs ? (page === "panel" ? panel_.implicitHeight + bottomZone.implicitHeight : trend_.implicitHeight) : pagesHeight + bottomZone.implicitHeight

    FontMetrics {
        id: captionMetrics
        font.family: hc.fontFamily
        font.pixelSize: Style.font.caption
    }

    FontMetrics {
        id: bodyMetrics
        font.family: hc.fontFamily
        font.pixelSize: Style.font.body
    }

    FontMetrics {
        id: displayMetrics
        font.family: hc.fontFamily
        font.pixelSize: Style.font.display
        font.bold: true
    }

    component CardText: Text {
        property bool tabular: false
        textFormat: Text.PlainText
        color: hc.foreground
        font.family: hc.fontFamily
        font.pixelSize: Style.font.body
        font.features: tabular ? hc.tnum : ({})
    }

    component CardCaption: CardText {
        color: hc.muted
        font.pixelSize: Style.font.caption
    }

    // A hairline with a gap above and below it; `line` false keeps the room.
    component CardDivider: Item {
        id: divider
        property real above: Style.space(8)
        property real below: 0
        property bool line: true
        width: parent ? parent.width : hc.cardWidth
        height: above + 1 + below

        Rectangle {
            visible: divider.line
            y: divider.above
            width: parent.width
            height: 1
            color: hc.faint(hc.foreground, 0.12)
        }
    }

    // A small text link; `active` (the current tab) reads in the text colour,
    // underlined in accent (a graphic, which needs only 3:1).
    component CardLink: CardCaption {
        id: link
        signal activated
        property bool active: false
        property bool clickable: true
        property color restColour: hc.muted
        property color hoverColour: Color.accent
        color: link.active ? hc.foreground : linkArea.containsMouse ? link.hoverColour : link.restColour
        font.bold: link.active

        Rectangle {
            visible: link.active
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.bottom
            anchors.topMargin: 1
            height: 2
            radius: 1
            color: Color.accent
        }

        MouseArea {
            id: linkArea
            anchors.fill: parent
            anchors.margins: -Style.space(4)
            enabled: link.clickable
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: link.activated()
        }
    }

    // Usage · Cleanup · Trend (D61: nouns; the page ids stay used · reclaim · trend).
    component Tabs: Row {
        spacing: Style.space(6)

        Repeater {
            model: [
                {
                    "text": "Usage",
                    "page": "used"
                },
                {
                    "text": "Cleanup",
                    "page": "reclaim"
                },
                {
                    "text": "Trend",
                    "page": "trend"
                }
            ]

            Row {
                id: tab
                required property var modelData
                required property int index
                spacing: Style.space(6)

                CardCaption {
                    visible: tab.index > 0
                    text: "·"
                }
                CardLink {
                    text: tab.modelData.text
                    active: hc.page === tab.modelData.page
                    onActivated: hc.page = tab.modelData.page
                }
            }
        }
    }

    // The Glance's footer: accent while the review condition holds (D58);
    // nothing at the right, the card is passive (D49).
    component Footer: Column {
        id: footer
        property string leftText: ""
        property string leftColour: "muted"
        width: parent ? parent.width : hc.cardWidth

        CardDivider {}
        Item {
            width: parent.width
            height: Style.space(8) + Math.ceil(captionMetrics.height) + 3

            CardCaption {
                anchors.left: parent.left
                anchors.bottom: parent.bottom
                anchors.bottomMargin: 3
                text: footer.leftText
                color: footer.leftColour === "accent" ? Color.accent : hc.muted
                tabular: true
            }
        }
    }

    // The hero: "27.4" "GiB" · caption; at the right the level word (absent
    // while Fine) or a time. The Glance's strings are the defaults.
    component Hero: Item {
        id: hero
        property string number: hc.glance ? hc.glance.hero_text : ""
        property string unit: hc.glance ? hc.glance.hero_unit : ""
        property string caption: hc.glance ? hc.glance.hero_caption : ""
        property string rightText: hc.level ? hc.level.word : ""
        property color rightColour: hc.role(hc.level ? hc.level.colour : "muted")
        property bool rightIsWord: true
        // with no number (before the first scan) this muted line takes the
        // number's place: never a lone "—" (D68)
        property string emptyText: ""
        width: parent ? parent.width : hc.cardWidth
        // without a card the line wraps (an engine error must be read whole)
        height: hc.live ? Math.ceil(displayMetrics.height) : fallbackLine.implicitHeight

        CardText {
            id: fallbackLine
            visible: !hc.live
            width: parent.width
            text: hc.doc && !hc.live ? (hc.doc.message || "") : hc.fallbackText
            wrapMode: Text.Wrap
            maximumLineCount: 3
            elide: Text.ElideRight
        }

        CardText {
            visible: hc.live && hero.number === ""
            anchors.verticalCenter: parent.verticalCenter
            text: hero.emptyText
            color: hc.muted
        }

        Row {
            id: heroRow
            visible: hc.live && hero.number !== ""
            spacing: Style.space(10)

            CardText {
                id: heroNumber
                text: hero.number
                font.pixelSize: Style.font.display
                font.bold: true
                tabular: true
            }
            CardText {
                anchors.baseline: heroNumber.baseline
                text: hero.unit
                font.pixelSize: Style.font.display
                font.bold: true
            }
            CardCaption {
                anchors.baseline: heroNumber.baseline
                text: hero.caption
            }
        }

        CardCaption {
            visible: hc.live
            anchors.right: parent.right
            // on the hero number's baseline (pixels: the number is in the Row)
            y: heroRow.y + heroNumber.y + heroNumber.baselineOffset - baselineOffset
            text: hero.rightText
            color: hero.rightColour
            font.bold: hero.rightIsWord
            font.letterSpacing: hero.rightIsWord ? 1.5 : 0
        }
    }

    // Cleanup's and Usage's hero: their view's number, or the "never" line.
    component PageHero: Hero {
        required property var view
        number: view ? view.hero_text : ""
        emptyText: view ? (hc.scanning ? view.running_text : view.never_text) : ""
        unit: view ? view.hero_unit : ""
        caption: view ? view.hero_caption : ""
        rightText: view ? view.when_text : ""
        rightColour: hc.muted
        rightIsWord: false
    }

    // The whole disk (D49): used in the level's colour, a 2 px gap, then free in
    // a neutral fill at 1.9:1 on the surface (tests/contrast.test.js).
    component DiskBar: Column {
        id: diskBar
        readonly property var disk: hc.glance ? hc.glance.disk : null
        readonly property real used: disk ? Math.max(0, Math.min(1, disk.used)) : 0
        readonly property color usedColour: hc.role(disk ? disk.colour : "accent")
        readonly property real gap: used > 0 && used < 1 ? 2 : 0
        width: parent ? parent.width : hc.cardWidth
        spacing: Style.space(5)

        Item {
            width: parent.width
            height: 11

            Rectangle {
                id: usedPart
                visible: width >= 1
                width: (parent.width - diskBar.gap) * diskBar.used
                height: parent.height
                radius: 3
                color: diskBar.usedColour
            }
            Rectangle {
                visible: width >= 1
                x: usedPart.width + diskBar.gap
                width: parent.width - x
                height: parent.height
                radius: 3
                color: hc.freeFill
            }
        }

        Item {
            width: parent.width
            height: Math.ceil(captionMetrics.height)

            Row {
                spacing: Style.space(5)

                Rectangle {
                    anchors.verticalCenter: parent.verticalCenter
                    width: 7
                    height: 7
                    radius: 1.5
                    color: diskBar.usedColour
                }
                CardCaption {
                    text: diskBar.disk ? diskBar.disk.used_text : ""
                    tabular: true
                }
            }
            Row {
                anchors.right: parent.right
                spacing: Style.space(5)

                Rectangle {
                    anchors.verticalCenter: parent.verticalCenter
                    width: 7
                    height: 7
                    radius: 1.5
                    color: hc.freeFill
                }
                CardCaption {
                    text: diskBar.disk ? diskBar.disk.free_text : ""
                }
            }
        }
    }

    // A 3 px track with a share of it filled.
    component BarTrack: Rectangle {
        id: track
        property real fill: 0
        property color fillColour: Color.accent
        height: 3
        radius: 1.5
        color: hc.faint(hc.foreground, 0.12)

        Rectangle {
            width: parent.width * track.fill
            height: parent.height
            radius: 1.5
            color: track.fillColour
        }
    }

    // One row: its label, a muted qualifier (no "·" between, D62) and its
    // value, over an optional track; the caller sets the height.
    component BarRow: Item {
        id: barRow
        property string label: ""
        property string qualifier: ""
        property string value: ""
        property color labelColour: hc.foreground
        property color valueColour: hc.foreground
        property bool track: false
        property real fill: 0
        property color fillColour: Color.accent
        width: parent ? parent.width : hc.cardWidth

        Row {
            id: rowLabel
            y: Style.space(2)
            width: rowValue.x - Style.space(12)
            spacing: Style.space(10)
            clip: true

            CardText {
                id: rowName
                width: Math.min(implicitWidth, rowLabel.width)
                text: barRow.label
                color: barRow.labelColour
                elide: Text.ElideRight
            }
            CardText {
                visible: text !== ""
                width: Math.max(0, rowLabel.width - rowName.width - rowLabel.spacing)
                anchors.baseline: rowName.baseline
                text: barRow.qualifier
                color: hc.muted
                elide: Text.ElideRight
            }
        }
        CardText {
            id: rowValue
            x: parent.width - implicitWidth
            y: rowLabel.y
            text: barRow.value
            color: barRow.valueColour
            tabular: true
        }
        BarTrack {
            visible: barRow.track
            y: parent.height - 3
            width: parent.width
            fill: barRow.fill
            fillColour: barRow.fillColour
        }
    }

    // One warning (D119): the fact (urgent for a read-only disk and Critical or
    // worse), one sentence on what it means, and in the full view at most two
    // quiet links styled like the footer's.
    component Warning: Column {
        id: warn
        required property var warning
        property bool linked: hc.full
        // a link the page's footer already offers is not repeated (D131)
        property string footerAction: ""
        width: parent ? parent.width : hc.cardWidth
        topPadding: Style.space(5)
        spacing: Style.space(2)

        CardCaption {
            width: parent.width
            text: warn.warning.fact
            color: warn.warning.colour === "urgent" ? Color.urgent : hc.foreground
            elide: Text.ElideRight
        }
        // the Glance (passive) says what a click on the bar does instead (D131)
        CardCaption {
            width: parent.width
            text: warn.linked ? warn.warning.meaning : warn.warning.glance
            elide: Text.ElideRight
        }
        Row {
            visible: warn.linked && warn.warning.links.length > 0
            topPadding: Style.space(1)
            spacing: Style.space(14)

            Repeater {
                model: warn.warning.links

                CardLink {
                    required property var modelData
                    visible: modelData.text !== warn.footerAction
                    text: modelData.text
                    restColour: Color.accent
                    hoverColour: Qt.lighter(Color.accent, 1.08)
                    onActivated: hc.follow(modelData)
                }
            }
        }
    }

    // Every warning under a page's hero; the Glance (passive) has no links.
    component Warnings: Column {
        id: warns
        property bool linked: hc.full
        visible: hc.warnings.length > 0
        width: parent ? parent.width : hc.cardWidth
        // the links' row needs air above a page's first row
        bottomPadding: linked ? Style.space(6) : 0

        Repeater {
            model: hc.warnings

            Warning {
                required property var modelData
                warning: modelData
                linked: warns.linked
                footerAction: hc.full ? hc.pageFooter.action : ""
            }
        }
    }

    // Glance
    Column {
        id: glance_
        visible: !hc.full
        width: parent.width
        spacing: 0
        readonly property bool scanned: hc.glance !== null && hc.glance.scanned === true

        Hero {}

        Column {
            visible: hc.live
            width: parent.width

            // Only once the Service has stalled: when it last updated, in urgent
            // (D10, D67); a fresh Glance shows no time.
            CardCaption {
                visible: hc.stalled
                topPadding: Style.space(5)
                width: parent.width
                text: hc.staleText
                color: Color.urgent
                elide: Text.ElideRight
            }

            // Every warning: the fact and what it means (D119).
            Warnings {
                linked: false
            }

            DiskBar {
                topPadding: Style.space(12)
                bottomPadding: Style.space(12)
            }

            // The trend line: "▼ 1.1 GiB a day · Critical in 9–14 days" (D65).
            CardText {
                bottomPadding: Style.space(8)
                text: hc.trendLine ? hc.trendLine.text : ""
                color: hc.trendLine && hc.trendLine.style === "dim" ? hc.muted : hc.foreground
                font.bold: hc.trendLine !== null && hc.trendLine.style === "bold"
                tabular: true
            }

            // What changed since the user last looked, only past 1 GiB (D115).
            CardText {
                visible: text !== ""
                width: parent.width
                bottomPadding: Style.space(8)
                text: hc.glance ? hc.glance.change_text : ""
                color: hc.muted
                elide: Text.ElideRight
                tabular: true
            }

            // The ledger's three biggest rows, straight under the bar's key, no
            // title (D49, D61); none before the first scan, only "Measuring…"
            // while it runs (D67).
            CardText {
                visible: !glance_.scanned && hc.scanning
                topPadding: Style.space(2)
                text: hc.glance ? hc.glance.running_text : ""
                color: hc.muted
            }

            Repeater {
                model: hc.glance && glance_.scanned ? hc.glance.where_rows : []

                BarRow {
                    required property var modelData
                    height: hc.rowHeight
                    label: modelData.label
                    value: modelData.text
                }
            }

            Footer {
                visible: glance_.scanned
                leftText: hc.glance ? hc.glance.footer_text : ""
                leftColour: hc.glance ? hc.glance.footer_colour : "muted"
            }

            // Not exact yet (D77): one muted line in place of the rows and the
            // footer ("One-time setup needed · click the drive icon").
            Footer {
                visible: !glance_.scanned && !hc.scanning && leftText !== ""
                leftText: hc.exactness ? hc.exactness.line : ""
            }
        }
    }

    // Trend
    Column {
        id: trend_
        visible: hc.full && hc.page === "trend"
        width: parent.width
        spacing: 0

        Hero {}

        Column {
            visible: hc.live
            width: parent.width

            Warnings {}

            CardCaption {
                topPadding: Style.space(5)
                bottomPadding: Style.space(12)
                width: parent.width
                text: hc.trendDoc ? hc.trendDoc.sub_text : ""
            }

            TrendChart {
                id: trendChart
                width: parent.width
                height: Style.space(190)
                chart: hc.live ? hc.doc.chart : null
                foreground: hc.foreground
                muted: hc.muted
                surface: hc.surface
                fontFamily: hc.fontFamily
                colourOf: hc.role
                faint: hc.faint
            }

            Item {
                width: parent.width
                height: Style.space(8)
            }

            // Trend · Critical · Last hour · Big changes: label, value, note (D65).
            Repeater {
                model: hc.trendDoc ? hc.trendDoc.rows : []

                Item {
                    id: kv
                    required property var modelData
                    width: trend_.width
                    // a big change's breakdown wraps under itself (D118)
                    height: Math.max(hc.rowHeight, kvNote.y + kvNote.height + Style.space(4))

                    CardText {
                        id: kvLabel
                        y: Style.space(2)
                        text: kv.modelData.label
                        color: hc.muted
                    }
                    // the longest label plus the gap the other rows use (D68)
                    CardText {
                        id: kvValue
                        x: hc.trendLabelWidth + Style.space(12)
                        y: kvLabel.y
                        text: kv.modelData.value
                        tabular: true
                    }
                    CardText {
                        id: kvNote
                        visible: text !== ""
                        x: kvValue.x + kvValue.width + Style.space(6)
                        y: kvLabel.y
                        width: kv.width - x
                        text: kv.modelData.note
                        wrapMode: Text.WordWrap
                        // "fast" is urgent; every other note muted (D65)
                        color: hc.textRole(kv.modelData.note_colour || "muted")
                        tabular: true
                    }
                }
            }
        }
    }

    // Cleanup (page id "reclaim")
    Column {
        id: reclaim_
        visible: hc.full && hc.page === "reclaim"
        width: parent.width
        spacing: 0

        PageHero {
            view: hc.reclaim
        }

        // Every warning, with its links (D119).
        Warnings {}

        // While Service rescans: one line (before the first scan the hero
        // says it, D68).
        CardText {
            visible: hc.live && hc.scanning && hc.reclaim.state === "ok"
            topPadding: Style.space(5)
            text: hc.reclaim ? hc.reclaim.running_text : ""
            color: hc.muted
        }

        Repeater {
            model: hc.reclaim && !hc.scanning ? hc.reclaim.groups : []

            Column {
                id: group
                required property var modelData
                width: reclaim_.width
                topPadding: Style.space(12)
                spacing: Style.space(2)

                // the snapshots sit below a hairline, not under a title (D62)
                CardDivider {
                    visible: group.modelData.hairline === true
                    above: 0
                }

                Item {
                    visible: group.modelData.title !== "" || group.modelData.total_text !== ""
                    width: parent.width
                    height: Math.ceil(captionMetrics.height) + Style.space(4)

                    CardCaption {
                        anchors.left: parent.left
                        anchors.bottom: parent.bottom
                        text: group.modelData.title
                        font.letterSpacing: 1.5
                    }
                    CardText {
                        anchors.right: parent.right
                        anchors.bottom: parent.bottom
                        text: group.modelData.total_text
                        tabular: true
                    }
                }

                Repeater {
                    model: group.modelData.rows

                    BarRow {
                        required property var modelData
                        width: group.width
                        height: hc.rowHeight + 3
                        label: modelData.label
                        qualifier: modelData.qualifier
                        value: modelData.text
                        labelColour: modelData.style === "dim" ? hc.muted : hc.foreground
                        valueColour: modelData.style === "row" ? hc.foreground : hc.muted
                        // the bar on the page's largest row's scale
                        track: modelData.style === "row"
                        fill: modelData.bar
                    }
                }
            }
        }
    }

    // Usage (page id "used")
    Column {
        id: used_
        visible: hc.full && hc.page === "used"
        width: parent.width
        spacing: 0

        PageHero {
            view: hc.used
        }

        Warnings {}

        CardText {
            visible: hc.live && text !== ""
            topPadding: Style.space(5)
            width: parent.width
            // before the first scan the hero says it (D68)
            text: hc.used && hc.used.state === "ok" ? (hc.scanning ? hc.used.running_text : hc.used.sub_text) : ""
            color: hc.used && !hc.scanning ? hc.textRole(hc.used.sub_colour) : hc.muted
            font.pixelSize: hc.scanning ? Style.font.body : Style.font.caption
            elide: Text.ElideRight
        }

        Item {
            visible: hc.live
            width: parent.width
            height: Style.space(6)
        }

        Repeater {
            model: hc.used && !hc.scanning ? hc.used.rows : []

            Column {
                id: urow
                required property var modelData
                width: used_.width
                spacing: 0

                // a dim row (metadata, reserved, the remainder) has no bar and
                // no track (D68); the bar is a true share of the disk
                BarRow {
                    height: urow.modelData.dim ? hc.rowHeight - Style.space(4) : hc.rowHeight + 3
                    label: urow.modelData.label
                    value: urow.modelData.text
                    labelColour: urow.modelData.dim ? hc.muted : hc.foreground
                    valueColour: urow.modelData.dim ? hc.muted : hc.foreground
                    track: !urow.modelData.dim
                    fill: urow.modelData.bar
                    fillColour: hc.role(urow.modelData.colour)
                }

                CardCaption {
                    visible: text !== ""
                    leftPadding: Style.space(18)
                    topPadding: Style.space(3)
                    bottomPadding: Style.space(2)
                    width: parent.width
                    text: urow.modelData.sub
                    elide: Text.ElideRight
                    tabular: true
                }
            }
        }
    }

    // A What now panel (D120): the pages' header with ‹ Back, at most three
    // short steps, a command beside a step with Copy.
    Column {
        id: panel_
        visible: hc.full && hc.page === "panel"
        width: parent.width
        spacing: 0

        Item {
            width: parent.width
            height: panelHero.height

            Hero {
                id: panelHero
                number: hc.panelDoc ? hc.panelDoc.title : ""
                unit: ""
                caption: hc.panelDoc ? hc.panelDoc.caption : ""
                rightText: ""
            }
            CardLink {
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                text: hc.words.back
                restColour: Color.accent
                hoverColour: Qt.lighter(Color.accent, 1.08)
                onActivated: hc.page = hc.backPage
            }
        }

        Item {
            width: parent.width
            height: Style.space(6)
        }

        Repeater {
            model: hc.panelDoc ? hc.panelDoc.steps : []

            Column {
                id: stepRow
                required property var modelData
                required property int index
                width: panel_.width
                topPadding: Style.space(6)
                spacing: Style.space(3)

                Row {
                    width: parent.width
                    spacing: Style.space(8)

                    CardText {
                        id: stepNumber
                        text: String(stepRow.index + 1)
                        color: hc.muted
                        tabular: true
                    }
                    CardText {
                        width: parent.width - stepNumber.width - parent.spacing
                        text: stepRow.modelData.text
                        elide: Text.ElideRight
                    }
                }
                Item {
                    visible: stepRow.modelData.command !== ""
                    width: parent.width
                    height: Math.ceil(bodyMetrics.height)

                    Row {
                        x: stepNumber.width + Style.space(8)
                        width: copyLink.x - x - Style.space(12)
                        spacing: Style.space(7)
                        clip: true

                        CardText {
                            text: stepRow.modelData.command
                            font.bold: true
                        }
                        // what the user types after the copied words (D131)
                        CardText {
                            visible: text !== ""
                            text: stepRow.modelData.placeholder
                            color: hc.muted
                        }
                    }
                    CardLink {
                        id: copyLink
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        text: hc.copiedStep === stepRow.index ? hc.words.copied : hc.words.copy
                        restColour: Color.accent
                        hoverColour: Qt.lighter(Color.accent, 1.08)
                        onActivated: {
                            hc.actionRequested("copy", stepRow.modelData.copy_argv);
                            hc.copiedStep = stepRow.index;
                            copiedTimer.restart();
                        }
                    }
                }
            }
        }

        Item {
            width: parent.width
            height: Style.space(8)
        }
    }

    // Setup (D80)
    readonly property bool setupBusy: hc.setupState === "running" || (hc.panel !== null && hc.panel.busy === true)
    readonly property var setupMessage: {
        const p = hc.panel;
        if (!p)
            return {
                "text": "",
                "urgent": false
            };
        const urgent = {
            "cancelled": p.cancelled,
            "needs-admin": p.needs_admin,
            "failed": p.failed
        }[hc.setupState];
        if (urgent && !hc.setupBusy)
            return {
                "text": urgent,
                "urgent": true
            };
        return {
            "text": hc.setupState === "terminal" && !hc.setupBusy ? p.terminal : p.note,
            "urgent": false
        };
    }

    Column {
        id: setup_
        visible: hc.full && hc.page === "setup"
        width: parent.width
        spacing: 0

        CardText {
            bottomPadding: Style.space(10)
            width: parent.width
            text: hc.panel ? hc.panel.title : ""
            font.pixelSize: Style.font.heading
            font.bold: true
        }
        CardText {
            bottomPadding: Style.space(10)
            width: parent.width
            text: hc.panel ? hc.panel.body : ""
            wrapMode: Text.Wrap
            lineHeight: 1.15
        }
        CardCaption {
            width: parent.width
            text: hc.setupMessage.text
            color: hc.setupMessage.urgent ? Color.urgent : hc.muted
            wrapMode: Text.Wrap
        }
        CardDivider {
            above: Style.space(12)
            below: Style.space(8)
        }
        Item {
            width: parent.width
            height: setupButton.height

            CardLink {
                visible: !hc.setupBusy
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                text: hc.panel ? hc.panel.not_now : ""
                hoverColour: hc.foreground
                onActivated: hc.actionRequested("not-now", [])
            }
            CardCaption {
                visible: hc.setupBusy
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                text: hc.panel ? hc.panel.running : ""
            }
            Rectangle {
                id: setupButton
                // not for a user who cannot authorize it: only Not now (D81)
                visible: !hc.setupBusy && hc.setupState !== "needs-admin"
                anchors.right: parent.right
                // the stock Button's padding and corner (Ui/Button.qml); filled,
                // which that Button does not paint
                width: setupLabel.implicitWidth + Style.spacing.controlPaddingX * 2
                height: setupLabel.implicitHeight + Style.spacing.controlPaddingY * 2
                radius: Style.cornerRadius
                color: setupArea.containsMouse ? Qt.lighter(Color.accent, 1.08) : Color.accent

                CardText {
                    id: setupLabel
                    anchors.centerIn: parent
                    text: hc.panel ? hc.panel.button : ""
                    // the surface where it reads at 4.5:1 on the accent, else
                    // white or black (tests/contrast.test.js)
                    color: hc.opaque(Contrast.labelOn(Color.accent, hc.surface, 4.5))
                    font.bold: true
                }
                MouseArea {
                    id: setupArea
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: hc.actionRequested("setup", [])
                }
            }
        }
        Item {
            width: parent.width
            height: 3
        }
    }

    // The full view's pinned bottom: its rows keep their height on every page.
    // The engine's footer for the page, idle or while Service scans (D94).
    readonly property var pageView: hc.page === "reclaim" ? hc.reclaim : hc.page === "used" ? hc.used : null
    readonly property var pageFooter: hc.page === "panel" && hc.panelDoc ? hc.panelDoc.footer : pageView ? (hc.scanning ? pageView.scanning_footer : pageView.footer) : {
        "left": "",
        "action": "",
        "verb": "",
        "argv": []
    }
    // A footer with nothing to say draws no hairline; its room stays, so the
    // tabs keep their place on every page.
    readonly property bool footerShown: pageFooter.left !== "" || pageFooter.action !== ""
    signal actionRequested(string verb, var argv)

    Column {
        id: bottomZone
        // nothing to pin where only Trend shows (D77): no tabs, no action;
        // a panel keeps its footer there too (D120)
        visible: hc.full && hc.page !== "setup" && (hc.showTabs || hc.page === "panel")
        width: parent.width
        y: hc.height - implicitHeight

        // The agent report's detail when it is totals only (D127).
        CardCaption {
            visible: text !== ""
            width: parent.width
            bottomPadding: Style.space(4)
            text: hc.page === "reclaim" && hc.reclaim ? hc.reclaim.mode_text : ""
            elide: Text.ElideRight
        }
        // The last cleanup's gap, named once (D116).
        CardCaption {
            visible: text !== ""
            width: parent.width
            bottomPadding: Style.space(6)
            text: hc.page === "reclaim" && hc.reclaim && !hc.scanning ? hc.reclaim.receipt_text : ""
            elide: Text.ElideRight
            tabular: true
        }
        CardDivider {
            line: hc.footerShown
        }
        Item {
            width: parent.width
            height: Style.space(8) + Math.ceil(captionMetrics.height)

            CardCaption {
                anchors.left: parent.left
                anchors.right: footerAction.left
                anchors.rightMargin: Style.space(12)
                anchors.bottom: parent.bottom
                text: hc.pageFooter.left
                elide: Text.ElideRight
                tabular: true
            }
            CardLink {
                id: footerAction
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                text: hc.pageFooter.action
                clickable: hc.pageFooter.verb !== ""
                restColour: Color.accent
                // the Set up button's hover: a lighter accent
                hoverColour: Qt.lighter(Color.accent, 1.08)
                onActivated: hc.actionRequested(hc.pageFooter.verb, hc.pageFooter.argv)
            }
        }
        // only what is exact (D77): the Trend page alone, no tabs
        Item {
            width: parent.width
            height: hc.showTabs ? Style.space(10) + Math.ceil(captionMetrics.height) + 3 : 0

            Tabs {
                visible: hc.live && hc.showTabs
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                anchors.bottomMargin: 3
            }
        }
    }
}
