import streamlit as st
import yfinance as yf
import pandas as pd
import datetime
import calendar
import plotly.graph_objects as go
import requests
import warnings
warnings.filterwarnings('ignore')

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="Kantitatif Piyasalar Analizörü", layout="wide", page_icon="📈")
st.title("📈 Kurumsal Bilanço & Halka Arz Analizörü")

# --- TRADINGVIEW API FONKSİYONLARI ---
@st.cache_data(ttl=3600)
def get_fast_earnings_calendar(target_month, target_year, market="america"):
    """Seçili ay için bilanço açıklayacak hisseleri TV API üzerinden saniyeler içinde çeker."""
    first_day = datetime.datetime(target_year, target_month, 1)
    last_day_num = calendar.monthrange(target_year, target_month)[1]
    last_day = datetime.datetime(target_year, target_month, last_day_num, 23, 59, 59)
    
    start_unix = int(first_day.timestamp())
    end_unix = int(last_day.timestamp())
    
    url = f"https://scanner.tradingview.com/{market}/scan?label-product=calendar-earnings"
    
    payload = {
        "filter": [
            {
                "left": "earnings_release_date,earnings_release_next_date",
                "operation": "in_range",
                "right": [start_unix, end_unix]
            }
        ],
        "columns": ["name", "earnings_release_next_date", "exchange"],
        "options": {"lang": "en"}
    }
    
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "user-agent": "Mozilla/5.0"
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json().get("data", [])
        
        upcoming = []
        for item in data:
            ticker_name = item["d"][0]
            timestamp = item["d"][1]
            
            if market == "turkey":
                ticker_name = f"{ticker_name}.IS"
                
            if pd.notna(timestamp):
                date_str = datetime.datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d')
                upcoming.append((ticker_name, date_str))
                
        return upcoming
    except Exception as e:
        st.error(f"TradingView Bilanço API hatası: {e}")
        return []

@st.cache_data(ttl=3600)
def get_fast_ipo_calendar(target_month, target_year, market="america"):
    """Seçili ay için planlanan Halka Arz (IPO) verilerini çeker."""
    first_day = datetime.datetime(target_year, target_month, 1)
    last_day_num = calendar.monthrange(target_year, target_month)[1]
    last_day = datetime.datetime(target_year, target_month, last_day_num, 23, 59, 59)
    
    start_unix = int(first_day.timestamp())
    end_unix = int(last_day.timestamp())
    
    url = "https://scanner.tradingview.com/global/scan?label-product=calendar-ipo"
    
    payload = {
        "columns": [
            "name", "description", "exchange", "ipo_offer_time", 
            "ipo_offer_price_usd", "ipo_offer_status"
        ],
        "filter": [
            {
                "left": "ipo_offer_time",
                "operation": "in_range",
                "right": [start_unix, end_unix]
            }
        ],
        "ignore_unknown_fields": False,
        "options": {"lang": "en"},
        "sort": {"sortBy": "ipo_offer_time", "sortOrder": "asc"},
        "markets": [market],
        "preset": "ipo_calendar"
    }
    
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "user-agent": "Mozilla/5.0"
    }
    
    try:
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
        data = response.json().get("data", [])
        
        ipos = []
        for item in data:
            d = item["d"]
            timestamp = d[3]
            date_str = datetime.datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d') if pd.notna(timestamp) else "Belirsiz"
            
            ipos.append({
                "Hisse / Sembol": d[0],
                "Şirket Adı": d[1],
                "Borsa": d[2],
                "Halka Arz Tarihi": date_str,
                "Tahmini Fiyat": d[4] if pd.notna(d[4]) else "Açıklanmadı",
                "Durum": d[5]
            })
            
        return pd.DataFrame(ipos)
    except Exception as e:
        st.error(f"TradingView IPO API hatası: {e}")
        return pd.DataFrame()

# --- YFINANCE ANALİZ FONKSİYONLARI ---
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
        
    def plot_fundamental_trends(fin_data, ticker):
        """Fiyat hareketinin nedenini açıklamak için temel finansal trendleri çizer."""
        try:
            # Verileri tarihe göre eskiden yeniye sırala
            inc = fin_data['income'].T.sort_index() if not fin_data['income'].empty else pd.DataFrame()
            cf = fin_data['cashflow'].T.sort_index() if not fin_data['cashflow'].empty else pd.DataFrame()
            
            fig = make_subplots(
                rows=3, cols=1, 
                shared_xaxes=True,
                vertical_spacing=0.1,
                subplot_titles=("1. Büyüme: Toplam Gelir vs Net Kâr", "2. Verimlilik: Net Kâr Marjı (%)", "3. Gerçeklik: Faaliyet Nakit Akışı")
            )
            
            # 1 & 2: Gelir, Net Kar ve Marj
            if 'Total Revenue' in inc.columns and 'Net Income' in inc.columns:
                years = inc.index.strftime('%Y')
                
                fig.add_trace(go.Bar(x=years, y=inc['Total Revenue'], name="Toplam Gelir", marker_color='lightblue'), row=1, col=1)
                fig.add_trace(go.Scatter(x=years, y=inc['Net Income'], name="Net Kâr", mode='lines+markers', line=dict(color='green', width=3)), row=1, col=1)
                
                margin = (inc['Net Income'] / inc['Total Revenue']) * 100
                fig.add_trace(go.Scatter(x=years, y=margin, name="Kâr Marjı (%)", mode='lines+markers', line=dict(color='purple', width=3)), row=2, col=1)
                
            # 3: Nakit Akışı (Kârın kalitesini ölçer)
            if not cf.empty and 'Operating Cash Flow' in cf.columns:
                cf_years = cf.index.strftime('%Y')
                fig.add_trace(go.Bar(x=cf_years, y=cf['Operating Cash Flow'], name="Faaliyet Nakit Akışı", marker_color='orange'), row=3, col=1)
                
            fig.update_layout(height=700, template='plotly_white', showlegend=True, hovermode="x unified")
            fig.update_yaxes(title_text="Para Birimi", row=1, col=1)
            fig.update_yaxes(title_text="Yüzde (%)", row=2, col=1)
            fig.update_yaxes(title_text="Para Birimi", row=3, col=1)
            
            return fig
        except Exception as e:
            return None
          
    df_events = pd.DataFrame(event_data)
    df_events['Average_Effect'] = df_events.mean(axis=1)
    return df_events

# --- KULLANICI ARAYÜZÜ (SIDEBAR) ---
st.sidebar.header("⚙️ Tarama Ayarları")
market_choice = st.sidebar.selectbox("Piyasa Seçimi", ["BIST (Türkiye)", "NASDAQ & NYSE (ABD)"])

now = datetime.datetime.now()
target_month = st.sidebar.selectbox("Takvim Ayı", [now.month, (now.month % 12) + 1], format_func=lambda x: f"{x}. Ay")

if st.sidebar.button("Piyasayı Tara"):
    tv_market = "turkey" if market_choice == "BIST (Türkiye)" else "america"
    
    with st.spinner("TradingView Sunucularından Veriler Çekiliyor..."):
        # Bilanço ve IPO takvimlerini eşzamanlı çek
        earnings_results = get_fast_earnings_calendar(target_month, now.year, market=tv_market)
        ipo_results = get_fast_ipo_calendar(target_month, now.year, market=tv_market)
        
        st.session_state['earnings_results'] = earnings_results
        st.session_state['ipo_results'] = ipo_results
        st.session_state['scanned_month'] = target_month

# --- ANA EKRAN YERLEŞİMİ (ANA SEKMELER) ---
if 'earnings_results' in st.session_state:
    main_tab1, main_tab2 = st.tabs(["📊 Bilanço Takvimi & Olay Analizi", "🚀 Yaklaşan Halka Arzlar (IPO)"])
    
    # 1. ANA SEKME: BİLANÇOLAR
    with main_tab1:
        e_results = st.session_state['earnings_results']
        
        if not e_results:
            st.warning(f"Seçilen ayda bilanço açıklayacak hisse bulunamadı.")
        else:
            st.success(f"{st.session_state['scanned_month']}. Ay Bilanço Takvimi: {len(e_results)} Hisse Bulundu!")
            
            ticker_options = {f"{t} (Tarih: {d})": t for t, d in e_results}
            selected_option = st.selectbox("Detaylı Analiz İçin Hisse Seçin", list(ticker_options.keys()))
            selected_ticker = ticker_options[selected_option]
            
            with st.spinner(f"{selected_ticker} geçmiş verileri ve finansalları çekiliyor..."):
                fin_data = fetch_financial_tables(selected_ticker)
                event_data = analyze_event_study(selected_ticker)
                
            # Bilanço Alt Sekmeleri
            sub_tab1, sub_tab2, sub_tab3, sub_tab4 = st.tabs(["📉 Olay Analizi (T±10)", "💰 Gelir Tablosu", "⚖️ Bilanço", "💵 Nakit Akışı"])
            
           with sub_tab1:
                st.markdown(f"#### {selected_ticker} Geçmiş Bilanço Etkisi (Son 5 Yıl)")
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
                    st.info("Bu hisse için yeterli tarihsel bilanço etkisi verisi bulunamadı.")
                
                # --- YENİ EKLENEN KISIM: FİNANSAL ARKA PLAN ---
                st.divider()
                st.markdown(f"#### 🔍 Fiyatlamanın Arka Planı: {selected_ticker} Temel Trend Analizi")
                st.caption("Piyasanın bilançoya verdiği tepkinin rasyonel gerekçelerini bu finansal trendlerde arayabilirsiniz.")
                
                fund_fig = plot_fundamental_trends(fin_data, selected_ticker)
                if fund_fig is not None and len(fund_fig.data) > 0:
                    st.plotly_chart(fund_fig, use_container_width=True)
                else:
                    st.warning("Bu hisse için standart formatta yıllık gelir veya nakit akışı verisi bulunamadı (Özellikle BIST hisselerinde veri eksikliği olabilir).")


            with sub_tab3:
                st.markdown(f"#### {selected_ticker} Yıllık Bilanço")
                if not fin_data['balance'].empty:
                    st.dataframe(fin_data['balance'].style.format("{:,.0f}"), use_container_width=True)
                else:
                    st.warning("Veri bulunamadı.")

            with sub_tab4:
                st.markdown(f"#### {selected_ticker} Yıllık Nakit Akışı")
                if not fin_data['cashflow'].empty:
                    st.dataframe(fin_data['cashflow'].style.format("{:,.0f}"), use_container_width=True)
                else:
                    st.warning("Veri bulunamadı.")

    # 2. ANA SEKME: HALKA ARZLAR (IPO)
    with main_tab2:
        ipo_df = st.session_state['ipo_results']
        st.markdown(f"### {st.session_state['scanned_month']}. Ay Halka Arz (IPO) Takvimi")
        
        if ipo_df.empty:
            st.info("Bu ay için planlanan herhangi bir Halka Arz (IPO) bulunamadı veya veriler henüz netleşmedi.")
        else:
            st.success(f"Toplam {len(ipo_df)} adet yaklaşan Halka Arz bulundu.")
            st.dataframe(ipo_df, use_container_width=True, hide_index=True)
else:
    st.info("Lütfen sol menüden piyasa ve ay seçimi yaparak 'Piyasayı Tara' butonuna basınız.")
