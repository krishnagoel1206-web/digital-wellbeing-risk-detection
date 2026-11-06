/**
 * Profile / Settings screen (Screen 8) - wired.
 *
 * Owns what is about the app rather than about the data: permissions, which
 * data the models use, clearing demonstration data, the backend connection,
 * and an honest statement of what this app is and is not.
 *
 * DELETION IS CONFIRMED, AND SCOPED
 * ---------------------------------
 * The only destructive action removes SYNTHETIC rows. It asks first, states
 * exactly how many rows will go, and cannot touch data measured from the
 * phone - the backend filters by source, so a mistake here cannot destroy
 * real history.
 */

import { Ionicons } from '@expo/vector-icons';
import * as UsageStats from '@modules/usage-stats';
import Constants from 'expo-constants';
import { Link } from 'expo-router';
import { useCallback, useState } from 'react';
import { Alert, Pressable, StyleSheet, Text, View } from 'react-native';

import { ApiStateView } from '@/components/ui/api-states';
import { Card } from '@/components/ui/card';
import { Screen } from '@/components/ui/screen';
import { ScopeSelector } from '@/components/ui/scope-selector';
import { Section } from '@/components/ui/section';
import { Radius, Spacing, Typography } from '@/constants/theme';
import { useApi } from '@/hooks/use-api';
import { useScope } from '@/hooks/use-scope';
import { useTheme } from '@/hooks/use-theme';
import { getResolvedBaseUrl, getResolvedReason } from '@/services/api/client';
import * as api from '@/services/api/endpoints';

function Row({
  icon,
  label,
  hint,
}: {
  icon: keyof typeof Ionicons.glyphMap;
  label: string;
  hint?: string;
}) {
  const theme = useTheme();
  return (
    <View style={styles.row}>
      <View style={[styles.icon, { backgroundColor: theme.primarySubtle }]}>
        <Ionicons name={icon} size={18} color={theme.primary} />
      </View>
      <View style={styles.rowText}>
        <Text style={[Typography.bodyStrong, { color: theme.text }]}>{label}</Text>
        {hint ? (
          <Text style={[Typography.caption, { color: theme.textSecondary }]}>{hint}</Text>
        ) : null}
      </View>
      <Ionicons name="chevron-forward" size={18} color={theme.textMuted} />
    </View>
  );
}

export default function ProfileScreen() {
  const theme = useTheme();
  const { deviceDays, refresh: refreshScope } = useScope();
  const [clearing, setClearing] = useState(false);

  const summary = useApi(useCallback(() => api.getUsageSummary(), []));
  const health = useApi(useCallback(() => api.getHealth(), []));

  const permissions = UsageStats.getPermissionState();
  const grantedCount = [
    permissions.usageAccess,
    permissions.callLog,
    permissions.notificationAccess,
  ].filter(Boolean).length;

  const syntheticCount = summary.data?.records_by_source.synthetic ?? 0;

  const clearDemoData = useCallback(() => {
    Alert.alert(
      'Remove demonstration data?',
      `This deletes ${syntheticCount} generated day${syntheticCount === 1 ? '' : 's'}. ` +
        'Days measured from your phone are not affected and cannot be removed by this action.',
      [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Remove',
          style: 'destructive',
          onPress: async () => {
            setClearing(true);
            try {
              const result = await api.deleteUsageBySource('synthetic');
              summary.refetch();
              refreshScope();
              Alert.alert(
                'Removed',
                `${result.deleted} generated day${result.deleted === 1 ? '' : 's'} deleted.`
              );
            } catch (error) {
              Alert.alert('Could not remove', String(error));
            } finally {
              setClearing(false);
            }
          },
        },
      ]
    );
  }, [syntheticCount, summary, refreshScope]);

  return (
    <Screen title="Profile" subtitle="Settings, data and what this app is.">
      <Section title="Data used for analysis">
        <Card>
          <ScopeSelector />
        </Card>
      </Section>

      <Section title="Permissions">
        <Card noPadding>
          <Link href="/permissions" asChild>
            <Pressable
              style={({ pressed }) => [pressed && { backgroundColor: theme.backgroundSelected }]}>
              <Row
                icon="key-outline"
                label="Phone access"
                hint={`${grantedCount} of 3 granted`}
              />
            </Pressable>
          </Link>
        </Card>
      </Section>

      <Section title="Your data">
        <ApiStateView state={summary} loadingLabel="Checking stored data">
          {(data) => (
            <Card>
              <View style={styles.statRow}>
                <View style={styles.stat}>
                  <Text style={[Typography.heading, { color: theme.text }]}>{deviceDays}</Text>
                  <Text style={[Typography.caption, { color: theme.textSecondary }]}>
                    from your phone
                  </Text>
                </View>
                <View style={styles.stat}>
                  <Text style={[Typography.heading, { color: theme.textMuted }]}>
                    {syntheticCount}
                  </Text>
                  <Text style={[Typography.caption, { color: theme.textSecondary }]}>
                    demonstration
                  </Text>
                </View>
              </View>

              <Text style={[Typography.caption, styles.note, { color: theme.textMuted }]}>
                {data.has_enough_for_baseline
                  ? 'Enough history for a personal baseline.'
                  : `A personal baseline needs ${data.min_days_for_baseline} days; you have ${data.total_records}.`}
              </Text>

              {syntheticCount > 0 ? (
                <Pressable
                  onPress={clearDemoData}
                  disabled={clearing}
                  style={({ pressed }) => [
                    styles.dangerButton,
                    {
                      borderColor: theme.border,
                      backgroundColor: pressed ? theme.riskHighSurface : 'transparent',
                    },
                  ]}>
                  <Ionicons name="trash-outline" size={15} color={theme.riskHigh} />
                  <Text style={[Typography.caption, { color: theme.riskHigh }]}>
                    {clearing ? 'Removing...' : 'Remove demonstration data'}
                  </Text>
                </Pressable>
              ) : null}
            </Card>
          )}
        </ApiStateView>
      </Section>

      <Section title="Backend connection">
        <Card>
          <View style={styles.rowBetween}>
            <Text style={[Typography.caption, { color: theme.textSecondary }]}>Status</Text>
            <View style={styles.inline}>
              <Ionicons
                name={health.data ? 'checkmark-circle' : 'close-circle'}
                size={14}
                color={health.data ? theme.riskLow : theme.riskHigh}
              />
              <Text style={[Typography.caption, { color: theme.text }]}>
                {health.data ? 'Connected' : 'Not reachable'}
              </Text>
            </View>
          </View>

          <View style={styles.rowBetween}>
            <Text style={[Typography.caption, { color: theme.textSecondary }]}>Address</Text>
            <Text style={[Typography.tabular, { color: theme.text }]} numberOfLines={1}>
              {getResolvedBaseUrl() ?? 'not resolved'}
            </Text>
          </View>

          {getResolvedReason() ? (
            <Text style={[Typography.caption, { color: theme.textMuted }]}>
              {getResolvedReason()}
            </Text>
          ) : null}

          {/* The backend reports its own LAN addresses, so a changed hotspot
              IP can be read here rather than hunted down on the laptop. */}
          {health.data?.lan_addresses && health.data.lan_addresses.length > 0 ? (
            <Text style={[Typography.caption, styles.note, { color: theme.textMuted }]}>
              Laptop addresses on this network: {health.data.lan_addresses.join(', ')}
            </Text>
          ) : null}

          {health.data ? (
            <Text style={[Typography.caption, styles.note, { color: theme.textMuted }]}>
              {health.data.models_trained.length} trained model
              {health.data.models_trained.length === 1 ? '' : 's'} loaded.
            </Text>
          ) : null}
        </Card>
      </Section>

      <Section title="About">
        <Card>
          <View style={styles.rowBetween}>
            <Text style={[Typography.caption, { color: theme.textSecondary }]}>App</Text>
            <Text style={[Typography.caption, { color: theme.text }]}>
              Digital Wellbeing Risk Detection
            </Text>
          </View>
          <View style={styles.rowBetween}>
            <Text style={[Typography.caption, { color: theme.textSecondary }]}>Version</Text>
            <Text style={[Typography.caption, { color: theme.text }]}>
              {Constants.expoConfig?.version ?? '1.0.0'}
            </Text>
          </View>

          <Text style={[Typography.caption, styles.disclaimer, { color: theme.textMuted }]}>
            This app reads the same usage statistics Android records for Digital Wellbeing.
            It is an academic prototype, not a medical device, and gives no medical advice or
            diagnosis.
          </Text>

          <Text style={[Typography.caption, styles.disclaimer, { color: theme.textMuted }]}>
            Some measurements cannot be read from Android at all. Notification counts begin
            only when you grant access - there is no history to recover. Daily battery use is
            not retained by the system, so it is left blank rather than estimated. Sleep is
            inferred from your longest period of inactivity and is always labelled an estimate.
          </Text>
        </Card>
      </Section>

      <Section title="Getting started">
        <Card noPadding>
          <Link href="/onboarding" asChild>
            <Pressable
              style={({ pressed }) => [pressed && { backgroundColor: theme.backgroundSelected }]}>
              <Row
                icon="sparkles-outline"
                label="View the welcome screen"
                hint="What this app measures and why"
              />
            </Pressable>
          </Link>
        </Card>
      </Section>
    </Screen>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.three,
    padding: Spacing.three,
  },
  rowBetween: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: Spacing.three,
    marginBottom: Spacing.one,
  },
  inline: { flexDirection: 'row', alignItems: 'center', gap: Spacing.one },
  icon: {
    width: 36,
    height: 36,
    borderRadius: Radius.md,
    alignItems: 'center',
    justifyContent: 'center',
  },
  rowText: { flex: 1, gap: 1 },
  statRow: { flexDirection: 'row', gap: Spacing.four },
  stat: { flex: 1, gap: 1 },
  note: { marginTop: Spacing.two, lineHeight: 17 },
  disclaimer: { marginTop: Spacing.three, lineHeight: 17 },
  dangerButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: Spacing.two,
    marginTop: Spacing.three,
    paddingVertical: Spacing.two + 2,
    borderRadius: Radius.md,
    borderWidth: StyleSheet.hairlineWidth,
  },
});
