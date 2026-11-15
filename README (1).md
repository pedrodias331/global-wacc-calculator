# Global Cost of Capital & WACC Calculator

A Streamlit application for comparing an industry's estimated **cost of equity, cost of debt and WACC in USD versus a selected country's local currency**. It attempts live data retrieval and supports granular manual overrides when a source is unavailable.

## Default model settings

- Industry: Software (System & Application)
- Country: United States (USD)
- Applied corporate tax: U.S. statutory rate
- Equity risk premium: 6.50%
- Debt / total capital: 30%
- All manual overrides: disabled

## Install and run locally

```bash
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

## Deploy on Streamlit Community Cloud

1. Create a **public** GitHub repository, such as `global-wacc-calculator`.
2. Upload `app.py`, `requirements.txt`, and `README.md` in the repository root.
3. Visit https://share.streamlit.io/ and sign in with GitHub.
4. Click **Create app** → **Yup, I have an app** (the wording may vary).
5. Select your repo and branch (`main`), and specify `app.py` as the main file.
6. Click **Deploy**. Future GitHub commits usually trigger a redeploy.

No API key or secrets are required by this implementation. It uses the OECD public SDMX API and tries to scrape the publicly visible Trading Economics instrument page.

## Financial model

For debt share `wD`, equity share `wE = 1-wD`, selected statutory tax `T`, unlevered industry beta `bU`, ERP `ERP`, 10-year government yields `yUS` and `yLC`, and BBB spreads `sUS` and `sLC`:

```text
D/E       = wD / wE
bL        = bU * (1 + (1-T) * D/E)
Ke_US     = yUS + bL * ERP
Ke_LC     = yLC + bL * ERP
Kd_US     = yUS + sUS
sLC_auto  = sUS * (1 + yLC) / (1 + yUS)
Kd_LC     = yLC + sLC
WACC_US   = wE * Ke_US + wD * Kd_US * (1-T)
WACC_LC   = wE * Ke_LC + wD * Kd_LC * (1-T)
```

Rates in these equations are decimals, but inputs and outputs shown in the UI are percentages. The **same** selected tax, capital weights and ERP are used for both currency columns. Automatic local BBB spread is **modelled by nominal-yield parity**, not fetched as an observed local-currency BBB spread. Users may override it. The selected-country government bond yield can incorporate sovereign and liquidity risk, so it is not always a genuinely risk-free local-currency rate.

## Data sources and refresh rules

| Data | Primary source | Refresh cache |
|---|---|---|
| US industry unlevered beta | [Damodaran US sector XLS](https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls), with [same-source HTML fallback](https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/Betas.html) | 8 h |
| Large nonfinancial Baa2/BBB spread | [Damodaran ratings table](https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/ratings.html) | 8 h |
| Combined statutory corporate income tax | [OECD Corporate Income Tax Rates dataset](https://www.oecd.org/en/data/datasets/corporate-income-tax-rates-database.html), queried through OECD SDMX v2.0; older v1.0 dataset used only as a disclosed fallback | 12 h |
| Country 10Y nominal yields | [Trading Economics Bonds](https://tradingeconomics.com/bonds), via individual public `/country/government-bond-yield` HTML pages | 90 min |

The **Refresh source data** button clears app caches and retries each source. The data status panel shows source links, the retrieved timestamp, source vintage or quote date where identifiable, and any fetch or parsing errors. Dates in the data panel are not automatically assumed to equal live quote dates.

### Handling source restrictions

Some data providers may return 403/429 errors or client-side-rendered pages to the Streamlit Cloud server. This app cannot guarantee Trading Economics will allow key-free scraping. It deliberately **does not** fill missing values with guessed rates or spreads. To finish a calculation when a source is blocked, enable the corresponding sidebar toggle under **Manual data overrides** and enter the value you've verified. If industry XLS download fails, the app tries Damodaran's published US industry HTML table. If the OECD 2026 API fails, it attempts the earlier official OECD dataset and discloses the older vintage. If neither works, supply the rate manually. No additional provider or estimated rate is used.

Selecting a country with no published Trading Economics 10-year sovereign yield or no OECD rate is possible, but users must provide those missing numbers manually. Choosing a country does not mean its values are guaranteed to be covered. USD-denominated sovereign securities should not be substituted for local-currency bonds.

### Notes / limitations

- OECD source is the **combined statutory** corporate rate (central + sub-central) where available, not an effective project-specific tax rate.
- The same Damodaran large nonfinancial BBB spread is used across sectors as a modelling assumption. Banks and other financial firms may need a different credit model.
- Government 10-year yields are nominal proxies for currency benchmark rates; foreign sovereign default risk, taxes, term premia and FX effects are not separately modelled.
- A common ERP and identical levered beta in USD/local currency are simplifying assumptions; the currency comparison is not an arbitrage-proof FX valuation model.
- The unlevered-beta manual override changes the selected industry and its row in the comparison; other sectors keep their published NYU betas.
- Not investment advice.
