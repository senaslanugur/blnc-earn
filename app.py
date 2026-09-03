import streamlit as st
import yfinance as yf
import pandas as pd
import datetime
import calendar
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
import warnings
warnings.filterwarnings('ignore')

# --- SAYFA AYARLARI ---
st.set_page_config(page_title="Kantitatif Piyasalar Analizörü", layout="wide", page_icon="📈")
st.title("📈 Kurumsal Bilanço & Halka Arz Analizörü")

# --- TRADINGVIEW API FONKSİYONLARI ---
@st.cache_data(ttl=3600)
def get_fast_earnings_calendar(target_month, target_year, market="america"):
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
    
    # yfinance'in hatalı veri kazımasından doğan KeyError çökmelerini engellemek için try-except kullanıyoruz
    try:
        hist = stock.history(period="5y")
        ed = stock.earnings_dates
    except Exception:
        # Hata fırlatan hisseyi atla
        return None
        
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


        
    df_events = pd.DataFrame(event_data)
    df_events['Average_Effect'] = df_events.mean(axis=1)
    return df_events

def plot_fundamental_trends(fin_data, ticker):
    try:
        inc = fin_data['income'].T.sort_index() if not fin_data['income'].empty else pd.DataFrame()
        cf = fin_data['cashflow'].T.sort_index() if not fin_data['cashflow'].empty else pd.DataFrame()
        
        fig = make_subplots(
            rows=3, cols=1, 
            shared_xaxes=True,
            vertical_spacing=0.1,
            subplot_titles=("1. Büyüme: Toplam Gelir vs Net Kâr", "2. Verimlilik: Net Kâr Marjı (%)", "3. Gerçeklik: Faaliyet Nakit Akışı")
        )
        
        if 'Total Revenue' in inc.columns and 'Net Income' in inc.columns:
            years = inc.index.strftime('%Y')
            
            fig.add_trace(go.Bar(x=years, y=inc['Total Revenue'], name="Toplam Gelir", marker_color='lightblue'), row=1, col=1)
            fig.add_trace(go.Scatter(x=years, y=inc['Net Income'], name="Net Kâr", mode='lines+markers', line=dict(color='green', width=3)), row=1, col=1)
            
            margin = (inc['Net Income'] / inc['Total Revenue']) * 100
            fig.add_trace(go.Scatter(x=years, y=margin, name="Kâr Marjı (%)", mode='lines+markers', line=dict(color='purple', width=3)), row=2, col=1)
            
        if not cf.empty and 'Operating Cash Flow' in cf.columns:
            cf_years = cf.index.strftime('%Y')
            fig.add_trace(go.Bar(x=cf_years, y=cf['Operating Cash Flow'], name="Faaliyet Nakit Akışı", marker_color='orange'), row=3, col=1)
            
        fig.update_layout(height=700, template='plotly_white', showlegend=True, hovermode="x unified")
        fig.update_yaxes(title_text="Miktar", row=1, col=1)
        fig.update_yaxes(title_text="Yüzde (%)", row=2, col=1)
        fig.update_yaxes(title_text="Miktar", row=3, col=1)
        
        return fig
    except Exception:
        return None

# --- YENİ EKLENEN: OTOMATİK TARAMA FONKSİYONU (SÜRPRİZ ORANI İLE GÜNCELLENDİ) ---
def auto_scan_opportunities(earnings_list):
    results = []
    progress_text = "Hisselerin tarihsel davranışları ve kazanç sürprizleri analiz ediliyor..."
    my_bar = st.progress(0, text=progress_text)
    total = len(earnings_list)
    
    for i, (ticker, date) in enumerate(earnings_list):
        my_bar.progress((i + 1) / total, text=f"Analiz ediliyor: {ticker} ({i+1}/{total})")
        
        df_events = analyze_event_study(ticker, days_window=10)
        
        if df_events is not None and not df_events.empty:
            past_cols = [c for c in df_events.columns if c != 'Average_Effect']
            if len(past_cols) < 3: # En az 3 geçmiş bilanço verisi şartı
                continue
                
            avg_effect = df_events['Average_Effect']
            avg_pre_ret = avg_effect.loc[0] - avg_effect.loc[-10]
            
            # 1. Kesinlik Oranını Hesapla
            wins = 0
            for col in past_cols:
                event_pre_ret = df_events.loc[0, col] - df_events.loc[-10, col]
                if avg_pre_ret > 0 and event_pre_ret > 0:
                    wins += 1
                elif avg_pre_ret < 0 and event_pre_ret < 0:
                    wins += 1
                    
            win_rate = (wins / len(past_cols)) * 100
            
            # 2. Sürpriz Oranını (Surprise %) Hesapla
            avg_surprise_str = "Veri Yok"
            try:
                stock = yf.Ticker(ticker)
                ed = stock.earnings_dates
                if ed is not None and not ed.empty and 'Surprise(%)' in ed.columns:
                    # Sadece geçmiş bilançoları al
                    past_ed = ed[ed.index < pd.Timestamp.now(tz='UTC')]
                    if not past_ed.empty:
                        avg_surp = past_ed['Surprise(%)'].mean()
                        if pd.notna(avg_surp):
                            # yfinance sürpriz oranını ondalık verir (Örn: 0.05 -> %5), 100 ile çarpıyoruz.
                            avg_surprise_str = f"%{avg_surp * 100:.1f}"
            except Exception:
                pass # Sürpriz verisi çekilemezse akışı bozma
            
            # 3. Bilanço Sonrası Zirve ve Düşüş Analizi
            post_segment = avg_effect.loc[0:10]
            peak_day = post_segment.idxmax()
            
            if avg_pre_ret > 0:
                pre_text = f"Bilanço açıklanmadan önceki 10 gün içinde ortalama %{avg_pre_ret:.1f} ARTAR."
            else:
                pre_text = f"Bilanço açıklanmadan önceki 10 gün içinde ortalama %{abs(avg_pre_ret):.1f} DÜŞER."
                
            if peak_day == 0:
                post_text = "Açıklandıktan hemen sonra (T+0) düşmeye başlar (Kâr realizasyonu)."
            else:
                post_text = f"Açıklandıktan sonra T+{peak_day}. güne kadar (ortalama %{(post_segment.loc[peak_day] - post_segment.loc[0]):.1f} daha) yükselir, sonrasında düşmeye başlar."
            
            results.append({
                "Hisse": ticker,
                "Bilanço Tarihi": date,
                "Kesinlik": f"%{win_rate:.0f}",
                "Ort. Sürpriz": avg_surprise_str, # Yeni Eklenen Metrik
                "Analiz Edilen Bilanço Sayısı": len(past_cols),
                "Yapay Zeka Analiz Özeti": f"{pre_text} {post_text}",
                "_win_rate_val": win_rate,
                "_avg_pre_val": abs(avg_pre_ret)
            })
            
    my_bar.empty()
    
    if results:
        df_res = pd.DataFrame(results)
        # Kesinlik oranına ve ardından getirinin büyüklüğüne göre yüksekten düşüğe sırala
        df_res = df_res.sort_values(by=['_win_rate_val', '_avg_pre_val'], ascending=[False, False])
        # Gizli sıralama sütunlarını kaldır
        df_res = df_res.drop(columns=['_win_rate_val', '_avg_pre_val'])
        return df_res
    return pd.DataFrame()


# --- KULLANICI ARAYÜZÜ (SIDEBAR) ---
st.sidebar.header("⚙️ Tarama Ayarları")
market_choice = st.sidebar.selectbox("Piyasa Seçimi", ["BIST (Türkiye)", "NASDAQ & NYSE (ABD)"])

now = datetime.datetime.now()
target_month = st.sidebar.selectbox("Takvim Ayı", [now.month, (now.month % 12) + 1], format_func=lambda x: f"{x}. Ay")

if st.sidebar.button("Piyasayı Tara"):
    tv_market = "turkey" if market_choice == "BIST (Türkiye)" else "america"
    
    with st.spinner("TradingView Sunucularından Veriler Çekiliyor..."):
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
            st.warning("Seçilen ayda bilanço açıklayacak hisse bulunamadı.")
        else:
            st.success(f"{st.session_state['scanned_month']}. Ay Bilanço Takvimi: {len(e_results)} Hisse Bulundu!")
            
            # YENİ MODÜL: OTOMATİK TARAYICI (Mevcut yapıyı bozmadan üst kısma eklendi)
            with st.expander("🤖 Tüm Hisseleri Otomatik Analiz Et (Fırsat Tarayıcı)", expanded=False):
                st.info("Bu özellik listedeki tüm hisselerin geçmiş bilançolarını tarayarak tutarlılığı en yüksek olan fırsatları sıralar. Hisse sayısına bağlı olarak tarama 1-2 dakika sürebilir.")
                if st.button("Taramayı Başlat"):
                    auto_df = auto_scan_opportunities(e_results)
                    if not auto_df.empty:
                        st.dataframe(auto_df, use_container_width=True, hide_index=True)
                    else:
                        st.warning("Yeterli veriye sahip model bulunamadı.")
            
            st.divider()
            
            # MEVCUT YAPI: MANUEL HİSSE SEÇİMİ VE DETAYLI ANALİZ
            ticker_options = {f"{t} (Tarih: {d})": t for t, d in e_results}
            selected_option = st.selectbox("Detaylı Grafik İncelemesi İçin Hisse Seçin", list(ticker_options.keys()))
            selected_ticker = ticker_options[selected_option]
            
            with st.spinner(f"{selected_ticker} geçmiş verileri ve finansalları çekiliyor..."):
                fin_data = fetch_financial_tables(selected_ticker)
                event_data = analyze_event_study(selected_ticker)
                
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
                
                st.divider()
                st.markdown(f"#### 🔍 Fiyatlamanın Arka Planı: {selected_ticker} Temel Trend Analizi")
                st.caption("Piyasanın bilançoya verdiği tepkinin rasyonel gerekçelerini bu bağımsız finansal trendlerde arayabilirsiniz.")
                
                fund_fig = plot_fundamental_trends(fin_data, selected_ticker)
                if fund_fig is not None and len(fund_fig.data) > 0:
                    st.plotly_chart(fund_fig, use_container_width=True)
                else:
                    st.warning("Bu hisse için standart formatta yıllık gelir veya nakit akışı verisi bulunamadı.")

            with sub_tab2:
                st.markdown(f"#### {selected_ticker} Yıllık Gelir Tablosu")
                if not fin_data['income'].empty:
                    st.dataframe(fin_data['income'].style.format("{:,.0f}"), use_container_width=True)
                else:
                    st.warning("Veri bulunamadı.")

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
