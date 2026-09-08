/**
 * Read-Only Trading Dashboard API Contract
 *
 * STRICT BOUNDARY:
 * These endpoints are strictly READ-ONLY.
 * NO: POST/PUT/DELETE orders, BUY/SELL, API key mutations, live enable, AWS mutations.
 *
 * Default implementation targets localhost (127.0.0.1) only.
 */

import type { BotStatus, PortfolioSummary, Position, Trade, TradingSnapshot } from './model'

export interface ApiHealthResponse {
  status: 'ok' | 'degraded'
  version: string
  timestamp: string
  environment: 'local' | 'development'
  readOnly: true
}

export interface ReadOnlyTradingApiEndpoints {
  /**
   * Health check endpoint
   * GET /api/health
   */
  getHealth: () => Promise<ApiHealthResponse>

  /**
   * Complete unified snapshot for the trading console
   * GET /api/trading/snapshot
   */
  getTradingSnapshot: () => Promise<TradingSnapshot>

  /**
   * Portfolio balance and summary
   * GET /api/portfolio
   */
  getPortfolio: () => Promise<PortfolioSummary>

  /**
   * Current open positions
   * GET /api/positions
   */
  getPositions: () => Promise<Position[]>

  /**
   * Executed trades history
   * GET /api/trades
   */
  getTrades: (options?: { limit?: number; since?: string }) => Promise<Trade[]>

  /**
   * Performance metrics
   * GET /api/performance
   */
  getPerformance: () => Promise<TradingSnapshot['performance']>

  /**
   * Bot execution and supervisor status
   * GET /api/bot/status
   */
  getBotStatus: () => Promise<BotStatus>
}
