"""USD versus local-currency cost of capital. Data are fetched at runtime, never guessed."""

from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO, StringIO
import re
import unicodedata

import pandas as pd
import pycountry
import requests
import streamlit as st
from babel.numbers import get_territory_currencies
from bs4 import BeautifulSoup


st.set_page_config(page_title="Global WACC Calculator", page_icon="🌐", layout="wide")

BETAS_XLS = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls"
BETAS_HTML = "https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/Betas.html"
RATINGS_URL = "https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/ratings.html"
OECD_DATASET = "https://data-explorer.oecd.org/vis?df%5Bag%5D=OECD.CTP.TPS&df%5Bid%5D=DSD_TAX_CIT%40DF_CIT&df%5Bvs%5D=2.0"
OECD_API_2026 = (
    "https://sdmx.oecd.org/public/rest/data/"
    "OECD.CTP.TPS,DSD_TAX_CIT@DF_CIT,2.0/.A..ST..S13..."
    "?startPeriod=2024&dimensionAtObservation=AllDimensions&format=csvfile"
)
OECD_API_2025 = (
    "https://sdmx.oecd.org/public/rest/data/"
    "OECD.CTP.TPS,DSD_TAX_CIT@DF_CIT,1.0/.A.CIT_C.ST..S13..."
    "?startPeriod=2024&dimensionAtObservation=AllDimensions&format=csvfile"
)
TE_BONDS = "https://tradingeconomics.com/bonds"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; GlobalWACC/1.0; financial-education)",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,text/csv,*/*;q=0.8",
}
MONTHS = (
    "January|February|March|April|May|June|July|August|September|"
    "October|November|December"
)
COUNTRY_DISPLAY = {
    "USA": "United States", "GBR": "United Kingdom", "KOR": "South Korea",
    "CZE": "Czech Republic", "TUR": "Turkey", "RUS": "Russia",
    "VNM": "Vietnam", "IRN": "Iran", "TWN": "Taiwan",
    "BOL": "Bolivia", "VEN": "Venezuela", "MDA": "Moldova",
    "TZA": "Tanzania", "BRN": "Brunei", "LAO": "Laos",
}
TE_SLUG = {
    "CZE": "czech-republic", "KOR": "south-korea", "TUR": "turkey",
    "RUS": "russia", "VNM": "vietnam", "TWN": "taiwan",
    "HKG": "hong-kong", "NZL": "new-zealand", "ARE": "united-arab-emirates",
}


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def norm(value: object) -> str:
    return re.sub(r"\s+", " ", str(value).strip()).casefold()


def http_get(url: str, timeout: int = 19) -> requests.Response:
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r


def extract_damodaran_table(raw: pd.DataFrame) -> pd.DataFrame:
    """Locate the actual header row in XLS or read_html output; never use cash-adjusted beta."""
    raw = raw.copy().reset_index(drop=True)
    # pandas.read_html may promote <th> cells into column headers instead of row 0.
    # Prepend them so we can use the same exact-column matching for HTML and XLS.
    header_row = pd.DataFrame([list(raw.columns)], columns=raw.columns)
    raw = pd.concat([header_row, raw], ignore_index=True)
    for row_no in range(min(45, len(raw))):
        labels = [norm(x) for x in raw.iloc[row_no].tolist()]
        industry_cols = [i for i, v in enumerate(labels) if v in ("industry name", "industry")]
        beta_cols = [i for i, v in enumerate(labels) if v == "unlevered beta"]
        if not industry_cols or not beta_cols:
            continue
        i_col, b_col = industry_cols[0], beta_cols[0]
        result = raw.iloc[row_no + 1 :, [i_col, b_col]].copy()
        result.columns = ["Industry", "Unlevered beta"]
        result["Industry"] = result["Industry"].astype(str).str.strip()
        result["Unlevered beta"] = pd.to_numeric(result["Unlevered beta"], errors="coerce")
        result = result.dropna(subset=["Unlevered beta"])
        result = result[
            result["Industry"].str.len().between(2, 100)
            & ~result["Industry"].str.casefold().isin(["total market", "nan", "total"])
            & result["Unlevered beta"].between(0.01, 10)
        ]
        result = result.drop_duplicates("Industry").sort_values("Industry").reset_index(drop=True)
        if len(result) >= 20:
            return result
    raise ValueError("Could not identify the Industry Name and exact Unlevered beta columns.")


@st.cache_data(ttl=8 * 3600, show_spinner=False)
def load_betas(refresh: int) -> dict:
    errors = []
    try:
        r = http_get(BETAS_XLS)
        # The source .xls is an old-style Excel workbook.
        book = pd.ExcelFile(BytesIO(r.content), engine="xlrd")
        for sheet in book.sheet_names:
            try:
                raw = pd.read_excel(book, sheet_name=sheet, header=None)
                data = extract_damodaran_table(raw)
                vintage = "Date not verified in XLS"
                try:
                    page_text = BeautifulSoup(http_get(BETAS_HTML).text, "html.parser").get_text(" ", strip=True)
                    date_match = re.search(r"Date of Analysis:\s*Data used is as of\s*(\w+\s+\d{4})", page_text, re.I)
                    if date_match:
                        vintage = date_match.group(1)
                except Exception:
                    pass  # Don't discard a valid XLS table because the metadata page is offline.
                return {"data": data, "url": BETAS_XLS, "vintage": vintage, "retrieved": now_utc(), "error": None}
            except (ValueError, IndexError, TypeError):
                continue
        raise ValueError("Excel workbook opened, but no valid industry-beta table was found.")
    except Exception as exc:
        errors.append(f"XLS: {exc}")
    # Same NYU US-sector dataset, served as an HTML table if the XLS is inaccessible.
    try:
        r = http_get(BETAS_HTML)
        tables = pd.read_html(StringIO(r.text), header=None)
        for table in tables:
            try:
                data = extract_damodaran_table(table)
                clean = BeautifulSoup(r.text, "html.parser").get_text(" ", strip=True)
                match = re.search(r"Date of Analysis:\s*Data used is as of\s*(\w+\s+\d{4})", clean, re.I)
                return {"data": data, "url": BETAS_HTML, "vintage": match.group(1) if match else "NYU website", "retrieved": now_utc(), "error": None}
            except ValueError:
                continue
        raise ValueError("No valid unlevered-beta table on the NYU page.")
    except Exception as exc:
        errors.append(f"HTML: {exc}")
        return {"data": pd.DataFrame(columns=["Industry", "Unlevered beta"]), "url": BETAS_XLS, "vintage": "Unavailable", "retrieved": now_utc(), "error": " | ".join(errors)}


def parse_bbb_spread(html: str) -> tuple[float, str]:
    soup = BeautifulSoup(html, "html.parser")
    full_text = soup.get_text(" ", strip=True)
    match_date = re.search(r"Date of Analysis:\s*Data used is as of\s*(\w+\s+\d{4})", full_text, re.I)
    vintage = match_date.group(1) if match_date else "Date not stated"
    for tr in soup.select("tr"):
        cells = [x.get_text(" ", strip=True) for x in tr.find_all(["td", "th"], recursive=False)]
        for idx, cell in enumerate(cells):
            if re.fullmatch(r"Baa2\s*/\s*BBB", cell, re.I):
                # First Baa2/BBB is the LARGE NON-FINANCIAL firms column.
                for candidate in cells[idx + 1 : idx + 3]:
                    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*%\s*", candidate)
                    if match:
                        number = float(match.group(1))
                        if 0 < number < 20:
                            return number, vintage
                raise ValueError("BBB row found but adjacent spread column was not identifiable.")
    raise ValueError("Baa2/BBB row not present in the ratings table.")


@st.cache_data(ttl=8 * 3600, show_spinner=False)
def load_bbb(refresh: int) -> dict:
    try:
        spread, vintage = parse_bbb_spread(http_get(RATINGS_URL).text)
        return {"value": spread, "url": RATINGS_URL, "vintage": vintage, "retrieved": now_utc(), "error": None}
    except Exception as exc:
        return {"value": None, "url": RATINGS_URL, "vintage": "Unavailable", "retrieved": now_utc(), "error": str(exc)}


def parse_oecd_csv(body: str) -> pd.DataFrame:
    frame = pd.read_csv(StringIO(body), dtype=str, low_memory=False)
    frame.columns = [str(c).strip().upper() for c in frame.columns]
    essential = {"REF_AREA", "TIME_PERIOD", "OBS_VALUE"}
    if not essential.issubset(frame.columns):
        raise ValueError(f"OECD CSV missing columns: {sorted(essential - set(frame.columns))}")
    for col, valid in [("FREQ", "A"), ("TARGETING", "ST"), ("INSTITUTIONAL_SECTOR", "S13")]:
        if col in frame.columns:
            frame = frame[frame[col].astype(str).str.strip().eq(valid)]
    # Do not conflate a central-only rate with a combined central+subcentral rate.
    if "MEASURE" in frame.columns:
        measures = frame["MEASURE"].dropna().unique().tolist()
        combined = [v for v in measures if re.search(r"CIT_C|COMBIN", str(v), re.I)]
        if combined:
            frame = frame[frame["MEASURE"].isin(combined)]
    if "UNIT_MEASURE" in frame.columns:
        units = frame["UNIT_MEASURE"].dropna().unique().tolist()
        percents = [v for v in units if re.search(r"PT|PERCENT", str(v), re.I)]
        if percents:
            frame = frame[frame["UNIT_MEASURE"].isin(percents)]
    frame = frame.copy()
    frame["year"] = pd.to_numeric(frame["TIME_PERIOD"], errors="coerce")
    frame["rate"] = pd.to_numeric(frame["OBS_VALUE"], errors="coerce")
    frame = frame[frame["year"].between(2020, 2100) & frame["rate"].between(0, 100)]
    # For ambiguous duplicate country-year values, discard rather than guessing.
    tidy = []
    for (iso, year), subset in frame.groupby(["REF_AREA", "year"]):
        distinct = sorted(set(round(float(v), 7) for v in subset["rate"]))
        if len(distinct) == 1:
            tidy.append({"iso3": iso, "year": int(year), "rate": distinct[0]})
    if not tidy:
        raise ValueError("OECD returned no unambiguous combined statutory corporate-tax rates.")
    return pd.DataFrame(tidy).sort_values("year").drop_duplicates("iso3", keep="last").reset_index(drop=True)


@st.cache_data(ttl=12 * 3600, show_spinner=False)
def load_oecd_taxes(refresh: int) -> dict:
    errors = []
    for url, dataset in [(OECD_API_2026, "OECD CIT database v2.0"), (OECD_API_2025, "OECD CIT database v1.0 (fallback)")]:
        try:
            data = parse_oecd_csv(http_get(url, timeout=24).text)
            if "USA" not in set(data["iso3"]):
                raise ValueError("No verified U.S. statutory rate in OECD response.")
            return {"data": data, "url": url, "vintage": dataset, "retrieved": now_utc(), "error": None}
        except Exception as exc:
            errors.append(f"{dataset}: {exc}")
    return {"data": pd.DataFrame(columns=["iso3", "year", "rate"]), "url": OECD_API_2026, "vintage": "Unavailable", "retrieved": now_utc(), "error": " | ".join(errors)}


def yield_url(iso3: str, country_name: str) -> str:
    if iso3 in TE_SLUG:
        slug = TE_SLUG[iso3]
    else:
        name = unicodedata.normalize("NFKD", country_name)
        slug = re.sub(r"[^a-z0-9]+", "-", "".join(c for c in name if not unicodedata.combining(c)).lower()).strip("-")
    return f"https://tradingeconomics.com/{slug}/government-bond-yield"


def parse_te_yield(html: str) -> tuple[float, str]:
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find("h1")
    title = heading.get_text(" ", strip=True) if heading else (soup.title.get_text(" ", strip=True) if soup.title else "")
    if not re.search(r"10\s*[- ]?\s*(?:year|y)\b", title, re.I):
        raise ValueError("Trading Economics page does not identify a 10-year government yield.")
    text = soup.get_text(" ", strip=True)
    # Single-instrument page: headline 'Actual' corresponds to its own 10Y bond quote.
    start = text.find(title)
    context = text[start : start + 4500] if start >= 0 else text[:4500]
    match = re.search(r"\bActual\s*:?[\s\xa0]*(-?\d{1,3}(?:[.,]\d{1,6})?)\s*%?\s*(?:Daily Change|Monthly|Yearly|Previous|Forecast)", context, re.I)
    if not match:
        raise ValueError("Visible 10-year 'Actual' yield not found (possibly JavaScript-only or blocked).")
    rate = float(match.group(1).replace(",", "."))
    if not -5 < rate < 150:
        raise ValueError("10-year yield outside reasonable parsing bounds; refused value.")
    # The quotation's trading date, if expressly given; otherwise label only retrieval time.
    day_match = re.search(rf"\bon\s+(({MONTHS})\s+\d{{1,2}},\s+\d{{4}})", context, re.I)
    return rate, (day_match.group(1) if day_match else "Trading date not separately verified")


@st.cache_data(ttl=90 * 60, show_spinner=False)
def load_te_yield(iso3: str, name: str, refresh: int) -> dict:
    url = yield_url(iso3, name)
    try:
        response = http_get(url)
        if response.url.rstrip("/").lower() != url.rstrip("/").lower():
            raise ValueError(f"Unexpected redirect to {response.url} (country quote not verified).")
        rate, as_of = parse_te_yield(response.text)
        return {"value": rate, "url": url, "vintage": as_of, "retrieved": now_utc(), "error": None}
    except Exception as exc:
        return {"value": None, "url": url, "vintage": "Unavailable", "retrieved": now_utc(), "error": str(exc)}


def tax_for_iso(table: pd.DataFrame, iso3: str) -> tuple[float | None, str]:
    subset = table[table["iso3"] == iso3]
    if subset.empty:
        return None, "Not covered / not retrieved"
    row = subset.iloc[-1]
    return float(row["rate"]), str(int(row["year"]))


def override(label: str, auto: float | None, key: str, *, min_value: float = 0.0, max_value: float = 100.0, suffix: str = "%") -> tuple[float | None, str]:
    enabled = st.toggle(f"Override {label}", value=False, key=f"{key}_enabled")
    if enabled:
        raw = st.text_input(
            f"{label} ({suffix or 'number'})",
            value=(f"{auto:.6g}" if auto is not None else ""),
            placeholder="Enter a verified number",
            key=f"{key}_value",
        )
        try:
            value = float(raw.strip().replace(",", "."))
            if not min_value <= value <= max_value:
                raise ValueError("Outside allowed range")
            return value, "Manual"
        except (TypeError, ValueError):
            st.caption("Enter a valid numeric manual value to enable calculations.")
            return None, "Manual input required"
    st.caption(f"Auto: {auto:.4f}{suffix}" if auto is not None else "Auto: unavailable — enable override to supply this value")
    return auto, "Automatic" if auto is not None else "Unavailable"


def compute(beta_u: float, rf_us: float, rf_local: float, erp: float, debt_share: float, tax: float, spread_us: float, spread_local: float) -> dict:
    d = debt_share / 100.0
    e = 1.0 - d
    t = tax / 100.0
    beta_l = beta_u * (1.0 + (1.0 - t) * d / e)
    ke_us = rf_us + beta_l * erp / 100.0
    ke_local = rf_local + beta_l * erp / 100.0
    kd_us = rf_us + spread_us / 100.0
    kd_local = rf_local + spread_local / 100.0
    return {
        "Unlevered beta": beta_u, "Relevered beta": beta_l,
        "USD cost of equity (%)": ke_us * 100,
        "Local cost of equity (%)": ke_local * 100,
        "USD pretax debt cost (%)": kd_us * 100,
        "Local pretax debt cost (%)": kd_local * 100,
        "USD after-tax debt cost (%)": kd_us * (1 - t) * 100,
        "Local after-tax debt cost (%)": kd_local * (1 - t) * 100,
        "USD WACC (%)": (e * ke_us + d * kd_us * (1 - t)) * 100,
        "Local WACC (%)": (e * ke_local + d * kd_local * (1 - t)) * 100,
    }


# ---------- Interface ----------
st.title("🌐 Global Cost of Capital & WACC")
st.write("Compare an industry's project financing costs in **USD** and a selected country's **local currency**.")

if "refresh_count" not in st.session_state:
    st.session_state.refresh_count = 0
with st.sidebar:
    st.header("Model inputs")
    if st.button("🔄 Refresh source data", use_container_width=True):
        st.session_state.refresh_count += 1
        st.cache_data.clear()
        st.rerun()

with st.spinner("Reading Damodaran, OECD and Trading Economics sources …"):
    betas = load_betas(st.session_state.refresh_count)
    bbb = load_bbb(st.session_state.refresh_count)
    oecd = load_oecd_taxes(st.session_state.refresh_count)

country_list = sorted(
    [(COUNTRY_DISPLAY.get(c.alpha_3, c.name), c.alpha_3, c.alpha_2) for c in pycountry.countries],
    key=lambda row: row[0].casefold(),
)
country_by_name = {name: (iso3, iso2) for name, iso3, iso2 in country_list}

industry_names = betas["data"]["Industry"].tolist()
industry_default = "Software (System & Application)"
if industry_default not in industry_names:
    for candidate in industry_names:
        if "software" in candidate.casefold() and "system" in candidate.casefold():
            industry_default = candidate
            break
if not industry_names:
    industry_names = [industry_default]

with st.sidebar:
    industry = st.selectbox("Industry", industry_names, index=industry_names.index(industry_default) if industry_default in industry_names else 0)
    country_name = st.selectbox("Target country", list(country_by_name), index=list(country_by_name).index("United States"))
    country_iso3, country_iso2 = country_by_name[country_name]
    erp_pct = st.number_input("Equity risk premium (%)", min_value=0.0, max_value=30.0, value=6.5, step=0.25, format="%.2f")
    debt_pct = st.slider("Debt financing (% of capital)", min_value=0, max_value=95, value=30, step=1)
    tax_choice = st.segmented_control(
        "Corporate tax applied to BOTH calculations",
        ["U.S. tax rate", "Selected-country tax rate"],
        default="U.S. tax rate",
    )
    st.caption("The selected rate, ERP and capital structure are identical for USD and local currency.")

with st.spinner("Retrieving 10-year yield quotations …"):
    us_bond = load_te_yield("USA", "United States", st.session_state.refresh_count)
    foreign_bond = us_bond if country_iso3 == "USA" else load_te_yield(country_iso3, country_name, st.session_state.refresh_count)

currency_codes = get_territory_currencies(country_iso2, tender=True)
local_ccy = currency_codes[0] if currency_codes else "Local currency"
if country_iso3 == "USA":
    local_ccy = "USD"

beta_rows = betas["data"].loc[betas["data"]["Industry"] == industry, "Unlevered beta"]
beta_auto = float(beta_rows.iloc[0]) if not beta_rows.empty else None
us_tax_auto, us_tax_year = tax_for_iso(oecd["data"], "USA")
foreign_tax_auto, foreign_tax_year = tax_for_iso(oecd["data"], country_iso3)

with st.sidebar.expander("Manual data overrides", expanded=False):
    st.caption("Each switch replaces only its own automatic input. Blank fields must be supplied; no substitute values are inserted.")
    beta_u, beta_origin = override("Unlevered beta", beta_auto, "beta", min_value=0, max_value=10, suffix="")
    us_tax, us_tax_origin = override("U.S. corporate tax rate", us_tax_auto, "us_tax")
    if country_iso3 == "USA":
        foreign_tax, foreign_tax_origin = us_tax, us_tax_origin
        st.caption("Target country is United States; its corporate tax equals the U.S. rate above.")
    else:
        foreign_tax, foreign_tax_origin = override("Target corporate tax rate", foreign_tax_auto, "foreign_tax")
    bbb_us, bbb_origin = override("USD BBB spread", bbb["value"], "bbb_spread")
    rf_us_pct, rf_us_origin = override("U.S. 10-year bond yield", us_bond["value"], "us_yield", min_value=-5, max_value=100)
    if country_iso3 == "USA":
        rf_foreign_pct, rf_foreign_origin = rf_us_pct, rf_us_origin
        st.caption("Target country is United States; local yield equals U.S. yield.")
    else:
        rf_foreign_pct, rf_foreign_origin = override("Target 10-year bond yield", foreign_bond["value"], "foreign_yield", min_value=-5, max_value=100)
    # Parity-based adjustment gives a MODELLED local-currency spread, not a live local BBB quote.
    bbb_local_auto = (
        bbb_us * (1 + rf_foreign_pct / 100) / (1 + rf_us_pct / 100)
        if None not in (bbb_us, rf_us_pct, rf_foreign_pct) and rf_us_pct > -100 else None
    )
    if country_iso3 == "USA":
        bbb_local, bbb_local_origin = bbb_us, bbb_origin
        st.caption(f"Auto local BBB spread: {bbb_us:.4f}% (same currency)" if bbb_us is not None else "Auto local BBB spread unavailable")
    else:
        st.caption("Auto local BBB spread = USD spread × (1 + local 10Y yield)/(1 + U.S. 10Y yield), with yields expressed as decimals.")
        bbb_local, bbb_local_origin = override("Target-currency BBB spread", bbb_local_auto, "foreign_spread")

if country_iso3 == "USA":
    # All U.S. overrides must carry across to the comparison column.
    foreign_tax = us_tax
    rf_foreign_pct = rf_us_pct
    bbb_local = bbb_us

tax_pct = us_tax if tax_choice == "U.S. tax rate" else foreign_tax

st.caption(
    f"**Selected:** {industry}  ·  **Country:** {country_name} ({local_ccy})  ·  "
    f"**Tax convention:** {tax_choice}  ·  **Debt:** {debt_pct}%  ·  **ERP:** {erp_pct:.2f}%"
)

status_rows = [
    ("Industry unlevered beta", beta_u, beta_origin, betas["vintage"], betas["url"]),
    ("U.S. corporate tax", us_tax, us_tax_origin, us_tax_year, oecd["url"]),
    (f"{country_name} corporate tax", foreign_tax, foreign_tax_origin, foreign_tax_year, oecd["url"]),
    ("USD BBB borrowing spread", bbb_us, bbb_origin, bbb["vintage"], bbb["url"]),
    ("U.S. government 10Y yield", rf_us_pct, rf_us_origin, us_bond["vintage"], us_bond["url"]),
    (f"{country_name} government 10Y yield", rf_foreign_pct, rf_foreign_origin, foreign_bond["vintage"], foreign_bond["url"]),
    (f"{local_ccy} BBB spread", bbb_local, bbb_local_origin if country_iso3 != "USA" else bbb_origin, "Parity-derived unless manually overwritten", TE_BONDS),
]

missing = []
for label, value, _, _, _ in status_rows:
    # If user chooses US tax, a foreign tax rate is optional (and vice versa).
    if label == "U.S. corporate tax" and tax_choice != "U.S. tax rate":
        continue
    if label == f"{country_name} corporate tax" and tax_choice != "Selected-country tax rate":
        continue
    if value is None:
        missing.append(label)

with st.expander("Live data status, vintages, and source links", expanded=bool(missing)):
    for label, value, origin, vintage, url in status_rows:
        display_value = f"{value:.4f}" if value is not None else "UNAVAILABLE"
        st.markdown(f"**{label}**: {display_value} · {origin} · {vintage} · [source]({url})")
    st.caption(f"Quote/database retrieval timestamps: NYU beta {betas['retrieved']} · BBB {bbb['retrieved']} · OECD {oecd['retrieved']} · U.S. 10Y {us_bond['retrieved']} · local 10Y {foreign_bond['retrieved']}. These are fetch times, not necessarily observation dates.")
    for name, result in [("Damodaran beta", betas), ("Damodaran BBB", bbb), ("OECD", oecd), ("Trading Economics U.S.", us_bond), (f"Trading Economics {country_name}", foreign_bond)]:
        if result["error"]:
            st.warning(f"{name}: {result['error']}")

if missing:
    st.warning("Calculations paused: " + ", ".join(dict.fromkeys(missing)) + ". Open **Manual data overrides** in the sidebar and enter the missing values, or try **Refresh source data**.")
    st.info("Automated sources can block scraping. The app deliberately does **not** assume a 4% yield, 1.5% BBB spread, or any other unverified fallback.")
    st.stop()

assert all(x is not None for x in (beta_u, rf_us_pct, rf_foreign_pct, tax_pct, bbb_us, bbb_local))
result = compute(beta_u, rf_us_pct / 100, rf_foreign_pct / 100, erp_pct, debt_pct, tax_pct, bbb_us, bbb_local)

st.subheader("Cost of capital — side-by-side")
left, right = st.columns(2, border=True)
with left:
    st.markdown("#### 🇺🇸 USD")
    st.metric("WACC", f"{result['USD WACC (%)']:.2f}%")
    st.metric("Cost of equity", f"{result['USD cost of equity (%)']:.2f}%")
    st.metric("Pre-tax cost of debt", f"{result['USD pretax debt cost (%)']:.2f}%")
    st.caption(f"10Y yield {rf_us_pct:.3f}% · BBB spread {bbb_us:.3f}%")
with right:
    st.markdown(f"#### 🌍 {local_ccy} — {country_name}")
    st.metric("WACC", f"{result['Local WACC (%)']:.2f}%", delta=f"{result['Local WACC (%)'] - result['USD WACC (%)']:+.2f} percentage points", delta_color="off")
    st.metric("Cost of equity", f"{result['Local cost of equity (%)']:.2f}%")
    st.metric("Pre-tax cost of debt", f"{result['Local pretax debt cost (%)']:.2f}%")
    st.caption(f"10Y yield {rf_foreign_pct:.3f}% · Modelled / manual BBB spread {bbb_local:.3f}%")

chart = pd.DataFrame(
    {"USD": [result["USD cost of equity (%)"], result["USD pretax debt cost (%)"], result["USD WACC (%)"]],
     local_ccy: [result["Local cost of equity (%)"], result["Local pretax debt cost (%)"], result["Local WACC (%)"]]},
    index=["Cost of equity", "Pretax cost of debt", "WACC"],
)
if country_iso3 != "USA" and local_ccy != "USD":
    st.bar_chart(chart, horizontal=True, x_label="Annual nominal cost (%)", stack=False)
else:
    st.info("With the United States selected, the two columns describe the same currency and must match.")

st.subheader("Model assumptions")
st.markdown(
    "- **Relevered beta:** βL = βU × [1 + (1 − T) × D/E], with D/E = debt share / equity share.\n"
    "- **Cost of equity:** Ke = government 10Y yield + βL × *the same* equity risk premium.\n"
    "- **Cost of debt:** Kd = government 10Y yield + BBB credit spread.\n"
    "- **WACC:** E/V × Ke + D/V × Kd × (1 − T); the selected corporate tax rate is used in **both** columns.\n"
    "- **Automatic local BBB spread:** USD BBB spread × (1 + local 10Y yield) / (1 + U.S. 10Y yield), using decimal yields. "
    "This is a **currency-parity assumption**, not an observed foreign BBB market quote."
)
st.caption(
    f"Model inputs: βU {beta_u:.3f} · βL {result['Relevered beta']:.3f} · "
    f"selected statutory tax {tax_pct:.2f}% · equity {100-debt_pct}% · debt {debt_pct}% · ERP {erp_pct:.2f}%. "
    "Government yields are nominal proxies for risk-free rates; emerging-market sovereign risk and credit risk may make them imperfect proxies."
)

st.subheader("All-industry WACC comparison")
if betas["data"].empty:
    st.info("The NYU sector dataset is unavailable, so a verified all-industry comparison cannot be generated.")
    industry_comparison = pd.DataFrame()
else:
    records = []
    for row in betas["data"].itertuples(index=False):
        sec_beta = float(beta_u) if row[0] == industry else float(row[1])
        calc = compute(sec_beta, rf_us_pct / 100, rf_foreign_pct / 100, erp_pct, debt_pct, tax_pct, bbb_us, bbb_local)
        records.append({"Industry": row[0], **calc})
    industry_comparison = pd.DataFrame(records).sort_values("USD WACC (%)").reset_index(drop=True)
    st.dataframe(industry_comparison, use_container_width=True, hide_index=True)
    st.caption("All sectors use the same debt share, tax rate, ERP, yields and BBB borrowing spread. A manual beta override affects only the selected industry's row.")
    st.download_button("⬇️ Download all-industry comparison (CSV)", industry_comparison.to_csv(index=False).encode("utf-8-sig"), "all_industries_wacc.csv", "text/csv", use_container_width=True)

summary = pd.DataFrame([
    {"Industry": industry, "Country": country_name, "Currency": local_ccy,
     "Applied tax source": tax_choice, "Applied tax (%)": tax_pct,
     "U.S. tax source year": us_tax_year, "Local tax source year": foreign_tax_year,
     "ERP (%)": erp_pct, "Debt weight (%)": debt_pct,
     "U.S. government 10Y (%)": rf_us_pct, "Local government 10Y (%)": rf_foreign_pct,
     "USD BBB spread (%)": bbb_us, "Local BBB spread (%)": bbb_local,
     "NYU beta vintage": betas["vintage"], "NYU BBB vintage": bbb["vintage"],
     "U.S. yield quote date": us_bond["vintage"], "Local yield quote date": foreign_bond["vintage"],
     "Retrieved UTC": now_utc(), **result}
])
st.download_button("⬇️ Download selected-project results (CSV)", summary.to_csv(index=False).encode("utf-8-sig"), "project_wacc_comparison.csv", "text/csv", use_container_width=True)
st.divider()
st.caption("Sources: [Damodaran U.S. industry betas](" + BETAS_XLS + ") · [Damodaran Baa2/BBB spread](" + RATINGS_URL + ") · [OECD corporate tax dataset](" + OECD_DATASET + ") · [Trading Economics bond yields](" + TE_BONDS + "). Data access depends on source availability and website terms. Model is illustrative, not a substitute for deal-specific credit, sovereign-risk, or tax analysis.")
