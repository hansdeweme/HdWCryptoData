# Copyright (c) 2024 Hans De Weme
# Licensed under the MIT License (https://opensource.org/licenses/MIT).
# Class TACharts
# Optional standalone analysis helper for the HdW_crypto_data examples
# Purpose: perform Technical Analyses and search for past and recent Trading Signals in the assets price data
# Exploratory chart signals are not validated trading strategies. 
# These are analytical demonstrations, not established predictive signals.
#
import pandas as pd
import numpy as np
import os
import pandas_ta as pta
import plotly.graph_objs as go
import plotly.io as pio
from   plotly.subplots       import make_subplots
from   ta.volatility         import BollingerBands
from   ta.trend              import STCIndicator
from   scipy.ndimage         import gaussian_filter, convolve1d
from   progressbar           import ProgressBar, Percentage, GranularBar, ETA

# class init arguments:
# asset    - crypto asset to get Technical Analyses and Trading Signals for
# data     - pandas dataframe that holds the assets price data  

class TACharts:
    def __init__(self, asset, data):
        self.df  = pd.DataFrame(data)
        if self.df.empty:
            print(f"[Warning] Time Series Data missing. {asset.upper()} DataFrame Empy.")
            return
        # USDT spot markt coin-pair to proces
        self.MARKET  = asset                                                      
        self.current_dir = os.getcwd()
        dt    = self.df.index[-1]
        value = self.df['close'].iloc[-1]
        print(f"[Info] Most recent date time in {asset.upper()}: {str(dt)}")
        print(f"[Info] Most recent value: {str(value)}")
        print(f"[Info] Dataframe Information:") 
        self.df.info()
        self.df.index = pd.to_datetime(self.df.index)
        print(f"[Info] Dataframe Ready for Charting")        

    def _last_period(self, period):
        """Compatibility replacement for pandas DataFrame.last(), removed in pandas 3."""
        if self.df.empty:
            return self.df.copy()
        period = str(period).replace("H", "h")
        offset = pd.tseries.frequencies.to_offset(period)
        start = self.df.index.max() - offset
        return self.df.loc[self.df.index > start].copy()
        
    def plot_raw(self, i):
        Days = str(i)+'D'
        D = self._last_period(Days)   
        # calculate RSI and SO (Stochastic Oscillator) for the chosen range
        D['RSI'] = pta.rsi(close=D['close'], window=14) 
        stoch = pta.stoch(D['high'], D['low'], D['close'], k=14, d=3, smooth_k=3)
        D['stoch_k'] = stoch.iloc[:, 0]
        D['stoch_d'] = stoch.iloc[:, 1]
        D['roc'] = pta.roc(D['close'], length=14)
        # Drop NaN values to ensure no plotting issues
        D = D.dropna(subset=['RSI', 'stoch_k', 'stoch_d', 'roc'])
        print(D)

        fig = make_subplots(specs=[[{"secondary_y": True}]])
        fig.add_trace(go.Candlestick(x=D.index, name= "CandleStick", open=D['open'], high=D['high'], low=D['low'], close=D['close']),  secondary_y=False,)
        fig.add_trace(go.Scatter(x=D.index, y=D['close'],   name="Price", mode='lines', line=dict(width=2, color = 'blue')),  secondary_y=False,)
        fig.add_trace(go.Scatter(x=D.index, y=D['RSI'],     name="RSI",   mode='lines', line=dict(width=2, color = 'green')), secondary_y=True,)
        fig.add_trace(go.Scatter(x=D.index, y=D['stoch_k'], name="SOk",   mode='lines', line=dict(width=2, color = 'gray')), secondary_y=True,)        
        fig.add_trace(go.Scatter(x=D.index, y=D['roc'],     name="RoC",   mode='lines', line=dict(width=2, color = 'red')),   secondary_y=True,)                        
        fig.update_layout(title=self.MARKET+' Price Info candlesticks total Dataset plus RSI and SO with RoC')
        fig.update_layout(xaxis_rangeslider_visible=True)
        pio.show(fig)

    def plot_pricevol(self, hrs):
        H = str(hrs)+'H' 
        D = self._last_period(H) 
        fig = make_subplots(specs=[[{"secondary_y": True}]])
        fig.add_trace(go.Scatter(x=D.index, y=D['close'],  name="Price",  mode='lines', line=dict(width=2, color = 'blue')),   secondary_y=False,)
        fig.add_trace(go.Scatter(x=D.index, y=D['volume'], name="Volume", mode='lines', line=dict(width=2, color = 'red')),  secondary_y=True,)
        fig.add_trace(go.Bar(x=D.index, y=(D['number_of_trades']*1000), name="Trades * 1.000", opacity=1), secondary_y=True,)
        fig.update_layout(title_text=self.MARKET+" Price - Volume - Trades Zoom for the most recent "+H)
        fig.update_xaxes(title_text="Time")
        fig.update_yaxes(title_text="Price",  secondary_y=False)
        fig.update_yaxes(title_text="Volume", secondary_y=True)
        pio.show(fig)

    # KELTNER CHANNEL CALCULATION
    def get_kc(self, high, low, close, kc_lookback, multiplier, atr_lookback):
        previous_close = close.shift(1)
        true_range = pd.concat([high - low, (high - previous_close).abs(), (low - previous_close).abs(),], axis=1,).max(axis=1)
        atr = true_range.ewm(alpha=1 / atr_lookback, adjust=False, min_periods=atr_lookback,).mean()
        middle = close.ewm(span=kc_lookback, adjust=False, min_periods=kc_lookback,).mean()
        upper = middle + multiplier * atr
        lower = middle - multiplier * atr
        return middle, upper, lower

    def implement_kc_strategy(self, prices, kc_upper, kc_lower):
        """Generate causal Keltner mean-reversion entry and exit signals.
        Entry:
            Previous close was below the previous lower channel and the current
            close has returned to or above the current lower channel.
        Exit:
            Previous close was above the previous upper channel and the current
            close has returned to or below the current upper channel.
        Signals are known only after the current candle closes. Marker prices are
        confirmation prices, not guaranteed executable trade prices.
        """
        prices = pd.Series(prices, copy=False).astype("float64")
        kc_upper = pd.Series(kc_upper, copy=False).reindex(prices.index)
        kc_lower = pd.Series(kc_lower, copy=False).reindex(prices.index)
        if not prices.index.is_unique:
            raise ValueError("prices index must be unique")
        if not prices.index.is_monotonic_increasing:
            raise ValueError("prices index must be chronologically sorted")

        buy_price = pd.Series(np.nan, index=prices.index, dtype="float64", name="kc_buy_price", )
        sell_price = pd.Series(np.nan, index=prices.index, dtype="float64", name="kc_sell_price",)
        kc_signal = pd.Series(0, index=prices.index, dtype="int8", name="kc_signal",)
        previous_price = prices.shift(1)
        previous_upper = kc_upper.shift(1)
        previous_lower = kc_lower.shift(1)
        # Price was outside the channel and has now returned inside it.
        buy_condition = ((previous_price < previous_lower) & (prices >= kc_lower))
        sell_condition = ((previous_price > previous_upper) & (prices <= kc_upper))
        # Long-only position state:
        # 0 = no position
        # 1 = long position
        position = 0
        for timestamp in prices.index:
            price = prices.loc[timestamp]
            # Warm-up observations and incomplete data cannot produce signals.
            if (
                pd.isna(price)
                or pd.isna(kc_upper.loc[timestamp])
                or pd.isna(kc_lower.loc[timestamp])
            ):
                continue
            if position == 0 and bool(buy_condition.loc[timestamp]):
                buy_price.loc[timestamp] = price
                kc_signal.loc[timestamp] = 1
                position = 1
            elif position == 1 and bool(sell_condition.loc[timestamp]):
                sell_price.loc[timestamp] = price
                kc_signal.loc[timestamp] = -1
                position = 0
        return buy_price, sell_price, kc_signal

    def do_keltner(self):
        print('\n')
        print("[Info] Calculating Average True Range (ATR) for the complete Dataset.")
        self.df['kc_middle'], self.df['kc_upper'], self.df['kc_lower'] = self.get_kc(self.df['high'], self.df['low'], self.df['close'], 20, 2, 10)

    def plot_keltner(self, i):
        Days = str(i)+'D'
        D = self._last_period(Days)   
        # calculating signals for the chosen range
        buy_price, sell_price, kc_signal = self.implement_kc_strategy(D['close'], D['kc_upper'], D['kc_lower'])
        
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=D.index, y=D['close'], mode='lines', name='price', line=dict(width=2, color = 'blue')))
        # Adding the Keltner Channel lines
        fig.add_trace(go.Scatter(x=D.index, y=D['kc_upper'],  mode='lines',  name='KC Upper 20',  line=dict(width=2, color='orange', dash='dash')))
        fig.add_trace(go.Scatter(x=D.index, y=D['kc_middle'], mode='lines',  name='KC Middle 20', line=dict(width=1.5, color='grey')))
        fig.add_trace(go.Scatter(x=D.index, y=D['kc_lower'],  mode='lines',  name='KC Lower 20',  line=dict(width=2, color='orange', dash='dash')))
        # Adding buy and sell signals
        fig.add_trace(go.Scatter(x=D.index, y=buy_price,  mode='markers', name='Buy Signal',  marker=dict(symbol='triangle-up', size=15, color='green')))
        fig.add_trace(go.Scatter(x=D.index, y=sell_price, mode='markers', name='Sell Signal', marker=dict(symbol='triangle-down', size=15, color='red')))
        # Updating the layout
        fig.update_layout(title='Keltner Channel 20 Trading Signals', legend=dict(x=1.02, y=0.8))
        fig.update_layout(xaxis_rangeslider_visible=True)
        pio.show(fig)

    # MOVING AVARAGES + EXPONENTIALLY WEIGHTED MOVING AVARAGE
    def calc_sma_ema(self):
        print('\n')
        print("[Info] calculating moving averages for the complete dataset.")
        
        self.df['7_hrs_MA']   = self.df['close'].rolling(window=7).mean()
        self.df['20_hrs_MA']  = self.df['close'].rolling(window=20).mean()
        self.df['50_hrs_MA']  = self.df['close'].rolling(window=50).mean()
        self.df['7_hrs_EM']   = self.df['close'].ewm(span=7,  adjust=False).mean()
        self.df['20_hrs_EM']  = self.df['close'].ewm(span=20, adjust=False).mean()
        self.df['50_hrs_EM']  = self.df['close'].ewm(span=50, adjust=False).mean()
        sma7  = self.df['7_hrs_MA'].to_numpy()
        sma20 = self.df['20_hrs_MA'].to_numpy() 
        sma50 = self.df['50_hrs_MA'].to_numpy()
        ema7  = self.df['7_hrs_EM'].to_numpy()
        ema20 = self.df['20_hrs_EM'].to_numpy()
        ema50 = self.df['50_hrs_EM'].to_numpy()
    # calculate SMA and EMA buy-sell signals
        sma = []
        ema = []
        for i in range(len(self.df)):
            if   sma7[i] > sma20[i] and sma20[i] > sma50[i]:
                sma.append(1)
            elif sma7[i] < sma20[i] and sma20[i] < sma50[i]:
                sma.append(-1)
            else:
                sma.append(np.nan)
        for i in range(len(self.df)):
            if   ema7[i] > ema20[i] and ema20[i] > ema50[i]:
                ema.append(1)
            elif ema7[i] < ema20[i] and ema20[i] < ema50[i]:
                ema.append(-1)
            else:
                ema.append(np.nan)
        self.df['SMA'] = sma
        self.df['EMA'] = ema

    def plot_sma(self, i):
        Days = str(i)+'D'
        D = self._last_period(Days)
        tekst1 = 'Moving Average for '+self.MARKET+' for most recent '+Days
        tekst2 = 'Note: Strong buy signal when SMA20 brakkes out above SMA50 - sell signal when SMA20 dives underneath SMA50!'
        fig = go.Figure()
        # raw data timeseries
        fig.add_trace(go.Scatter(x=D.index, y=D['close'], mode='lines', name='price', line=dict(color='blue', width = 2)))
        # Add moving average plot
        fig.add_trace(go.Scatter(x=D.index, y=D['7_hrs_MA'],   mode='lines', name='7-hrs Moving Average',  line=dict(color='yellow', width = 1)))  
        fig.add_trace(go.Scatter(x=D.index, y=D['20_hrs_MA'],  mode='lines', name='20-hrs Moving Average', line=dict(color='red', width = 1)))
        fig.add_trace(go.Scatter(x=D.index, y=D['50_hrs_MA'],  mode='lines', name='50-hrs Moving Average', line=dict(color='grey', width = 1)))
        fig.update_layout(title=tekst1, xaxis_title=tekst2, yaxis_title='Price', legend_title='Legenda')
        fig.update_layout(xaxis_rangeslider_visible=True)
        fig.update_xaxes(title_font_color='blue')
        fig.update_xaxes(title_font_size=15)
        pio.show(fig)

    # Calculate Technical Indicators
    def calculate_tis(self, data):
        # The Schaff Trend Cycle (STC) indicator combines cycle analysis and moving averages to identify trends and potential reversals.
        # It oscillates between 0 and 100, with values above 50 indicating a bullish trend and values below 50 indicating a bearish trend
        RSI_WINDOW  = 14
        STD_DEV = 2
        SMA_PERIOD  = 28 # Exchange is open 5 * 4 = 20 days per month, crypto exchanges 7 * 4 = 28
        indicator_bb = BollingerBands(close=data['close'], window=SMA_PERIOD, window_dev=STD_DEV)
        # Add Bollinger Bands features
        data['BB_mid']  = indicator_bb.bollinger_mavg()
        data['BB_high'] = indicator_bb.bollinger_hband()
        data['BB_low']  = indicator_bb.bollinger_lband()  
        data['RSI'] = pta.rsi(data['close'], window=RSI_WINDOW)
        # The Schaff Trend Cycle (STC) indicator to identify market trends and potential buy or sell signals.
        stc_window_slow = 50    # window_slow is around 50 periods, is 'smoother' trend, less sensitive to price changes
        stc_window_fast = 23    # window_fast is around 23 periods to captures the shorter-term price trends
        stc_cycle = 10          # cycle indicates sensitivity fot market trends and cycli, default = 10: higher values volatile market, lower sideways
        indicator_stc = STCIndicator(close=data['close'], window_slow=stc_window_slow, window_fast=stc_window_fast, cycle=stc_cycle, smooth1=3, smooth2=3)
        # Add features
        data['STC'] = indicator_stc.stc()
        return data

    # Trading signals
    # Note: 34 and 84 and 73 and 97 are more-or-less arbitrairy thresholds 
    def calculate_signals(self, data):
        data['RSI_entry_ind'] = np.where(np.logical_and((data['RSI'] > 34), (data['RSI'].shift() <= 34)), 1, 0)
        data['RSI_exit_ind']  = np.where(np.logical_and((data['RSI'] < 84), (data['RSI'].shift() >= 84)), 1, 0)
        #Calculate upper / lower boundary for BB
        close_prices = data['close'].to_numpy()
        max_close    = np.amax(close_prices)
        min_close    = np.amin(close_prices)
        diff_close   = max_close - min_close
        data['BB_low_adj']    = data["BB_low"] + (diff_close * 0.09)
        data['BB_entry_ind']  = np.where((data["close"] <= data["BB_low_adj"]), 1, 0)
        data['BB_high_adj']   = data["BB_high"] - (diff_close * 0.07)
        data['BB_exit_ind']   = np.where((data["close"] >= data["BB_high_adj"]), 1, 0)
        data['STC_entry_ind'] = np.where(np.logical_and(data['STC'] > 73, data['STC'].shift() <= 73), 1, 0)
        data['STC_exit_ind']  = np.where(np.logical_and(data['STC'] < 97, data['STC'].shift() >= 97), 1, 0)
        return data

    # Strategy
    def execute_strategy(self, data):
        close_prices = data['close'].to_numpy()
        rsi_entry    = data['RSI_entry_ind'].to_numpy()
        rsi_exit     = data['RSI_exit_ind'].to_numpy()  
        bb_entry     = data['BB_entry_ind'].to_numpy()
        bb_exit      = data['BB_exit_ind'].to_numpy()
        stc_entry    = data['STC_entry_ind'].to_numpy()
        stc_exit     = data['STC_exit_ind'].to_numpy()  
        ema          = data['EMA'].to_numpy()
        sma          = data['SMA'].to_numpy()
        required_entry_signals = 2 #max 3: rsi, bb, stc
        required_exit_signals  = 2
        entry_prices = []
        exit_prices  = []
        # visualizing progress bar
        widgets=[Percentage(), ' ', GranularBar(), ' ', ETA(), ]    
        with ProgressBar(max_value=len(close_prices), widgets=widgets) as bar:
            for i in range(len(close_prices)):
                current_price = close_prices[i]
                num_entry_signals = 0
                num_exit_signals = 0
                lookback_ind = i - 20
                if lookback_ind >= 0:
                    rsi_entry_lookback = rsi_entry[lookback_ind:i]
                    rsi_exit_lookback  = rsi_exit[lookback_ind:i]
                    bb_entry_lookback  = bb_entry[lookback_ind:i]
                    bb_exit_lookback   = bb_exit[lookback_ind:i]
                    stc_entry_lookback = stc_entry[lookback_ind:i]
                    stc_exit_lookback  = stc_exit[lookback_ind:i]
                    if 1 in rsi_entry_lookback:
                        num_entry_signals += 1
                    if 1 in rsi_exit_lookback:
                        num_exit_signals += 1
                    if 1 in bb_entry_lookback:
                        num_entry_signals += 1
                    if 1 in bb_exit_lookback:
                        num_exit_signals += 1           
                    if 1 in stc_entry_lookback:
                        num_entry_signals += 1
                    if 1 in stc_exit_lookback:
                        num_exit_signals += 1                  
                #  Verify Entry indicators with ema/sma check
                if num_entry_signals >= required_entry_signals and ema[i] == 1 and sma[i] == 1: 
                    entry_prices.append(current_price)
                    exit_prices.append(np.nan)
                #  Exit strategy
                elif num_exit_signals >= required_exit_signals and ema[i] == -1 and sma[i] == -1: 
                    entry_prices.append(np.nan)
                    exit_prices.append(current_price)
                else:
                #  Neither entry nor exit
                    entry_prices.append(np.nan)
                    exit_prices.append(np.nan)
                bar.update(i)
        return entry_prices, exit_prices

    # plot
    def plot_graph(self, symbol, data, entry_prices, exit_prices):
        #  Plot close price and bollinger bands
        fig = go.Figure()
        fig.add_trace(go.Scatter(x = data.index, y = data['close'],   line=dict(color="blue", width=1.5), name="Price"))  
        fig.add_trace(go.Scatter(x = data.index, y = data['BB_high'], line=dict(color="orange", width=1), name="BB High"))
        fig.add_trace(go.Scatter(x = data.index, y = data['BB_mid'],  line=dict(color="#ffd866", width=1), name="BB Mid"))
        fig.add_trace(go.Scatter(x = data.index, y = data['BB_low'],  line=dict(color="orange", width=1), name="BB Low"))
        #  Plot RSI en STC
        #  fig.add_trace(go.Scatter(x = data.index, y = data['RSI'], line=dict(color="blue", width=1), name="RSI"), row = 2, col = 1)
        #  fig.add_trace(go.Scatter(x = data.index, y = data['STC'], line=dict(color="gray", width=1), name="STC"), row = 2, col = 1)
        #  Add buy and sell indicators
        fig.add_trace(go.Scatter(x=data.index, y=entry_prices, marker_symbol="star-triangle-up",  marker=dict(size=10, color='#90EE90'), line=dict(color='black', width=1), mode='markers',name='Buy'))
        fig.add_trace(go.Scatter(x=data.index, y=exit_prices, marker_symbol="star-triangle-down", marker=dict(size=10, color='red'), line=dict(color='black', width=1), mode='markers',name='Sell'))   
        fig.update_layout(title={'text':f"{symbol} with Bollinger Bands", 'x':0.5})
        fig.update_layout(xaxis_rangeslider_visible=True)
        pio.show(fig) 
    
    def get_last_signals(self, D, entry_prices, exit_prices):
        sma7  = D['7_hrs_MA'].to_numpy()
        sma20 = D['20_hrs_MA'].to_numpy() 
        sma50 = D['50_hrs_MA'].to_numpy()
        ema7  = D['7_hrs_EM'].to_numpy()
        ema20 = D['20_hrs_EM'].to_numpy()
        ema50 = D['50_hrs_EM'].to_numpy()
        ema   = D['EMA'].to_numpy()
        sma   = D['SMA'].to_numpy()
        rsi   = D['RSI'].to_numpy()
        dtm   = D.index.to_numpy()    
        # we are looking for the most recent value, so we do an inverse check of all arrays, starting with most recent value  
        ema   = ema[::-1]
        sma   = sma[::-1]
        sma7  = sma7[::-1]
        sma20 = sma20[::-1]
        sma50 = sma50[::-1]
        ema7  = ema7[::-1]
        ema20 = ema20[::-1]
        ema50 = ema50[::-1]
        rsi   = rsi[::-1]
        dtm   = dtm[::-1]
        entry_prices = entry_prices[::-1]
        exit_prices  = exit_prices[::-1]
        for i in range(len(entry_prices)):
            if np.isnan(entry_prices[i]) == False:  # most recent entry/buy price found! 
                pprice = f"{ entry_prices[i]:.4f}"
                prsi   = f"{rsi[i]:.0f}"
                pdk    = pd.Timestamp(dtm[i]).strftime('%Y-%m-%d %H:%M')
                ps7    = f"{sma7[i]:.4f}" 
                ps20   = f"{sma20[i]:.4f}" 
                ps50   = f"{sma50[i]:.4f}"           
                em7    = f"{ema7[i]:.4f}" 
                em20   = f"{ema20[i]:.4f}" 
                em50   = f"{ema50[i]:.4f}"           
                print('Most recent Buy signal on: ' + pdk + " price: " +pprice+ " RSI = "+prsi+ " SMA7 = "+ps7+ " SMA20 = "+ps20+ " SMA50 = "+ps50+ " EMA7 = "+em7+ " EMA20 = "+em20+ " EMA50 = "+em50)
                break # stop zoeken na 1e hit
        for i in range(len(exit_prices)):
            if np.isnan(exit_prices[i]) == False:  # most recent entry/buy price found!
                pprice = f"{exit_prices[i]:.4f}"
                prsi   = f"{rsi[i]:.0f}"
                pdk    = pd.Timestamp(dtm[i]).strftime('%Y-%m-%d %H:%M')
                ps7    = f"{sma7[i]:.4f}" 
                ps20   = f"{sma20[i]:.4f}" 
                ps50   = f"{sma50[i]:.4f}"
                em7    = f"{ema7[i]:.4f}" 
                em20   = f"{ema20[i]:.4f}" 
                em50   = f"{ema50[i]:.4f}"  
                print('Most recent Sell signal on: ' + pdk + " price: " +pprice+ " RSI = "+prsi+ " SMA7 = "+ps7+ " SMA20 = "+ps20+ " SMA50 = "+ps50+ " EMA7 = "+em7+ " EMA20 = "+em20+ " EMA50 = "+em50)
                break # stop searching after first hit

    def do_sim(self, i):
        Days = str(i)+'D'
        D = self._last_period(Days)    
        D = self.calculate_tis(D)
        D = self.calculate_signals(D)
        entry_prices, exit_prices = self.execute_strategy(D)
        self.plot_graph(self.MARKET, D, entry_prices, exit_prices)
        self.get_last_signals(D, entry_prices, exit_prices)

    # GAUSSIAN FILTERS, Bands compare to Bollinger bands and Keltner channels and bands
    def std_filtered_gaussian(self, data, sigma, n_poles=1):
        # Apply the n-pole Gaussian filter. The sigma is modified using the std_dev.
        for _ in range(n_poles):
            data = gaussian_filter(data, sigma=sigma)
        return data

    def plot_gaussian_ema(self, data):
        filtered_close_series = self.std_filtered_gaussian(data['close'], sigma=1, n_poles=1)
        ema1_series = data['close'].rolling(window=3).mean()
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=data.index, y=data['close'], mode='lines', name='Price', line=dict(color='blue', width=0.5)))
        fig.add_trace(go.Scatter(x=data.index, y=filtered_close_series, mode='lines', name='Gefilterde Price (Std: 1, Poles: 1)', line=dict(color='green', width=0.5)))
        fig.add_trace(go.Scatter(x=data.index, y=ema1_series, mode='lines', name='EMA (3)', line=dict(color='orange', width=0.5)))
        pio.show(fig)
        
    def plot_gaussian_trends(self, data):
        filtered_close_series1 = self.std_filtered_gaussian(data['close'], sigma=5, n_poles=1)
        filtered_close_series2 = self.std_filtered_gaussian(data['close'], sigma=20, n_poles=1)
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=data.index, y=data['close'], mode='lines', name='Price', line=dict(color='blue', width=0.5)))
        fig.add_trace(go.Scatter(x=data.index, y=filtered_close_series1, mode='lines', name='Filtered close (Std: 10, Poles: 1)', line=dict(color='green', width=0.5)))
        fig.add_trace(go.Scatter(x=data.index, y=filtered_close_series2, mode='lines', name='Filtered close (Std: 20, Poles: 1)', line=dict(color='purple', width=0.5)))
        pio.show(fig)

    def plot_gaussian_poles(self, data):
        filtered_close_series1 = self.std_filtered_gaussian(data['close'], sigma=1, n_poles=1)
        filtered_close_series2 = self.std_filtered_gaussian(data['close'], sigma=1, n_poles=5)
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=data.index, y=data['close'], mode='lines', name='Price', line=dict(color='blue', width=2)))
        fig.add_trace(go.Scatter(x=data.index, y=filtered_close_series1, mode='lines', name='Filtered close (Std: 1, Poles: 1)', line=dict(color='green', width=2)))
        fig.add_trace(go.Scatter(x=data.index, y=filtered_close_series2, mode='lines', name='Filtered close (Std: 1, Poles: 5)', line=dict(color='purple', width=2)))
        pio.show(fig)
        
    def plot_gaussian_historical_turning_points(self, data, mrkt):
        filtered_close = self.std_filtered_gaussian(data['close'], sigma=1, n_poles=1)
        filtered_high  = self.std_filtered_gaussian(data['high'], sigma=1, n_poles=1)
        filtered_low   = self.std_filtered_gaussian(data['low'], sigma=1, n_poles=1)
        filtered_average = (filtered_high + filtered_low + filtered_close) / 3
        band_low = (2 * filtered_average) - filtered_low
        band_high = (2 * filtered_average) - filtered_high
        # Calculate the slope of the filtered data
        filtered_close = self.std_filtered_gaussian(data['close'], sigma=1, n_poles=1)
        slope = convolve1d(filtered_close, [1, 0, -1])
        # Identify retrospective turning points in a symmetrically smoothed series
        peak_indices   = np.where((slope[:-1] > 0) & (slope[1:] < 0))[0]
        valley_indices = np.where((slope[:-1] < 0) & (slope[1:] > 0))[0]
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=data.index, y=data['close'], mode='lines', name='Price', line=dict(color='blue', width=2)))
        fig.add_trace(go.Scatter(x=data.index, y=band_low, mode='lines', name='High Band - Resistance', line=dict(color='red', width=1, dash='dash')))
        fig.add_trace(go.Scatter(x=data.index, y=band_high, mode='lines', name='Low Band - Support', line=dict(color='green', width=1, dash='dash')))
        fig.add_trace(go.Scatter(x=data.index, y=filtered_close, mode='lines', name='Filtred Price (Std: 1, Poles: 1)', line=dict(color='gray', width=2)))
        fig.add_trace(go.Scatter(x=data.index[peak_indices+1], y=filtered_close[peak_indices+1], marker_symbol="star-triangle-down",   marker=dict(size=10, color='red'), line=dict(color='black', width=1), mode='markers', name='Retrospective Peak'))
        fig.add_trace(go.Scatter(x=data.index[valley_indices+1], y=filtered_close[valley_indices], marker_symbol="star-triangle-up",  marker=dict(size=10, color='#90EE90'), line=dict(color='black', width=1), mode='markers', name='Retrospective Trough'))
        fig.add_annotation(text=("Retrospective analysis: Gaussian smoothing uses observations before and after each point. Markers are not real-time signals."),
            xref="paper", yref="paper", x=0.5, y=1.08, showarrow=False, font=dict(size=11, color="gray"),)        
        fig.update_layout(xaxis_rangeslider_visible=True)
        fig.update_layout(title='Gaussian-Smoothed Historical Turning Points')
        pio.show(fig)

    def do_gauss(self, i):
        Days = str(i)+'D'
        D = self._last_period(Days)     
        self.plot_gaussian_historical_turning_points(D, self.MARKET)
        # plot_gaussian_ema(D)       # Plot Gaussian Filter  + EMA
        # plot_gaussian_trends(D)    # Plot Gaussian trends
        # plot_gaussian_poles(D)     # Plot Gaussian Filter  + EMA
 
    def save_data(self):
        self.df.to_excel(self.MARKET+'_enhanced.xlsx', engine='openpyxl')
        

# Backward-compatible alias for older scripts that imported DataInfo.
DataInfo = TACharts
