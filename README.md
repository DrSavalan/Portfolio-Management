# Portfolio Management
<img width="1708" height="896" alt="Example" src="https://github.com/user-attachments/assets/6bd15491-1ef5-4ec2-a539-a469cbe35cac" />

### Cross-market insights for clearer portfolio decisions.

Portfolio Management is a MetaTrader 5–connected analytics dashboard for exploring allocations across metals, energy, agricultural commodities, an equity index, and major currency pairs. It is designed for investors, banks, financial institutions, funds, and commodity importers and exporters who want a clearer view of market data, portfolio risk, and historical allocation choices.

The application downloads available price candles from a local MetaTrader 5 terminal, saves them to Excel, and calculates portfolio analytics from the saved data. It does not place trades or promise returns.


<img width="1388" height="827" alt="image" src="https://github.com/user-attachments/assets/77a2ed8e-68b0-4ff0-b945-8f6a8c7fa327" />

## Built for cross-market portfolio review

- **10 tracked instruments:** Gold, Silver, Nasdaq100, Copper, Brent, Coffee, Cocoa, USD/JPY, USD/CHF, and EUR/USD.
- **Three selectable horizons:** Short-term (M15, approximately three months), medium-term (H1, one calendar year), and long-term (H4, approximately four years), requesting up to 8,760 candles per instrument.
- **Four optimization objectives:** Shortest historical drawdown duration, lowest maximum drawdown, highest historical return, or highest Sharpe ratio.
- **Clear allocation constraints:** Long-only weights in 5% increments, from 0% to a maximum of 30% per instrument.
- **Rebalancing controls:** Previous allocations are carried forward to moderate changes, with turnover capped per cycle.
- **Risk and performance context:** Historical return, maximum drawdown, drawdown duration in calendar time, Sharpe ratio, Sortino ratio, Calmar ratio, volatility, and estimated annualized return.
- **Data transparency:** Download progress, actual shared analysis dates, and warnings when available history is limited or a symbol must be excluded.
- **Excel persistence:** Downloaded candles are saved in a separate workbook for each timeframe.

## Who it is for

For investors and capital allocators, the dashboard provides a practical way to compare historical allocation objectives and review the risk profile of a proposed basket.

For banks, financial institutions, and funds, it offers a research starting point for cross-market portfolio analysis—not a replacement for internal risk systems, investment governance, or due diligence.

For commodity importers, exporters, and trading businesses, it brings commodity prices and selected currency markets into one view to support market monitoring and analysis.

## How it works

1. Connect to a running local MetaTrader 5 terminal.
2. Request up to 8,760 completed candles per tracked instrument for the selected timeframe.
3. Save available market data to a timeframe-specific Excel workbook.
4. Align available price histories over their shared dates, within the selected calendar horizon.
5. Calculate candidate long-only allocations and rank them by the selected historical objective.
6. Display allocation weights, rebalance guidance, risk statistics, and the dates and intervals used.

If an instrument has fewer than 8,760 candles, its available history is used. Instruments with fewer than two candles are excluded because they cannot provide a return series. If the shared history is shorter than the selected horizon, the dashboard reports the actual dates used and displays a warning. Missing data is not replaced with demo prices.

## Requirements

- Windows with Python installed
- MetaTrader 5 installed and running, with price history available for the desired instruments
- Python packages: Flask, MetaTrader5, NumPy, pandas, and openpyxl

## Run locally

Install the dependencies:

```powershell
python -m pip install Flask MetaTrader5 numpy pandas openpyxl
