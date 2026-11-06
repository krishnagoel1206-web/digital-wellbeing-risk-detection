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
import { Tabs } from 'expo-router/tabs';
import { Platform, StyleSheet, type ColorValue } from 'react-native';

import { Spacing, Typography } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';

/** Icon helper: outline when inactive, solid when active, so the active tab
 *  is distinguished by SHAPE as well as colour. */
function tabIcon(base: string) {
  // `color` is RN's ColorValue, not string - the navigator passes a ColorValue.
  return ({ color, size, focused }: { color: ColorValue; size: number; focused: boolean }) => (
    <Ionicons
      name={(focused ? base : `${base}-outline`) as keyof typeof Ionicons.glyphMap}
      size={size}
      color={color as string}
    />
  );
}

export default function TabsLayout() {
  const theme = useTheme();

  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        tabBarActiveTintColor: theme.primary,
        tabBarInactiveTintColor: theme.textMuted,
        tabBarStyle: {
          backgroundColor: theme.backgroundElement,
          borderTopColor: theme.border,
          borderTopWidth: StyleSheet.hairlineWidth,
          // Give Android room for the gesture bar.
          height: Platform.select({ android: 64, default: 84 }),
          paddingTop: Spacing.one,
          paddingBottom: Platform.select({ android: Spacing.two, default: Spacing.four }),
        },
        tabBarLabelStyle: {
          ...Typography.label,
          letterSpacing: 0.2,
          textTransform: 'none',
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
