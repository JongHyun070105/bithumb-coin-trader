import math
import random
from bithumb_coin_trader.research_statistics import deflated_sharpe_ratio, pstdev, mean

random.seed(42)
mu_daily = 1.0 / 252
sigma_daily = 1.0 / math.sqrt(252)

daily_returns = [random.gauss(mu_daily, sigma_daily) for _ in range(252)]

print("Mean:", mean(daily_returns))
print("StdDev:", pstdev(daily_returns))
print("Observed Daily Sharpe:", mean(daily_returns)/pstdev(daily_returns))
print("Observed Annualized Sharpe:", (mean(daily_returns)/pstdev(daily_returns))*math.sqrt(252))

res = deflated_sharpe_ratio(
    daily_returns,
    trial_sharpes=[0.0]*10,
    trial_count=10
)
print("DSR Result:", res)

# Check PBO
from bithumb_coin_trader.research_statistics import cscv_probability_backtest_overfitting

returns_by_cand = {
    f"c_{i}": [random.gauss(mu_daily, sigma_daily) for _ in range(252)]
    for i in range(5)
}
res_pbo = cscv_probability_backtest_overfitting(returns_by_cand)
print("PBO Result:", res_pbo)
