/**
 * The branded screen shown every time the app starts.
 *
 * It does real work rather than just decorating the wait: it starts the
 * embedded Python analysis engine and loads the first data while it is on
 * screen, so Home opens with its figures already in place instead of a
 * column of loading cards. The models run on the phone - no laptop, no
 * network - and the first start after an install also unpacks the
 * interpreter, which is the slow part.
 *
 * It never blocks forever: after MAX_WAIT_MS it steps aside regardless, and
 * the screens show their own loading state for whatever is still arriving.
 */

import * as UsageStats from '@modules/usage-stats';
import { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Animated, Image, StyleSheet, Text, View } from 'react-native';

import { Spacing, Typography } from '@/constants/theme';
import * as api from '@/services/api/endpoints';

const BRAND = '#1C5CAB';
const MIN_SHOW_MS = 1200;
const MAX_WAIT_MS = 25_000;

async function warmUp(onStep: (label: string) => void): Promise<void> {
  if (UsageStats.isPythonAvailable()) {
    onStep('Starting the analysis engine…');
    try {
      await UsageStats.configurePython();
    } catch {
      // The first real request retries configuration and reports properly.
    }
  }
  onStep('Loading your usage…');
  // Light calls that also prime the database and the model cache.
  await Promise.allSettled([api.getUsageSummary(), api.getMlStatus()]);
}

export function StartScreen({ onDone }: { onDone: () => void }) {
  const [step, setStep] = useState('Starting…');
  const opacity = useRef(new Animated.Value(1)).current;

  useEffect(() => {
    let finished = false;
    const started = Date.now();

    const finish = () => {
      if (finished) return;
      finished = true;
      const wait = Math.max(0, MIN_SHOW_MS - (Date.now() - started));
      setTimeout(() => {
        setStep('Ready');
        Animated.timing(opacity, { toValue: 0, duration: 280, useNativeDriver: true }).start(() =>
          onDone()
        );
      }, wait);
    };

    const timeout = setTimeout(finish, MAX_WAIT_MS);
    warmUp(setStep).finally(finish);
    return () => clearTimeout(timeout);
  }, [onDone, opacity]);

  return (
    <Animated.View style={[StyleSheet.absoluteFill, styles.root, { opacity }]}>
      <View style={styles.center}>
        <Image
          source={require('../../../assets/images/splash-icon.png')}
          style={styles.logo}
          accessibilityIgnoresInvertColors
        />
        <Text style={[Typography.title, styles.name]}>Wellbeing Insights</Text>
        <Text style={[Typography.body, styles.tagline]}>
          What your screen time means, how sure we are, and what to do about it.
        </Text>
      </View>
      <View style={styles.footer}>
        <ActivityIndicator color="#FFFFFF" />
        <Text style={[Typography.caption, styles.step]}>{step}</Text>
        <Text style={[Typography.caption, styles.note]}>Everything runs on this phone.</Text>
      </View>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  root: { backgroundColor: BRAND, zIndex: 10, elevation: 10 },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: Spacing.five },
  logo: { width: 132, height: 132, marginBottom: Spacing.three },
  name: { color: '#FFFFFF', textAlign: 'center' },
  tagline: { color: 'rgba(255,255,255,0.82)', textAlign: 'center', marginTop: Spacing.two },
  footer: { alignItems: 'center', gap: Spacing.two, paddingBottom: Spacing.six },
  step: { color: '#FFFFFF' },
  note: { color: 'rgba(255,255,255,0.65)' },
});
