/**
 * Design system for the Digital Wellbeing Risk & Deviation Detection app.
 *
 * ---------------------------------------------------------------------------
 * COLOUR DECISIONS ARE VALIDATED, NOT EYEBALLED
 * ---------------------------------------------------------------------------
 * The data-visualisation palette below was checked with a contrast/CVD
 * validator against THIS app's real card surfaces (#FFFFFF light,
 * #17181A dark) rather than against generic defaults. Recorded results:
 *
 *   Categorical slots 1-3, all-pairs:
 *     light  PASS  worst CVD dE 9.2 (deutan), worst normal-vision dE 24.0
 *     dark   PASS  worst CVD dE 9.4 (deutan), worst normal-vision dE 20.9
 *
 *   Known constraints that the UI must honour:
 *     1. `series3` (aqua) is 2.82:1 on the light card - BELOW 3:1. Any chart
 *        using it in light mode MUST carry visible direct labels (relief rule).
 *     2. `riskMedium` (status warning) is 1.83:1 on the light card. Status
 *        colour therefore NEVER carries meaning alone - every risk badge ships
 *        an icon AND a text label. See the RiskBadge component.
 *     3. Only the first THREE categorical slots validate for all-pairs charts
 *        (donut/scatter, where any mark may be compared with any other).
 *        For four-plus categories use a single-hue bar chart with direct
 *        labels instead of a multi-colour donut.
 *
 * Text inks were all verified at or above 4.5:1 on their own surface.
 *
 * ---------------------------------------------------------------------------
 * STRUCTURE
 * ---------------------------------------------------------------------------
 * `Colors.light` and `Colors.dark` MUST declare identical keys - the exported
 * `ThemeColor` type is their key intersection, and ThemedView/ThemedText
 * accept any of those keys. The five original template keys (text, background,
 * backgroundElement, backgroundSelected, textSecondary) are preserved so the
 * existing themed components keep working unchanged.
 */

import '@/global.css';

import { Platform } from 'react-native';

export const Colors = {
  light: {
    // --- original template keys (kept for backward compatibility) ---
    /** Primary ink. Verified 19.68:1 on the card. */
    text: '#0B0B0B',
    /** Page plane - the colour behind the cards. */
    background: '#F7F7F8',
    /** Card / raised element surface. This is also the chart surface. */
    backgroundElement: '#FFFFFF',
    /** A pressed or selected surface. */
    backgroundSelected: '#ECECEF',
    /** Secondary ink. Verified 7.94:1. */
    textSecondary: '#52514E',

    // --- added ink ---
    /** Muted ink for axis ticks and fine print. Verified 5.07:1. */
    textMuted: '#6E6E73',
    /** Ink to place on top of a filled primary surface. */
    onPrimary: '#FFFFFF',

    // --- structure ---
    /** Hairline ring around cards and inputs. */
    border: 'rgba(11,11,11,0.10)',
    /** A more assertive divider. */
    borderStrong: 'rgba(11,11,11,0.18)',

    // --- brand / primary action ---
    primary: '#2A78D6',
    primaryPressed: '#1C5CAB',
    /** Low-chroma primary wash for selected chips and highlights. */
    primarySubtle: '#EAF2FD',

    // --- RESERVED status colours: risk levels only, never used as a series ---
    riskLow: '#0CA30C',
    riskMedium: '#FAB219',
    riskHigh: '#D03B3B',
    /** Tinted backgrounds for risk badges. */
    riskLowSurface: '#E8F6E8',
    riskMediumSurface: '#FEF4E0',
    riskHighSurface: '#FBEAEA',

    // --- categorical series slots (fixed order, never cycled) ---
    series1: '#2A78D6', // blue
    series2: '#EB6834', // orange
    series3: '#1BAF7A', // aqua - needs direct labels in light mode
    series4: '#EDA100', // yellow - avoid beside series2 in all-pairs charts
    series5: '#E87BA4', // magenta
    series6: '#008300', // green
    series7: '#6250D6', // violet
    series8: '#E34948', // red

    // --- chart chrome ---
    grid: '#E1E0D9',
    axis: '#C3C2B7',
  },

  dark: {
    // --- original template keys ---
    text: '#F0EFEC',
    background: '#0B0B0C',
    backgroundElement: '#17181A',
    backgroundSelected: '#24262A',
    textSecondary: '#C3C2B7',

    // --- added ink ---
    textMuted: '#8E8E93',
    onPrimary: '#FFFFFF',

    // --- structure ---
    border: 'rgba(255,255,255,0.10)',
    borderStrong: 'rgba(255,255,255,0.18)',

    // --- brand / primary action ---
    primary: '#3987E5',
    primaryPressed: '#2A78D6',
    primarySubtle: '#152335',

    // --- RESERVED status colours (same steps; status is never themed) ---
    riskLow: '#0CA30C',
    riskMedium: '#FAB219',
    riskHigh: '#D03B3B',
    riskLowSurface: '#132413',
    riskMediumSurface: '#2E2410',
    riskHighSurface: '#2C1515',

    // --- categorical series, stepped for the dark surface (not a flip) ---
    series1: '#3987E5',
    series2: '#D95926',
    series3: '#199E70',
    series4: '#C98500',
    series5: '#D55181',
    series6: '#008300',
    series7: '#9085E9',
    series8: '#E66767',

    // --- chart chrome ---
    grid: '#2C2C2A',
    axis: '#383835',
  },
} as const;

export type ThemeColor = keyof typeof Colors.light & keyof typeof Colors.dark;

/** The three risk levels the whole app speaks in. */
export type RiskLevel = 'low' | 'medium' | 'high';

/**
 * Maps a risk level to its colour keys. Deliberately does NOT include the
 * label or icon - those live in the RiskBadge component, which is what
 * enforces that colour is never the only signal.
 */
export const RiskColorKeys: Record<RiskLevel, { fg: ThemeColor; bg: ThemeColor }> = {
  low: { fg: 'riskLow', bg: 'riskLowSurface' },
  medium: { fg: 'riskMedium', bg: 'riskMediumSurface' },
  high: { fg: 'riskHigh', bg: 'riskHighSurface' },
};

/** Categorical slots in fixed assignment order. Never cycle past slot 8. */
export const SeriesKeys = [
  'series1',
  'series2',
  'series3',
  'series4',
  'series5',
  'series6',
  'series7',
  'series8',
] as const satisfies readonly ThemeColor[];

/**
 * Safe slot count for charts where ANY mark may be compared with any other
 * (donut, scatter). Past this, use a single-hue bar chart with direct labels.
 */
export const ALL_PAIRS_SERIES_CAP = 3;

export const Fonts = Platform.select({
  ios: {
    sans: 'system-ui',
    serif: 'ui-serif',
    rounded: 'ui-rounded',
    mono: 'ui-monospace',
  },
  default: {
    sans: 'normal',
    serif: 'serif',
    rounded: 'normal',
    mono: 'monospace',
  },
  web: {
    sans: 'var(--font-display)',
    serif: 'var(--font-serif)',
    rounded: 'var(--font-rounded)',
    mono: 'var(--font-mono)',
  },
});

/** 4px base spacing scale. Original template keys preserved. */
export const Spacing = {
  half: 2,
  one: 4,
  two: 8,
  three: 16,
  four: 24,
  five: 32,
  six: 64,
} as const;

/** Corner radii. Cards are generously rounded for a soft, premium feel. */
export const Radius = {
  sm: 8,
  md: 12,
  lg: 16,
  xl: 20,
  xxl: 28,
  pill: 999,
} as const;

/**
 * Elevation. Shadows are kept very subtle - the brief calls for "subtle
 * shadows", and heavy shadows read as dated. Android uses `elevation`,
 * iOS uses the shadow* props, so each level supplies both.
 */
export const Elevation = {
  none: Platform.select({
    android: { elevation: 0 },
    default: { shadowOpacity: 0 },
  }),
  card: Platform.select({
    android: { elevation: 1 },
    default: {
      shadowColor: '#000000',
      shadowOpacity: 0.06,
      shadowRadius: 8,
      shadowOffset: { width: 0, height: 2 },
    },
  }),
  raised: Platform.select({
    android: { elevation: 3 },
    default: {
      shadowColor: '#000000',
      shadowOpacity: 0.1,
      shadowRadius: 16,
      shadowOffset: { width: 0, height: 6 },
    },
  }),
} as const;

/**
 * Type scale. Large standalone numbers use proportional figures by default;
 * `tabular` is reserved for numbers that must align vertically (table rows,
 * axis ticks) per the visualisation guidance.
 */
export const Typography = {
  /** Hero figure - the risk score on the dashboard. */
  display: {
    fontSize: 56,
    lineHeight: 60,
    fontWeight: '700' as const,
    letterSpacing: -1.5,
  },
  title: {
    fontSize: 32,
    lineHeight: 38,
    fontWeight: '700' as const,
    letterSpacing: -0.6,
  },
  heading: {
    fontSize: 22,
    lineHeight: 28,
    fontWeight: '600' as const,
    letterSpacing: -0.3,
  },
  subheading: { fontSize: 17, lineHeight: 24, fontWeight: '600' as const },
  body: { fontSize: 15, lineHeight: 22, fontWeight: '400' as const },
  bodyStrong: { fontSize: 15, lineHeight: 22, fontWeight: '600' as const },
  caption: { fontSize: 13, lineHeight: 18, fontWeight: '400' as const },
  /** Small all-caps section label. */
  label: {
    fontSize: 11,
    lineHeight: 14,
    fontWeight: '700' as const,
    letterSpacing: 0.8,
  },
  /** Axis ticks and any vertically-aligned numbers. */
  tabular: {
    fontSize: 12,
    lineHeight: 16,
    fontWeight: '500' as const,
    fontVariant: ['tabular-nums' as const],
  },
} as const;

export const BottomTabInset = Platform.select({ ios: 50, android: 80 }) ?? 0;
export const MaxContentWidth = 800;
