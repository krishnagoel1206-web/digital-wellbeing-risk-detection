/**
 * Analytics screen (Screen 4) - wired to real data.
 *
 * DESCRIPTIVE ONLY. Everything here is a direct measurement from the stored
 * records: no model, no prediction, no inference. Keeping that separate from
 * the Insights tab is deliberate - a user should always be able to tell
 * whether a number was measured or predicted, and the simplest guarantee is
 * to put them on different screens.
 *
 * The category breakdown is a single-hue BAR chart, not a donut. Phase 2
 * palette validation showed only the first three categorical colours stay
 * distinguishable when every mark may be compared with every other, which is
 * exactly a donut's situation; four categories would fail that test.
 */

import { useCallback, useMemo } from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { BarChart, type BarDatum } from '@/components/charts/bar-chart';
import { LineChart } from '@/components/charts/line-chart';
import { ApiStateView } from '@/components/ui/api-states';
import { Card } from '@/components/ui/card';
import { Screen } from '@/components/ui/screen';
import { ScopeSelector } from '@/components/ui/scope-selector';
import { Section } from '@/components/ui/section';
import { StatTile } from '@/components/ui/stat-tile';
import { Spacing, Typography } from '@/constants/theme';
import { useApi } from '@/hooks/use-api';
import { useScope } from '@/hooks/use-scope';
import { useTheme } from '@/hooks/use-theme';
import * as api from '@/services/api/endpoints';
import type { UsageRecord } from '@/types/api';
import { formatCount, formatMinutes, formatShortDate } from '@/utils/format';

/** Mean of the defined values only; null when nothing is defined.
 *  Returning null rather than 0 keeps "not recorded" distinct from "zero". */
function meanOf(values: (number | null | undefined)[]): number | null {
  const defined = values.filter((v): v is number => v !== null && v !== undefined);
  if (defined.length === 0) return null;
  return defined.reduce((sum, v) => sum + v, 0) / defined.length;
}

export default function AnalyticsScreen() {
  const theme = useTheme();
  const { scope } = useScope();

  // These charts describe the user's own behaviour, so they follow the scope
  // rather than silently including generated demonstration days.
  const records = useApi(
    useCallback(
      () => api.getUsageRecords(api.sourcesForScope(scope), { limit: 60 }),
      [scope]
    ),
    [scope]
  );

  // Records arrive newest-first; analysis reads better oldest-first.
  const ordered = useMemo(
    () => (records.data ? [...records.data].reverse() : []),
    [records.data]
  );

  const stats = useMemo(() => {
    if (ordered.length === 0) return null;

    const peak = ordered.reduce<UsageRecord | null>(
      (best, r) =>
        best === null || r.total_screen_minutes > best.total_screen_minutes ? r : best,
      null
    );

    // Weekday vs weekend, derived from the date rather than assumed.
    const weekend: number[] = [];
    const weekday: number[] = [];
    for (const record of ordered) {
      const day = new Date(`${record.record_date}T00:00:00`).getDay();
      (day === 0 || day === 6 ? weekend : weekday).push(record.total_screen_minutes);
    }

    return {
      avgScreen: meanOf(ordered.map((r) => r.total_screen_minutes)),
      peak,
      weekendAvg: meanOf(weekend),
      weekdayAvg: meanOf(weekday),
      avgLaunches: meanOf(ordered.map((r) => r.app_launches)),
      avgUnlocks: meanOf(ordered.map((r) => r.unlock_count)),
      avgNotifications: meanOf(ordered.map((r) => r.notification_count)),
      avgLateNight: meanOf(ordered.map((r) => r.late_night_minutes)),
      avgSleep: meanOf(ordered.map((r) => r.sleep_hours)),
      social: meanOf(ordered.map((r) => r.social_minutes)),
      gaming: meanOf(ordered.map((r) => r.gaming_minutes)),
      productivity: meanOf(ordered.map((r) => r.productivity_minutes)),
    };
  }, [ordered]);

  const categoryBars: BarDatum[] = useMemo(() => {
    if (!stats) return [];
    const known = (stats.social ?? 0) + (stats.gaming ?? 0) + (stats.productivity ?? 0);
    // Whatever is not attributed to a named category. Shown explicitly so the
    // parts visibly add up to the whole rather than quietly going missing.
    const other = Math.max(0, (stats.avgScreen ?? 0) - known);

    return [
      { label: 'Social media', value: stats.social ?? 0, display: formatMinutes(stats.social) },
      {
        label: 'Work and study',
        value: stats.productivity ?? 0,
        display: formatMinutes(stats.productivity),
      },
      { label: 'Games', value: stats.gaming ?? 0, display: formatMinutes(stats.gaming) },
      { label: 'Everything else', value: other, display: formatMinutes(other) },
    ].filter((bar) => bar.value > 0);
  }, [stats]);

  const lateNightBars: BarDatum[] = useMemo(
    () =>
      ordered.slice(-7).map((r) => ({
        label: formatShortDate(r.record_date),
        value: r.late_night_minutes ?? 0,
        display: formatMinutes(r.late_night_minutes),
      })),
    [ordered]
  );

  return (
    <Screen
      title="Analytics"
      subtitle="What your usage actually looks like. Measured, not predicted.">
      <Card>
        <ScopeSelector />
      </Card>

      <ApiStateView state={records} loadingLabel="Loading your records">
        {(data) =>
          data.length === 0 ? (
            <Card>
              <Text style={[Typography.body, { color: theme.textSecondary }]}>
                No records yet. Sync your phone on the Data tab.
              </Text>
            </Card>
          ) : (
            <View style={styles.stack}>
              <Section title="Typical day" subtitle={`Averaged over ${ordered.length} days`}>
                <Card>
                  <View style={styles.grid}>
                    <StatTile
                      label="Screen time"
                      value={formatMinutes(stats?.avgScreen)}
                      hint="per day"
                    />
                    <StatTile
                      label="App opens"
                      value={formatCount(stats?.avgLaunches)}
                      hint="per day"
                    />
                  </View>
                  <View style={[styles.grid, styles.gapTop]}>
                    <StatTile
                      label="Unlocks"
                      value={formatCount(stats?.avgUnlocks)}
                      hint="per day"
                    />
                    <StatTile
                      label="Late-night use"
                      value={formatMinutes(stats?.avgLateNight)}
                      hint="11pm - 6am"
                    />
                  </View>
                  <View style={[styles.grid, styles.gapTop]}>
                    <StatTile
                      label="Notifications"
                      value={formatCount(stats?.avgNotifications)}
                      hint={
                        stats?.avgNotifications === null ? 'not recorded yet' : 'per day'
                      }
                    />
                    <StatTile
                      label="Sleep (estimated)"
                      value={
                        stats?.avgSleep !== null && stats?.avgSleep !== undefined
                          ? `${stats.avgSleep.toFixed(1)}h`
                          : '--'
                      }
                      hint="from inactivity"
                    />
                  </View>
                </Card>
              </Section>

              <Section title="Daily screen time" subtitle="Hours per day over the period">
                <Card>
                  <LineChart
                    data={ordered.map((r) => r.total_screen_minutes / 60)}
                    labels={ordered.map((r) => formatShortDate(r.record_date))}
                    formatValue={(_, i) => formatMinutes(ordered[i].total_screen_minutes)}
                    height={180}
                    color={theme.series1}
                    gridColor={theme.grid}
                    showArea
                    showDots={ordered.length <= 20}
                    startAtZero
                    gradientId="analyticsTrend"
                  />
                  <View style={styles.axis}>
                    <Text style={[Typography.tabular, { color: theme.textMuted }]}>
                      {ordered[0] ? formatShortDate(ordered[0].record_date) : ''}
                    </Text>
                    <Text style={[Typography.tabular, { color: theme.textMuted }]}>
                      {ordered.length > 0
                        ? formatShortDate(ordered[ordered.length - 1].record_date)
                        : ''}
                    </Text>
                  </View>
                </Card>
              </Section>

              <Section title="Where the time goes" subtitle="Average minutes per day by category">
                <Card>
                  <BarChart
                    data={categoryBars}
                    color={theme.series1}
                    emptyMessage="No category breakdown available for these days."
                  />
                </Card>
              </Section>

              <Section title="Late-night use" subtitle="Minutes between 11pm and 6am">
                <Card>
                  <BarChart
                    data={lateNightBars}
                    color={theme.series2}
                    emptyMessage="No late-night usage recorded."
                  />
                </Card>
              </Section>

              <Section title="Weekdays vs weekends">
                <Card>
                  <View style={styles.grid}>
                    <StatTile label="Weekday average" value={formatMinutes(stats?.weekdayAvg)} />
                    <StatTile label="Weekend average" value={formatMinutes(stats?.weekendAvg)} />
                  </View>
                  {stats?.weekdayAvg != null && stats?.weekendAvg != null ? (
                    <Text
                      style={[Typography.caption, styles.gapTop, { color: theme.textSecondary }]}>
                      {stats.weekendAvg > stats.weekdayAvg
                        ? `About ${formatMinutes(stats.weekendAvg - stats.weekdayAvg)} more per day at weekends.`
                        : `About ${formatMinutes(stats.weekdayAvg - stats.weekendAvg)} more per day on weekdays.`}
                    </Text>
                  ) : (
                    <Text style={[Typography.caption, styles.gapTop, { color: theme.textMuted }]}>
                      Not enough days of each kind to compare yet.
                    </Text>
                  )}
                </Card>
              </Section>

              {stats?.peak ? (
                <Section title="Heaviest day in this period">
                  <Card>
                    <Text style={[Typography.heading, { color: theme.text }]}>
                      {formatMinutes(stats.peak.total_screen_minutes)}
                    </Text>
                    <Text style={[Typography.caption, { color: theme.textSecondary }]}>
                      {formatShortDate(stats.peak.record_date)} ·{' '}
                      {formatCount(stats.peak.app_launches)} app opens ·{' '}
                      {formatMinutes(stats.peak.late_night_minutes)} late at night
                    </Text>
                  </Card>
                </Section>
              ) : null}
            </View>
          )
        }
      </ApiStateView>
    </Screen>
  );
}

const styles = StyleSheet.create({
  stack: { gap: Spacing.four },
  grid: { flexDirection: 'row', gap: Spacing.three },
  gapTop: { marginTop: Spacing.three },
  axis: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginTop: Spacing.one,
  },
});
