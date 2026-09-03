import streamlit as st
import yfinance as yf
import pandas as pd
import datetime
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="Kantitatif Bilanço Analizörü", layout="wide")
st.title("📊 Kurumsal Bilanço & Olay Analizi (Event Study)")

# --- TRADINGVIEW API FONKSİYONU ---
@st.cache_data(ttl=86400) # Listeyi günde 1 kez çekmek yeterli
def fetch_tradingview_tickers(market_choice):
    tickers = []
    try:
        if market_choice == "BIST (Türkiye)":
            url = "https://scanner.tradingview.com/turkey/scan"
            # Sadece hisse senetlerini filtrele
            payload = {
                "columns": ["name"],
                "range": [0, 1000] # BIST'te ~500 hisse var, 1000 yeterli
            }
            response = requests.post(url, json=payload)
            response.raise_for_status()
            data = response.json().get("data", [])
            
            # Yahoo Finance uyumluluğu için sonuna .IS ekle
            for item in data:
                ticker = item["d"][0]
                tickers.append(f"{ticker}.IS")
                
        elif market_choice == "NASDAQ & NYSE (ABD)":
            url = "https://scanner.tradingview.com/america/scan"
            # Sadece NASDAQ ve NYSE borsalarındaki hisseleri filtrele
            payload = {
                "filter": [{"left": "exchange", "operation": "in_range", "right": ["NASDAQ", "NYSE"]}],
                "columns": ["name"],
                "range": [0, 8000] # Tüm ABD majör piyasası
            }
            response = requests.post(url, json=payload)
            response.raise_for_status()
            data = response.json().get("data", [])
            
            for item in data:
                tickers.append(item["d"][0])
                
    except Exception as e:
        st.error(f"TradingView verisi çekilirken hata oluştu: {e}")
        
    return tickers

# --- ANALİZ FONKSİYONLARI ---
@st.cache_data(ttl=3600)
def scan_earnings(tickers, target_month, target_year, max_scan=500):
    upcoming = []
    # Streamlit Cloud'da binlerce hisseyi taramak timeout veya IP ban'a sebep olabilir.
    # Bu yüzden taramayı güvenli bir limitle sınırlandırıyoruz (Kullanıcı arayüzünden ayarlanabilir)
    scan_list = tickers[:max_scan]
    
    progress_text = "Bilanço takvimi taranıyor..."
    progress_bar = st.progress(0, text=progress_text)
    
    for i, ticker in enumerate(scan_list):
        try:
            stock = yf.Ticker(ticker)
            ed = stock.earnings_dates
            if ed is not None and not ed.empty:
                future_dates = ed[ed.index >= pd.Timestamp.now(tz='UTC')]
                if not future_dates.empty:
                    next_date = future_dates.index.min()
                    if next_date.month == target_month and next_date.year == target_year:
                        upcoming.append((ticker, next_date.strftime('%Y-%m-%d')))
        except Exception:
            pass
        
        # Her 10 hissede bir arayüzü güncelle (performans için)
        if i % 10 == 0 or i == len(scan_list) - 1:
            progress_bar.progress((i + 1) / len(scan_list), text=f"Taranıyor: {ticker} ({i+1}/{len(scan_list)})")
        
    progress_bar.empty()
    return upcoming

@st.cache_data(ttl=86400)
def fetch_financial_tables(ticker):
    stock = yf.Ticker(ticker)
    return {
        "income": stock.financials.dropna(how='all') if stock.financials is not None else pd.DataFrame(),
        "balance": stock.balance_sheet.dropna(how='all') if stock.balance_sheet is not None else pd.DataFrame(),
        "cashflow": stock.cashflow.dropna(how='all') if stock.cashflow is not None else pd.DataFrame()
    }

@st.cache_data(ttl=86400)
def analyze_event_study(ticker, days_window=10):
    stock = yf.Ticker(ticker)
    hist = stock.history(period="5y")
    ed = stock.earnings_dates
    
    if ed is None or ed.empty or hist.empty:
        return None
        
    past_dates = ed[ed.index < pd.Timestamp.now(tz='UTC')].index
    event_data = {}
    
    for date in past_dates:
        date_tz = date.tz_convert(hist.index.tz) if date.tzinfo else date.tz_localize(hist.index.tz)
        if date_tz not in hist.index:
            closest_days = hist.index[hist.index <= date_tz]
            if closest_days.empty: continue
            actual_date = closest_days[-1]
        else:
            actual_date = date_tz

        loc = hist.index.get_loc(actual_date)
        
        if loc >= days_window and loc < len(hist) - days_window:
            window_prices = hist.iloc[loc - days_window : loc + days_window + 1]['Close']
            base_price = window_prices.iloc[0]
            normalized = ((window_prices / base_price) - 1) * 100
            relative_days = list(range(-days_window, days_window + 1))
            event_data[actual_date.strftime('%Y-%m-%d')] = pd.Series(data=normalized.values, index=relative_days)

    if not event_data:
        return None
        
    df_events = pd.DataFrame(event_data)
    df_events['Average_Effect'] = df_events.mean(axis=1)
    return df_events

# --- KULLANICI ARAYÜZÜ (SIDEBAR) ---
st.sidebar.header("Taramayı Başlat")
market_choice = st.sidebar.selectbox("Piyasa Seçimi", ["BIST (Türkiye)", "NASDAQ & NYSE (ABD)"])

# Seçilen piyasaya göre TV'den hisseleri çek
with st.sidebar:
    with st.spinner("TradingView'dan hisse listesi çekiliyor..."):
        all_tickers = fetch_tradingview_tickers(market_choice)
        st.success(f"TV Scanner: Toplam {len(all_tickers)} hisse bulundu.")

now = datetime.datetime.now()
target_month = st.sidebar.selectbox("Bilanço Ayı", [now.month, (now.month % 12) + 1], format_func=lambda x: f"{x}. Ay")

# IP Ban / Timeout koruması için tarama limiti
scan_limit = st.sidebar.slider("Taranacak Maksimum Hisse Sayısı", min_value=50, max_value=len(all_tickers), value=500, step=50, 
                               help="Yahoo Finance API limitlerine takılmamak için taramayı sınırlandırın.")

if st.sidebar.button("Bilanço Takvimini Tara"):
    results = scan_earnings(all_tickers, target_month, now.year, max_scan=scan_limit)
    st.session_state['scan_results'] = results

# --- ANA EKRAN (GÖRSELLEŞTİRME) ---
if 'scan_results' in st.session_state:
    results = st.session_state['scan_results']
    
    if not results:
        st.warning(f"Seçilen havuzda {target_month}. ayda bilanço açıklayacak hisse bulunamadı.")
    else:
        st.success(f"{target_month}. ayda bilanço açıklayacak {len(results)} adet hisse bulundu!")
        ticker_options = {f"{t} (Tarih: {d})": t for t, d in results}
        selected_option = st.selectbox("Detaylı Analiz İçin Hisse Seçin", list(ticker_options.keys()))
        selected_ticker = ticker_options[selected_option]
        
        with st.spinner(f"{selected_ticker} için finansallar ve tarihsel veriler çekiliyor..."):
            fin_data = fetch_financial_tables(selected_ticker)
            event_data = analyze_event_study(selected_ticker)
            
        tab1, tab2, tab3, tab4 = st.tabs(["📉 Bilanço Olay Analizi (T±10)", "💰 Gelir Tablosu", "⚖️ Bilanço", "💵 Nakit Akışı"])
        
        with tab1:
            st.markdown(f"### {selected_ticker} Geçmiş Bilanço Etkisi (Son 5 Yıl)")
            if event_data is not None:
                fig = go.Figure()
                for col in event_data.columns:
                    if col != 'Average_Effect':
                        fig.add_trace(go.Scatter(x=event_data.index, y=event_data[col], mode='lines', 
                                                 line=dict(color='rgba(150, 150, 150, 0.3)', width=1), showlegend=False))
                fig.add_trace(go.Scatter(x=event_data.index, y=event_data['Average_Effect'], mode='lines+markers', 
                                         line=dict(color='royalblue', width=4), name='Ortalama Etki'))
                
                fig.add_vline(x=0, line_width=2, line_dash="dash", line_color="red", annotation_text="T=0 (Bilanço Günü)")
                fig.update_layout(height=500, title="Bilanço Açıklanmadan Önceki ve Sonraki Kümülatif Getiri (%)",
                                  xaxis_title="Günler (T-10 / T+10)", yaxis_title="Getiri (%)", template="plotly_white")
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Yeterli geçmiş fiyat/bilanço verisi bulunamadı.")

        with tab2:
            st.markdown(f"### {selected_ticker} Gelir Tablosu (Yıllık)")
            if not fin_data['income'].empty:
                st.dataframe(fin_data['income'].style.format("{:,.0f}"), use_container_width=True)
            else:
                st.warning("Veri bulunamadı.")

        with tab3:
            st.markdown(f"### {selected_ticker} Bilanço (Yıllık)")
            if not fin_data['balance'].empty:
                st.dataframe(fin_data['balance'].style.format("{:,.0f}"), use_container_width=True)
            else:
                st.warning("Veri bulunamadı.")

        with tab4:
            st.markdown(f"### {selected_ticker} Nakit Akış Tablosu (Yıllık)")
            if not fin_data['cashflow'].empty:
                st.dataframe(fin_data['cashflow'].style.format("{:,.0f}"), use_container_width=True)
            else:
                st.warning("Veri bulunamadı.")
