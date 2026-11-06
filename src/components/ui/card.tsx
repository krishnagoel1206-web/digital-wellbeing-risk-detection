/**
 * Card - the single surface primitive the whole app is built from.
 *
 * Every panel on every screen is a Card, so spacing, radius, hairline ring
 * and elevation stay identical everywhere. The brief asks for "rounded cards,
 * subtle shadows, consistent spacing" - centralising it here is what makes
 * that true by construction instead of by discipline.
 */

import type { ReactNode } from 'react';
import { StyleSheet, View, type StyleProp, type ViewStyle } from 'react-native';

import { Elevation, Radius, Spacing } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';

export type CardProps = {
  children: ReactNode;
  /** `flat` drops the shadow - use inside an already-raised surface. */
  variant?: 'raised' | 'flat';
  /** Remove interior padding when the child manages its own (e.g. a chart). */
  noPadding?: boolean;
  style?: StyleProp<ViewStyle>;
};

export function Card({ children, variant = 'raised', noPadding = false, style }: CardProps) {
  const theme = useTheme();

  return (
    <View
      style={[
        styles.base,
        {
          backgroundColor: theme.backgroundElement,
          borderColor: theme.border,
        },
        variant === 'raised' ? Elevation.card : Elevation.none,
        noPadding ? null : styles.padded,
        style,
      ]}>
      {children}
    </View>
  );
}

const styles = StyleSheet.create({
  base: {
    borderRadius: Radius.xl,
    borderWidth: StyleSheet.hairlineWidth,
    overflow: 'hidden',
  },
  padded: {
    padding: Spacing.three,
  },
});
