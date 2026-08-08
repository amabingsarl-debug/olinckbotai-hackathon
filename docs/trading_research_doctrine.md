# OLINCK BOT AI - Trading Research Doctrine

This note records the evidence-based trading principles used to keep the bot from learning noisy or dangerous behavior.

## Source Principles

- Backtests are not proof. Historical performance must survive walk-forward and out-of-sample validation before a strategy can receive capital.
- A strategy with attractive in-sample results but weak live or paper performance must be quarantined, not optimized harder.
- Position sizing must never be increased automatically by AI. Good evidence can preserve or slightly reduce risk; weak evidence must reduce or block risk.
- Market microstructure matters. Spread, depth, slippage, liquidity, and pump exhaustion must be checked before fast crypto or meme-coin entries.
- News and catalysts are filters, not guarantees. Negative catalysts block entries; positive catalysts can only help a setup that already passes price, volatility, and liquidity checks.

## Sources Reviewed

- Marcos Lopez de Prado, *Advances in Financial Machine Learning*, Wiley, 2018.
- David Aronson, *Evidence-Based Technical Analysis*, Wiley, 2006.
- Larry Harris, *Trading and Exchanges: Market Microstructure for Practitioners*, Oxford University Press, 2002.
- Moskowitz, Ooi, and Pedersen, "Time Series Momentum", SSRN, 2012.
- Wiecki, Campbell, Lent, and Stauth, "All that Glitters Is Not Gold: Comparing Backtest and Out-of-Sample Performance on a Large Cohort of Trading Algorithms", SSRN, 2016/2019.
- Aparicio and Lopez de Prado, "How Hard Is It to Pick the Right Model? MCS and Backtest Overfitting", SSRN, 2018.
- Tinsley, "Walk Forward Correlation: A Diagnostic for Over-Fitting and Structural Edge in Trading Strategy Optimisation", SSRN, 2026.
- Ang and Timmermann, "Regime Changes and Financial Markets", NBER, 2011.
- Dickinson, "Liquidity Fragility and Asymmetric Price Discovery in Crypto-Asset Markets", SSRN, 2026.
- Mercik and Bedowska-Sojka, "When Markets Never Sleep: Intraday Liquidity Patterns and Volatility Effects in Cryptocurrency Trading", SSRN, 2026.
- Varma, "Within-Venue Monitoring of BTC/USDT Liquidity and Resiliency on Binance", Journal of Risk and Financial Management, 2026.
- Dimpfl, "Bitcoin Market Microstructure", SSRN, 2017.
- Aldridge, "Slippage in AMM Markets", SSRN, 2022.
- Shu, Yu, and Mulvey, "Downside Risk Reduction Using Regime-Switching Signals", Journal of Asset Management, 2024.

## Current Bot Rules

- Strong live allocation now requires a holdout profit factor of at least 1.6.
- Moderate historical edges are not trusted with full size; they are reduced or quarantined.
- Scout/opportunity trades require stronger evidence than before: high opportunity score, positive holdout return, and holdout profit factor at least 1.45.
- Recovery mode only accepts unusually strong core setups and blocks scouts and meme sprints.
- Three recent losing trades in the same symbol/strategy/style block that setup.
- Boosting after good results is intentionally tiny and requires at least eight recent trades, positive PnL, profit factor at least 1.6, and win rate at least 55%.
- Crypto entries should be rejected when spreads widen, depth falls, or sell-side impact dominates.
- Every entry now passes a general execution-quality gate before risk sizing: order book availability, spread, 24h quote volume, two-sided 1% depth, bid/ask imbalance, exhaustion wicks, and estimated slippage.
- Scout trades require stricter execution quality than core trades because small fast trades can be destroyed by fees and slippage.
- Thin but tradable markets are not treated like deep markets; they receive a lower risk multiplier even when the signal is valid.
- Intraday crypto liquidity is not constant; the bot should learn which hours have poor execution quality and reduce or block entries during those windows.
- Regime switching must control exposure. In high-volatility or bear/liquidation regimes, the correct action is often no trade.
- For meme coins and new listings, liquidity resiliency matters more than headline momentum. A fast pump with weak bid depth is treated as a trap candidate.

## Removed Assumption

The bot must not assume that "more trades means more profit." More trades only help when the edge survives fees, slippage, correlation, live feedback, and adverse market regimes.
