/**
 * Loading, error and "not ready" panels.
 *
 * WHY THESE ARE DISTINCT COMPONENTS
 * ---------------------------------
 * "Could not reach the backend" and "the model has not been trained yet" look
 * identical if both render as an empty card, but they mean completely
 * different things and need different actions from the user. Showing a blank
 * dashboard for either would imply the user has no usage, which is the kind
 * of quiet misrepresentation the brief forbids.
 *
 * So: NotReadyPanel is informational (amber, explains what is missing),
 * ErrorPanel is a fault (red, shows the URL being tried and offers retry).
 */

import { Ionicons } from '@expo/vector-icons';
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from 'react-native';

import { Radius, Spacing, Typography } from '@/constants/theme';
import { useTheme } from '@/hooks/use-theme';
import { ApiError, getResolvedBaseUrl, getResolvedReason } from '@/services/api/client';

export function LoadingPanel({ label = 'Loading' }: { label?: string }) {
  const theme = useTheme();
  return (
    <View style={[styles.panel, { backgroundColor: theme.backgroundElement, borderColor: theme.border }]}>
      <ActivityIndicator color={theme.primary} />
      <Text style={[Typography.caption, { color: theme.textSecondary }]}>{label}</Text>
    </View>
  );
}

/**
 * Shown when the backend refuses with 409: a model that cannot run yet.
 *
 * `message` is the backend's own reason and is authoritative - it names the
 * specific measurement or day count responsible. `hint` is static screen
 * copy, so it must only ever ADD guidance ("where to look", "what to do"),
 * never restate the requirement. A hint reading "needs at least 7 days"
 * beside a message explaining that the real blocker is notification access
 * contradicts the accurate half of the panel.
 */
export function NotReadyPanel({
  message,
  hint,
}: {
  message: string;
  hint?: string;
}) {
  const theme = useTheme();
  return (
    <View
      style={[
        styles.panel,
        styles.alignStart,
        { backgroundColor: theme.riskMediumSurface, borderColor: theme.border },
      ]}>
      <View style={styles.row}>
        <Ionicons name="information-circle-outline" size={16} color={theme.riskMedium} />
        <Text style={[Typography.bodyStrong, styles.flex, { color: theme.riskMedium }]}>
          Not available yet
        </Text>
      </View>
      <Text style={[Typography.caption, { color: theme.textSecondary }]}>{message}</Text>
      {hint ? (
        <Text style={[Typography.caption, { color: theme.textMuted }]}>{hint}</Text>
      ) : null}
    </View>
  );
}

export function ErrorPanel({
  error,
  onRetry,
}: {
  error: ApiError;
  onRetry?: () => void;
}) {
  const theme = useTheme();

  // A connection problem needs the URL shown, because the usual cause is the
  // phone and laptop being on different networks or the server not running.
  const isConnection = error.isConnectionProblem;

  return (
    <View
      style={[
        styles.panel,
        styles.alignStart,
        { backgroundColor: theme.riskHighSurface, borderColor: theme.border },
      ]}>
      <View style={styles.row}>
        <Ionicons
          name={isConnection ? 'cloud-offline-outline' : 'alert-circle-outline'}
          size={16}
          color={theme.riskHigh}
        />
        <Text style={[Typography.bodyStrong, styles.flex, { color: theme.riskHigh }]}>
          {isConnection ? 'Cannot reach the backend' : 'Something went wrong'}
        </Text>
      </View>

      <Text style={[Typography.caption, { color: theme.textSecondary }]}>{error.message}</Text>

      {isConnection ? (
        <View style={[styles.urlBox, { backgroundColor: theme.backgroundSelected }]}>
          {getResolvedBaseUrl() ? (
            <>
              <Text style={[Typography.tabular, { color: theme.textSecondary }]} numberOfLines={1}>
                {getResolvedBaseUrl()}
              </Text>
              <Text style={[Typography.caption, { color: theme.textMuted }]}>
                {getResolvedReason()}
              </Text>
            </>
          ) : (
            <>
              <Text style={[Typography.caption, { color: theme.textMuted }]}>
                Addresses tried:
              </Text>
              {error.attempted.map((candidate) => (
                <Text
                  key={candidate.url}
                  style={[Typography.tabular, { color: theme.textSecondary }]}
                  numberOfLines={1}>
                  {candidate.url}
                </Text>
              ))}
            </>
          )}
        </View>
      ) : null}

      {onRetry ? (
        <Pressable
          onPress={onRetry}
          style={({ pressed }) => [
            styles.retry,
            { backgroundColor: pressed ? theme.primaryPressed : theme.primary },
          ]}
          accessibilityRole="button">
          <Ionicons name="refresh" size={14} color={theme.onPrimary} />
          <Text style={[Typography.caption, { color: theme.onPrimary }]}>Try again</Text>
        </Pressable>
      ) : null}
    </View>
  );
}

/**
 * Renders the right panel for an api state, or the children when data is
 * available. Keeps every screen's branching identical.
 */
export function ApiStateView<T>({
  state,
  loadingLabel,
  notReadyHint,
  children,
}: {
  state: { data: T | null; error: ApiError | null; isLoading: boolean; refetch: () => void };
  loadingLabel?: string;
  notReadyHint?: string;
  children: (data: T) => React.ReactNode;
}) {
  if (state.isLoading) {
    return <LoadingPanel label={loadingLabel} />;
  }

  if (state.error) {
    if (state.error.isNotReady) {
      return <NotReadyPanel message={state.error.message} hint={notReadyHint} />;
    }
    return <ErrorPanel error={state.error} onRetry={state.refetch} />;
  }

  if (state.data === null) {
    return <LoadingPanel label={loadingLabel} />;
  }

  return <>{children(state.data)}</>;
}

const styles = StyleSheet.create({
  panel: {
    borderRadius: Radius.lg,
    borderWidth: StyleSheet.hairlineWidth,
    padding: Spacing.three,
    gap: Spacing.two,
    alignItems: 'center',
  },
  alignStart: {
    alignItems: 'stretch',
  },
  row: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: Spacing.two,
  },
  flex: {
    flex: 1,
  },
  urlBox: {
    borderRadius: Radius.sm,
    paddingVertical: Spacing.two,
    paddingHorizontal: Spacing.two + 2,
    gap: 2,
  },
  retry: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: Spacing.one + 2,
    alignSelf: 'flex-start',
    borderRadius: Radius.pill,
    paddingVertical: Spacing.two,
    paddingHorizontal: Spacing.three,
  },
});
