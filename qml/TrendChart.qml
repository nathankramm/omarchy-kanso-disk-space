pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Shapes
import qs.Commons
import "../js/Model.js" as Model

// The Trend chart from the engine's 0..1 `chart` (x: 90 days ago to the horizon):
// every position is a 0..1 value times a pixel size, every label an engine string.
Item {
    id: tc
    // tabular figures, so numbers line up
    readonly property var tnum: ({
            "tnum": 1
        })

    property var chart: null
    property color foreground: Color.foreground
    property color muted: Color.muted
    property color surface: Color.popups.background
    property string fontFamily: Style.font.family
    property var colourOf: function (role) {
        return tc.foreground;
    }
    // the card's faint(colour, alpha), passed in as colourOf is
    required property var faint

    readonly property var points: chart ? chart.points : []
    readonly property int labelSize: Style.font.caption - 1
    readonly property real plotH: height - axisH
    readonly property real axisH: Math.ceil(axisMetrics.height) + Style.space(6)

    function px(x) {
        return x * tc.width;
    }
    function py(y) {
        return tc.plotH - y * tc.plotH;
    }

    FontMetrics {
        id: axisMetrics
        font.family: tc.fontFamily
        font.pixelSize: tc.labelSize
    }

    component ChartText: Text {
        property bool tabular: false
        textFormat: Text.PlainText
        color: tc.muted
        font.family: tc.fontFamily
        font.pixelSize: tc.labelSize
        font.features: tabular ? tc.tnum : ({})
    }

    // The update-blocked band: 0 up to 10 GiB, faint urgent
    Rectangle {
        visible: tc.chart !== null
        x: 0
        y: tc.py(tc.chart ? tc.chart.band.y : 0)
        width: tc.width
        height: tc.plotH - y
        color: tc.faint(Color.urgent, 0.14)

        ChartText {
            // only where the band can hold it: on a roomy disk it is a sliver
            visible: parent.height >= implicitHeight
            x: Style.space(4)
            anchors.verticalCenter: parent.verticalCenter
            text: tc.chart ? tc.chart.band.text : ""
            color: Color.urgent
        }
    }

    // The level lines: solid hairlines, labelled at the left
    // A label sits above its line; where lines crowd (a roomy disk puts 50,
    // 30 and 15 GiB a few pixels apart) a label is drawn only when it clears
    // the one above it. The lines always draw. Pixels only, no data maths.
    readonly property real labelH: Math.ceil(axisMetrics.height) + 1
    readonly property var labelShown: {
        var out = [], lastY = -1e9;
        var lines = tc.chart ? tc.chart.lines : [];
        for (let i = 0; i < lines.length; i++) {
            let y = tc.py(lines[i].y);
            let room = y - lastY >= tc.labelH && y - tc.labelH >= 0;
            out.push(room);
            if (room)
                lastY = y;
        }
        return out;
    }

    Repeater {
        model: tc.chart ? tc.chart.lines : []

        Item {
            id: level
            required property var modelData
            required property int index
            readonly property color tint: tc.colourOf(level.modelData.colour)
            y: tc.py(level.modelData.y)
            width: tc.width

            Rectangle {
                width: parent.width
                height: 1
                color: tc.faint(level.tint, 0.55)
            }
            ChartText {
                visible: tc.labelShown[level.index] === true
                x: Style.space(4)
                y: -implicitHeight - 1
                text: level.modelData.text
                color: level.tint
            }
        }
    }

    // Free space: a soft accent fade under an accent line
    Shape {
        anchors.fill: parent
        preferredRendererType: Shape.CurveRenderer
        visible: tc.points.length > 1

        ShapePath {
            strokeWidth: -1
            fillGradient: LinearGradient {
                x1: 0
                y1: 0
                x2: 0
                y2: tc.plotH
                GradientStop {
                    position: 0
                    color: tc.faint(Color.accent, 0.28)
                }
                GradientStop {
                    position: 1
                    color: tc.faint(Color.accent, 0.0)
                }
            }
            startX: tc.points.length ? tc.px(tc.points[0].x) : 0
            startY: tc.plotH
            PathPolyline {
                path: tc.areaPath
            }
        }

        ShapePath {
            strokeWidth: 1.5
            strokeColor: Color.accent
            fillColor: "transparent"
            joinStyle: ShapePath.RoundJoin
            capStyle: ShapePath.RoundCap
            PathPolyline {
                path: tc.linePath
            }
        }
    }

    readonly property var linePath: {
        var out = [];
        for (let i = 0; i < tc.points.length; i++)
            out.push(Qt.point(tc.px(tc.points[i].x), tc.py(tc.points[i].y)));
        return out;
    }
    readonly property var areaPath: {
        if (tc.points.length < 2)
            return [];
        var out = [Qt.point(tc.px(tc.points[0].x), tc.plotH)].concat(tc.linePath);
        out.push(Qt.point(tc.px(tc.points[tc.points.length - 1].x), tc.plotH));
        return out;
    }

    // A single sample has no line to draw: a dot (D51).
    Rectangle {
        visible: tc.points.length === 1
        width: 5
        height: 5
        radius: 2.5
        color: Color.accent
        x: tc.points.length === 1 ? tc.px(tc.points[0].x) - 2.5 : 0
        y: tc.points.length === 1 ? tc.py(tc.points[0].y) - 2.5 : 0
    }

    // No trend yet: one muted line from the engine says why, in the empty
    // part of the chart, never over drawn samples (D51, D131).
    ChartText {
        id: noteText
        readonly property var place: Model.notePlace(tc.points, width, height, tc.width, tc.plotH, Style.space(8))
        visible: text !== ""
        x: place.x
        y: place.y
        width: Math.min(implicitWidth, tc.width - Style.space(16))
        horizontalAlignment: Text.AlignHCenter
        text: tc.chart ? tc.chart.note_text : ""
        font.pixelSize: Style.font.caption
        elide: Text.ElideRight
    }

    // The forecast: the only dashed line, to its dot and date
    Shape {
        anchors.fill: parent
        preferredRendererType: Shape.CurveRenderer
        visible: tc.chart !== null && tc.chart.forecast !== null

        ShapePath {
            strokeWidth: 1.5
            strokeColor: Color.accent
            strokeStyle: ShapePath.DashLine
            dashPattern: [3, 3]
            fillColor: "transparent"
            startX: tc.chart && tc.chart.forecast ? tc.px(tc.chart.forecast.from.x) : 0
            startY: tc.chart && tc.chart.forecast ? tc.py(tc.chart.forecast.from.y) : 0
            PathLine {
                x: tc.chart && tc.chart.forecast ? tc.px(tc.chart.forecast.to.x) : 0
                y: tc.chart && tc.chart.forecast ? tc.py(tc.chart.forecast.to.y) : 0
            }
        }
    }

    Rectangle {
        id: forecastDot
        visible: tc.chart !== null && tc.chart.forecast !== null && tc.chart.forecast.dot
        width: 6
        height: 6
        radius: 3
        color: Color.urgent
        x: tc.chart && tc.chart.forecast ? tc.px(tc.chart.forecast.to.x) - 3 : 0
        y: tc.chart && tc.chart.forecast ? tc.py(tc.chart.forecast.to.y) - 3 : 0
    }

    ChartText {
        visible: forecastDot.visible
        // above the dot, kept inside the chart
        x: Math.min(tc.width - implicitWidth, forecastDot.x + forecastDot.width / 2 - implicitWidth / 2)
        y: forecastDot.y - implicitHeight - Style.space(2)
        text: tc.chart && tc.chart.forecast ? tc.chart.forecast.label : ""
        color: tc.foreground
    }

    // The x axis: jump ticks and three month labels
    Rectangle {
        y: tc.plotH
        width: tc.width
        height: 1
        color: tc.faint(tc.foreground, 0.12)
    }

    Repeater {
        model: tc.chart ? tc.chart.jumps : []

        Rectangle {
            required property var modelData
            x: tc.px(modelData.x)
            y: tc.plotH - Style.space(3)
            width: 1
            height: Style.space(6)
            color: tc.muted
        }
    }

    Repeater {
        model: tc.chart ? tc.chart.months : []

        ChartText {
            required property var modelData
            x: Math.max(0, Math.min(tc.width - implicitWidth, tc.px(modelData.x) - implicitWidth / 2))
            y: tc.plotH + Style.space(5)
            text: modelData.text
        }
    }

    // Hover: crosshair, a ringed dot and the point's own label
    property int hoverIndex: -1
    readonly property var hovered: hoverIndex >= 0 && hoverIndex < points.length ? points[hoverIndex] : null

    // The nearest point the engine gave, by pixel x.
    function nearest(mouseX) {
        var best = -1, bestD = 1e9;
        for (let i = 0; i < tc.points.length; i++) {
            let d = Math.abs(tc.px(tc.points[i].x) - mouseX);
            if (d < bestD) {
                bestD = d;
                best = i;
            }
        }
        return best;
    }

    MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        onPositionChanged: mouse => tc.hoverIndex = tc.nearest(mouse.x)
        onExited: tc.hoverIndex = -1
    }

    Rectangle {
        visible: tc.hovered !== null
        x: tc.hovered ? Math.round(tc.px(tc.hovered.x)) : 0
        width: 1
        height: tc.plotH
        color: tc.faint(tc.foreground, 0.35)
    }

    Rectangle {
        visible: tc.hovered !== null
        width: 7
        height: 7
        radius: 3.5
        color: tc.surface
        border.width: 1.5
        border.color: tc.foreground
        x: tc.hovered ? tc.px(tc.hovered.x) - 3.5 : 0
        y: tc.hovered ? tc.py(tc.hovered.y) - 3.5 : 0
    }

    Rectangle {
        id: readout
        visible: tc.hovered !== null
        readonly property real gap: Style.space(6)
        readonly property real at: tc.hovered ? tc.px(tc.hovered.x) : 0
        width: readoutText.implicitWidth + Style.space(12)
        height: readoutText.implicitHeight + Style.space(6)
        radius: 3
        color: tc.faint(tc.foreground, 0.1)
        // right of the crosshair, or left of it near the right edge
        x: at + gap + width <= tc.width ? at + gap : at - gap - width
        y: Style.space(4)

        ChartText {
            id: readoutText
            anchors.centerIn: parent
            text: tc.hovered ? tc.hovered.label : ""
            color: tc.foreground
            tabular: true
        }
    }
}
