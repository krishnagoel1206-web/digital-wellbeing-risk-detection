/**
 * Horizontal bar chart with direct labels.
 *
 * WHY BARS AND NOT A DONUT
 * ------------------------
 * The app-category breakdown has four or more slices. Palette validation in
 * Phase 2 showed that only the first THREE categorical colours stay
 * distinguishable when any mark may be compared with any other - which is
 * exactly the situation in a donut, where every slice sits beside every
 * other. A four-slice donut would put yellow next to orange and fail both
 * the colour-vision and normal-vision separation floors.
 *
 * A single-hue bar chart with direct labels avoids the problem entirely:
 * length carries the comparison, and the label carries identity, so colour
 * is doing no work it cannot do reliably.
 */

import { StyleSheet, Text, View } from 'react-native';

import { Radius, Spacing, Typography } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';

export type BarDatum = {
  label: string;
  value: number;
  /** Preformatted value shown at the end of the row. */
  display: string;
};

export function BarChart({
  data,
  color,
  emptyMessage = 'No data to show.',
}: {
  data: BarDatum[];
  color?: string;
  emptyMessage?: string;
}) {
  const theme = useTheme();
  const barColor = color ?? theme.series1;

  const max = Math.max(...data.map((d) => d.value), 0);

  if (data.length === 0 || max <= 0) {
    return (
      <Text style={[Typography.caption, { color: theme.textSecondary }]}>{emptyMessage}</Text>
    );
  }

  return (
    <View style={styles.wrap}>
      {data.map((item) => {
        // Percentage of the largest bar, so the longest fills the row.
        const fraction = max > 0 ? item.value / max : 0;
        return (
          <View key={item.label} style={styles.row}>
            <View style={styles.header}>
              <Text
                style={[Typography.caption, styles.label, { color: theme.textSecondary }]}
                numberOfLines={1}>
                {item.label}
              </Text>
              {/* Direct label: identity and value never depend on colour. */}
              <Text style={[Typography.tabular, { color: theme.text }]}>{item.display}</Text>
            </View>
            <View style={[styles.track, { backgroundColor: theme.backgroundSelected }]}>
              <View
                style={[
                  styles.fill,
                  {
                    width: `${Math.max(2, fraction * 100)}%`,
                    backgroundColor: barColor,
                  },
                ]}
              />
            </View>
          </View>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { gap: Spacing.three },
  row: { gap: Spacing.one },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: Spacing.two,
  },
  label: { flex: 1 },
  track: {
    height: 8,
    borderRadius: Radius.pill,
    overflow: 'hidden',
  },
  // 4px rounded end on the data mark, anchored to the baseline.
  fill: { height: 8, borderRadius: Radius.pill },
});
