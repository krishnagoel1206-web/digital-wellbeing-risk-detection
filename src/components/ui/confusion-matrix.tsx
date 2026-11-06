/**
 * Confusion matrix.
 *
 * Cell shading uses a SEQUENTIAL single-hue ramp - one hue, light to dark,
 * carrying magnitude. A rainbow or categorical palette here would be wrong:
 * the quantity is continuous, so hue must not change with it.
 *
 * Every cell also prints its count. The shading is a reading aid; the number
 * is the data. That matters because the lightest steps of a sequential ramp
 * fall below 3:1 contrast against the surface by design, so colour alone
 * could not carry the value.
 */

import { StyleSheet, Text, View } from 'react-native';

import { Radius, Spacing, Typography } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';

export function ConfusionMatrix({
  matrix,
  labels,
}: {
  matrix: number[][];
  labels: string[];
}) {
  const theme = useTheme();
  const max = Math.max(...matrix.flat(), 1);

  return (
    <View style={styles.wrap}>
      <Text style={[Typography.caption, { color: theme.textMuted }]}>
        Rows: what it actually was. Columns: what the model said.
      </Text>

      {/* Column headers */}
      <View style={styles.row}>
        <View style={styles.corner} />
        {labels.map((label) => (
          <View key={label} style={styles.cell}>
            <Text style={[Typography.caption, styles.center, { color: theme.textSecondary }]}>
              {label}
            </Text>
          </View>
        ))}
      </View>

      {matrix.map((rowValues, rowIndex) => (
        <View key={labels[rowIndex]} style={styles.row}>
          <View style={styles.corner}>
            <Text style={[Typography.caption, { color: theme.textSecondary }]}>
              {labels[rowIndex]}
            </Text>
          </View>
          {rowValues.map((value, columnIndex) => {
            const intensity = value / max;
            const onDiagonal = rowIndex === columnIndex;
            return (
              <View
                key={`${rowIndex}-${columnIndex}`}
                style={[
                  styles.cell,
                  styles.valueCell,
                  {
                    // Correct predictions use the "good" status hue, errors
                    // the neutral brand hue, so the diagonal reads at a
                    // glance without hue carrying the magnitude.
                    backgroundColor: onDiagonal
                      ? `rgba(12,163,12,${0.08 + intensity * 0.45})`
                      : `rgba(42,120,214,${0.06 + intensity * 0.35})`,
                  },
                ]}>
                <Text style={[Typography.tabular, styles.center, { color: theme.text }]}>
                  {value}
                </Text>
              </View>
            );
          })}
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { gap: Spacing.one },
  row: { flexDirection: 'row', gap: 3 },
  corner: { width: 62, justifyContent: 'center' },
  cell: { flex: 1, alignItems: 'center', justifyContent: 'center', paddingVertical: Spacing.one },
  valueCell: {
    borderRadius: Radius.sm,
    paddingVertical: Spacing.two + 2,
  },
  center: { textAlign: 'center' },
});
