/**
 * StatTile - a single labelled number.
 *
 * Per the visualisation guidance, when the data's job is to deliver ONE
 * headline figure, the right form is a stat tile, not a chart. These carry
 * daily screen time, app launches, notification count and so on.
 *
 * `delta` is optional and, when present, always ships an arrow icon beside the
 * percentage - direction is never conveyed by red/green alone.
 *
 * Note `goodDirection`: for screen time, DOWN is good; for productivity-app
 * usage, UP is good. The caller declares which, so the tile never assumes
 * that "increase" means "worse".
 */

import { Ionicons } from '@expo/vector-icons';
import { StyleSheet, Text, View } from 'react-native';

import { Radius, Spacing, Typography } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';

export type StatTileProps = {
  /** Short label, e.g. "Screen time". */
  label: string;
  /** The formatted value, e.g. "6h 24m". Pass a string so the caller owns units. */
  value: string | null;
  /** Optional change vs the previous period, as a signed percentage. */
  delta?: number | null;
  /** Which direction counts as an improvement for THIS metric. */
  goodDirection?: 'up' | 'down';
  /** Optional supporting line under the value. */
  hint?: string;
};

export function StatTile({
  label,
  value,
  delta = null,
  goodDirection = 'down',
  hint,
}: StatTileProps) {
  const theme = useTheme();

  const hasDelta = delta !== null && Number.isFinite(delta) && delta !== 0;
  const rising = hasDelta && (delta as number) > 0;
  const isGood = hasDelta ? (rising ? goodDirection === 'up' : goodDirection === 'down') : false;

  const deltaColor = !hasDelta ? theme.textMuted : isGood ? theme.riskLow : theme.riskHigh;

  return (
    <View style={[styles.wrap, { backgroundColor: theme.backgroundSelected }]}>
      <Text style={[Typography.caption, { color: theme.textMuted, fontWeight: '500' }]} numberOfLines={1}>
        {label}
      </Text>

      <Text style={[Typography.heading, styles.value, { color: theme.text }]} numberOfLines={1}>
        {value ?? '--'}
      </Text>

      {hasDelta ? (
        <View style={styles.deltaRow}>
          {/* Arrow carries direction independently of colour. */}
          <Ionicons
            name={rising ? 'arrow-up' : 'arrow-down'}
            size={12}
            color={deltaColor}
          />
          <Text style={[Typography.caption, { color: deltaColor }]}>
            {Math.abs(delta as number).toFixed(0)}%
          </Text>
        </View>
      ) : hint ? (
        <Text style={[Typography.caption, { color: theme.textMuted }]} numberOfLines={1}>
          {hint}
        </Text>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    gap: Spacing.one,
    // flex:1 + minWidth:0 makes tiles share a row as equal columns and lets
    // long values truncate instead of pushing their neighbour off screen.
    flex: 1,
    minWidth: 0,
    paddingVertical: Spacing.two + 4,
    paddingHorizontal: Spacing.three - 2,
    borderRadius: Radius.md,
  },
  value: {
    includeFontPadding: false,
  },
  deltaRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.half + 1,
  },
});
