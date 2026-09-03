import streamlit as st
import yfinance as yf
import pandas as pd
import datetime
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="Kantitatif Bilanço Analizörü", layout="wide")
st.title("📊 Kurumsal Bilanço & Olay Analizi (Event Study)")

# --- YARDIMCI VERİ SETLERİ ---
# Tam piyasa taraması Streamlit'i çökerteceği için örnek havuzlar tanımlanır.
MARKET_TICKERS = {
    "BIST 30 (Örnek)": ["AKBNK.IS", "BIMAS.IS", "EREGL.IS", "FROTO.IS", "GARAN.IS", "KCHOL.IS", "SAHOL.IS", "SASA.IS", "SISE.IS", "THYAO.IS", "TUPRS.IS", "YKBNK.IS"],
    "US Büyük Teknoloji": ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "NFLX", "AMD", "INTC"],
    "Özel Havuz (Manuel)": []
}

# --- FONKSİYONLAR ---
@st.cache_data(ttl=3600) # Yahoo IP ban yememek için veriyi 1 saat önbellekte tut
def scan_earnings(tickers, target_month, target_year):
    upcoming = []
    progress_bar = st.progress(0)
    
    for i, ticker in enumerate(tickers):
        try:
            stock = yf.Ticker(ticker)
            ed = stock.earnings_dates
            if ed is not None and not ed.empty:
                # Sadece gelecekteki en yakın tarihi bul
                future_dates = ed[ed.index >= pd.Timestamp.now(tz='UTC')]
                if not future_dates.empty:
                    next_date = future_dates.index.min()
                    if next_date.month == target_month and next_date.year == target_year:
                        upcoming.append((ticker, next_date.strftime('%Y-%m-%d')))
        except Exception:
            pass
        
        progress_bar.progress((i + 1) / len(tickers))
        
    progress_bar.empty()
    return upcoming

@st.cache_data(ttl=86400)
def fetch_financial_tables(ticker):
    stock = yf.Ticker(ticker)
    return {
        "income": stock.financials.dropna(how='all'),
        "balance": stock.balance_sheet.dropna(how='all'),
        "cashflow": stock.cashflow.dropna(how='all')
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
            # Formül: R_t = ((P_t / P_0) - 1) * 100
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
market_choice = st.sidebar.selectbox("Piyasa / Havuz Seçimi", list(MARKET_TICKERS.keys()))

if market_choice == "Özel Havuz (Manuel)":
    custom_tickers = st.sidebar.text_input("Hisse Sembolleri (Virgülle ayırın, Örn: ASML, BABA, FROTO.IS)")
    tickers_to_scan = [x.strip().upper() for x in custom_tickers.split(",") if x.strip()]
else:
    tickers_to_scan = MARKET_TICKERS[market_choice]

now = datetime.datetime.now()
target_month = st.sidebar.selectbox("Bilanço Ayı", [now.month, (now.month % 12) + 1], format_func=lambda x: f"{x}. Ay")

if st.sidebar.button("Takvimi Tara"):
    if not tickers_to_scan:
        st.sidebar.error("Lütfen taranacak hisseleri belirtin.")
    else:
        with st.spinner("Yahoo Finance taranıyor, lütfen bekleyin..."):
            results = scan_earnings(tickers_to_scan, target_month, now.year)
            st.session_state['scan_results'] = results

# --- ANA EKRAN (GÖRSELLEŞTİRME) ---
if 'scan_results' in st.session_state:
    results = st.session_state['scan_results']
    
    if not results:
        st.warning(f"Seçilen havuzda {target_month}. ayda bilanço açıklayacak hisse bulunamadı.")
    else:
        st.success(f"{len(results)} adet hisse bulundu!")
        ticker_options = {f"{t} (Tarih: {d})": t for t, d in results}
        selected_option = st.selectbox("Detaylı Analiz İçin Hisse Seçin", list(ticker_options.keys()))
        selected_ticker = ticker_options[selected_option]
        
        # Verileri Çek
        with st.spinner(f"{selected_ticker} için finansallar ve tarihsel veriler çekiliyor..."):
            fin_data = fetch_financial_tables(selected_ticker)
            event_data = analyze_event_study(selected_ticker)
            
        # TAB'LAR (Sekmeler)
        tab1, tab2, tab3, tab4 = st.tabs(["📉 Bilanço Olay Analizi (T±10)", "💰 Gelir Tablosu (Kâr/Zarar)", "⚖️ Bilanço (Balance Sheet)", "💵 Nakit Akışı"])
        
        # TAB 1: EVENT STUDY
        with tab1:
            st.markdown(f"### {selected_ticker} Geçmiş Bilanço Etkisi (Son 5 Yıl)")
            if event_data is not None:
                fig = go.Figure()
                # Geçmiş tüm bilançolar (Gri)
                for col in event_data.columns:
                    if col != 'Average_Effect':
                        fig.add_trace(go.Scatter(x=event_data.index, y=event_data[col], mode='lines', 
                                                 line=dict(color='rgba(150, 150, 150, 0.3)', width=1), showlegend=False))
                # Ortalama Etki (Mavi, Kalın)
                fig.add_trace(go.Scatter(x=event_data.index, y=event_data['Average_Effect'], mode='lines+markers', 
                                         line=dict(color='royalblue', width=4), name='Ortalama Etki'))
                
                fig.add_vline(x=0, line_width=2, line_dash="dash", line_color="red", annotation_text="T=0 (Bilanço)")
                fig.update_layout(height=500, title="Bilanço Açıklanmadan Önceki ve Sonraki Kümülatif Getiri (%)",
                                  xaxis_title="Günler (T-10 / T+10)", yaxis_title="Getiri (%)", template="plotly_white")
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("Yeterli geçmiş bilanço verisi bulunamadı.")

        # TAB 2: GELİR TABLOSU
        with tab2:
            st.markdown(f"### {selected_ticker} Gelir Tablosu (Yıllık)")
            if not fin_data['income'].empty:
                st.dataframe(fin_data['income'].style.format("{:,.0f}"), use_container_width=True)
            else:
                st.warning("Veri bulunamadı.")

        # TAB 3: BİLANÇO
        with tab3:
            st.markdown(f"### {selected_ticker} Bilanço (Yıllık)")
            if not fin_data['balance'].empty:
                st.dataframe(fin_data['balance'].style.format("{:,.0f}"), use_container_width=True)
            else:
                st.warning("Veri bulunamadı.")

        # TAB 4: NAKİT AKIŞI
        with tab4:
            st.markdown(f"### {selected_ticker} Nakit Akış Tablosu (Yıllık)")
            if not fin_data['cashflow'].empty:
                st.dataframe(fin_data['cashflow'].style.format("{:,.0f}"), use_container_width=True)
            else:
                st.warning("Veri bulunamadı.")
