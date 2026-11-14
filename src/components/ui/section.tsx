/**
 * Section - a titled block of content with optional trailing action.
 *
 * Keeps vertical rhythm identical across all eight screens, which is what
 * stops the dashboard from feeling "overcrowded" as the brief warns against.
 */

import type { ReactNode } from 'react';
import { StyleSheet, Text, View } from 'react-native';

import { Spacing, Typography } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';

export type SectionProps = {
  title: string;
  /** Plain-language explanation shown under the title. */
  subtitle?: string;
  /** Rendered at the trailing edge of the header row, e.g. a "See all" link. */
  action?: ReactNode;
  children: ReactNode;
};

export function Section({ title, subtitle, action, children }: SectionProps) {
  const theme = useTheme();

  return (
    <View style={styles.wrap}>
      <View style={styles.header}>
        <View style={styles.titleCol}>
          <Text style={[Typography.label, styles.title, { color: theme.textMuted }]}>
            {title.toUpperCase()}
          </Text>
          {subtitle ? (
            <Text style={[Typography.caption, { color: theme.textMuted }]}>{subtitle}</Text>
          ) : null}
        </View>
        {action ?? null}
      </View>
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    gap: Spacing.two + 2,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    gap: Spacing.two,
    paddingHorizontal: Spacing.one,
  },
  titleCol: {
    flex: 1,
    gap: Spacing.half,
  },
  title: {
    includeFontPadding: false,
  },
});
