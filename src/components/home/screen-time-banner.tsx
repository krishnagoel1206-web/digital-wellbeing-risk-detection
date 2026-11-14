/**
 * Home banner: today's screen time so far against the user's usual day.
 * Only shown once today has gone past the usual level - the in-app twin of
 * the background notification.
 */

import { Ionicons } from '@expo/vector-icons';
import * as UsageStats from '@modules/usage-stats';
import { useFocusEffect } from 'expo-router';
import { useCallback, useState } from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { Radius, Spacing, Typography } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { formatMinutes } from '@/utils/format';

export function ScreenTimeBanner() {
  const theme = useTheme();
  const [state, setState] = useState(() => UsageStats.getScreenAlertState());

  useFocusEffect(
    useCallback(() => {
      setState(UsageStats.getScreenAlertState());
    }, [])
  );

  if (!state || !state.usualMinutes || state.todayMinutes == null) return null;
  if (state.todayMinutes < state.usualMinutes) return null;

  const unusual = state.highMinutes > state.usualMinutes && state.todayMinutes >= state.highMinutes;
  const colour = unusual ? theme.riskHigh : theme.riskMedium;
  const surface = unusual ? theme.riskHighSurface : theme.riskMediumSurface;

  return (
    <View style={[styles.banner, { backgroundColor: surface, borderColor: theme.border }]}>
      <Ionicons name={unusual ? 'warning' : 'phone-portrait-outline'} size={20} color={colour} />
      <View style={styles.flex}>
        <Text style={[Typography.bodyStrong, { color: colour }]}>
          {unusual ? 'Unusual day - time to close the phone' : 'Time for a break'}
        </Text>
        <Text style={[Typography.caption, { color: theme.text }]}>
          {`${formatMinutes(state.todayMinutes)} today, more than your usual ${formatMinutes(state.usualMinutes)}.`}
        </Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  banner: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: Spacing.three,
    padding: Spacing.three,
    borderRadius: Radius.lg,
    borderWidth: StyleSheet.hairlineWidth,
    marginBottom: Spacing.three,
  },
  flex: { flex: 1, gap: 2 },
});
