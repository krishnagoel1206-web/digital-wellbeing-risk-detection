/**
 * How far a day is from the user's OWN normal.
 *
 * Deliberately not a RiskBadge: an unusual day is a change relative to
 * yourself, not a risk level. A habitually light user having a heavy day is
 * "severe change" while still being low risk, and showing "High risk" there
 * would contradict the classifier on the same screen.
 */

import { Ionicons } from '@expo/vector-icons';
import { StyleSheet, Text, View } from 'react-native';

import { Radius, Spacing, Typography } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import type { AnomalySeverity } from '@/types/api';

export function SeverityTag({ severity }: { severity: AnomalySeverity }) {
  const theme = useTheme();
  const tone =
    severity === 'severe'
      ? { fg: theme.riskHigh, bg: theme.riskHighSurface, icon: 'warning' as const }
      : severity === 'moderate'
        ? { fg: theme.text, bg: theme.riskMediumSurface, icon: 'alert-circle' as const }
        : { fg: theme.textSecondary, bg: theme.backgroundSelected, icon: 'ellipse-outline' as const };
  return (
    <View
      style={[styles.tag, { backgroundColor: tone.bg }]}
      accessibilityRole="text"
      accessibilityLabel={`${severity} change from your usual`}>
      <Ionicons name={tone.icon} size={12} color={tone.fg} />
      <Text style={[Typography.label, { color: tone.fg }]}>{`${severity.toUpperCase()} CHANGE`}</Text>
    </View>
  );
}

/** One line describing the multivariate (all measures together) test. */
export function combinationLine(day: {
  mahalanobis_p: number | null;
  unusual_combination: boolean;
}): string | null {
  const p = day.mahalanobis_p;
  if (p == null) return null;
  const pText = p < 0.001 ? 'below 0.001' : p.toFixed(p < 0.01 ? 3 : 2);
  if (day.unusual_combination) {
    return `All measures together: an unusual combination for you (p ${pText.startsWith('below') ? '' : '= '}${pText}), even though no single measure is extreme.`;
  }
  if (p < 0.01) {
    return `All measures together: also unusual as a combination (p ${pText.startsWith('below') ? '' : '= '}${pText}).`;
  }
  return `All measures together: a combination you have had before (p ${pText.startsWith('below') ? '' : '= '}${pText}).`;
}

const styles = StyleSheet.create({
  tag: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    alignSelf: 'flex-start',
    paddingHorizontal: Spacing.two,
    paddingVertical: 3,
    borderRadius: Radius.pill,
  },
});
