/**
 * "How sure are we?" - the Bayesian layer over the risk classifier.
 *
 * Each recent week is classified on its own; the soft results are combined
 * with a Dirichlet(1,1,1) prior. The card leads with the posterior
 * probability of the most likely class, then shows how often each class
 * occurs with a credible interval, and the week-by-week record it came from.
 */

import { Ionicons } from '@expo/vector-icons';
import { useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { Card } from '@/components/ui/card';
import { Radius, Spacing, Typography } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import type { RiskConfidenceResponse, RiskLevelName } from '@/types/api';
import { formatPercent, formatShortDate } from '@/utils/format';

export function RiskConfidenceCard({ data: c }: { data: RiskConfidenceResponse }) {
  const theme = useTheme();
  const colorFor = (k: RiskLevelName) =>
    ({ Low: theme.riskLow, Medium: theme.riskMedium, High: theme.riskHigh })[k];
  const top = c.most_likely_class;
  const [showMethod, setShowMethod] = useState(false);

  return (
    <Card>
      <View style={styles.stack}>
        <View style={styles.headline}>
          <Text style={[Typography.display, { color: theme.text }]}>
            {formatPercent(c.prob_most_common[top])}
          </Text>
          <View style={styles.flex}>
            <Text style={[Typography.bodyStrong, { color: theme.text }]}>
              {`sure your typical week is ${top} risk`}
            </Text>
            <Text style={[Typography.caption, { color: theme.textSecondary }]}>
              {`${c.verdict} · from ${c.n_weeks} weeks`}
            </Text>
          </View>
        </View>

        <View style={[styles.note, { backgroundColor: theme.backgroundSelected }]}>
          <Text style={[Typography.caption, { color: theme.text }]}>
            {`${formatPercent(c.prob_elevated_majority)} probability that most of your weeks are Medium or High risk.`}
          </Text>
        </View>

        <View style={styles.stackSm}>
          <Text style={[Typography.label, { color: theme.textMuted }]}>
            {`SHARE OF YOUR WEEKS IN EACH CLASS · ${formatPercent(c.credible_mass)} CREDIBLE INTERVAL`}
          </Text>
          {c.classes.map((k) => {
            const [lo, hi] = c.share_interval[k];
            return (
              <View key={k} style={styles.stackXs}>
                <View style={styles.rowBetween}>
                  <Text style={[Typography.caption, { color: theme.text }]}>{k}</Text>
                  <Text style={[Typography.tabular, { color: theme.textSecondary }]}>
                    {`${formatPercent(c.share_mean[k])}  (${formatPercent(lo)}–${formatPercent(hi)})`}
                  </Text>
                </View>
                {/* Track = 0-100%. Faint band = credible interval, solid = mean. */}
                <View style={[styles.track, { backgroundColor: theme.backgroundSelected }]}>
                  <View
                    style={[
                      styles.band,
                      { left: `${lo * 100}%`, width: `${(hi - lo) * 100}%`, backgroundColor: colorFor(k) },
                    ]}
                  />
                  <View
                    style={[styles.fill, { width: `${c.share_mean[k] * 100}%`, backgroundColor: colorFor(k) }]}
                  />
                </View>
              </View>
            );
          })}
        </View>

        <View style={styles.stackSm}>
          <Text style={[Typography.label, { color: theme.textMuted }]}>WEEK BY WEEK</Text>
          <View style={styles.weeks}>
            {[...c.weeks].reverse().map((w) => (
              <View key={w.start} style={styles.week}>
                <View style={[styles.dot, { backgroundColor: colorFor(w.predicted) }]}>
                  <Text style={[Typography.label, { color: theme.onPrimary }]}>{w.predicted[0]}</Text>
                </View>
                <Text
                  style={[
                    Typography.caption,
                    { color: w.synthetic_days ? theme.textMuted : theme.textSecondary },
                  ]}>
                  {formatShortDate(w.start)}
                </Text>
              </View>
            ))}
          </View>
          <Text style={[Typography.caption, { color: theme.textMuted }]}>
            Oldest to newest, by the day each week starts. The letter is the risk level that
            week was given.
          </Text>
        </View>

        <Pressable
          onPress={() => setShowMethod((v) => !v)}
          style={[styles.toggle, { borderTopColor: theme.border }]}
          accessibilityRole="button">
          <Text style={[Typography.caption, styles.flex, { color: theme.primary }]}>
            {showMethod ? 'Hide how this works' : 'How this works'}
          </Text>
          <Ionicons
            name={showMethod ? 'chevron-up' : 'chevron-down'}
            size={16}
            color={theme.primary}
          />
        </Pressable>
        {showMethod ? (
          <View style={styles.stackSm}>
            <Text style={[Typography.caption, { color: theme.textSecondary }]}>
              {`Each week's class probabilities are added as evidence to a ${c.prior} prior, giving a Dirichlet posterior over how often you are in each class.`}
            </Text>
            {c.caveats
              .filter((x): x is string => Boolean(x))
              .map((x) => (
                <Text key={x} style={[Typography.caption, { color: theme.textMuted }]}>
                  {`• ${x}`}
                </Text>
              ))}
          </View>
        ) : null}
      </View>
    </Card>
  );
}

const styles = StyleSheet.create({
  stack: { gap: Spacing.three },
  stackSm: { gap: Spacing.two },
  stackXs: { gap: 4 },
  flex: { flex: 1 },
  headline: { flexDirection: 'row', alignItems: 'center', gap: Spacing.three },
  rowBetween: { flexDirection: 'row', justifyContent: 'space-between', gap: Spacing.two },
  note: { padding: Spacing.two + 2, borderRadius: Radius.md },
  track: { height: 8, borderRadius: 4, overflow: 'hidden' },
  band: { position: 'absolute', top: 0, bottom: 0, opacity: 0.3 },
  fill: { height: 8, borderTopRightRadius: 4, borderBottomRightRadius: 4 },
  weeks: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  week: { alignItems: 'center', gap: 2, minWidth: 48 },
  toggle: {
    flexDirection: 'row',
    alignItems: 'center',
    borderTopWidth: StyleSheet.hairlineWidth,
    paddingTop: Spacing.three,
  },
  dot: { width: 24, height: 24, borderRadius: 12, alignItems: 'center', justifyContent: 'center' },
});
