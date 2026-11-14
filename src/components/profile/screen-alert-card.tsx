/**
 * Profile -> "Screen-time warnings": on/off, the two levels, a test button.
 */

import { Ionicons } from '@expo/vector-icons';
import * as UsageStats from '@modules/usage-stats';
import { useFocusEffect } from 'expo-router';
import { useCallback, useState } from 'react';
import { Alert, Linking, Pressable, StyleSheet, Switch, Text, View } from 'react-native';

import { Card } from '@/components/ui/card';
import { Radius, Spacing, Typography } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { refreshScreenAlert } from '@/services/usage/screen-alert';
import { formatMinutes } from '@/utils/format';

export function ScreenAlertCard() {
  const theme = useTheme();
  const [state, setState] = useState(() => UsageStats.getScreenAlertState());

  const reload = useCallback(() => setState(UsageStats.getScreenAlertState()), []);
  useFocusEffect(
    useCallback(() => {
      refreshScreenAlert()
        .catch(() => {})
        .finally(reload);
    }, [reload])
  );

  if (!state) return null;
  const ready = state.usualMinutes > 0;

  const toggle = async (on: boolean) => {
    UsageStats.setScreenAlertEnabled(on);
    if (on && !state.notificationsAllowed) await UsageStats.requestPostNotifications();
    reload();
  };

  const test = async () => {
    let allowed = state.notificationsAllowed;
    if (!allowed) allowed = await UsageStats.requestPostNotifications();
    if (!allowed) {
      Alert.alert(
        'Notifications are off',
        'Allow notifications for this app in Android settings to receive warnings.',
        [
          { text: 'Cancel', style: 'cancel' },
          { text: 'Open settings', onPress: () => Linking.openSettings() },
        ]
      );
      reload();
      return;
    }
    UsageStats.sendTestScreenAlert();
    reload();
  };

  return (
    <Card>
      <View style={styles.header}>
        <View style={[styles.icon, { backgroundColor: theme.primarySubtle }]}>
          <Ionicons name="notifications-outline" size={18} color={theme.primary} />
        </View>
        <View style={styles.flex}>
          <Text style={[Typography.bodyStrong, { color: theme.text }]}>Screen-time warnings</Text>
          <Text style={[Typography.caption, { color: theme.textSecondary }]}>
            {state.enabled ? 'On - checked every 15 minutes' : 'Off'}
          </Text>
        </View>
        <Switch
          value={state.enabled}
          onValueChange={toggle}
          trackColor={{ true: theme.primary, false: theme.backgroundSelected }}
        />
      </View>

      {ready ? (
        <View style={[styles.levels, { borderTopColor: theme.border }]}>
          <View style={styles.rowBetween}>
            <Text style={[Typography.caption, styles.flex, { color: theme.textSecondary }]}>
              First warning - past your usual day
            </Text>
            <Text style={[Typography.tabular, { color: theme.text }]}>
              {formatMinutes(state.usualMinutes)}
            </Text>
          </View>
          <View style={styles.rowBetween}>
            <Text style={[Typography.caption, styles.flex, { color: theme.textSecondary }]}>
              Second warning - an unusual day
            </Text>
            <Text style={[Typography.tabular, { color: theme.text }]}>
              {formatMinutes(state.highMinutes)}
            </Text>
          </View>
          <Text style={[Typography.caption, { color: theme.textMuted }]}>
            {`Learned from your last ${state.basisDays} days and updated as you use the app.`}
          </Text>
        </View>
      ) : (
        <Text style={[Typography.caption, styles.gapTop, { color: theme.textMuted }]}>
          Needs a week of recorded days to learn your usual screen time.
        </Text>
      )}

      {state.enabled && !state.notificationsAllowed ? (
        <Text style={[Typography.caption, styles.gapTop, { color: theme.riskMedium }]}>
          Notifications are blocked, so warnings cannot appear. Tap below to allow them.
        </Text>
      ) : null}

      <Pressable
        onPress={test}
        style={({ pressed }) => [
          styles.button,
          { borderColor: theme.border, backgroundColor: pressed ? theme.backgroundSelected : 'transparent' },
        ]}
        accessibilityRole="button">
        <Ionicons name="paper-plane-outline" size={14} color={theme.primary} />
        <Text style={[Typography.caption, { color: theme.primary }]}>
          {state.notificationsAllowed ? 'Send a test warning' : 'Allow notifications'}
        </Text>
      </Pressable>
    </Card>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  header: { flexDirection: 'row', alignItems: 'center', gap: Spacing.three },
  icon: {
    width: 36,
    height: 36,
    borderRadius: Radius.md,
    alignItems: 'center',
    justifyContent: 'center',
  },
  levels: {
    gap: Spacing.one + 2,
    marginTop: Spacing.three,
    paddingTop: Spacing.three,
    borderTopWidth: StyleSheet.hairlineWidth,
  },
  rowBetween: { flexDirection: 'row', alignItems: 'center', gap: Spacing.two },
  gapTop: { marginTop: Spacing.two },
  button: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: Spacing.one + 2,
    marginTop: Spacing.three,
    paddingVertical: Spacing.two + 2,
    borderRadius: Radius.pill,
    borderWidth: StyleSheet.hairlineWidth,
  },
});
