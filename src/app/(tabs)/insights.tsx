/**
 * ML Insights screen (Screen 5) - wired to the live models.
 *
 * This is where model OUTPUT appears, as opposed to the Analytics tab which
 * shows measurements. Every section:
 *
 *   - names the model behind it,
 *   - shows that model's measured accuracy next to its prediction, and
 *   - renders an explanation, not a number, when the model cannot answer.
 *
 * The last point matters most. Each endpoint returns HTTP 409 with a real
 * message when a model is untrained or history is too short, and
 * ApiStateView turns that into an informational panel. "Not enough data yet"
 * is therefore a visible, explained state rather than a blank card or - far
 * worse - a plausible-looking fabricated figure.
 *
 * ORDERING IS DELIBERATE
 * ----------------------
 * The classifier leads because it is the only risk signal that validated
 * (ROC-AUC 0.80 on held-out data). The PCA composite sits lower down and is
 * called a behaviour index, because it did NOT validate: it separates Low
 * from the rest but places Medium at 53.4 and High at 54.4 - barely a point
 * apart.
 */

import { Ionicons } from '@expo/vector-icons';
import { useCallback } from 'react';
import { RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { LineChart } from '@/components/charts/line-chart';
import { ApiStateView } from '@/components/ui/api-states';
import { Card } from '@/components/ui/card';
import { RiskConfidenceCard } from '@/components/insights/risk-confidence-card';
import { RiskBadge } from '@/components/ui/risk-badge';
import { ScopeSelector } from '@/components/ui/scope-selector';
import { Section } from '@/components/ui/section';
import { SeverityTag, combinationLine } from '@/components/ui/severity-tag';
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
import { formatMinutes, formatPercent, formatShortDate, riskLevelKey } from '@/utils/format';

/** Muted aside used for caveats, so they read as context not findings. */
function Caveat({ children }: { children: string }) {
  const theme = useTheme();
  return (
    <View style={styles.caveat}>
      <Ionicons name="information-circle-outline" size={12} color={theme.textMuted} />
      <Text style={[Typography.caption, styles.flex, { color: theme.textMuted }]}>
        {children}
      </Text>
    </View>
  );
}

/**
 * States, prominently, that a model was not fitted on the user's own data.
 *
 * This is a banner rather than another line of small print at the bottom of
 * the card, because the thing it corrects is a WRONG READING of the numbers
 * directly above it: predictions produced from a model trained on generated
 * days look exactly like a personal forecast. A caveat below the chart is read
 * after the misreading has already happened, if at all.
 *
 * Carries an icon and the word "Demonstration" alongside the amber, so the
 * status is never conveyed by colour alone.
 */
function ProvenanceBanner({
  fittedOn,
  note,
  readiness,
}: {
  fittedOn: 'real' | 'mixed' | 'synthetic' | 'unknown';
  note: string;
  readiness?: string;
}) {
  const theme = useTheme();
  if (fittedOn === 'real') return null;

  return (
    <View style={[styles.banner, { backgroundColor: theme.riskMediumSurface }]}>
      <Ionicons name="flask-outline" size={14} color={theme.riskMedium} />
      <View style={styles.flex}>
        <Text style={[Typography.bodyStrong, { color: theme.text }]}>
          Demonstration, not a forecast about you
        </Text>
        <Text style={[Typography.caption, styles.bannerBody, { color: theme.textSecondary }]}>
          {note}
        </Text>
        {readiness ? (
          <Text style={[Typography.caption, styles.bannerBody, { color: theme.textMuted }]}>
            {readiness}
          </Text>
        ) : null}
      </View>
    </View>
  );
}

export default function InsightsScreen() {
  const theme = useTheme();
  const { scope } = useScope();

  // Every query is keyed on scope, so switching it refetches everything.
  const classification = useApi(
    useCallback(() => api.getRiskClassification(scope), [scope]),
    [scope]
  );
  const forecast = useApi(useCallback(() => api.getForecast(scope, 7), [scope]), [scope]);
  const persona = useApi(useCallback(() => api.getPersona(scope), [scope]), [scope]);
  const anomalies = useApi(
    useCallback(() => api.getAnomalies(scope, { limit: 10, only_anomalous: true }), [scope]),
    [scope]
  );
  const confidence = useApi(
    useCallback(() => api.getRiskConfidence(scope, 12), [scope]),
    [scope]
  );

  const refreshAll = useCallback(() => {
    classification.refetch();
    confidence.refetch();
    forecast.refetch();
    persona.refetch();
    anomalies.refetch();
  }, [classification, confidence, forecast, persona, anomalies]);

  return (
    <View style={[styles.root, { backgroundColor: theme.background }]}>
      <SafeAreaView style={styles.safeArea} edges={['top', 'left', 'right']}>
        <ScrollView
          contentContainerStyle={styles.content}
          showsVerticalScrollIndicator={false}
          refreshControl={
            <RefreshControl
              refreshing={classification.isRefreshing}
              onRefresh={refreshAll}
              tintColor={theme.primary}
            />
          }>
          <View style={styles.header}>
            <Text style={[Typography.title, { color: theme.text }]}>Insights</Text>
            <Text style={[Typography.body, { color: theme.textSecondary }]}>
              What the models found. Each shows how accurate it actually is.
            </Text>
          </View>

          <Card>
            <ScopeSelector />
          </Card>

          {/* ---------- 1. classification: the validated risk signal ---------- */}
          <Section title="Risk category" subtitle="Classifier trained on 7,500 people">
            <ApiStateView
              state={classification}
              loadingLabel="Classifying your usage"
              notReadyHint="Sync more days, or change the scope above.">
              {(data) => (
                <Card>
                  <View style={styles.rowBetween}>
                    <RiskBadge level={riskLevelKey(data.risk_class)} />
                    <Text style={[Typography.caption, { color: theme.textMuted }]}>
                      {data.days_averaged} days averaged
                    </Text>
                  </View>

                  <View style={styles.probs}>
                    {(['Low', 'Medium', 'High'] as const).map((label) => {
                      const value = data.probabilities[label] ?? 0;
                      const colour =
                        label === 'Low'
                          ? theme.riskLow
                          : label === 'Medium'
                            ? theme.riskMedium
                            : theme.riskHigh;
                      return (
                        <View key={label} style={styles.probItem}>
                          <Text style={[Typography.caption, { color: theme.textSecondary }]}>
                            {label}
                          </Text>
                          <Text style={[Typography.bodyStrong, { color: theme.text }]}>
                            {formatPercent(value)}
                          </Text>
                          <View
                            style={[styles.track, { backgroundColor: theme.backgroundSelected }]}>
                            <View
                              style={[
                                styles.fill,
                                {
                                  width: `${Math.max(0, Math.min(100, value * 100))}%`,
                                  backgroundColor: colour,
                                },
                              ]}
                            />
                          </View>
                        </View>
                      );
                    })}
                  </View>

                  <View style={[styles.metrics, { borderTopColor: theme.border }]}>
                    <Text style={[Typography.label, { color: theme.textMuted }]}>
                      HOW GOOD IS THIS MODEL
                    </Text>
                    <Text style={[Typography.caption, { color: theme.textSecondary }]}>
                      {data.model} · ROC-AUC{' '}
                      {data.model_accuracy.roc_auc_ovr?.toFixed(2) ?? '--'} · accuracy{' '}
                      {formatPercent(data.model_accuracy.test_accuracy)} against a{' '}
                      {formatPercent(data.model_accuracy.majority_baseline)} baseline
                    </Text>
                  </View>

                  {data.caveats
                    .filter((c): c is string => Boolean(c))
                    .map((caveat) => (
                      <Caveat key={caveat}>{caveat}</Caveat>
                    ))}
                </Card>
              )}
            </ApiStateView>
          </Section>

          {/* ---------- 1b. Bayesian confidence in the class ---------- */}
          <Section
            title="How sure are we?"
            subtitle="Bayesian check: each recent week classified on its own">
            <ApiStateView
              state={confidence}
              loadingLabel="Weighing the evidence"
              notReadyHint="Needs at least two weeks with 5+ recorded days each.">
              {(data) => <RiskConfidenceCard data={data} />}
            </ApiStateView>
          </Section>

          {/* ---------- 2. forecast ---------- */}
          <Section title="Predicted screen time" subtitle="Next 7 days">
            <ApiStateView
              state={forecast}
              loadingLabel="Forecasting"
              notReadyHint="Needs a run of consecutive days to fill the model's lag features.">
              {(data) => (
                <Card>
                  {data.training_data ? (
                    <ProvenanceBanner
                      fittedOn={data.training_data.fitted_on}
                      note={data.training_data.note}
                      readiness={
                        data.personal_model.can_train_on_your_data
                          ? undefined
                          : data.personal_model.explanation
                      }
                    />
                  ) : null}

                  <LineChart
                    data={data.predictions.map((p) => p.predicted_hours)}
                    height={150}
                    color={theme.series3}
                    gridColor={theme.grid}
                    showArea
                    showDots
                    startAtZero
                    gradientId="forecastChart"
                  />

                  <View style={styles.forecastRow}>
                    {data.predictions.slice(0, 4).map((p) => (
                      <View key={p.date} style={styles.forecastItem}>
                        <Text style={[Typography.caption, { color: theme.textMuted }]}>
                          {p.date.slice(5)}
                        </Text>
                        <Text style={[Typography.bodyStrong, { color: theme.text }]}>
                          {formatMinutes(p.predicted_minutes)}
                        </Text>
                        {/* "firm" was wrong for the first day: it does not
                            compound earlier predictions, but it is still a
                            prediction. And now that the day in progress is
                            excluded from the model's inputs, that first day is
                            TODAY - a whole-day total, deliberately larger than
                            the day-so-far shown on Usage. Saying so here is the
                            difference between a useful number and one that
                            looks like a contradiction. */}
                        <Text
                          style={[
                            Typography.caption,
                            { color: p.is_recursive ? theme.textMuted : theme.series3 },
                          ]}>
                          {p.is_current_day
                            ? 'today, full day'
                            : p.is_recursive
                              ? 'compounds'
                              : 'from history'}
                        </Text>
                      </View>
                    ))}
                  </View>

                  {data.week_comparison ? (
                    <View style={styles.row}>
                      <Ionicons
                        name={
                          data.week_comparison.within_model_error
                            ? 'remove'
                            : data.week_comparison.change_minutes_per_day > 0
                              ? 'trending-up'
                              : 'trending-down'
                        }
                        size={16}
                        color={theme.textSecondary}
                      />
                      <Text style={[Typography.caption, styles.flex, { color: theme.text }]}>
                        {data.week_comparison.summary}
                      </Text>
                    </View>
                  ) : null}

                  <View style={[styles.metrics, { borderTopColor: theme.border }]}>
                    <Text style={[Typography.label, { color: theme.textMuted }]}>
                      HOW GOOD IS THIS MODEL
                    </Text>
                    <Text style={[Typography.caption, { color: theme.textSecondary }]}>
                      {data.model} · typical error{' '}
                      {formatMinutes(data.accuracy.rmse_minutes)} versus{' '}
                      {formatMinutes(data.accuracy.naive_baseline_rmse)} for simply guessing
                      today&apos;s figure
                    </Text>
                    {data.personal ? (
                      <View style={styles.personal}>
                        <Text style={[Typography.caption, { color: theme.text }]}>
                          {`Trained on this phone from your ${data.personal.days_used} real days (${formatShortDate(
                            data.personal.first_date
                          )} – ${formatShortDate(data.personal.last_date)}). Each option below was tested by forecasting 7 days from points in your past:`}
                        </Text>
                        {data.personal.candidates.map((c) => (
                          <View key={c.name} style={styles.rowBetween}>
                            <Text
                              style={[
                                Typography.caption,
                                { color: c.name === data.personal?.chosen ? theme.text : theme.textSecondary },
                              ]}>
                              {`${c.name === data.personal?.chosen ? '✓ ' : '   '}${c.name}${c.kind === 'baseline' ? ' (simple rule)' : ''}`}
                            </Text>
                            <Text style={[Typography.tabular, { color: theme.textSecondary }]}>
                              {`±${formatMinutes(c.rmse_minutes)}`}
                            </Text>
                          </View>
                        ))}
                      </View>
                    ) : null}
                  </View>

                  {data.caveats
                    .filter((c): c is string => Boolean(c))
                    .map((caveat) => (
                      <Caveat key={caveat}>{caveat}</Caveat>
                    ))}
                </Card>
              )}
            </ApiStateView>
          </Section>

          {/* ---------- 3. anomalies ---------- */}
          <Section title="Unusual days" subtitle="Measured against your own normal">
            <ApiStateView
              state={anomalies}
              loadingLabel="Comparing with your baseline"
              notReadyHint="A personal baseline needs about two weeks of records.">
              {(data) =>
                data.days.length === 0 ? (
                  <Card>
                    <View style={styles.row}>
                      <Ionicons name="checkmark-circle" size={18} color={theme.riskLow} />
                      <Text style={[Typography.body, styles.flex, { color: theme.textSecondary }]}>
                        Nothing unusual across {data.days_assessed} days.
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
                        <View style={styles.anomaly}>
                          <View style={styles.rowBetween}>
                            <Text style={[Typography.bodyStrong, { color: theme.text }]}>
                              {day.date}
                            </Text>
                            {/* A change from your own normal, not a risk level. */}
                            <SeverityTag severity={day.severity} />
                          </View>
                          <Text style={[Typography.caption, { color: theme.textSecondary }]}>
                            {day.explanation}
                          </Text>
                          {day.contributing_features
                            .filter((feature) => feature.feature !== 'social_ratio')
                            .slice(0, 3)
                            .map((feature) => (
                            <Text
                              key={feature.feature}
                              style={[Typography.caption, { color: theme.textMuted }]}>
                              {feature.label}: {feature.value} vs your usual {feature.your_usual}
                            </Text>
                          ))}
                          {combinationLine(day) ? (
                            <Text style={[Typography.caption, { color: theme.textMuted }]}>
                              {combinationLine(day)}
                            </Text>
                          ) : null}
                        </View>
                      </View>
                    ))}
                  </Card>
                )
              }
            </ApiStateView>
          </Section>

          {/* ---------- 4. persona ---------- */}
          <Section title="Your closest group" subtitle="From clustering 7,500 people">
            <ApiStateView
              state={persona}
              loadingLabel="Finding your group"
              notReadyHint="Check Usage Data to see which measurements are still building up.">
              {(data) => (
                <Card>
                  <Text style={[Typography.heading, { color: theme.text }]}>{data.name}</Text>
                  <Text style={[Typography.caption, { color: theme.textSecondary }]}>
                    {formatPercent(data.population_share)} of people fall in this group
                  </Text>

                  {/* The structure verdict always travels with the persona, so
                      a weak grouping can never be shown as a firm finding. */}
                  {data.reliability.structure === 'weak' ||
                  data.reliability.structure === 'none' ? (
                    <Caveat>
                      {`Grouping is ${data.reliability.structure}: this is your closest match, not a distinct behavioural type.`}
                    </Caveat>
                  ) : null}
                  {data.assignment_was_close_run ? (
                    <Caveat>
                      You sit almost exactly between two groups, so this could easily have gone
                      the other way.
                    </Caveat>
                  ) : null}
                </Card>
              )}
            </ApiStateView>
          </Section>

          <View style={styles.bottomSpacer} />
        </ScrollView>
      </SafeAreaView>
    </View>
  );
}

const styles = StyleSheet.create({
  banner: {
    flexDirection: 'row',
    gap: Spacing.two,
    padding: Spacing.three,
    borderRadius: Radius.md,
    marginBottom: Spacing.three,
  },
  bannerBody: { marginTop: Spacing.one, lineHeight: 17 },
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
  personal: { gap: 4, marginTop: Spacing.two },
  rowBetween: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: Spacing.two,
  },
  probs: { flexDirection: 'row', gap: Spacing.three, marginTop: Spacing.three },
  probItem: { flex: 1, gap: 2 },
  track: { height: 4, borderRadius: Radius.pill, overflow: 'hidden', marginTop: Spacing.one },
  fill: { height: 4, borderRadius: Radius.pill },
  metrics: {
    marginTop: Spacing.three,
    paddingTop: Spacing.three,
    borderTopWidth: StyleSheet.hairlineWidth,
    gap: Spacing.one,
  },
  caveat: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: Spacing.one + 2,
    marginTop: Spacing.two,
  },
  forecastRow: { flexDirection: 'row', gap: Spacing.two, marginTop: Spacing.three },
  forecastItem: { flex: 1, gap: 1 },
  anomaly: {
    paddingVertical: Spacing.three,
    paddingHorizontal: Spacing.three,
    gap: Spacing.one,
  },
  divider: { height: StyleSheet.hairlineWidth, marginHorizontal: Spacing.three },
  bottomSpacer: { height: BottomTabInset + Spacing.four },
});
