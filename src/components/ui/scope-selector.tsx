/**
 * Segmented control for choosing which data the analysis uses.
 *
 * Shown on the screens that display model output, because the answer to
 * "where did this number come from" should be visible at the point the
 * number is, not buried in settings.
 */

import { Ionicons } from '@expo/vector-icons';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { Radius, Spacing, Typography } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { SCOPE_DESCRIPTIONS, useScope, type AnalysisScope } from '@/hooks/use-scope';

const OPTIONS: { value: AnalysisScope; short: string }[] = [
  { value: 'device', short: 'My phone' },
  { value: 'all', short: 'With demo' },
];

export function ScopeSelector() {
  const theme = useTheme();
  const { scope, setScope, deviceDays } = useScope();

  return (
    <View style={styles.wrap}>
      <View style={[styles.track, { backgroundColor: theme.backgroundSelected }]}>
        {OPTIONS.map((option) => {
          const selected = scope === option.value;
          return (
            <Pressable
              key={option.value}
              onPress={() => setScope(option.value)}
              style={[
                styles.segment,
                selected && { backgroundColor: theme.backgroundElement },
              ]}
              accessibilityRole="button"
              accessibilityState={{ selected }}>
              <Text
                style={[
                  Typography.caption,
                  { color: selected ? theme.text : theme.textSecondary },
                ]}>
                {option.short}
              </Text>
            </Pressable>
          );
        })}
      </View>

      <View style={styles.note}>
        <Ionicons
          name={scope === 'device' ? 'phone-portrait-outline' : 'flask-outline'}
          size={12}
          color={scope === 'device' ? theme.riskLow : theme.riskMedium}
        />
        <Text style={[Typography.caption, styles.flex, { color: theme.textMuted }]}>
          {SCOPE_DESCRIPTIONS[scope]}
          {scope === 'device' ? ` ${deviceDays} day${deviceDays === 1 ? '' : 's'} recorded.` : ''}
        </Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { gap: Spacing.two },
  track: {
    flexDirection: 'row',
    borderRadius: Radius.md,
    padding: 3,
    gap: 3,
  },
  segment: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: Spacing.two,
    borderRadius: Radius.sm,
  },
  note: { flexDirection: 'row', alignItems: 'center', gap: Spacing.one + 2 },
  flex: { flex: 1 },
});
