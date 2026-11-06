/**
 * Screen - the outer wrapper every screen in the app uses.
 *
 * Centralises: page background, safe-area insets, the max content width that
 * keeps lines readable on large/landscape displays, consistent horizontal
 * padding, and the bottom inset that stops content hiding behind the tab bar.
 *
 * Without this, each of the eight screens would re-implement the same padding
 * and inset maths and they would drift apart.
 */

import type { ReactNode } from 'react';
import { ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { BottomTabInset, MaxContentWidth, Spacing, Typography } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';

export type ScreenProps = {
  children: ReactNode;
  /** Large page title rendered above the content. */
  title?: string;
  /** Plain-language line under the title. */
  subtitle?: string;
  /** Set false for screens that manage their own scrolling (e.g. a list). */
  scroll?: boolean;
  /** Extra space between child blocks. */
  gap?: number;
};

export function Screen({
  children,
  title,
  subtitle,
  scroll = true,
  gap = Spacing.four,
}: ScreenProps) {
  const theme = useTheme();

  const header =
    title || subtitle ? (
      <View style={styles.header}>
        {title ? <Text style={[Typography.title, { color: theme.text }]}>{title}</Text> : null}
        {subtitle ? (
          <Text style={[Typography.body, { color: theme.textSecondary }]}>{subtitle}</Text>
        ) : null}
      </View>
    ) : null;

  const body = (
    <>
      {header}
      {children}
    </>
  );

  return (
    <View style={[styles.root, { backgroundColor: theme.background }]}>
      <SafeAreaView style={styles.safeArea} edges={['top', 'left', 'right']}>
        {scroll ? (
          <ScrollView
            contentContainerStyle={[styles.content, { gap }]}
            showsVerticalScrollIndicator={false}>
            {body}
            <View style={styles.bottomSpacer} />
          </ScrollView>
        ) : (
          <View style={[styles.content, styles.flex, { gap }]}>{body}</View>
        )}
      </SafeAreaView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    flexDirection: 'row',
    justifyContent: 'center',
  },
  safeArea: {
    flex: 1,
    width: '100%',
    maxWidth: MaxContentWidth,
  },
  content: {
    paddingHorizontal: Spacing.three,
    paddingTop: Spacing.three,
  },
  flex: {
    flex: 1,
  },
  header: {
    gap: Spacing.one,
  },
  bottomSpacer: {
    height: BottomTabInset + Spacing.four,
  },
});
