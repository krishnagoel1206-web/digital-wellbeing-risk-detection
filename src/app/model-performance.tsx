/**
 * Model Performance screen (Screen 7) - the evaluation report.
 *
 * EVERY FIGURE HERE IS READ FROM DISK, NOT COMPUTED IN THE APP.
 * The training scripts write a metrics JSON alongside each .joblib artifact,
 * and the API serves that file unchanged. These numbers therefore cannot
 * drift away from the models that actually shipped - recomputing them in the
 * app would silently diverge the moment the two disagreed.
 *
 * The screen deliberately leads with the BASELINE rather than the accuracy.
 * An accuracy of 0.62 means nothing until you know that always guessing the
 * most common class scores 0.38; a reader shown only the first number has
 * been given a misleading impression.
 */

import { Ionicons } from '@expo/vector-icons';
import { useCallback, useState } from 'react';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { ApiStateView } from '@/components/ui/api-states';
import { Card } from '@/components/ui/card';
import { ConfusionMatrix } from '@/components/ui/confusion-matrix';
import { Section } from '@/components/ui/section';
import {
  BottomTabInset,
  MaxContentWidth,
  Spacing,
  Typography,
} from '@/constants/theme';
import { useApi } from '@/hooks/use-api';
import { useTheme } from '@/hooks/use-theme';
import * as api from '@/services/api/endpoints';
import { formatMinutes, formatNumber, formatPercent } from '@/utils/format';

export default function ModelPerformanceScreen() {
  const theme = useTheme();
  const performance = useApi(useCallback(() => api.getModelPerformance(), []));
  const [expanded, setExpanded] = useState<string | null>(null);

  return (
    <View style={[styles.root, { backgroundColor: theme.background }]}>
      <SafeAreaView style={styles.safeArea} edges={['left', 'right', 'bottom']}>
        <ScrollView contentContainerStyle={styles.content} showsVerticalScrollIndicator={false}>
          <Text style={[Typography.body, { color: theme.textSecondary }]}>
            How well each model performed on data it had never seen.
          </Text>

          <ApiStateView
            state={performance}
            loadingLabel="Loading evaluation report"
            notReadyHint="The trained models are bundled with the app; reinstalling the latest build restores them.">
            {(data) => (
              <View style={styles.stack}>
                {/* ================= CLASSIFICATION ================= */}
                {data.classification ? (
                  <>
                    <Section
                      title="Risk classification"
                      subtitle={`${data.classification.n_test} held-out people`}>
                      <Card>
                        {/* Baseline first - the number that gives the rest meaning. */}
                        <View style={styles.baselineRow}>
                          <View style={styles.flex}>
                            <Text style={[Typography.caption, { color: theme.textSecondary }]}>
                              Without a model
                            </Text>
                            <Text style={[Typography.heading, { color: theme.textMuted }]}>
                              {formatPercent(data.classification.majority_baseline)}
                            </Text>
                            <Text style={[Typography.caption, { color: theme.textMuted }]}>
                              always picks the most common level
                            </Text>
                          </View>
                          <Ionicons name="arrow-forward" size={18} color={theme.textMuted} />
                          <View style={styles.flex}>
                            <Text style={[Typography.caption, { color: theme.textSecondary }]}>
                              Best model
                            </Text>
                            <Text style={[Typography.heading, { color: theme.riskLow }]}>
                              {formatPercent(
                                data.classification.models.find(
                                  (m) => m.name === data.classification?.best_model
                                )?.cv_accuracy
                              )}
                            </Text>
                          </View>
                        </View>

                        <Text style={[Typography.caption, styles.note, { color: theme.textMuted }]}>
                          {data.classification.expected_ceiling_note}
                        </Text>
                      </Card>
                    </Section>

                    <Section title="All seven classifiers" subtitle="Tap one for detail">
                      <Card noPadding>
                        <View style={[styles.tableHeader, { borderBottomColor: theme.border }]}>
                          <Text style={[Typography.label, styles.colName, { color: theme.textMuted }]}>
                            MODEL
                          </Text>
                          <Text style={[Typography.label, styles.colNum, { color: theme.textMuted }]}>
                            ACC
                          </Text>
                          <Text style={[Typography.label, styles.colNum, { color: theme.textMuted }]}>
                            F1
                          </Text>
                          <Text style={[Typography.label, styles.colNum, { color: theme.textMuted }]}>
                            AUC
                          </Text>
                        </View>

                        {[...data.classification.models]
                          .sort((a, b) => b.cv_f1_macro - a.cv_f1_macro)
                          .map((model, index) => {
                            const isBest = model.name === data.classification?.best_model;
                            const isOpen = expanded === model.name;
                            return (
                              <View key={model.name}>
                                {index > 0 ? (
                                  <View style={[styles.divider, { backgroundColor: theme.border }]} />
                                ) : null}
                                <Pressable
                                  onPress={() => setExpanded(isOpen ? null : model.name)}
                                  style={({ pressed }) => [
                                    styles.tableRow,
                                    pressed && { backgroundColor: theme.backgroundSelected },
                                  ]}>
                                  <View style={styles.colName}>
                                    <Text
                                      style={[
                                        Typography.caption,
                                        { color: theme.text, fontWeight: isBest ? '700' : '400' },
                                      ]}>
                                      {model.name}
                                    </Text>
                                    {isBest ? (
                                      <Text style={[Typography.caption, { color: theme.riskLow }]}>
                                        selected
                                      </Text>
                                    ) : null}
                                  </View>
                                  <Text style={[Typography.tabular, styles.colNum, { color: theme.text }]}>
                                    {formatNumber(model.test.accuracy, 3)}
                                  </Text>
                                  <Text style={[Typography.tabular, styles.colNum, { color: theme.text }]}>
                                    {formatNumber(model.test.f1_macro, 3)}
                                  </Text>
                                  <Text style={[Typography.tabular, styles.colNum, { color: theme.text }]}>
                                    {formatNumber(model.test.roc_auc_ovr, 3)}
                                  </Text>
                                </Pressable>

                                {isOpen ? (
                                  <View style={styles.detail}>
                                    <View style={styles.detailGrid}>
                                      {([
                                        ['Precision', model.test.precision_macro],
                                        ['Recall', model.test.recall_macro],
                                        ['CV accuracy', model.cv_accuracy],
                                        ['CV F1', model.cv_f1_macro],
                                      ] as const).map(([label, value]) => (
                                        <View key={label} style={styles.detailItem}>
                                          <Text
                                            style={[Typography.caption, { color: theme.textSecondary }]}>
                                            {label}
                                          </Text>
                                          <Text style={[Typography.tabular, { color: theme.text }]}>
                                            {formatNumber(value, 3)}
                                          </Text>
                                        </View>
                                      ))}
                                    </View>

                                    <ConfusionMatrix
                                      matrix={model.test.confusion}
                                      labels={model.test.labels}
                                    />

                                    {Object.keys(model.feature_importance).length > 0 ? (
                                      <View style={styles.importance}>
                                        <Text style={[Typography.label, { color: theme.textMuted }]}>
                                          WHAT THE MODEL RELIED ON
                                        </Text>
                                        {Object.entries(model.feature_importance)
                                          .sort((a, b) => b[1] - a[1])
                                          .slice(0, 5)
                                          .map(([feature, value]) => (
                                            <View key={feature} style={styles.rowBetween}>
                                              <Text
                                                style={[
                                                  Typography.caption,
                                                  { color: theme.textSecondary },
                                                ]}>
                                                {feature.replace(/_/g, ' ')}
                                              </Text>
                                              <Text
                                                style={[Typography.tabular, { color: theme.text }]}>
                                                {formatNumber(value, 3)}
                                              </Text>
                                            </View>
                                          ))}
                                      </View>
                                    ) : null}
                                  </View>
                                ) : null}
                              </View>
                            );
                          })}
                      </Card>
                    </Section>

                  </>
                ) : null}

                {/* ================= REGRESSION ================= */}
                {data.regression ? (
                  <>
                    <Section
                      title="Screen-time forecast"
                      subtitle={`${data.regression.n_samples} days used`}>
                      <Card>
                        <View style={styles.baselineRow}>
                          <View style={styles.flex}>
                            <Text style={[Typography.caption, { color: theme.textSecondary }]}>
                              Without a model
                            </Text>
                            <Text style={[Typography.heading, { color: theme.textMuted }]}>
                              {formatMinutes(data.regression.naive_persistence_rmse)}
                            </Text>
                            <Text style={[Typography.caption, { color: theme.textMuted }]}>
                              tomorrow = same as today
                            </Text>
                          </View>
                          <Ionicons name="arrow-forward" size={18} color={theme.textMuted} />
                          <View style={styles.flex}>
                            <Text style={[Typography.caption, { color: theme.textSecondary }]}>
                              Best model error
                            </Text>
                            <Text style={[Typography.heading, { color: theme.riskLow }]}>
                              {formatMinutes(
                                Math.min(...data.regression.models.map((m) => m.rmse))
                              )}
                            </Text>
                          </View>
                        </View>

                        {data.regression.sample_size_warning ? (
                          <Text style={[Typography.caption, styles.note, { color: theme.riskMedium }]}>
                            {data.regression.sample_size_warning}
                          </Text>
                        ) : null}
                        <Text style={[Typography.caption, styles.note, { color: theme.textMuted }]}>
                          {data.regression.cv_note}
                        </Text>
                      </Card>
                    </Section>

                    <Section title="Linear, Ridge and Lasso">
                      <Card noPadding>
                        <View style={[styles.tableHeader, { borderBottomColor: theme.border }]}>
                          <Text style={[Typography.label, styles.colName, { color: theme.textMuted }]}>
                            MODEL
                          </Text>
                          <Text style={[Typography.label, styles.colNum, { color: theme.textMuted }]}>
                            RMSE
                          </Text>
                          <Text style={[Typography.label, styles.colNum, { color: theme.textMuted }]}>
                            MAE
                          </Text>
                          <Text style={[Typography.label, styles.colNum, { color: theme.textMuted }]}>
                            R2
                          </Text>
                        </View>

                        {[...data.regression.models]
                          .sort((a, b) => a.rmse - b.rmse)
                          .map((model, index) => (
                            <View key={model.name}>
                              {index > 0 ? (
                                <View style={[styles.divider, { backgroundColor: theme.border }]} />
                              ) : null}
                              <View style={styles.tableRow}>
                                <View style={styles.colName}>
                                  <Text style={[Typography.caption, { color: theme.text }]}>
                                    {model.name}
                                  </Text>
                                  {model.name === data.regression?.best_model ? (
                                    <Text style={[Typography.caption, { color: theme.riskLow }]}>
                                      selected
                                    </Text>
                                  ) : null}
                                </View>
                                <Text style={[Typography.tabular, styles.colNum, { color: theme.text }]}>
                                  {formatNumber(model.rmse, 1)}
                                </Text>
                                <Text style={[Typography.tabular, styles.colNum, { color: theme.text }]}>
                                  {formatNumber(model.mae, 1)}
                                </Text>
                                <Text style={[Typography.tabular, styles.colNum, { color: theme.text }]}>
                                  {formatNumber(model.r2, 3)}
                                </Text>
                              </View>
                            </View>
                          ))}
                      </Card>
                    </Section>
                  </>
                ) : null}
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
  stack: { gap: Spacing.four },
  flex: { flex: 1 },
  rowBetween: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: Spacing.two,
  },
  baselineRow: { flexDirection: 'row', alignItems: 'center', gap: Spacing.three },
  note: { marginTop: Spacing.three, lineHeight: 17 },
  tableHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: Spacing.three,
    paddingVertical: Spacing.two,
    borderBottomWidth: StyleSheet.hairlineWidth,
    gap: Spacing.two,
  },
  tableRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: Spacing.three,
    paddingVertical: Spacing.three,
    gap: Spacing.two,
  },
  colName: { flex: 1 },
  colNum: { width: 52, textAlign: 'right' },
  divider: { height: StyleSheet.hairlineWidth, marginHorizontal: Spacing.three },
  detail: {
    paddingHorizontal: Spacing.three,
    paddingBottom: Spacing.three,
    gap: Spacing.three,
  },
  detailGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: Spacing.three },
  detailItem: { minWidth: 70, gap: 1 },
  importance: { gap: Spacing.one },
  bottomSpacer: { height: BottomTabInset + Spacing.four },
});
