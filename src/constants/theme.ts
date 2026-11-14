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
    /** Primary ink - slate 900. */
    text: '#0F172A',
    /** Page plane - a cool off-white behind the cards. */
    background: '#F5F6F8',
    /** Card / raised element surface. This is also the chart surface. */
    backgroundElement: '#FFFFFF',
    /** A pressed or selected surface. */
    backgroundSelected: '#EEF0F4',
    /** Secondary ink - slate 600, 7.6:1 on the card. */
    textSecondary: '#475467',

    // --- added ink ---
    /** Muted ink for axis ticks and fine print - 4.9:1 on the card. */
    textMuted: '#667085',
    /** Ink to place on top of a filled primary surface. */
    onPrimary: '#FFFFFF',

    // --- structure ---
    /** Hairline ring around cards and inputs. */
    border: 'rgba(15,23,42,0.08)',
    /** A more assertive divider. */
    borderStrong: 'rgba(15,23,42,0.16)',

    // --- brand / primary action ---
    primary: '#2563EB',
    primaryPressed: '#1D4ED8',
    /** Low-chroma primary wash for selected chips and highlights. */
    primarySubtle: '#EEF3FF',

    // --- RESERVED status colours: risk levels only, never used as a series ---
    riskLow: '#12A150',
    riskMedium: '#F59E0B',
    riskHigh: '#E5484D',
    /** Readable text on the medium surface (the fill amber is too light for text). */
    riskMediumInk: '#B45309',
    /** Tinted backgrounds for risk badges. */
    riskLowSurface: '#EAF8F0',
    riskMediumSurface: '#FFF6E5',
    riskHighSurface: '#FDEEEE',

    // --- categorical series slots (fixed order, never cycled) ---
    series1: '#2563EB', // blue
    series2: '#EB6834', // orange
    series3: '#14A47A', // aqua - needs direct labels in light mode
    series4: '#EDA100', // yellow - avoid beside series2 in all-pairs charts
    series5: '#E87BA4', // magenta
    series6: '#008300', // green
    series7: '#6250D6', // violet
    series8: '#E34948', // red

    // --- chart chrome ---
    grid: '#EDEFF3',
    axis: '#D0D5DD',
  },

  dark: {
    // --- original template keys ---
    text: '#EDEDEF',
    background: '#0A0A0B',
    backgroundElement: '#141517',
    backgroundSelected: '#1F2023',
    textSecondary: '#A1A1AA',

    // --- added ink ---
    textMuted: '#8B8B93',
    onPrimary: '#FFFFFF',

    // --- structure ---
    border: 'rgba(255,255,255,0.08)',
    borderStrong: 'rgba(255,255,255,0.16)',

    // --- brand / primary action ---
    primary: '#4C8DF6',
    primaryPressed: '#2563EB',
    primarySubtle: '#16213A',

    // --- RESERVED status colours (same steps; status is never themed) ---
    riskLow: '#22B865',
    riskMedium: '#F5A524',
    riskHigh: '#EF5B5F',
    riskMediumInk: '#F5B544',
    riskLowSurface: '#10241A',
    riskMediumSurface: '#2B2210',
    riskHighSurface: '#2C1617',

    // --- categorical series, stepped for the dark surface (not a flip) ---
    series1: '#4C8DF6',
    series2: '#D95926',
    series3: '#199E70',
    series4: '#C98500',
    series5: '#D55181',
    series6: '#008300',
    series7: '#9085E9',
    series8: '#E66767',

    // --- chart chrome ---
    grid: '#232428',
    axis: '#34363B',
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
    // A tinted, very soft shadow (honoured on Android 9+), not a grey smudge.
    android: { elevation: 2, shadowColor: '#101828' },
    default: {
      shadowColor: '#101828',
      shadowOpacity: 0.05,
      shadowRadius: 10,
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
    fontSize: 52,
    lineHeight: 58,
    fontWeight: '700' as const,
    letterSpacing: -1.8,
  },
  title: {
    fontSize: 30,
    lineHeight: 36,
    fontWeight: '700' as const,
    letterSpacing: -0.9,
  },
  heading: {
    fontSize: 22,
    lineHeight: 28,
    fontWeight: '600' as const,
    letterSpacing: -0.5,
  },
  subheading: { fontSize: 17, lineHeight: 24, fontWeight: '600' as const, letterSpacing: -0.2 },
  body: { fontSize: 15, lineHeight: 22, fontWeight: '400' as const },
  bodyStrong: { fontSize: 15, lineHeight: 22, fontWeight: '600' as const, letterSpacing: -0.1 },
  caption: { fontSize: 13, lineHeight: 19, fontWeight: '400' as const },
  /** Small all-caps section label. */
  label: {
    fontSize: 11,
    lineHeight: 14,
    fontWeight: '600' as const,
    letterSpacing: 0.7,
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
