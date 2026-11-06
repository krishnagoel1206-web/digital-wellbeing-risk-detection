/**
 * Usage Data screen (Screen 3) - device sync, no manual entry.
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
import { ActivityIndicator, AppState, Pressable, StyleSheet, Text, View } from 'react-native';

import { ApiStateView } from '@/components/ui/api-states';
import { Card } from '@/components/ui/card';
import { Screen } from '@/components/ui/screen';
import { Section } from '@/components/ui/section';
import { Radius, Spacing, Typography } from '@/constants/theme';
import { useApi } from '@/hooks/use-api';
import { useTheme } from '@/hooks/use-theme';
import * as api from '@/services/api/endpoints';
import {
  ensureBackgroundCapture,
  type BackgroundCaptureState,
} from '@/services/usage/background-capture';
import { captureAndFlush, type CaptureAndFlushResult } from '@/services/usage/capture';
import {
  DEFAULT_SYNC_DAYS,
  getSyncStatus,
} from '@/services/usage/device-sync';
import { formatMinutes } from '@/utils/format';

export default function DataScreen() {
  const theme = useTheme();

  const [permissions, setPermissions] = useState(() => UsageStats.getPermissionState());
  const [syncing, setSyncing] = useState(false);
  const [capture, setCapture] = useState<CaptureAndFlushResult | null>(null);
  const [captureState, setCaptureState] = useState(() => UsageStats.getCaptureStatus());
  const [background, setBackground] = useState<BackgroundCaptureState | null>(null);
  const [syncError, setSyncError] = useState<string | null>(null);
  const appState = useRef(AppState.currentState);

  const status = useApi(useCallback(() => getSyncStatus(), []));
  const summary = useApi(useCallback(() => api.getUsageSummary(), []));
  // null = every provenance, deliberately. This screen is an inventory of
  // what is stored, including demonstration rows, each labelled with its
  // source - unlike the charts, which must show only the user's own days.
  const records = useApi(useCallback(() => api.getUsageRecords(null, { limit: 10 }), []));

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

  const runSync = useCallback(async () => {
    setSyncing(true);
    setSyncError(null);
    try {
      // captureAndFlush, not the old read-and-POST. The read is the half with
      // a deadline - Android forgets the day after about a week - so it is
      // stored locally first and the upload becomes a retryable second step.
      // Previously an unreachable laptop threw the read away entirely.
      const result = await captureAndFlush();
      setCapture(result);

      if (result.uploaded > 0 || result.stillPending === 0) {
        status.refetch();
        summary.refetch();
        records.refetch();
      }
      setCaptureState(UsageStats.getCaptureStatus());
    } catch (error) {
      setSyncError(error instanceof Error ? error.message : String(error));
    } finally {
      setSyncing(false);
    }
  }, [status, summary, records]);

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

      {/* --- sync action --- */}
      {canSync ? (
        <Section title="Sync" subtitle={`Reads the last ${DEFAULT_SYNC_DAYS} days`}>
          <Card>
            <Pressable
              onPress={runSync}
              disabled={syncing}
              style={({ pressed }) => [
                styles.button,
                styles.noTopMargin,
                {
                  backgroundColor: syncing
                    ? theme.backgroundSelected
                    : pressed
                      ? theme.primaryPressed
                      : theme.primary,
                },
              ]}
              accessibilityRole="button">
              {syncing ? (
                <ActivityIndicator size="small" color={theme.textSecondary} />
              ) : (
                <Ionicons name="sync" size={16} color={theme.onPrimary} />
              )}
              <Text
                style={[
                  Typography.bodyStrong,
                  { color: syncing ? theme.textSecondary : theme.onPrimary },
                ]}>
                {syncing ? 'Reading your phone...' : 'Sync now'}
              </Text>
            </Pressable>

            {/* Permission coverage, so missing columns are explained up front */}
            <View style={styles.coverage}>
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
                {captureState.firstDate} to {captureState.lastDate}
              </Text>
            ) : null}

            {/* The single most important number on this card. A run of silent
                background failures is invisible unless the last successful
                capture is shown, and by the time it is noticed the missing
                days are unrecoverable. */}
            <Text style={[Typography.caption, styles.gapTop, { color: theme.textMuted }]}>
              {captureState.lastCaptureMs
                ? `Last captured ${new Date(captureState.lastCaptureMs).toLocaleString()}`
                : 'Not captured yet on this device.'}
            </Text>

            {background ? (
              <View style={[styles.row, styles.gapTop]}>
                <Ionicons
                  name={background.registered ? 'sync-circle-outline' : 'warning-outline'}
                  size={14}
                  color={background.registered ? theme.riskLow : theme.riskMedium}
                />
                <Text style={[Typography.caption, styles.flex, { color: theme.textSecondary }]}>
                  {background.detail}
                </Text>
              </View>
            ) : null}
          </Card>
        </Section>
      ) : null}

      {/* --- sync result --- */}
      {syncError ? (
        <Card>
          <View style={styles.row}>
            <Ionicons name="alert-circle-outline" size={18} color={theme.riskHigh} />
            <Text style={[Typography.bodyStrong, styles.flex, { color: theme.riskHigh }]}>
              Sync failed
            </Text>
          </View>
          <Text style={[Typography.caption, styles.gapTop, { color: theme.textSecondary }]}>
            {syncError}
          </Text>
        </Card>
      ) : null}

      {capture?.captureBlocked ? (
        <Card>
          <View style={styles.row}>
            <Ionicons name="alert-circle-outline" size={16} color={theme.riskHigh} />
            <Text style={[Typography.bodyStrong, styles.flex, { color: theme.text }]}>
              Nothing could be captured
            </Text>
          </View>
          <Text style={[Typography.caption, styles.gapTop, { color: theme.textSecondary }]}>
            {capture.captureBlocked}
          </Text>
        </Card>
      ) : null}

      {/* An unreachable backend is a DEFERRAL, not a failure: the days are
          already stored on the phone. Saying "could not sync" here would be
          misleading, because the half that expires has already succeeded. */}
      {capture?.uploadDeferred ? (
        <Card>
          <View style={styles.row}>
            <Ionicons name="cloud-offline-outline" size={16} color={theme.riskMedium} />
            <Text style={[Typography.bodyStrong, styles.flex, { color: theme.text }]}>
              Saved on your phone, not yet sent
            </Text>
          </View>
          <Text style={[Typography.caption, styles.gapTop, { color: theme.textSecondary }]}>
            {capture.uploadDeferred}
          </Text>
        </Card>
      ) : null}

      {capture?.response ? (
        <Section title="Last sync">
          <Card>
            <View style={styles.statRow}>
              {[
                { label: 'Added', value: capture.response.created },
                { label: 'Updated', value: capture.response.updated },
                { label: 'Skipped', value: capture.response.skipped },
              ].map((stat) => (
                <View key={stat.label} style={styles.stat}>
                  <Text style={[Typography.heading, { color: theme.text }]}>{stat.value}</Text>
                  <Text style={[Typography.caption, { color: theme.textSecondary }]}>
                    {stat.label}
                  </Text>
                </View>
              ))}
            </View>

            {capture.capture ? (
              <Text style={[Typography.caption, styles.gapTop, { color: theme.textMuted }]}>
                read {capture.capture.daysRead} days from Android ·{' '}
                {capture.capture.created} newly stored · {capture.capture.updated} updated ·{' '}
                {capture.uploaded} sent
                {capture.capture.refusedDegraded > 0
                  ? ` · ${capture.capture.refusedDegraded} older read${
                      capture.capture.refusedDegraded === 1 ? '' : 's'
                    } refused for reporting less than the day already saved`
                  : ''}
              </Text>
            ) : null}

            {/* Why rows were skipped - never hidden */}
            {capture.response.skipped_detail.length > 0 ? (
              <View style={styles.skipped}>
                <Text style={[Typography.label, { color: theme.textMuted }]}>
                  WHY SOME DAYS WERE SKIPPED
                </Text>
                {capture.response.skipped_detail.slice(0, 4).map((item) => (
                  <Text
                    key={item.record_date}
                    style={[Typography.caption, { color: theme.textSecondary }]}>
                    {item.record_date}: {item.reason}
                  </Text>
                ))}
                {capture.response.skipped_detail.length > 4 ? (
                  <Text style={[Typography.caption, { color: theme.textMuted }]}>
                    and {capture.response.skipped_detail.length - 4} more
                  </Text>
                ) : null}
              </View>
            ) : null}

            {/* Fields that arrived empty, with the reason */}
            {Object.keys(capture.response.missing_fields).length > 0 ? (
              <View style={styles.skipped}>
                <Text style={[Typography.label, { color: theme.textMuted }]}>
                  FIELDS YOUR PHONE COULD NOT SUPPLY
                </Text>
                {Object.entries(capture.response.missing_fields).map(([field, reason]) => (
                  <Text key={field} style={[Typography.caption, { color: theme.textSecondary }]}>
                    {field.replace(/_/g, ' ')}: {reason}
                  </Text>
                ))}
              </View>
            ) : null}
          </Card>
        </Section>
      ) : null}

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
                  {data.first_date} to {data.last_date}
                </Text>
              ) : (
                <Text style={[Typography.caption, { color: theme.textSecondary }]}>
                  Nothing synced from the phone yet.
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
                        {field.replace(/_/g, ' ')}: {count} of {data.device_days} days
                        {partial && since ? ` · from ${since}` : ''}
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
                  No records yet. Sync to pull in your phone&apos;s usage.
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
                          {record.record_date}
                        </Text>
                        <Text style={[Typography.caption, { color: theme.textMuted }]}>
                          {record.source}
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
  gapTop: { marginTop: Spacing.two },
  listRow: { flexDirection: 'row', alignItems: 'center', gap: Spacing.two },
  coverage: { gap: Spacing.one, marginTop: Spacing.three },
  button: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: Spacing.two,
    height: 44,
    borderRadius: Radius.md,
    marginTop: Spacing.three,
  },
  noTopMargin: { marginTop: 0 },
  link: { marginTop: Spacing.two, textDecorationLine: 'underline' },
  statRow: { flexDirection: 'row', gap: Spacing.four },
  stat: { flex: 1, gap: 2 },
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
