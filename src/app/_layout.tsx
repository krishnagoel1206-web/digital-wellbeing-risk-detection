/**
 * Root layout - a Stack that contains the tab group plus the screens that are
 * pushed over it.
 *
 * Route map:
 *   (tabs)              -> the 5 bottom tabs (Home, Data, Analytics, Insights, Profile)
 *   onboarding          -> Welcome screen, presented outside the tabs
 *   recommendations     -> pushed from Home
 *   model-performance   -> pushed from Home
 *
 * Phase 2 changes from the Expo template:
 *  - Removed <AnimatedSplashOverlay />. Reason: it is Expo-branded (it renders
 *    the Expo logo on a #208AEF background), which does not belong in this
 *    app. We will introduce our own branded launch experience later.
 *  - Because that overlay was the only caller of SplashScreen.hideAsync(),
 *    removing it would leave the native splash up forever and the app would
 *    hang on a blue screen. We now hide it explicitly once this layout mounts.
 *
 * Note for the record: an earlier version of this comment claimed the template
 * overlay was buggy because it spread `StyleSheet.absoluteFill`. That was
 * incorrect. In React Native 0.88 `absoluteFill` IS a plain object
 * ({position:'absolute', left/right/top/bottom: 0}), so the template's spread
 * was valid; it is `absoluteFillObject` that no longer exists in these types.
 * The brief blue flash seen at launch was simply the native splash screen,
 * which is expected behaviour and not a defect.
 *
 * Launch flow:
 *   native splash -> StartScreen (every launch: starts the on-phone analysis
 *   engine and loads the first data) -> Home, or on FIRST launch only ->
 *   onboarding -> permissions (if usage access is missing) -> Home.
 * "First launch" is a flag kept in SharedPreferences by the usage-stats
 * module. Onboarding stays reachable from Profile afterwards.
 */

import * as UsageStats from '@modules/usage-stats';
import { DarkTheme, DefaultTheme, Stack, ThemeProvider, router } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import { useCallback, useEffect, useState } from 'react';
import { useColorScheme } from 'react-native';

import { StartScreen } from '@/components/start/start-screen';

import { ScopeProvider } from '@/hooks/use-scope';
import { ensureBackgroundCapture } from '@/services/usage/background-capture';
import { startForegroundCapture } from '@/services/usage/foreground-capture';
import { refreshScreenAlert } from '@/services/usage/screen-alert';

// Keep the native splash visible until the first screen is ready to show.
SplashScreen.preventAutoHideAsync();

export default function RootLayout() {
  const colorScheme = useColorScheme();
  const isDark = colorScheme === 'dark';
  const [starting, setStarting] = useState(true);

  const onStarted = useCallback(() => {
    setStarting(false);
    if (!UsageStats.getAppFlag('onboarded')) {
      router.push('/onboarding');
      return;
    }
    // Learn today's warning levels from the stored days and keep the
    // 15-minute background check running (see services/usage/screen-alert).
    refreshScreenAlert().catch((error) => console.log(`[screen-alert] ${String(error)}`));
  }, []);

  useEffect(() => {
    // hideAsync() rejects harmlessly if the splash is already hidden (for
    // example after a Fast Refresh), so the rejection is swallowed.
    SplashScreen.hideAsync().catch(() => {});
  }, []);

  useEffect(() => {
    // Registered here, at the root, because the usage history this captures
    // EXPIRES: Android discards its event stream after about a week, so a day
    // nobody reads inside that window cannot be recovered afterwards. Waiting
    // for the user to reach the Usage Data screen and press Sync is therefore
    // not a workable capture strategy.
    //
    // Registration is idempotent, and repeating it on each start is what
    // restores the task after a force-stop, which Android treats as a signal
    // to stop scheduling it. Failures are reported on the Usage Data screen
    // rather than thrown, since the app is fully usable without it.
    ensureBackgroundCapture()
      .then((state) =>
        console.log(
          `[background-capture] registered=${state.registered} ` +
            `availability=${String(state.availability)}`
        )
      )
      .catch((error) => console.log(`[background-capture] ${String(error)}`));
  }, []);

  useEffect(() => {
    // Opening the app is the one moment Android is certainly willing to run
    // our code, so it is the most reliable capture opportunity there is - the
    // background task is best-effort and can be refused outright. Throttled
    // internally, so flicking between apps does not re-read constantly.
    return startForegroundCapture();
  }, []);

  return (
    <ThemeProvider value={isDark ? DarkTheme : DefaultTheme}>
      <ScopeProvider>
      <Stack screenOptions={{ headerShown: false }}>
        <Stack.Screen name="(tabs)" />
        <Stack.Screen name="onboarding" options={{ animation: 'fade' }} />
        <Stack.Screen
          name="permissions"
          options={{ headerShown: true, title: 'Permissions' }}
        />
        <Stack.Screen
          name="recommendations"
          options={{ headerShown: true, title: 'Recommendations' }}
        />
        <Stack.Screen
          name="model-performance"
          options={{ headerShown: true, title: 'Model performance' }}
        />
      </Stack>
      {/* Drawn over the tabs while the engine starts, so Home loads beneath it. */}
      {starting ? <StartScreen onDone={onStarted} /> : null}
      </ScopeProvider>
    </ThemeProvider>
  );
}
