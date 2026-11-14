/**
 * LineChart - a minimal line/area chart built directly on react-native-svg.
 *
 * Why hand-built instead of a chart library:
 *  - Full control over the premium/minimal visual style this app targets.
 *  - Only one native dependency (react-native-svg), which ships inside Expo Go.
 *  - No Skia / new-architecture compatibility risk on React Native 0.88.
 *
 * Used for: daily & weekly screen-time trends, actual-vs-predicted regression
 * output, and risk-score history.
 */

import { useState } from 'react';
import {
  StyleSheet,
  Text,
  View,
  type GestureResponderEvent,
  type LayoutChangeEvent,
} from 'react-native';
import Svg, {
  Circle,
  Defs,
  LinearGradient,
  Path,
  Stop,
  Line as SvgLine,
} from 'react-native-svg';

import { Radius, Spacing, Typography } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';

export type LineChartProps = {
  /** Y values in data order. Needs at least 2 points to draw a line. */
  data: number[];
  /** Chart height in px. Width is measured from the parent container. */
  height?: number;
  /** Stroke colour of the line. */
  color?: string;
  /** Fill a soft gradient under the line. */
  showArea?: boolean;
  /** Draw a dot on each data point. */
  showDots?: boolean;
  /** Number of horizontal gridlines (0 disables). */
  gridLines?: number;
  /** Colour for gridlines. */
  gridColor?: string;
  /** Pin the Y axis to 0 instead of the data minimum. */
  startAtZero?: boolean;
  /** Unique id, required so multiple gradients on one screen don't collide. */
  gradientId?: string;
  /**
   * One label per point (usually a date). When given, touching or dragging
   * across the chart shows that point's label and value.
   */
  labels?: string[];
  /** How a value is written in the touch read-out. */
  formatValue?: (value: number, index: number) => string;
};

const TOOLTIP_W = 132;

/** Inner padding so strokes and dots are not clipped at the edges. */
const PAD = { top: 10, right: 6, bottom: 10, left: 6 };

/**
 * Builds a smooth path from cubic Bezier segments with horizontal control
 * points. Looks better than straight segments for usage trends, and because
 * both control points sit on the segment's vertical midline the curve cannot
 * overshoot in X.
 */
function buildSmoothPath(points: { x: number; y: number }[]): string {
  if (points.length === 0) return '';
  if (points.length === 1) return `M ${points[0].x} ${points[0].y}`;

  let d = `M ${points[0].x} ${points[0].y}`;
  for (let i = 0; i < points.length - 1; i++) {
    const p = points[i];
    const next = points[i + 1];
    const midX = (p.x + next.x) / 2;
    d += ` C ${midX} ${p.y}, ${midX} ${next.y}, ${next.x} ${next.y}`;
  }
  return d;
}

export function LineChart({
  data,
  height = 180,
  color = '#3C87F7',
  showArea = true,
  showDots = false,
  gridLines = 4,
  gridColor = 'rgba(128,128,128,0.15)',
  startAtZero = false,
  gradientId = 'lineChartFill',
  labels,
  formatValue = (v) => v.toFixed(1),
}: LineChartProps) {
  const theme = useTheme();
  const [width, setWidth] = useState(0);
  const [active, setActive] = useState<number | null>(null);

  const onLayout = (e: LayoutChangeEvent) => {
    const w = e.nativeEvent.layout.width;
    // Only update on a real change to avoid a render loop.
    if (Math.abs(w - width) > 0.5) setWidth(w);
  };

  // Render the measuring container first; we need a width before we can plot.
  const canPlot = width > 0 && data.length >= 2;

  const innerW = Math.max(width - PAD.left - PAD.right, 0);
  const innerH = Math.max(height - PAD.top - PAD.bottom, 0);

  let linePath = '';
  let areaPath = '';
  let points: { x: number; y: number }[] = [];

  if (canPlot) {
    const rawMin = Math.min(...data);
    const rawMax = Math.max(...data);
    const min = startAtZero ? Math.min(0, rawMin) : rawMin;
    const max = rawMax;
    // Guard against a flat series, which would divide by zero.
    const span = max - min || 1;

    points = data.map((value, i) => ({
      x: PAD.left + (data.length === 1 ? innerW / 2 : (i / (data.length - 1)) * innerW),
      y: PAD.top + innerH - ((value - min) / span) * innerH,
    }));

    linePath = buildSmoothPath(points);
    const baselineY = PAD.top + innerH;
    areaPath = `${linePath} L ${points[points.length - 1].x} ${baselineY} L ${points[0].x} ${baselineY} Z`;
  }

  const interactive = Boolean(labels) && canPlot;

  // Nearest point to the finger, by x only - the reader is asking "which day".
  const pick = (e: GestureResponderEvent) => {
    if (!interactive) return;
    const x = e.nativeEvent.locationX;
    const step = data.length > 1 ? innerW / (data.length - 1) : innerW;
    const i = Math.round((x - PAD.left) / (step || 1));
    setActive(Math.max(0, Math.min(data.length - 1, i)));
  };

  const activePoint = active !== null && active < points.length ? points[active] : null;
  const tooltipLeft = activePoint
    ? Math.max(0, Math.min(width - TOOLTIP_W, activePoint.x - TOOLTIP_W / 2))
    : 0;

  return (
    <View
      onLayout={onLayout}
      style={{ width: '100%', height }}
      onStartShouldSetResponder={() => interactive}
      onMoveShouldSetResponder={() => interactive}
      // Let a vertical swipe still scroll the screen.
      onResponderTerminationRequest={() => true}
      onResponderGrant={pick}
      onResponderMove={pick}>
      {canPlot ? (
        <Svg width={width} height={height}>
          {showArea ? (
            <Defs>
              <LinearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
                <Stop offset="0" stopColor={color} stopOpacity={0.2} />
                <Stop offset="1" stopColor={color} stopOpacity={0} />
              </LinearGradient>
            </Defs>
          ) : null}

          {/* Gridlines sit behind the data. */}
          {Array.from({ length: gridLines }).map((_, i) => {
            const y = PAD.top + (innerH / Math.max(gridLines - 1, 1)) * i;
            return (
              <SvgLine
                key={`grid-${i}`}
                x1={PAD.left}
                y1={y}
                x2={PAD.left + innerW}
                y2={y}
                stroke={gridColor}
                strokeWidth={1}
                strokeDasharray={i === gridLines - 1 ? undefined : '3 4'}
              />
            );
          })}

          {showArea ? <Path d={areaPath} fill={`url(#${gradientId})`} /> : null}

          <Path
            d={linePath}
            fill="none"
            stroke={color}
            strokeWidth={2.25}
            strokeLinecap="round"
            strokeLinejoin="round"
          />

          {showDots
            ? points.map((p, i) => (
                <Circle
                  key={`dot-${i}`}
                  cx={p.x}
                  cy={p.y}
                  r={3.25}
                  fill={color}
                  stroke={theme.backgroundElement}
                  strokeWidth={1.75}
                />
              ))
            : null}

          {activePoint ? (
            <>
              <SvgLine
                x1={activePoint.x}
                y1={PAD.top}
                x2={activePoint.x}
                y2={PAD.top + innerH}
                stroke={theme.textMuted}
                strokeWidth={1}
                strokeDasharray="3 3"
              />
              <Circle
                cx={activePoint.x}
                cy={activePoint.y}
                r={6}
                fill={color}
                stroke={theme.background}
                strokeWidth={2}
              />
            </>
          ) : null}
        </Svg>
      ) : null}

      {activePoint && active !== null && labels ? (
        <View
          pointerEvents="none"
          style={[
            styles.tooltip,
            {
              left: tooltipLeft,
              backgroundColor: theme.backgroundElement,
              borderColor: theme.border,
            },
          ]}>
          <Text style={[Typography.caption, { color: theme.textSecondary }]} numberOfLines={1}>
            {labels[active] ?? ''}
          </Text>
          <Text style={[Typography.bodyStrong, { color: theme.text }]} numberOfLines={1}>
            {formatValue(data[active], active)}
          </Text>
        </View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  tooltip: {
    position: 'absolute',
    top: 0,
    width: TOOLTIP_W,
    paddingVertical: Spacing.one,
    paddingHorizontal: Spacing.two,
    borderRadius: Radius.md,
    borderWidth: StyleSheet.hairlineWidth,
    alignItems: 'center',
    elevation: 4,
    shadowColor: '#101828',
    shadowOpacity: 0.12,
    shadowRadius: 12,
    shadowOffset: { width: 0, height: 4 },
  },
});

export default LineChart;
