/**
 * One function per backend endpoint.
 *
 * Centralising these means no screen contains a URL string, so a route
 * change is a single edit here rather than a search through the UI.
 */

import type {
  DataSourceName,
  ActualVsPredictedResponse,
  AnomaliesResponse,
  FeatureDictionaryResponse,
  ForecastResponse,
  HealthResponse,
  ImportResultResponse,
  MlStatusResponse,
  ModelPerformanceResponse,
  PersonaResponse,
  RecommendationsResponse,
  RiskClassificationResponse,
  RiskScoreResponse,
  SeedDemoResponse,
  UsageRecord,
  UsageRecordInput,
  UsageSummary,
} from '@/types/api';

import { getSyncStatus } from '@/services/usage/device-sync';

import { request } from './client';

// --- health ---------------------------------------------------------------

export const getHealth = () => request<HealthResponse>('/api/health');

export const getMlStatus = () => request<MlStatusResponse>('/api/ml/status');

// --- usage records --------------------------------------------------------

export const getUsageSummary = () => request<UsageSummary>('/api/usage/summary');

/**
 * Raw stored days.
 *
 * `sources` is REQUIRED, and null is the explicit way to ask for everything.
 * An optional filter here would default to including generated demonstration
 * rows, which is right for the Data screen (an inventory of what is stored)
 * and wrong for any chart of the user's own behaviour - and the difference is
 * invisible at the call site. Making the choice explicit is what turns that
 * into a compiler error instead of a wrong chart.
 */
export const getUsageRecords = (
  sources: DataSourceName[] | null,
  params?: {
    limit?: number;
    offset?: number;
    start_date?: string;
    end_date?: string;
  }
) =>
  request<UsageRecord[]>('/api/usage/records', {
    query: { ...params, ...(sources ? { sources } : {}) },
  });

/** The provenances an AnalysisScope covers, for record queries. */
export const sourcesForScope = (scope: AnalysisScope): DataSourceName[] | null => {
  if (scope === 'device') return ['device'];
  if (scope === 'real') return ['device', 'manual', 'csv'];
  return null;
};

export const createUsageRecord = (payload: UsageRecordInput) =>
  request<UsageRecord>('/api/usage/records', { method: 'POST', body: payload });

export const deleteUsageRecord = (id: number) =>
  request<void>(`/api/usage/records/${id}`, { method: 'DELETE' });

export const deleteUsageBySource = (source: 'manual' | 'csv' | 'synthetic') =>
  request<{ deleted: number; source: string }>('/api/usage/records', {
    method: 'DELETE',
    query: { source },
  });

// --- data -----------------------------------------------------------------

export const getFeatureDictionary = () =>
  request<FeatureDictionaryResponse>('/api/data/feature-dictionary');

export const seedDemoData = (params?: {
  days?: number;
  profile?: string;
  seed?: number;
  replace?: boolean;
}) => request<SeedDemoResponse>('/api/data/seed-demo', { method: 'POST', query: params });

export const getDemoProfiles = () =>
  request<{ profiles: { key: string; screen_minutes_mean: number }[] }>(
    '/api/data/demo-profiles'
  );

export const importCsv = (file: { uri: string; name: string; mimeType?: string }) => {
  const formData = new FormData();
  // React Native's FormData accepts this object shape for file parts; the
  // cast is needed because the DOM typings expect a Blob.
  formData.append('file', {
    uri: file.uri,
    name: file.name,
    type: file.mimeType ?? 'text/csv',
  } as unknown as Blob);

  return request<ImportResultResponse>('/api/data/import-csv', {
    method: 'POST',
    formData,
  });
};

export const getImportHistory = () =>
  request<{
    imports: {
      id: number;
      filename: string;
      rows_in_file: number;
      rows_accepted: number;
      rows_rejected: number;
      rows_duplicate: number;
      created_at: string | null;
    }[];
  }>('/api/data/import-history');

// --- ML -------------------------------------------------------------------

/**
 * `scope` selects which stored data the model runs on - see hooks/use-scope.
 * It is threaded through every ML call so a screen can never silently mix
 * measured days with generated demonstration days.
 */
export type AnalysisScope = 'device' | 'real' | 'all';

/** The Home dashboard headline: the validated classifier. */
export const getRiskClassification = (scope: AnalysisScope) =>
  request<RiskClassificationResponse>('/api/ml/risk-classification', { query: { scope } });

export const getForecast = (scope: AnalysisScope, horizon = 7) =>
  request<ForecastResponse>('/api/ml/forecast', { query: { horizon, scope } });

export const getActualVsPredicted = (scope: AnalysisScope) =>
  request<ActualVsPredictedResponse>('/api/ml/actual-vs-predicted', { query: { scope } });

/** The PCA behaviour index. NOT the headline risk - it did not validate. */
export const getRiskScore = (scope: AnalysisScope) =>
  request<RiskScoreResponse>('/api/ml/risk-score', { query: { scope } });

export const getRiskScoreMethod = () =>
  request<Record<string, unknown>>('/api/ml/risk-score-method');

export const getPersona = (scope: AnalysisScope) =>
  request<PersonaResponse>('/api/ml/persona', { query: { scope } });

export const getClusteringReport = () =>
  request<Record<string, unknown>>('/api/ml/clustering-report');

export const getAnomalies = (
  scope: AnalysisScope,
  params?: { limit?: number; only_anomalous?: boolean }
) => request<AnomaliesResponse>('/api/ml/anomalies', { query: { ...params, scope } });

export const getRecommendations = (scope: AnalysisScope) =>
  request<RecommendationsResponse>('/api/ml/recommendations', { query: { scope } });

export const getRecommendationsMethod = () =>
  request<Record<string, unknown>>('/api/ml/recommendations-method');

export const getModelPerformance = () =>
  request<ModelPerformanceResponse>('/api/ml/model-performance');


/** Re-exported so screens import one module for all API access. */
export { getSyncStatus };
