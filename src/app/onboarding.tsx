/**
 * Welcome / Onboarding screen (Screen 1 of the brief).
 *
 * Its job is to explain, in plain language, what "digital wellbeing analysis"
 * means before the user is shown any scores. The brief is explicit that ML
 * terminology should not be pushed at ordinary users, so this screen describes
 * what the app does behaviourally ("learns your normal") rather than naming
 * algorithms.
 *
 * It also sets expectations honestly up front: the app needs a couple of weeks
 * of data before its personal baseline means anything, and it is not a medical
 * tool. Saying so here prevents the app from over-claiming later.
 */

import { Ionicons } from '@expo/vector-icons';
import * as UsageStats from '@modules/usage-stats';
import { useRouter } from 'expo-router';
import { Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { MaxContentWidth, Radius, Spacing, Typography } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';

type Point = {
  icon: keyof typeof Ionicons.glyphMap;
  title: string;
  body: string;
};

const POINTS: Point[] = [
  {
    icon: 'pulse-outline',
    title: 'It learns your normal',
    body: 'Instead of judging you against a fixed limit, the app learns how you usually use your phone and compares each day with your own pattern.',
  },
  {
    icon: 'moon-outline',
    title: 'It looks at more than hours',
    body: 'Late-night use, notification volume and how your time is split between apps all matter - not just the total on the screen.',
  },
  {
    icon: 'trending-up-outline',
    title: 'It spots unusual days',
    body: 'When a day looks genuinely different from your baseline, the app tells you which part of your behaviour changed, and by how much.',
  },
  {
    icon: 'shield-checkmark-outline',
    title: 'Your data stays yours',
    body: 'Usage records are stored for analysis and you can export or delete them at any time.',
  },
];

export default function OnboardingScreen() {
  const theme = useTheme();
  const router = useRouter();

  return (
    <View style={[styles.root, { backgroundColor: theme.background }]}>
      <SafeAreaView style={styles.safeArea}>
        <ScrollView
          contentContainerStyle={styles.content}
          showsVerticalScrollIndicator={false}>
          {/* Mark */}
          <View style={[styles.mark, { backgroundColor: theme.primarySubtle }]}>
            <Ionicons name="pulse-outline" size={30} color={theme.primary} />
          </View>

          <View style={styles.heading}>
            <Text style={[Typography.title, { color: theme.text }]}>
              Understand your screen habits
            </Text>
            <Text style={[Typography.body, { color: theme.textSecondary }]}>
              This app studies your own phone-use patterns and tells you when something
              changes - in plain language, without judging you against anyone else.
            </Text>
          </View>

          <View style={styles.points}>
            {POINTS.map((point) => (
              <View key={point.title} style={styles.point}>
                <View style={[styles.pointIcon, { backgroundColor: theme.backgroundElement, borderColor: theme.border }]}>
                  <Ionicons name={point.icon} size={18} color={theme.primary} />
                </View>
                <View style={styles.pointText}>
                  <Text style={[Typography.bodyStrong, { color: theme.text }]}>
                    {point.title}
                  </Text>
                  <Text style={[Typography.caption, styles.pointBody, { color: theme.textSecondary }]}>
                    {point.body}
                  </Text>
                </View>
              </View>
            ))}
          </View>

          {/* Expectations set honestly before any score is shown. */}
          <View
            style={[
              styles.notice,
              { backgroundColor: theme.backgroundElement, borderColor: theme.border },
            ]}>
            <Ionicons name="information-circle-outline" size={16} color={theme.textSecondary} />
            <Text style={[Typography.caption, styles.noticeText, { color: theme.textSecondary }]}>
              You will need about two weeks of daily records before the app can judge what is
              normal for you. Until then it will say so rather than guess. This is a wellbeing
              prototype, not a medical tool.
            </Text>
          </View>
        </ScrollView>

        {/* Pinned action */}
        <View style={styles.footer}>
          <Pressable
            // First launch: remember it, then go straight on to permissions
            // if usage access is missing (nothing works without it), else to
            // Home. replace() so Back cannot return to the welcome screen.
            // Opened later from Profile: just return.
            onPress={() => {
              const firstLaunch = !UsageStats.getAppFlag('onboarded');
              UsageStats.setAppFlag('onboarded', true);
              if (!firstLaunch && router.canGoBack()) {
                router.back();
              } else if (!UsageStats.getPermissionState().usageAccess) {
                router.replace('/permissions');
              } else {
                router.replace('/');
              }
            }}
            style={({ pressed }) => [
              styles.cta,
              { backgroundColor: pressed ? theme.primaryPressed : theme.primary },
            ]}
            accessibilityRole="button"
            accessibilityLabel="Get started">
            <Text style={[Typography.bodyStrong, { color: theme.onPrimary }]}>Get started</Text>
            <Ionicons name="arrow-forward" size={17} color={theme.onPrimary} />
          </Pressable>
        </View>
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
    paddingHorizontal: Spacing.four,
    paddingTop: Spacing.five,
    gap: Spacing.four,
    paddingBottom: Spacing.four,
  },
  mark: {
    width: 64,
    height: 64,
    borderRadius: Radius.xl,
    alignItems: 'center',
    justifyContent: 'center',
  },
  heading: {
    gap: Spacing.two,
  },
  points: {
    gap: Spacing.three,
  },
  point: {
    flexDirection: 'row',
    gap: Spacing.three,
  },
  pointIcon: {
    width: 38,
    height: 38,
    borderRadius: Radius.md,
    borderWidth: StyleSheet.hairlineWidth,
    alignItems: 'center',
    justifyContent: 'center',
  },
  pointText: {
    flex: 1,
    gap: Spacing.half,
  },
  pointBody: {
    lineHeight: 19,
  },
  notice: {
    flexDirection: 'row',
    gap: Spacing.two,
    borderRadius: Radius.lg,
    borderWidth: StyleSheet.hairlineWidth,
    padding: Spacing.three,
  },
  noticeText: {
    flex: 1,
    lineHeight: 18,
  },
  footer: {
    paddingHorizontal: Spacing.four,
    paddingTop: Spacing.two,
    paddingBottom: Spacing.three,
  },
  cta: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: Spacing.two,
    height: 52,
    borderRadius: Radius.lg,
  },
});
