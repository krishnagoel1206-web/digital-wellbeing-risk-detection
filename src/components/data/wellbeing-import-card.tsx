/**
 * Import history copied from Digital Wellbeing.
 *
 * Android keeps the detailed events this app reads for about a week; Digital
 * Wellbeing keeps daily totals for about four. Unusual-day detection needs
 * 14 days, so older days are copied from Digital Wellbeing's screens into a
 * file placed in this app's folder over USB. This card appears only while
 * that file is present, imports it on request, then removes it.
 *
 * Imported days are stored as source "wellbeing" - real data about this
 * phone, kept distinct from the app's own measurements.
 */

import * as UsageStats from '@modules/usage-stats';
import { useState } from 'react';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';

import { Card } from '@/components/ui/card';
import { Section } from '@/components/ui/section';
import { Radius, Spacing, Typography } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import * as api from '@/services/api/endpoints';
import type { WellbeingDay, WellbeingImportResult } from '@/types/api';
import { formatShortDate } from '@/utils/format';

export const WELLBEING_IMPORT_FILE = 'wellbeing_history.json';

type ImportFile = { source?: string; read_at?: string; days: WellbeingDay[] };

function readFile(): ImportFile | null {
  const raw = UsageStats.readImportFile(WELLBEING_IMPORT_FILE);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as ImportFile;
    return Array.isArray(parsed.days) && parsed.days.length > 0 ? parsed : null;
  } catch {
    return null;
  }
}

export function WellbeingImportCard({ onImported }: { onImported?: () => void }) {
  const theme = useTheme();
  const [file, setFile] = useState<ImportFile | null>(readFile);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<WellbeingImportResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!file && !result) return null;

  const run = async () => {
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.importWellbeing(file.days, file.read_at);
      setResult(r);
      UsageStats.deleteImportFile(WELLBEING_IMPORT_FILE);
      setFile(null);
      onImported?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const dates = file?.days.map((d) => d.date).sort() ?? [];
  const c = result?.counts ?? {};

  return (
    <Section title="Digital Wellbeing history" subtitle="Older days the phone can no longer re-read">
      <Card>
        {file ? (
          <>
            <Text style={[Typography.body, { color: theme.text }]}>
              {`${file.days.length} days found, ${formatShortDate(dates[0])} – ${formatShortDate(
                dates[dates.length - 1]
              )}: screen time, unlocks and notifications as shown in Digital Wellbeing.`}
            </Text>
            <Text style={[Typography.caption, styles.gap, { color: theme.textSecondary }]}>
              Stored labelled as Digital Wellbeing, never as this app&apos;s own readings. Days
              you entered yourself are left alone, and recent phone readings are kept.
            </Text>
            <Pressable
              onPress={run}
              disabled={busy}
              accessibilityRole="button"
              style={({ pressed }) => [
                styles.button,
                { backgroundColor: pressed ? theme.primaryPressed : theme.primary, opacity: busy ? 0.6 : 1 },
              ]}>
              {busy ? <ActivityIndicator color={theme.onPrimary} size="small" /> : null}
              <Text style={[Typography.bodyStrong, { color: theme.onPrimary }]}>
                {busy ? 'Importing…' : 'Import these days'}
              </Text>
            </Pressable>
          </>
        ) : null}

        {result ? (
          <View style={styles.report}>
            <Text style={[Typography.bodyStrong, { color: theme.text }]}>Imported</Text>
            {[
              [c.inserted, 'new days added'],
              [c.replaced_demo, 'demo days replaced'],
              [c.replaced_old_phone_reading, 'old phone readings corrected (taken before the screen-time fix)'],
              [c.refreshed, 'earlier imports refreshed'],
              [c.kept, 'days kept as they were'],
              [c.rejected, 'rejected'],
            ]
              .filter(([n]) => Number(n) > 0)
              .map(([n, label]) => (
                <Text key={String(label)} style={[Typography.caption, { color: theme.textSecondary }]}>
                  {`• ${n} ${label}`}
                </Text>
              ))}
          </View>
        ) : null}

        {error ? (
          <Text style={[Typography.caption, styles.gap, { color: theme.riskHigh }]}>{error}</Text>
        ) : null}
      </Card>
    </Section>
  );
}

const styles = StyleSheet.create({
  gap: { marginTop: Spacing.two },
  report: { gap: 2 },
  button: {
    marginTop: Spacing.three,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: Spacing.two,
    paddingVertical: Spacing.two + 4,
    borderRadius: Radius.md,
  },
});
