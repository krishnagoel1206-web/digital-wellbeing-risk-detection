/**
 * Usage Data screen (Screen 3) - captured automatically; no Sync button, no manual entry.
 *
 * Reads the same usage statistics Android records for Digital Wellbeing and
 * pushes them to the backend. There is deliberately no form here: the data is
 * measured rather than recalled, which is less work for the user and more
 * trustworthy for the models.
 *
 * WHAT THIS SCREEN IS CAREFUL ABOUT
 * ---------------------------------
 * It reports what was actually stored, including what was SKIPPED and why.
 * Three things routinely get skipped, each for a real reason:
 *
 *   - days Android reported no activity for, which may simply be older than
 *     its ~1 week event retention, so storing them as zero would fabricate
 *     quiet days;
 *   - days already recorded from another source, which a device sync must not
 *     overwrite;
 *   - days where nothing changed since the last sync.
 *
 * Showing those plainly is the difference between "synced 7 days" and an
 * honest account of what the phone could and could not tell us.
 */

import { Ionicons } from '@expo/vector-icons';
import * as UsageStats from '@modules/usage-stats';
import { Link } from 'expo-router';
import { useCallback, useEffect, useRef, useState } from 'react';
import { AppState, Pressable, StyleSheet, Text, View } from 'react-native';

import { LineChart } from '@/components/charts/line-chart';
import { ApiStateView } from '@/components/ui/api-states';
import { WellbeingImportCard } from '@/components/data/wellbeing-import-card';
import { Card } from '@/components/ui/card';
import { Screen } from '@/components/ui/screen';
import { Section } from '@/components/ui/section';
import { Radius, Spacing, Typography } from '@/constants/theme';
import { useApi } from '@/hooks/use-api';
import { useScope } from '@/hooks/use-scope';
import { useTheme } from '@/hooks/use-theme';
import * as api from '@/services/api/endpoints';
import {
  ensureBackgroundCapture,
  type BackgroundCaptureState,
} from '@/services/usage/background-capture';
import { getSyncStatus } from '@/services/usage/device-sync';
import { captureIfDue } from '@/services/usage/foreground-capture';
import {
  formatDayAndDate,
  formatFieldName,
  formatMinutes,
  formatShortDate,
  formatSource,
} from '@/utils/format';

/** How many recent days the trend chart shows. */
const TREND_DAYS = 14;

export default function DataScreen() {
  const theme = useTheme();

  const [permissions, setPermissions] = useState(() => UsageStats.getPermissionState());
  const [captureState, setCaptureState] = useState(() => UsageStats.getCaptureStatus());
  const [background, setBackground] = useState<BackgroundCaptureState | null>(null);
  const appState = useRef(AppState.currentState);

  const status = useApi(useCallback(() => getSyncStatus(), []));
  const summary = useApi(useCallback(() => api.getUsageSummary(), []));
  // null = every provenance, deliberately. This screen is an inventory of
  // what is stored, including demonstration rows, each labelled with its
  // source - unlike the charts, which must show only the user's own days.
  const records = useApi(useCallback(() => api.getUsageRecords(null, { limit: 10 }), []));
  // The chart shows only the user's own days, like every other chart.
  const { scope } = useScope();
  const trendRecords = useApi(
    useCallback(
      () => api.getUsageRecords(api.sourcesForScope(scope), { limit: TREND_DAYS }),
      [scope]
    ),
    [scope]
  );

  // Permissions are granted in Android settings, so re-check on foreground.
  useEffect(() => {
    // Shown rather than assumed: Android can refuse background work entirely
    // (battery saver, a user force-stop), and a silent refusal would look
    // exactly like capture quietly working.
    ensureBackgroundCapture()
      .then(setBackground)
      .catch(() => setBackground(null));
    setCaptureState(UsageStats.getCaptureStatus());
  }, []);

  useEffect(() => {
    const subscription = AppState.addEventListener('change', (next) => {
      // AppState.currentState is nullable, so coalesce before matching.
      const previous = appState.current ?? '';
      if (/inactive|background/.test(previous) && next === 'active') {
        setPermissions(UsageStats.getPermissionState());
      }
      appState.current = next;
    });
    return () => subscription.remove();
  }, []);

  // Capture runs by itself (app open, return to foreground, background
  // task). Opening this screen nudges one more pass, then refreshes the
  // figures so they include anything it just stored.
  useEffect(() => {
    let cancelled = false;
    captureIfDue('data-screen').finally(() => {
      if (cancelled) return;
      setCaptureState(UsageStats.getCaptureStatus());
      status.refetch();
      summary.refetch();
      records.refetch();
      trendRecords.refetch();
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const canSync = permissions.moduleAvailable && permissions.usageAccess;

  return (
    <Screen title="Usage data" subtitle="Read straight from your phone - nothing to type in.">
      {/* --- permission gate --- */}
      {!permissions.moduleAvailable ? (
        <Card>
          <View style={styles.row}>
            <Ionicons name="warning-outline" size={18} color={theme.riskHigh} />
            <Text style={[Typography.bodyStrong, styles.flex, { color: theme.text }]}>
              Not available in this build
            </Text>
          </View>
          <Text style={[Typography.caption, styles.gapTop, { color: theme.textSecondary }]}>
            Reading phone usage needs a native module, which Expo Go cannot load.
            Run the development build instead.
          </Text>
        </Card>
      ) : !permissions.usageAccess ? (
        <Card>
          <View style={styles.row}>
            <Ionicons name="lock-closed-outline" size={18} color={theme.riskMedium} />
            <Text style={[Typography.bodyStrong, styles.flex, { color: theme.text }]}>
              Usage access needed
            </Text>
          </View>
          <Text style={[Typography.caption, styles.gapTop, { color: theme.textSecondary }]}>
            Android will not report any screen-time data until you allow this app
            to read usage access.
          </Text>
          <Link href="/permissions" asChild>
            <Pressable
              style={({ pressed }) => [
                styles.button,
                { backgroundColor: pressed ? theme.primaryPressed : theme.primary },
              ]}>
              <Ionicons name="key-outline" size={15} color={theme.onPrimary} />
              <Text style={[Typography.bodyStrong, { color: theme.onPrimary }]}>
                Set up permissions
              </Text>
            </Pressable>
          </Link>
        </Card>
      ) : null}

      {/* --- one-off import of older days from Digital Wellbeing --- */}
      <WellbeingImportCard
        onImported={() => {
          status.refetch();
          summary.refetch();
          records.refetch();
        }}
      />

      {/* --- what gets captured (automatically, on open and in the background) --- */}
      {canSync ? (
        <Section
          title="What is captured"
          subtitle="Automatically, whenever you open the app and in the background">
          <Card>
            {/* Permission coverage, so missing columns are explained up front */}
            <View style={styles.coverageTop}>
              {[
                { label: 'Screen time, apps, late night', ok: permissions.usageAccess },
                { label: 'Calls', ok: permissions.callLog },
                { label: 'Notifications', ok: permissions.notificationAccess },
              ].map((item) => (
                <View key={item.label} style={styles.listRow}>
                  <Ionicons
                    name={item.ok ? 'checkmark-circle' : 'close-circle-outline'}
                    size={14}
                    color={item.ok ? theme.riskLow : theme.textMuted}
                  />
                  <Text
                    style={[
                      Typography.caption,
                      styles.flex,
                      { color: item.ok ? theme.textSecondary : theme.textMuted },
                    ]}>
                    {item.label}
                  </Text>
                </View>
              ))}
            </View>

            {!permissions.callLog || !permissions.notificationAccess ? (
              <Link href="/permissions" asChild>
                <Pressable>
                  <Text style={[Typography.caption, styles.link, { color: theme.primary }]}>
                    Grant the remaining permissions
                  </Text>
                </Pressable>
              </Link>
            ) : null}
          </Card>
        </Section>
      ) : null}

      {/* --- what is stored on the phone, independent of the backend --- */}
      {permissions.moduleAvailable ? (
        <Section
          title="Saved on this phone"
          subtitle="Android forgets usage after about a week. These days do not.">
          <Card>
            <View style={styles.statRow}>
              <View style={styles.flex}>
                <Text style={[Typography.heading, { color: theme.text }]}>
                  {captureState.capturedDays}
                </Text>
                <Text style={[Typography.caption, { color: theme.textSecondary }]}>
                  days captured
                </Text>
              </View>
              <View style={styles.flex}>
                <Text
                  style={[
                    Typography.heading,
                    {
                      color:
                        captureState.pendingUpload > 0 ? theme.riskMedium : theme.textMuted,
                    },
                  ]}>
                  {captureState.pendingUpload}
                </Text>
                <Text style={[Typography.caption, { color: theme.textSecondary }]}>
                  waiting to send
                </Text>
              </View>
            </View>

            {captureState.firstDate && captureState.lastDate ? (
              <Text style={[Typography.caption, styles.gapTop, { color: theme.textMuted }]}>
                {formatShortDate(captureState.firstDate)} – {formatShortDate(captureState.lastDate)}
              </Text>
            ) : null}

            {/* The single most important number on this card. A run of silent
                background failures is invisible unless the last successful
                capture is shown, and by the time it is noticed the missing
                days are unrecoverable. */}
            <Text style={[Typography.caption, styles.gapTop, { color: theme.textMuted }]}>
              {captureState.lastCaptureMs
                ? `Last captured ${new Date(captureState.lastCaptureMs).toLocaleString(undefined, { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' })}`
                : 'Not captured yet on this device.'}
            </Text>

            {background ? (
              <View style={[styles.rowTop, styles.gapTop]}>
                <Ionicons
                  name={background.registered ? 'checkmark-circle' : 'warning-outline'}
                  size={14}
                  style={styles.iconNudge}
                  color={background.registered ? theme.riskLow : theme.riskMedium}
                />
                <Text style={[Typography.caption, styles.flex, { color: theme.textSecondary }]}>
                  {background.registered
                    ? 'Also captured in the background, about twice a day.'
                    : background.detail}
                </Text>
              </View>
            ) : null}
          </Card>
        </Section>
      ) : null}

      {/* --- daily screen time, last 14 days --- */}
      <Section title={`Last ${TREND_DAYS} days`} subtitle="Daily screen time in hours">
        <ApiStateView state={trendRecords} loadingLabel="Loading trend">
          {(rows) =>
            rows.length < 2 ? (
              <Card>
                <Text style={[Typography.caption, { color: theme.textSecondary }]}>
                  At least two days are needed to draw a trend.
                </Text>
              </Card>
            ) : (
              <Card>
                <LineChart
                  data={[...rows].reverse().map((r) => r.total_screen_minutes / 60)}
                  labels={[...rows].reverse().map((r) => formatShortDate(r.record_date))}
                  formatValue={(_, i) =>
                    formatMinutes([...rows].reverse()[i]?.total_screen_minutes)
                  }
                  height={170}
                  color={theme.series1}
                  gridColor={theme.grid}
                  showArea
                  showDots
                  startAtZero
                  gradientId="dataTrend"
                />
                <View style={styles.axisRow}>
                  <Text style={[Typography.tabular, { color: theme.textMuted }]}>
                    {formatShortDate(rows[rows.length - 1]?.record_date ?? '')}
                  </Text>
                  <Text style={[Typography.tabular, { color: theme.textMuted }]}>
                    {formatShortDate(rows[0]?.record_date ?? '')}
                  </Text>
                </View>
              </Card>
            )
          }
        </ApiStateView>
      </Section>

      {/* --- what the backend holds --- */}
      <Section title="Stored history">
        <ApiStateView state={status} loadingLabel="Checking stored data">
          {(data) => (
            <Card>
              <Text style={[Typography.heading, { color: theme.text }]}>
                {data.device_days} day{data.device_days === 1 ? '' : 's'} from your phone
              </Text>
              {data.first_date ? (
                <Text style={[Typography.caption, { color: theme.textSecondary }]}>
                  {formatShortDate(data.first_date)} – {formatShortDate(data.last_date ?? '')}
                </Text>
              ) : (
                <Text style={[Typography.caption, { color: theme.textSecondary }]}>
                  Nothing captured from the phone yet.
                </Text>
              )}

              {summary.data ? (
                <Text style={[Typography.caption, styles.gapTop, { color: theme.textMuted }]}>
                  {summary.data.total_records} records in total ·{' '}
                  {summary.data.has_enough_for_baseline
                    ? 'enough for a personal baseline'
                    : `needs ${summary.data.min_days_for_baseline} days for a baseline`}
                </Text>
              ) : null}

              {Object.keys(data.field_coverage).length > 0 ? (
                <View style={styles.skipped}>
                  <Text style={[Typography.label, { color: theme.textMuted }]}>FIELD COVERAGE</Text>
                  {Object.entries(data.field_coverage).map(([field, count]) => {
                    // Partial coverage is normal, not a fault, so the start
                    // date is shown beside the count whenever it explains the
                    // gap - notification counts exist only from the day access
                    // was granted and can never be backfilled.
                    const since = data.field_first_seen[field];
                    const partial = count > 0 && count < data.device_days;
                    return (
                      <Text
                        key={field}
                        style={[Typography.caption, { color: theme.textSecondary }]}>
                        {formatFieldName(field)}: {count} of {data.device_days} days
                        {partial && since ? ` · since ${formatShortDate(since)}` : ''}
                      </Text>
                    );
                  })}
                </View>
              ) : null}
            </Card>
          )}
        </ApiStateView>
      </Section>

      {/* --- recent rows --- */}
      <Section title="Recent days">
        <ApiStateView state={records} loadingLabel="Loading records">
          {(data) =>
            data.length === 0 ? (
              <Card>
                <Text style={[Typography.caption, { color: theme.textSecondary }]}>
                  No records yet. Your phone&apos;s usage appears here automatically.
                </Text>
              </Card>
            ) : (
              <Card noPadding>
                {data.map((record, index) => (
                  <View key={record.id}>
                    {index > 0 ? (
                      <View style={[styles.divider, { backgroundColor: theme.border }]} />
                    ) : null}
                    <View style={styles.recordRow}>
                      <View style={styles.flex}>
                        <Text style={[Typography.bodyStrong, { color: theme.text }]}>
                          {formatDayAndDate(record.record_date)}
                        </Text>
                        <Text style={[Typography.caption, { color: theme.textMuted }]}>
                          {formatSource(record.source)}
                        </Text>
                      </View>
                      <Text style={[Typography.bodyStrong, { color: theme.text }]}>
                        {formatMinutes(record.total_screen_minutes)}
                      </Text>
                    </View>
                  </View>
                ))}
              </Card>
            )
          }
        </ApiStateView>
      </Section>
    </Screen>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  row: { flexDirection: 'row', alignItems: 'center', gap: Spacing.two },
  rowTop: { flexDirection: 'row', alignItems: 'flex-start', gap: Spacing.two },
  iconNudge: { marginTop: 2 },
  gapTop: { marginTop: Spacing.two },
  listRow: { flexDirection: 'row', alignItems: 'center', gap: Spacing.two },
  coverageTop: { gap: Spacing.one },
  axisRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginTop: Spacing.one,
  },
  button: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: Spacing.two,
    height: 44,
    borderRadius: Radius.md,
    marginTop: Spacing.three,
  },
  link: { marginTop: Spacing.two, textDecorationLine: 'underline' },
  statRow: { flexDirection: 'row', gap: Spacing.four },
  skipped: { gap: Spacing.one, marginTop: Spacing.three },
  recordRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.three,
    paddingVertical: Spacing.three,
    paddingHorizontal: Spacing.three,
  },
  divider: { height: StyleSheet.hairlineWidth, marginHorizontal: Spacing.three },
});
