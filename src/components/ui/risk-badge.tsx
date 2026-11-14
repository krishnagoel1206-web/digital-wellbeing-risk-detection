/**
 * RiskBadge - displays a Low / Medium / High risk level.
 *
 * WHY THIS COMPONENT EXISTS AT ALL
 * --------------------------------
 * Contrast validation against this app's real light card surface (#FFFFFF)
 * measured the medium-risk status colour (#FAB219) at just 1.83:1. That is far
 * below the 3:1 threshold, so the colour CANNOT be the only thing telling a
 * user their risk level - anyone with low vision or a colour-vision deficiency
 * would miss it, and so would anyone looking at a greyscale printout of the
 * project report.
 *
 * The documented mitigation is to pair the colour with an icon AND a text
 * label. This component hard-codes that pairing: there is no prop to turn the
 * icon or the label off. Risk level is therefore always carried by three
 * independent channels (shape, text, colour), and no screen can accidentally
 * regress to colour-alone.
 */

import { Ionicons } from '@expo/vector-icons';
import { StyleSheet, Text, View } from 'react-native';

import { Radius, RiskColorKeys, Spacing, Typography, type RiskLevel } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';

/**
 * Icon and wording per level. The icons are deliberately different SHAPES
 * (circle / triangle / octagon-ish alert), not three colours of the same
 * glyph, so the shape alone distinguishes them.
 */
const RISK_META: Record<RiskLevel, { icon: keyof typeof Ionicons.glyphMap; label: string }> = {
  low: { icon: 'checkmark-circle', label: 'Low risk' },
  medium: { icon: 'alert-circle', label: 'Medium risk' },
  high: { icon: 'warning', label: 'High risk' },
};

export type RiskBadgeProps = {
  level: RiskLevel;
  /** `sm` for inline use in a list row, `md` for a card header. */
  size?: 'sm' | 'md';
};

export function RiskBadge({ level, size = 'md' }: RiskBadgeProps) {
  const theme = useTheme();
  const meta = RISK_META[level];
  const colorKeys = RiskColorKeys[level];

  const fg = theme[colorKeys.fg];
  // Amber fill is too light for text; the badge text uses the deeper ink.
  const ink = level === 'medium' ? theme.riskMediumInk : fg;
  const bg = theme[colorKeys.bg];

  const iconSize = size === 'sm' ? 13 : 16;
  const textStyle = size === 'sm' ? Typography.label : Typography.caption;

  return (
    <View
      style={[
        styles.badge,
        size === 'sm' ? styles.badgeSm : styles.badgeMd,
        { backgroundColor: bg },
      ]}
      // Announce the level as one phrase rather than icon-then-text.
      accessibilityRole="text"
      accessibilityLabel={meta.label}>
      <Ionicons name={meta.icon} size={iconSize} color={fg} />
      <Text style={[textStyle, styles.text, { color: ink, fontWeight: '600' }]} numberOfLines={1}>
        {meta.label}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  badge: {
    flexDirection: 'row',
    alignItems: 'center',
    alignSelf: 'flex-start',
    borderRadius: Radius.pill,
    gap: Spacing.one + 2,
  },
  badgeSm: {
    paddingVertical: Spacing.half + 1,
    paddingHorizontal: Spacing.two,
  },
  badgeMd: {
    paddingVertical: Spacing.one + 1,
    paddingHorizontal: Spacing.two + 2,
  },
  text: {
    // Wording is part of the accessibility contract - never hidden.
    includeFontPadding: false,
  },
});
