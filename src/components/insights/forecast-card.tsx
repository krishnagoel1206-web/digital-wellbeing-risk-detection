/**
 * "Predicted screen time" card on Insights.
 *
 * Reads top to bottom as a person would ask: how much will I use my phone,
 * is that more or less than now, which days, and how was this worked out.
 */

import { Ionicons } from '@expo/vector-icons';
import { StyleSheet, Text, View } from 'react-native';

import { LineChart } from '@/components/charts/line-chart';
import { Spacing, Typography } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import type { ForecastResponse } from '@/types/api';
import { formatMinutes, formatShortDate } from '@/utils/format';

function dayLabel(iso: string, isToday: boolean): string {
  if (isToday) return 'Today';
  const date = new Date(`${iso}T00:00:00`);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short' });
}

export function ForecastCard({ data }: { data: ForecastResponse }) {
  const theme = useTheme();
  const preds = data.predictions;
  if (preds.length === 0) return null;

  const minutes = preds.map((p) => p.predicted_minutes);
  const average = minutes.reduce((a, b) => a + b, 0) / minutes.length;
  const low = Math.min(...minutes);
  const high = Math.max(...minutes);
  const labels = preds.map((p) => dayLabel(p.date, p.is_current_day));
  const personal = data.personal;
  const chosen = personal?.candidates.find((c) => c.name === personal.chosen);

  return (
    <View style={styles.wrap}>
      {/* --- headline --- */}
      <View>
        <Text style={[Typography.caption, { color: theme.textSecondary }]}>
          {`Next ${preds.length} days, on average`}
        </Text>
        <Text style={[Typography.title, { color: theme.text }]}>
          {`${formatMinutes(average)} a day`}
        </Text>
        <Text style={[Typography.caption, { color: theme.textMuted }]}>
          {`Between ${formatMinutes(low)} and ${formatMinutes(high)} depending on the day`}
        </Text>
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

      {/* --- chart (touch a point to read it) --- */}
      <View>
        <LineChart
          data={preds.map((p) => p.predicted_hours)}
          labels={labels}
          formatValue={(_, i) => formatMinutes(preds[i].predicted_minutes)}
          height={150}
          color={theme.series3}
          gridColor={theme.grid}
          showArea
          showDots
          startAtZero
          gradientId="forecastChart"
        />
        <Text style={[Typography.caption, styles.hint, { color: theme.textMuted }]}>
          Touch the graph to see each day
        </Text>
      </View>

      {/* --- day by day --- */}
      <View style={[styles.list, { borderTopColor: theme.border }]}>
        {preds.map((p, i) => (
          <View key={p.date} style={styles.rowBetween}>
            <Text style={[Typography.caption, { color: theme.textSecondary }]}>
              {p.is_current_day ? 'Today (whole day)' : labels[i]}
            </Text>
            <Text style={[Typography.tabular, { color: theme.text }]}>
              {formatMinutes(p.predicted_minutes)}
            </Text>
          </View>
        ))}
      </View>

      {/* --- how it was worked out --- */}
      <View style={[styles.list, { borderTopColor: theme.border }]}>
        <Text style={[Typography.label, { color: theme.textMuted }]}>HOW THIS IS WORKED OUT</Text>
        {personal ? (
          <>
            <Text style={[Typography.caption, { color: theme.textSecondary }]}>
              {`Learned on this phone from your own ${personal.days_used} days (${formatShortDate(
                personal.first_date
              )} – ${formatShortDate(personal.last_date)}). ${personal.candidates.length} ways of predicting were tested on your past weeks, and the most accurate one is used: `}
              <Text style={{ color: theme.text }}>{personal.chosen}</Text>
              {chosen ? `, usually within ±${formatMinutes(chosen.rmse_minutes)} of the real figure.` : '.'}
            </Text>
            <View style={styles.candidates}>
              {personal.candidates.map((c) => {
                const isChosen = c.name === personal.chosen;
                return (
                  <View key={c.name} style={styles.rowBetween}>
                    <Text
                      style={[
                        Typography.caption,
                        styles.flex,
                        { color: isChosen ? theme.text : theme.textMuted },
                      ]}>
                      {`${isChosen ? '✓ ' : '   '}${c.name}${c.kind === 'baseline' ? ' (simple rule)' : ' (trained model)'}`}
                    </Text>
                    <Text
                      style={[Typography.tabular, { color: isChosen ? theme.text : theme.textMuted }]}>
                      {`±${formatMinutes(c.rmse_minutes)}`}
                    </Text>
                  </View>
                );
              })}
            </View>
          </>
        ) : (
          <Text style={[Typography.caption, { color: theme.textSecondary }]}>
            {`${data.model}, usually within ±${formatMinutes(data.accuracy.rmse_minutes)} of the real figure. ${data.personal_model.explanation}`}
          </Text>
        )}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { gap: Spacing.three },
  flex: { flex: 1 },
  row: { flexDirection: 'row', alignItems: 'center', gap: Spacing.two },
  rowBetween: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: Spacing.two,
  },
  hint: { textAlign: 'center', marginTop: Spacing.one },
  list: { gap: Spacing.one + 2, borderTopWidth: StyleSheet.hairlineWidth, paddingTop: Spacing.three },
  candidates: { gap: 2, marginTop: Spacing.one },
});
