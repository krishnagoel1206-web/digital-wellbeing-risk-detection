/**
 * PhasePlaceholder - an honest "not built yet" state.
 *
 * The brief is explicit that the app must never imply functionality that does
 * not exist. So rather than filling unbuilt screens with convincing-looking
 * fake charts, each one states plainly what it will do and which development
 * phase delivers it. Nothing here can be mistaken for real output.
 */

import { Ionicons } from '@expo/vector-icons';
import { StyleSheet, Text, View } from 'react-native';

import { Radius, Spacing, Typography } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';

export type PhasePlaceholderProps = {
  /** Ionicons glyph representing the screen's purpose. */
  icon: keyof typeof Ionicons.glyphMap;
  /** What this screen will eventually do, in plain language. */
  description: string;
  /** Which roadmap phase builds it, e.g. "Phase 4". */
  phase: string;
  /** The specific things this screen will show once built. */
  willShow?: string[];
};

export function PhasePlaceholder({
  icon,
  description,
  phase,
  willShow = [],
}: PhasePlaceholderProps) {
  const theme = useTheme();

  return (
    <View
      style={[
        styles.wrap,
        { backgroundColor: theme.backgroundElement, borderColor: theme.border },
      ]}>
      <View style={[styles.iconCircle, { backgroundColor: theme.primarySubtle }]}>
        <Ionicons name={icon} size={26} color={theme.primary} />
      </View>

      <Text style={[Typography.body, styles.centerText, { color: theme.textSecondary }]}>
        {description}
      </Text>

      {willShow.length > 0 ? (
        <View style={styles.list}>
          {willShow.map((item) => (
            <View key={item} style={styles.listRow}>
              <Ionicons name="ellipse" size={5} color={theme.textMuted} />
              <Text style={[Typography.caption, styles.listText, { color: theme.textMuted }]}>
                {item}
              </Text>
            </View>
          ))}
        </View>
      ) : null}

      <View style={[styles.pill, { backgroundColor: theme.backgroundSelected }]}>
        <Ionicons name="construct-outline" size={12} color={theme.textSecondary} />
        <Text style={[Typography.label, { color: theme.textSecondary }]}>
          {`BUILT IN ${phase.toUpperCase()}`}
        </Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    borderRadius: Radius.xl,
    borderWidth: StyleSheet.hairlineWidth,
    padding: Spacing.four,
    alignItems: 'center',
    gap: Spacing.three,
  },
  iconCircle: {
    width: 56,
    height: 56,
    borderRadius: Radius.pill,
    alignItems: 'center',
    justifyContent: 'center',
  },
  centerText: {
    textAlign: 'center',
  },
  list: {
    alignSelf: 'stretch',
    gap: Spacing.two,
    paddingHorizontal: Spacing.two,
  },
  listRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.two,
  },
  listText: {
    flex: 1,
  },
  pill: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.one + 2,
    borderRadius: Radius.pill,
    paddingVertical: Spacing.one + 2,
    paddingHorizontal: Spacing.two + 2,
  },
});
