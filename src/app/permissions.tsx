/**
 * Permissions screen.
 *
 * Shows each permission, whether it is granted, and - importantly - exactly
 * what it unlocks and what stays unavailable without it. The brief forbids
 * implying data the app does not have, so a denied permission is explained
 * rather than silently producing blank charts.
 *
 * Two of these are "special access" permissions that cannot be requested with
 * a dialog: Android only lets the user enable them in Settings. So those
 * buttons open the relevant Settings screen, and the state is re-checked when
 * the user comes back (via AppState) rather than assuming they granted it.
 */

import { Ionicons } from '@expo/vector-icons';
import * as UsageStats from '@modules/usage-stats';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  Alert,
  AppState,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Card } from '@/components/ui/card';
import { Section } from '@/components/ui/section';
import {
  BottomTabInset,
  MaxContentWidth,
  Radius,
  Spacing,
  Typography,
} from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';

type PermissionRow = {
  key: 'usage' | 'calls' | 'notifications';
  title: string;
  /** What is gained by granting it. */
  unlocks: string[];
  /** What stays unavailable without it. */
  withoutIt: string;
  granted: boolean;
  /** 'settings' means Android requires a manual toggle, not a dialog. */
  grantVia: 'settings' | 'dialog';
  action: () => void | Promise<void>;
};

export default function PermissionsScreen() {
  const theme = useTheme();

  const [state, setState] = useState(() => UsageStats.getPermissionState());
  const [observingSince, setObservingSince] = useState<number | null>(null);
  const appState = useRef(AppState.currentState);

  const refresh = useCallback(() => {
    setState(UsageStats.getPermissionState());
    setObservingSince(UsageStats.notificationObservingSince());
  }, []);

  useEffect(() => {
    refresh();

    // Special-access permissions are granted in Settings, so the only
    // reliable moment to re-check is when the app returns to the foreground.
    const subscription = AppState.addEventListener('change', (next) => {
      // AppState.currentState is nullable, so coalesce before matching.
      const previous = appState.current ?? '';
      if (/inactive|background/.test(previous) && next === 'active') {
        refresh();
      }
      appState.current = next;
    });
    return () => subscription.remove();
  }, [refresh]);

  const requestCallLog = useCallback(async () => {
    try {
      const response = await UsageStats.requestCallLogPermission();
      refresh();
      if (!response.granted && !response.canAskAgain) {
        Alert.alert(
          'Call log permission denied',
          'Android will not ask again. You can still enable it from Settings > ' +
            'Apps > this app > Permissions.'
        );
      }
    } catch (error) {
      Alert.alert('Could not request permission', String(error));
    }
  }, [refresh]);

  if (!state.moduleAvailable) {
    return (
      <View style={[styles.root, { backgroundColor: theme.background }]}>
        <SafeAreaView style={styles.safeArea}>
          <ScrollView contentContainerStyle={styles.content}>
            <Text style={[Typography.title, { color: theme.text }]}>Permissions</Text>
            <Card>
              <View style={styles.row}>
                <Ionicons name="warning-outline" size={18} color={theme.riskHigh} />
                <Text style={[Typography.bodyStrong, styles.flex, { color: theme.text }]}>
                  Not available in this build
                </Text>
              </View>
              <Text style={[Typography.caption, styles.gapTop, { color: theme.textSecondary }]}>
                Reading phone usage needs a native module, which Expo Go cannot
                load. Run the app from a development build instead.
              </Text>
            </Card>
          </ScrollView>
        </SafeAreaView>
      </View>
    );
  }

  const rows: PermissionRow[] = [
    {
      key: 'usage',
      title: 'Usage access',
      unlocks: [
        'Screen time per day',
        'Time split across social, games and work apps',
        'How many times you opened apps',
        'Late-night use, from the exact times of day',
        'Phone unlocks',
      ],
      withoutIt: 'Without this the app has no usage data at all.',
      granted: state.usageAccess,
      grantVia: 'settings',
      action: () => UsageStats.openUsageSettings(),
    },
    {
      key: 'calls',
      title: 'Call log',
      unlocks: ['Number of calls per day', 'Total time spent on calls'],
      withoutIt: 'Call figures stay empty. Everything else still works.',
      granted: state.callLog,
      grantVia: 'dialog',
      action: requestCallLog,
    },
    {
      key: 'notifications',
      title: 'Notification access',
      unlocks: ['How many notifications arrive each day'],
      withoutIt:
        'Notification counts stay empty. Note that Android keeps no ' +
        'notification history, so counting can only start from the moment you ' +
        'grant this - earlier days can never be filled in.',
      granted: state.notificationAccess,
      grantVia: 'settings',
      action: () => UsageStats.openNotificationSettings(),
    },
  ];

  const grantedCount = rows.filter((r) => r.granted).length;

  return (
    <View style={[styles.root, { backgroundColor: theme.background }]}>
      <SafeAreaView style={styles.safeArea} edges={['left', 'right', 'bottom']}>
        <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
          <View style={styles.header}>
            <Text style={[Typography.body, { color: theme.textSecondary }]}>
              This app reads the same usage data Android records for Digital
              Wellbeing. {grantedCount} of {rows.length} permissions granted.
            </Text>
          </View>

          {rows.map((row) => (
            <Card key={row.key}>
              <View style={styles.row}>
                <Ionicons
                  name={row.granted ? 'checkmark-circle' : 'ellipse-outline'}
                  size={20}
                  color={row.granted ? theme.riskLow : theme.textMuted}
                />
                <Text style={[Typography.subheading, styles.flex, { color: theme.text }]}>
                  {row.title}
                </Text>
                <Text
                  style={[
                    Typography.label,
                    { color: row.granted ? theme.riskLow : theme.textMuted },
                  ]}>
                  {row.granted ? 'GRANTED' : 'NOT GRANTED'}
                </Text>
              </View>

              <View style={styles.list}>
                {row.unlocks.map((item) => (
                  <View key={item} style={styles.listRow}>
                    <Ionicons name="ellipse" size={4} color={theme.textMuted} />
                    <Text style={[Typography.caption, styles.flex, { color: theme.textSecondary }]}>
                      {item}
                    </Text>
                  </View>
                ))}
              </View>

              {!row.granted ? (
                <>
                  <Text style={[Typography.caption, styles.without, { color: theme.riskMediumInk }]}>
                    {row.withoutIt}
                  </Text>
                  <Pressable
                    onPress={row.action}
                    style={({ pressed }) => [
                      styles.button,
                      { backgroundColor: pressed ? theme.primaryPressed : theme.primary },
                    ]}
                    accessibilityRole="button">
                    <Ionicons
                      name={row.grantVia === 'settings' ? 'settings-outline' : 'hand-right-outline'}
                      size={15}
                      color={theme.onPrimary}
                    />
                    <Text style={[Typography.bodyStrong, { color: theme.onPrimary }]}>
                      {row.grantVia === 'settings' ? 'Open Android settings' : 'Allow'}
                    </Text>
                  </Pressable>
                  {row.grantVia === 'settings' ? (
                    <Text style={[Typography.caption, { color: theme.textMuted }]}>
                      Android only allows this to be switched on in Settings. Find
                      this app in the list, turn it on, then come back.
                    </Text>
                  ) : null}
                </>
              ) : null}

              {row.key === 'notifications' && row.granted ? (
                <Text style={[Typography.caption, styles.gapTop, { color: theme.textMuted }]}>
                  {observingSince
                    ? `Counting notifications since ${new Date(observingSince).toLocaleDateString()}. Days before that are recorded as unknown, not zero.`
                    : 'Counting will begin with the next notification that arrives.'}
                </Text>
              ) : null}
            </Card>
          ))}

          <Section title="What cannot be read">
            <Card>
              <Text style={[Typography.caption, { color: theme.textSecondary }]}>
                Battery use per day is not available. Android does not keep a
                historical record of it, and sampling it occasionally would give
                a number too unreliable to show. It is left blank rather than
                estimated.
              </Text>
              <Text style={[Typography.caption, styles.gapTop, { color: theme.textSecondary }]}>
                Sleep is estimated from your longest stretch of not using the
                phone overnight. It is marked as an estimate everywhere it
                appears, and is left out entirely when the gap looks
                implausible.
              </Text>
            </Card>
          </Section>

          <View style={styles.bottomSpacer} />
        </ScrollView>
      </SafeAreaView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, flexDirection: 'row', justifyContent: 'center' },
  safeArea: { flex: 1, width: '100%', maxWidth: MaxContentWidth },
  content: {
    paddingHorizontal: Spacing.three,
    paddingTop: Spacing.three,
    gap: Spacing.three,
  },
  header: { gap: Spacing.one },
  flex: { flex: 1 },
  row: { flexDirection: 'row', alignItems: 'center', gap: Spacing.two },
  gapTop: { marginTop: Spacing.two },
  list: { gap: Spacing.one, marginTop: Spacing.two, paddingLeft: Spacing.one },
  listRow: { flexDirection: 'row', alignItems: 'center', gap: Spacing.two },
  without: { marginTop: Spacing.three, lineHeight: 18 },
  button: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: Spacing.two,
    height: 44,
    borderRadius: Radius.md,
    marginTop: Spacing.three,
  },
  bottomSpacer: { height: BottomTabInset + Spacing.four },
});
