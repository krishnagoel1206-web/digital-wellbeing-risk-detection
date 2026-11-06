/**
 * Home Dashboard (Screen 2) - wired to the live backend.
 *
 * WHAT LEADS THIS SCREEN, AND WHY
 * -------------------------------
 * The headline risk comes from the CLASSIFIER, not from the PCA score.
 *
 * Phase 7 validation measured the PCA composite at Spearman rho 0.18 against
 * the dataset's own labels, with mean scores of 53.4 for Medium and 54.4 for
 * High - under one point apart. It separates Low from the rest and then goes
 * flat, so it did not validate as a risk measure. The classifier reached
 * ROC-AUC 0.80 on held-out data, so it is the better-supported signal and is
 * what the dashboard leads with. The PCA score still appears, on the Insights
 * screen, labelled as a behaviour index with its caveat.
 *
 * Every number on this screen comes from the API. Nothing is hard-coded, and
 * where a model is untrained or history is too short the screen says so
 * rather than showing a placeholder figure.
 */

import { Ionicons } from '@expo/vector-icons';
import { Link } from 'expo-router';
import { useCallback } from 'react';
import { Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { LineChart } from '@/components/charts/line-chart';
import { ApiStateView } from '@/components/ui/api-states';
import { Card } from '@/components/ui/card';
import { RiskBadge } from '@/components/ui/risk-badge';
import { ScoreRing } from '@/components/ui/score-ring';
import { Section } from '@/components/ui/section';
import { StatTile } from '@/components/ui/stat-tile';
import {
  BottomTabInset,
  MaxContentWidth,
  Radius,
  Spacing,
  Typography,
} from '@/constants/theme';
import { useApi } from '@/hooks/use-api';
import { useScope } from '@/hooks/use-scope';
import { useTheme } from '@/hooks/use-theme';
import * as api from '@/services/api/endpoints';
import { formatCount, formatMinutes, formatPercent, percentChange, riskLevelKey } from '@/utils/format';

/** How many recent days the trend chart shows. */
const TREND_DAYS = 14;

function NavRow({
  href,
  icon,
  label,
  hint,
}: {
  href: '/recommendations' | '/model-performance';
  icon: keyof typeof Ionicons.glyphMap;
  label: string;
  hint: string;
}) {
  const theme = useTheme();
  return (
    <Link href={href} asChild>
      <Pressable
        style={({ pressed }) => [
          styles.navRow,
          { backgroundColor: pressed ? theme.backgroundSelected : 'transparent' },
        ]}>
        <View style={[styles.navIcon, { backgroundColor: theme.primarySubtle }]}>
          <Ionicons name={icon} size={18} color={theme.primary} />
        </View>
        <View style={styles.navText}>
          <Text style={[Typography.bodyStrong, { color: theme.text }]}>{label}</Text>
          <Text style={[Typography.caption, { color: theme.textSecondary }]}>{hint}</Text>
        </View>
        <Ionicons name="chevron-forward" size={18} color={theme.textMuted} />
      </Pressable>
    </Link>
  );
}

export default function HomeScreen() {
  const theme = useTheme();

  // Home passed no scope at all, so every call here fell back to 'all' and the
  // headline was computed over 359 generated demonstration days alongside the
  // user's own. Insights had been honest about this the whole time, which is
  // what made the two screens disagree. The endpoint helpers now REQUIRE a
  // scope, so this cannot be forgotten again without a compiler error.
  const { scope } = useScope();

  const summary = useApi(useCallback(() => api.getUsageSummary(), []));
  const risk = useApi(
    useCallback(() => api.getRiskClassification(scope), [scope]),
    [scope]
  );
  const records = useApi(
    useCallback(
      () => api.getUsageRecords(api.sourcesForScope(scope), { limit: TREND_DAYS }),
      [scope]
    ),
    [scope]
  );
  const anomalies = useApi(
    useCallback(() => api.getAnomalies(scope, { limit: 5, only_anomalous: true }), [scope]),
    [scope]
  );
  const score = useApi(useCallback(() => api.getRiskScore(scope), [scope]), [scope]);

  const refreshAll = useCallback(() => {
    summary.refetch();
    risk.refetch();
    records.refetch();
    anomalies.refetch();
    score.refetch();
  }, [summary, risk, records, anomalies, score]);

  const isRefreshing =
    summary.isRefreshing || risk.isRefreshing || records.isRefreshing;

  // Records arrive newest-first; reverse for a left-to-right time axis.
  const trend = records.data ? [...records.data].reverse() : [];
  const trendValues = trend.map((r) => r.total_screen_minutes / 60);

  const latest = records.data?.[0] ?? null;
  const previous = records.data?.[1] ?? null;

  return (
    <View style={[styles.root, { backgroundColor: theme.background }]}>
      <SafeAreaView style={styles.safeArea} edges={['top', 'left', 'right']}>
        <ScrollView
          contentContainerStyle={styles.content}
          showsVerticalScrollIndicator={false}
          refreshControl={
            <RefreshControl
              refreshing={isRefreshing}
              onRefresh={refreshAll}
              tintColor={theme.primary}
            />
          }>
          <View style={styles.header}>
            <Text style={[Typography.title, { color: theme.text }]}>Your wellbeing</Text>
            <Text style={[Typography.body, { color: theme.textSecondary }]}>
              A look at how you have been using your phone.
            </Text>
          </View>

          {/* Demonstration-data warning, driven by the API rather than a
              hard-coded flag, so it disappears automatically once the user
              has only their own records. */}
          {summary.data?.contains_synthetic_data ? (
            <View
              style={[
                styles.banner,
                { backgroundColor: theme.riskMediumSurface, borderColor: theme.border },
              ]}>
              <Ionicons name="flask-outline" size={13} color={theme.riskMedium} />
              <Text style={[Typography.caption, styles.flex, { color: theme.riskMedium }]}>
                Includes demonstration data
                {summary.data.records_by_source.synthetic
                  ? ` (${summary.data.records_by_source.synthetic} generated days)`
                  : ''}
              </Text>
            </View>
          ) : null}

          {/* --- HEADLINE: the classifier --- */}
          <Section
            title="Risk category"
            subtitle="Based on how your typical day compares with similar people">
            <ApiStateView
              state={risk}
              loadingLabel="Assessing your usage"
              notReadyHint="Add more daily records, or train the model on the backend.">
              {(data) => (
                <Card>
                  <View style={styles.heroRow}>
                    <ScoreRing
                      value={Math.round((data.confidence ?? 0) * 100)}
                      level={riskLevelKey(data.risk_class)}
                      size={140}
                      caption="confidence"
                    />
                    <View style={styles.heroMeta}>
                      <RiskBadge level={riskLevelKey(data.risk_class)} />
                      <Text style={[Typography.caption, { color: theme.textSecondary }]}>
                        Averaged over {data.days_averaged} day
                        {data.days_averaged === 1 ? '' : 's'} of your history.
                      </Text>
                      {data.model_accuracy.roc_auc_ovr !== null ? (
                        <Text style={[Typography.caption, { color: theme.textMuted }]}>
                          Model: {data.model} · ROC-AUC{' '}
                          {data.model_accuracy.roc_auc_ovr.toFixed(2)}
                        </Text>
                      ) : null}
                    </View>
                  </View>

                  {/* Class probabilities, so a 53/47 split is visible as the
                      near-tie it is rather than being presented as certainty. */}
                  <View style={styles.probRow}>
                    {(['Low', 'Medium', 'High'] as const).map((label) => {
                      const value = data.probabilities[label] ?? 0;
                      return (
                        <View key={label} style={styles.probItem}>
                          <Text style={[Typography.caption, { color: theme.textSecondary }]}>
                            {label}
                          </Text>
                          <Text style={[Typography.bodyStrong, { color: theme.text }]}>
                            {formatPercent(value)}
                          </Text>
                          <View
                            style={[styles.probTrack, { backgroundColor: theme.backgroundSelected }]}>
                            <View
                              style={[
                                styles.probFill,
                                {
                                  width: `${Math.max(0, Math.min(100, value * 100))}%`,
                                  backgroundColor: theme[
                                    label === 'Low'
                                      ? 'riskLow'
                                      : label === 'Medium'
                                        ? 'riskMedium'
                                        : 'riskHigh'
                                  ],
                                },
                              ]}
                            />
                          </View>
                        </View>
                      );
                    })}
                  </View>
                </Card>
              )}
            </ApiStateView>
          </Section>

          {/* --- Bayesian uncertainty, from the risk-score endpoint --- */}
          {score.data?.bayesian ? (
            <Section title="How often your days run high">
              <Card>
                <Text style={[Typography.heading, { color: theme.text }]}>
                  {formatPercent(score.data.bayesian.estimated_rate)}
                </Text>
                <Text style={[Typography.caption, { color: theme.textSecondary }]}>
                  of your days, with 95% confidence between{' '}
                  {formatPercent(score.data.bayesian.credible_interval[0])} and{' '}
                  {formatPercent(score.data.bayesian.credible_interval[1])}
                </Text>
                {!score.data.bayesian.is_reliable ? (
                  <Text style={[Typography.caption, styles.topGap, { color: theme.riskMedium }]}>
                    Not enough data yet for this to be reliable.
                  </Text>
                ) : null}
              </Card>
            </Section>
          ) : null}

          {/* --- Today's numbers --- */}
          <Section
            title={latest ? `Latest day · ${latest.record_date}` : 'Latest day'}
            subtitle="Your most recent recorded day">
            <ApiStateView state={records} loadingLabel="Loading your records">
              {(data) =>
                data.length === 0 ? (
                  <Card>
                    <Text style={[Typography.body, { color: theme.textSecondary }]}>
                      No usage records yet. Add your first day on the Data tab.
                    </Text>
                  </Card>
                ) : (
                  <Card>
                    <View style={styles.tileGrid}>
                      <StatTile
                        label="Screen time"
                        value={formatMinutes(latest?.total_screen_minutes)}
                        delta={percentChange(
                          latest?.total_screen_minutes,
                          previous?.total_screen_minutes
                        )}
                        goodDirection="down"
                      />
                      <StatTile
                        label="App opens"
                        value={formatCount(latest?.app_launches)}
                        delta={percentChange(latest?.app_launches, previous?.app_launches)}
                        goodDirection="down"
                      />
                    </View>
                    <View style={[styles.tileGrid, styles.topGap]}>
                      <StatTile
                        label="Notifications"
                        value={formatCount(latest?.notification_count)}
                        delta={percentChange(
                          latest?.notification_count,
                          previous?.notification_count
                        )}
                        goodDirection="down"
                      />
                      <StatTile
                        label="Late-night use"
                        value={formatMinutes(latest?.late_night_minutes)}
                        hint="11pm - 6am"
                      />
                    </View>
                  </Card>
                )
              }
            </ApiStateView>
          </Section>

          {/* --- Trend --- */}
          <Section title={`Last ${TREND_DAYS} days`} subtitle="Daily screen time in hours">
            <ApiStateView state={records} loadingLabel="Loading trend">
              {(data) =>
                data.length < 2 ? (
                  <Card>
                    <Text style={[Typography.caption, { color: theme.textSecondary }]}>
                      At least two days are needed to draw a trend.
                    </Text>
                  </Card>
                ) : (
                  <Card>
                    <LineChart
                      data={trendValues}
                      height={170}
                      color={theme.series1}
                      gridColor={theme.grid}
                      showArea
                      showDots
                      startAtZero
                      gradientId="homeTrend"
                    />
                    <View style={styles.axisRow}>
                      <Text style={[Typography.tabular, { color: theme.textMuted }]}>
                        {trend[0]?.record_date.slice(5)}
                      </Text>
                      <Text style={[Typography.tabular, { color: theme.textMuted }]}>
                        {trend[trend.length - 1]?.record_date.slice(5)}
                      </Text>
                    </View>
                  </Card>
                )
              }
            </ApiStateView>
          </Section>

          {/* --- Anomaly alerts --- */}
          <Section title="Unusual days" subtitle="Compared with your own normal">
            <ApiStateView
              state={anomalies}
              loadingLabel="Checking your baseline"
              notReadyHint="A personal baseline needs about two weeks of records.">
              {(data) =>
                data.days.length === 0 ? (
                  <Card>
                    <View style={styles.row}>
                      <Ionicons name="checkmark-circle" size={18} color={theme.riskLow} />
                      <Text style={[Typography.body, styles.flex, { color: theme.textSecondary }]}>
                        Nothing unusual in your recent history.
                      </Text>
                    </View>
                  </Card>
                ) : (
                  <Card noPadding>
                    {data.days.map((day, index) => (
                      <View key={day.date}>
                        {index > 0 ? (
                          <View style={[styles.divider, { backgroundColor: theme.border }]} />
                        ) : null}
                        <View style={styles.anomalyRow}>
                          <View style={styles.anomalyHeader}>
                            <Text style={[Typography.bodyStrong, { color: theme.text }]}>
                              {day.date}
                            </Text>
                            <RiskBadge
                              level={day.severity === 'severe' ? 'high' : 'medium'}
                              size="sm"
                            />
                          </View>
                          <Text style={[Typography.caption, { color: theme.textSecondary }]}>
                            {day.explanation}
                          </Text>
                        </View>
                      </View>
                    ))}
                  </Card>
                )
              }
            </ApiStateView>
          </Section>

          {/* --- Reports --- */}
          <Section title="Reports">
            <Card noPadding>
              <NavRow
                href="/recommendations"
                icon="bulb-outline"
                label="Recommendations"
                hint="Suggestions, with the evidence behind each"
              />
              <View style={[styles.divider, { backgroundColor: theme.border }]} />
              <NavRow
                href="/model-performance"
                icon="speedometer-outline"
                label="Model performance"
                hint="Accuracy, F1, ROC-AUC, RMSE"
              />
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
    gap: Spacing.four,
  },
  header: { gap: Spacing.one },
  flex: { flex: 1 },
  row: { flexDirection: 'row', alignItems: 'center', gap: Spacing.two },
  topGap: { marginTop: Spacing.three },
  banner: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.one + 2,
    borderRadius: Radius.md,
    borderWidth: StyleSheet.hairlineWidth,
    paddingVertical: Spacing.two,
    paddingHorizontal: Spacing.two + 2,
  },
  heroRow: { flexDirection: 'row', alignItems: 'center', gap: Spacing.three },
  heroMeta: { flex: 1, gap: Spacing.two },
  probRow: {
    flexDirection: 'row',
    gap: Spacing.three,
    marginTop: Spacing.four,
  },
  probItem: { flex: 1, gap: 2 },
  probTrack: {
    height: 4,
    borderRadius: Radius.pill,
    overflow: 'hidden',
    marginTop: Spacing.one,
  },
  probFill: { height: 4, borderRadius: Radius.pill },
  tileGrid: { flexDirection: 'row', gap: Spacing.three },
  axisRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginTop: Spacing.one,
  },
  anomalyRow: {
    paddingVertical: Spacing.three,
    paddingHorizontal: Spacing.three,
    gap: Spacing.one,
  },
  anomalyHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: Spacing.two,
  },
  navRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.three,
    paddingVertical: Spacing.three,
    paddingHorizontal: Spacing.three,
  },
  navIcon: {
    width: 36,
    height: 36,
    borderRadius: Radius.md,
    alignItems: 'center',
    justifyContent: 'center',
  },
  navText: { flex: 1, gap: 1 },
  divider: { height: StyleSheet.hairlineWidth, marginHorizontal: Spacing.three },
  bottomSpacer: { height: BottomTabInset + Spacing.four },
});
