/**
 * Recommendations screen (Screen 6) - wired to the KNN engine.
 *
 * THE DESIGN PROBLEM THIS SCREEN SOLVES
 * -------------------------------------
 * Two kinds of suggestion arrive from the backend and they are NOT equally
 * supported:
 *
 *   population_comparison  "people whose usage resembles yours and who are
 *                          classified lower-risk typically spend X less"
 *                          - an association measured across 7,500 people.
 *
 *   personal_baseline      "this behaviour has been unusual for you lately"
 *                          - measured against the user's own history, and
 *                          making no claim about risk at all.
 *
 * Rendering them as one undifferentiated list of tips would misrepresent
 * both. So each card shows its basis as a labelled chip, and the evidence
 * numbers sit beneath the advice rather than being hidden.
 *
 * Nothing here is phrased as a promise. The dataset is cross-sectional -
 * 7,500 people observed once each, nobody followed over time - so it holds
 * no outcomes and cannot say what happens after someone changes their
 * habits. The backend enforces that wording; this screen surfaces the policy
 * so the limitation is visible rather than implicit.
 */

import { Ionicons } from '@expo/vector-icons';
import { useCallback } from 'react';
import { RefreshControl, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ApiStateView } from '@/components/ui/api-states';
import { Card } from '@/components/ui/card';
import { Section } from '@/components/ui/section';
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
import type { Recommendation } from '@/types/api';

const BASIS_LABEL: Record<Recommendation['evidence_basis'], string> = {
  population_comparison: 'Compared with similar people',
  personal_baseline: 'Compared with your own normal',
};

const BASIS_ICON: Record<Recommendation['evidence_basis'], keyof typeof Ionicons.glyphMap> = {
  population_comparison: 'people-outline',
  personal_baseline: 'person-outline',
};

/** Keys worth showing from the evidence blob, with readable names. */
const EVIDENCE_LABELS: Record<string, string> = {
  your_value: 'Your average',
  low_risk_peer_median: 'Typical for lower-risk people',
  gap: 'Difference',
  n_low_risk_neighbours: 'People compared',
  days_deviant: 'Days affected',
  days_examined: 'Days examined',
  your_usual: 'Your usual',
  most_extreme_value: 'Most extreme',
};

function RecommendationCard({ item }: { item: Recommendation }) {
  const theme = useTheme();
  const isPopulation = item.evidence_basis === 'population_comparison';

  const evidenceRows = Object.entries(item.evidence)
    .filter(([key]) => key in EVIDENCE_LABELS)
    .map(([key, value]) => [EVIDENCE_LABELS[key], String(value)] as const);

  return (
    <Card>
      <View style={styles.rowBetween}>
        <View
          style={[
            styles.chip,
            { backgroundColor: isPopulation ? theme.primarySubtle : theme.backgroundSelected },
          ]}>
          <Ionicons
            name={BASIS_ICON[item.evidence_basis]}
            size={12}
            color={isPopulation ? theme.primary : theme.textSecondary}
          />
          <Text
            style={[
              Typography.caption,
              { color: isPopulation ? theme.primary : theme.textSecondary },
            ]}>
            {BASIS_LABEL[item.evidence_basis]}
          </Text>
        </View>
        <Text style={[Typography.label, { color: theme.textMuted }]}>{`#${item.priority}`}</Text>
      </View>

      <Text style={[Typography.subheading, styles.title, { color: theme.text }]}>
        {item.title}
      </Text>
      <Text style={[Typography.body, { color: theme.textSecondary }]}>{item.detail}</Text>

      <View style={[styles.action, { backgroundColor: theme.backgroundSelected }]}>
        <Ionicons name="arrow-forward-circle-outline" size={15} color={theme.primary} />
        <Text style={[Typography.caption, styles.flex, { color: theme.text }]}>
          {item.action}
        </Text>
      </View>

      {evidenceRows.length > 0 ? (
        <View style={[styles.evidence, { borderTopColor: theme.border }]}>
          <Text style={[Typography.label, { color: theme.textMuted }]}>THE NUMBERS</Text>
          {evidenceRows.map(([label, value]) => (
            <View key={label} style={styles.rowBetween}>
              <Text style={[Typography.caption, { color: theme.textSecondary }]}>{label}</Text>
              <Text style={[Typography.tabular, { color: theme.text }]}>{value}</Text>
            </View>
          ))}
        </View>
      ) : null}

      {typeof item.evidence.note === 'string' ? (
        <Text style={[Typography.caption, styles.note, { color: theme.textMuted }]}>
          {item.evidence.note}
        </Text>
      ) : null}
    </Card>
  );
}

export default function RecommendationsScreen() {
  const theme = useTheme();
  const { scope } = useScope();

  const recommendations = useApi(
    useCallback(() => api.getRecommendations(scope), [scope]),
    [scope]
  );

  return (
    <View style={[styles.root, { backgroundColor: theme.background }]}>
      <SafeAreaView style={styles.safeArea} edges={['left', 'right', 'bottom']}>
        <ScrollView
          contentContainerStyle={styles.content}
          showsVerticalScrollIndicator={false}
          refreshControl={
            <RefreshControl
              refreshing={recommendations.isRefreshing}
              onRefresh={recommendations.refetch}
              tintColor={theme.primary}
            />
          }>
          <Text style={[Typography.body, { color: theme.textSecondary }]}>
            Suggestions based on your own behaviour, with the reasoning shown for each.
          </Text>

          <ApiStateView
            state={recommendations}
            loadingLabel="Working out suggestions"
            notReadyHint="Needs at least 7 days of history.">
            {(data) => (
              <View style={styles.stack}>
                {data.recommendations.length === 0 ? (
                  <Card>
                    <View style={styles.row}>
                      <Ionicons name="checkmark-circle" size={18} color={theme.riskLow} />
                      <Text style={[Typography.body, styles.flex, { color: theme.textSecondary }]}>
                        Nothing stands out right now.
                      </Text>
                    </View>
                    {/* An empty list is a result, not a failure - say why. */}
                    <Text style={[Typography.caption, styles.note, { color: theme.textMuted }]}>
                      {!data.neighbourhood.comparison_possible &&
                      data.neighbourhood.why_no_comparison
                        ? data.neighbourhood.why_no_comparison
                        : 'Your usage is close to that of similar lower-risk people, and nothing has been unusual for you recently.'}
                    </Text>
                  </Card>
                ) : (
                  data.recommendations.map((item) => (
                    <RecommendationCard key={item.id} item={item} />
                  ))
                )}

                <Section title="How these were worked out">
                  <Card>
                    <View style={styles.rowBetween}>
                      <Text style={[Typography.caption, { color: theme.textSecondary }]}>
                        People with similar usage
                      </Text>
                      <Text style={[Typography.tabular, { color: theme.text }]}>
                        {data.neighbourhood.n_neighbours}
                      </Text>
                    </View>
                    <View style={styles.rowBetween}>
                      <Text style={[Typography.caption, { color: theme.textSecondary }]}>
                        Of those, classified lower-risk
                      </Text>
                      <Text style={[Typography.tabular, { color: theme.text }]}>
                        {data.neighbourhood.n_low_risk_neighbours}
                      </Text>
                    </View>
                    <View style={styles.rowBetween}>
                      <Text style={[Typography.caption, { color: theme.textSecondary }]}>
                        From your own history
                      </Text>
                      <Text style={[Typography.tabular, { color: theme.text }]}>
                        {data.sources.personal_baseline}
                      </Text>
                    </View>

                    {data.personal_baseline_note ? (
                      <Text style={[Typography.caption, styles.note, { color: theme.textMuted }]}>
                        {data.personal_baseline_note}
                      </Text>
                    ) : null}
                  </Card>
                </Section>

                <Section title="What these suggestions can claim">
                  <Card>
                    {data.claim_policy ? (
                      <>
                        <View style={styles.policyRow}>
                          <Ionicons name="checkmark-circle" size={14} color={theme.riskLow} />
                          <Text
                            style={[
                              Typography.caption,
                              styles.flex,
                              { color: theme.textSecondary },
                            ]}>
                            {data.claim_policy.allowed}
                          </Text>
                        </View>
                        <View style={styles.policyRow}>
                          <Ionicons name="close-circle" size={14} color={theme.riskHigh} />
                          <Text
                            style={[
                              Typography.caption,
                              styles.flex,
                              { color: theme.textSecondary },
                            ]}>
                            {data.claim_policy.forbidden}
                          </Text>
                        </View>
                      </>
                    ) : null}

                    {data.caveats.map((caveat) => (
                      <Text
                        key={caveat}
                        style={[Typography.caption, styles.note, { color: theme.textMuted }]}>
                        {caveat}
                      </Text>
                    ))}
                  </Card>
                </Section>
              </View>
            )}
          </ApiStateView>

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
  stack: { gap: Spacing.three },
  flex: { flex: 1 },
  row: { flexDirection: 'row', alignItems: 'center', gap: Spacing.two },
  rowBetween: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: Spacing.two,
  },
  chip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.one,
    borderRadius: Radius.pill,
    paddingVertical: Spacing.half + 1,
    paddingHorizontal: Spacing.two,
  },
  title: { marginTop: Spacing.two },
  action: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: Spacing.two,
    borderRadius: Radius.md,
    padding: Spacing.two + 2,
    marginTop: Spacing.three,
  },
  evidence: {
    marginTop: Spacing.three,
    paddingTop: Spacing.three,
    borderTopWidth: StyleSheet.hairlineWidth,
    gap: Spacing.one,
  },
  note: { marginTop: Spacing.two, lineHeight: 17 },
  policyRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: Spacing.two,
    marginBottom: Spacing.two,
  },
  bottomSpacer: { height: BottomTabInset + Spacing.four },
});
