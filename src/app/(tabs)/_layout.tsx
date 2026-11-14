/**
 * Bottom tab navigator - 5 tabs.
 *
 * WHY 5 AND NOT 8
 * ---------------
 * The brief lists eight screens, but eight bottom tabs is unreadable on a
 * phone and contradicts the "minimal, clean" requirement. So the five tabs
 * here are the destinations a user returns to repeatedly, while the two
 * report-style screens (Recommendations, Model Performance) are pushed from
 * Home as stack screens, and Onboarding sits outside the tabs entirely.
 *
 * WHY JS `Tabs` AND NOT `NativeTabs`
 * ----------------------------------
 * The Expo template shipped with `NativeTabs`, which renders a true platform
 * tab bar but exposes very little styling control. This app needs a custom
 * look, so we use the JavaScript tab bar from `expo-router/tabs`, which is
 * fully themeable. Note the import path: in expo-router 58 `Tabs` is NOT on
 * the package root - it lives at `expo-router/tabs`.
 */

import { Ionicons } from '@expo/vector-icons';
import { Tabs, type BottomTabBarButtonProps } from 'expo-router/tabs';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Pressable, StyleSheet, View, type ColorValue, type GestureResponderEvent } from 'react-native';

import { Radius, Spacing, Typography } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';

/** Icon helper: outline when inactive, solid when active, so the active tab
 *  is distinguished by SHAPE as well as colour. */
function TabIcon({
  base,
  color,
  focused,
}: {
  base: string;
  color: ColorValue;
  focused: boolean;
}) {
  const theme = useTheme();
  return (
    <View style={[styles.pill, focused && { backgroundColor: theme.primarySubtle }]}>
      <Ionicons
        name={(focused ? base : `${base}-outline`) as keyof typeof Ionicons.glyphMap}
        size={21}
        color={color as string}
      />
    </View>
  );
}

/** Tab button without Android's borderless ripple, which spills outside the
 *  floating bar; the pill behind the icon is the feedback instead. */
function TabButton({
  children,
  style,
  onPress,
  onLongPress,
  accessibilityState,
  accessibilityLabel,
  testID,
}: BottomTabBarButtonProps) {
  return (
    <Pressable
      onPress={onPress as ((e: GestureResponderEvent) => void) | undefined}
      onLongPress={onLongPress}
      style={({ pressed }) => [style, pressed && styles.pressed]}
      accessibilityRole="tab"
      accessibilityState={accessibilityState}
      accessibilityLabel={accessibilityLabel}
      testID={testID}>
      {children}
    </Pressable>
  );
}

function tabIcon(base: string) {
  // `color` is RN's ColorValue, not string - the navigator passes a ColorValue.
  const Icon = ({ color, focused }: { color: ColorValue; size: number; focused: boolean }) => (
    <TabIcon base={base} color={color} focused={focused} />
  );
  Icon.displayName = `TabIcon(${base})`;
  return Icon;
}

export default function TabsLayout() {
  const theme = useTheme();
  const insets = useSafeAreaInsets();

  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: theme.primary,
        tabBarInactiveTintColor: theme.textMuted,
        // A floating bar, lifted off the screen edge.
        tabBarStyle: {
          position: 'absolute',
          marginHorizontal: Spacing.three,
          bottom: insets.bottom + Spacing.two,
          height: 68,
          paddingTop: Spacing.two,
          paddingBottom: Spacing.two,
          borderRadius: Radius.xxl,
          backgroundColor: theme.backgroundElement,
          borderTopWidth: 0,
          borderWidth: StyleSheet.hairlineWidth,
          borderColor: theme.border,
          elevation: 10,
          shadowColor: '#101828',
          shadowOpacity: 0.12,
          shadowRadius: 20,
          shadowOffset: { width: 0, height: 8 },
        },
        tabBarItemStyle: { paddingTop: 2 },
        tabBarButton: (props) => <TabButton {...props} />,
        tabBarLabelStyle: {
          ...Typography.label,
          fontSize: 11,
          fontWeight: '600',
          letterSpacing: 0.1,
          textTransform: 'none',
          marginTop: 2,
        },
      }}>
      <Tabs.Screen
        name="index"
        options={{ title: 'Home', tabBarIcon: tabIcon('home') }}
      />
      <Tabs.Screen
        name="data"
        options={{ title: 'Data', tabBarIcon: tabIcon('create') }}
      />
      <Tabs.Screen
        name="analytics"
        options={{ title: 'Analytics', tabBarIcon: tabIcon('stats-chart') }}
      />
      <Tabs.Screen
        name="insights"
        options={{ title: 'Insights', tabBarIcon: tabIcon('bulb') }}
      />
      <Tabs.Screen
        name="profile"
        options={{ title: 'Profile', tabBarIcon: tabIcon('person-circle') }}
      />
    </Tabs>
  );
}

const styles = StyleSheet.create({
  pressed: { opacity: 0.6 },
  pill: {
    width: 52,
    height: 30,
    borderRadius: Radius.pill,
    alignItems: 'center',
    justifyContent: 'center',
  },
});
