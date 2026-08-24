# Selective high-win-rate research mission

Find out whether any replayable Bithumb KRW-BTC spot strategy can achieve a
historical out-of-sample win rate of at least 70% without sacrificing net
expectancy or hiding behind a tiny sample.

This is a falsifiable research target, not a promise of profit. A candidate is
eligible for the final sealed holdout only when it passes every development
gate:

- completed public 30-minute OHLCV only;
- LONG/FLAT spot signals, no shorting or pyramiding;
- signal observed on a closed bar and executed at the next 30-minute open;
- chronological development folds with no future-dependent features, labels,
  preprocessing, thresholds, or hyperparameters;
- base costs of 0.25% fee plus 5 bps slippage per fill;
- stress costs of 0.50% fee plus 10 bps slippage per fill;
- observed closed-trade win rate at least 70%;
- at least 30 non-final-liquidation closed trades;
- positive compounded return under base and stress costs;
- profit factor greater than 1 under base costs;
- maximum drawdown no greater than 15%;
- positive return in at least 60% of chronological development folds;
- Wilson 95% lower confidence bound for win probability at least 50%.

The final 4,000 candles are sealed while candidates are built and ranked. At
most the top three development candidates may be evaluated once on that sealed
holdout. A candidate is only a historical research survivor if the sealed
holdout also has positive base and stress returns, at least 10 closed trades,
and a win rate of at least 60%. If no candidate passes, cash is the correct
result.

No result may automatically change paper or live settings. Prospective forward
evidence is still required before any future promotion decision.
