/**
 * ScoreRing - the hero figure for the Digital Wellbeing Risk Score (0-100).
 *
 * This is a radial "meter": one value against a known 0-100 range. A ring is
 * appropriate here precisely because the range is fixed and meaningful - it is
 * NOT used anywhere in this app to compare categories against each other
 * (that would be a pie chart of unrelated magnitudes, which is an anti-pattern).
 *
 * `value` is intentionally `number | null`. A null renders an explicit
 * "Not enough data" state rather than a misleading 0, because the backend
 * refuses to produce a score until the user has enough history for the
 * personal baseline to mean anything.
 */

import { StyleSheet, Text, View } from 'react-native';
import Svg, { Circle, G } from 'react-native-svg';

import { RiskColorKeys, Spacing, Typography, type RiskLevel } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';

export type ScoreRingProps = {
  /** 0-100 risk score, or null when there is not enough history yet. */
  value: number | null;
  /** Drives the arc colour. Omitted when value is null. */
  level?: RiskLevel;
  /** Outer diameter in px. */
  size?: number;
  /** Ring thickness in px. */
  thickness?: number;
  /** Small caption under the number, e.g. "Risk score". */
  caption?: string;
};

export function ScoreRing({
  value,
  level,
  size = 180,
  thickness = 12,
  caption = 'Risk score',
}: ScoreRingProps) {
  const theme = useTheme();

  const hasValue = value !== null && Number.isFinite(value);
  // Clamp so a bad backend value can never draw a broken arc.
  const clamped = hasValue ? Math.max(0, Math.min(100, value as number)) : 0;

  const radius = (size - thickness) / 2;
  const circumference = 2 * Math.PI * radius;
  const dashOffset = circumference * (1 - clamped / 100);

  const arcColor = hasValue && level ? theme[RiskColorKeys[level].fg] : theme.axis;

  return (
    <View style={[styles.wrap, { width: size, height: size }]}>
      <Svg width={size} height={size}>
        {/* Rotate so the arc starts at 12 o'clock instead of 3 o'clock. */}
        <G rotation={-90} origin={`${size / 2}, ${size / 2}`}>
          {/* Track */}
          <Circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            stroke={theme.backgroundSelected}
            strokeWidth={thickness}
            fill="none"
          />
          {/* Value arc - hidden entirely when there is no value, so an empty
              ring is unambiguous rather than looking like a score of zero. */}
          {hasValue ? (
            <Circle
              cx={size / 2}
              cy={size / 2}
              r={radius}
              stroke={arcColor}
              strokeWidth={thickness}
              strokeLinecap="round"
              strokeDasharray={circumference}
              strokeDashoffset={dashOffset}
              fill="none"
            />
          ) : null}
        </G>
      </Svg>

      <View style={styles.center} pointerEvents="none">
        {hasValue ? (
          <>
            <Text style={[Typography.display, styles.value, { color: theme.text }]}>
              {Math.round(clamped)}
            </Text>
            <Text style={[Typography.caption, { color: theme.textSecondary }]}>{caption}</Text>
          </>
        ) : (
          <>
            <Text style={[Typography.subheading, styles.empty, { color: theme.textSecondary }]}>
              Not enough{'\n'}data yet
            </Text>
          </>
        )}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    alignItems: 'center',
    justifyContent: 'center',
  },
  center: {
    // In React Native 0.88 `absoluteFill` is a plain object
    // ({position:'absolute', left/right/top/bottom: 0}), so spreading it is
    // correct. The older `absoluteFillObject` no longer exists in the types.
    ...StyleSheet.absoluteFill,
    alignItems: 'center',
    justifyContent: 'center',
    gap: Spacing.half,
  },
  value: {
    includeFontPadding: false,
  },
  empty: {
    textAlign: 'center',
  },
});
