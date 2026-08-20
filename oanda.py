import requests
import pandas as pd
from pprint import pprint
from flatten_dict import flatten
from time import sleep
import json
from pyrfc3339 import generate
from datetime import datetime, timezone
from dateutil import parser
from retry import retry


class OandaClient():
    def __init__(self):
        self.side = 'practice'   # or 'trade'
        self.api_token = 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'
        self.account_id = '000-000-00000000-000'
        self.account_currency = 'GBP'
        self.instrument = 'SPX500_USD'


    @retry(tries=3, delay=1)
    def available_instruments(self, get_spreads=False, as_percentage=False):

        url = f'https://api-fx{self.side}.oanda.com/v3/accounts/{self.account_id}/instruments'

        headers = {'Content-type':'application/json',
                   'Authorization': f'Bearer {self.api_token}'}

        response = requests.get(url, headers=headers)
        data = response.json()

        
        metals = [(instrument['name'], instrument['displayName']) for instrument in data['instruments'] if instrument['type'] == 'METAL']
        cfds = [(instrument['name'], instrument['displayName']) for instrument in data['instruments'] if instrument['type'] == 'CFD']
        currencies = [(instrument['name'], instrument['displayName']) for instrument in data['instruments'] if instrument['type'] == 'CURRENCY' and instrument['name'][-3:] == self.account_currency]

        if get_spreads == False:

            return metals, cfds, currencies

        else:
            spreads = []
            for symbol in [instrument[0] for instrument in metals + cfds + currencies]:
                spread = self.get_spread(instrument=symbol, as_percentage=as_percentage)
                spreads.append(spread)
                sleep(0.1)

            return [(a,*b) for a,b in zip(spreads, metals + cfds + currencies)]


    @retry(tries=3, delay=1)
    def get_candles(self, count=144, price='M', granularity='M30', resample_to='2h', regular_hours_only=True, start_of_day='9h30min'):
        url = f'https://api-fx{self.side}.oanda.com/v3/instruments/{self.instrument}/candles'

        headers = {'Content-type':'application/json',
                   'Authorization': f'Bearer {self.api_token}'}

        params = {'count':count,
                  'price':price,
                  'granularity':granularity}

        response = requests.get(url, headers=headers, params=params)
        candles = response.json()['candles']

        rows = []
        for data in candles:
            flat = flatten(data)
            rows.append(flat)

        df = pd.DataFrame(rows)
        df.columns = [str(name) for name in df.columns]
        df = df.rename(columns={"('complete',)":'finished', 
                                "('volume',)":'vol', 
                                "('time',)":'time', 
                                "('mid', 'o')":'open', 
                                "('mid', 'h')":'high', 
                                "('mid', 'l')":'low', 
                                "('mid', 'c')":'close'})

        df['start_time'] = pd.to_datetime(df['time'])
        df = df.set_index('start_time')
        df = df.tz_convert('America/New_York')

        if regular_hours_only:
            df = df.between_time('9:30', '16:29')

        if resample_to:
            ohlc_dict = {'open':'first', 
                        'high':'max', 
                        'low':'min', 
                        'close':'last', 
                        'finished':'any', 
                        'vol':'sum'}

            df = df.resample(resample_to, offset=start_of_day).apply(ohlc_dict).dropna(how='any') 

        df[['open', 'high', 'low', 'close', 'vol']] = df[['open', 'high', 'low', 'close', 'vol']].apply(pd.to_numeric) 
        df = df.reset_index()
        df = df.drop(columns='finished')

        return df


    @retry(tries=3, delay=1)    
    def get_trade_status(self):
        url = f'https://api-fx{self.side}.oanda.com/v3/accounts/{self.account_id}/positions/{self.instrument}'

        headers = {'Content-type':'application/json',
                   'Authorization': f'Bearer {self.api_token}'}

        response = requests.get(url, headers=headers)
             
        if float(response.json()['position']['long']['units']) > 0.0:
            trade_status = 'in_long'

        elif float(response.json()['position']['short']['units']) < 0.0:
            trade_status = 'in_short'
            
        else:
            trade_status = 'none'
        
        return trade_status


    @retry(tries=3, delay=1)
    def kelly_position_size(self, decimal_win_rate, average_win, average_loss, kelly_fraction=0.5):
        
        position_fraction = kelly_fraction * (decimal_win_rate - ((1-decimal_win_rate) / (average_win/average_loss)))

        return position_fraction


    @retry(tries=3, delay=1)
    def calculate_units_from_risk_percentage(self, risk_perc, entry_price, sl_price, foreign_conversion=False, max_leverage=False):
        url = f'https://api-fx{self.side}.oanda.com/v3/accounts/{self.account_id}/summary'        

        headers = {'Content-type':'application/json',
                   'Authorization': f'Bearer {self.api_token}'}
        
        response = requests.get(url, headers=headers)
        balance = response.json()['account']['balance']       
        units = (float(balance) * risk_perc) / (entry_price - sl_price)

        if foreign_conversion:
            url = f'https://api-fx{self.side}.oanda.com/v3/accounts/{self.account_id}/pricing?instruments={self.account_currency}_{self.instrument[-3:]}'

            response = requests.get(url, headers=headers)
            exchange_rate = response.json()['prices'][0]['closeoutAsk']
            units = float(exchange_rate) * units

        if max_leverage:
            url = f'https://api-fx{self.side}.oanda.com/v3/accounts/{self.account_id}/summary'        
            
            response = requests.get(url, headers=headers)
            balance = response.json()['account']['balance']        
            
            url2 = f'https://api-fx{self.side}.oanda.com/v3/accounts/{self.account_id}/pricing?instruments={self.instrument[:-4]}_{self.account_currency}'

            response2 = requests.get(url2, headers=headers)         
            price = response2.json()['prices'][0]['closeoutAsk']
            intrinsic_lev = float(price) / float(balance)

            if units * intrinsic_lev > max_leverage-1:
                units = (max_leverage-1) / intrinsic_lev

        return round(units, 2)


    @retry(tries=3, delay=1)
    def calculate_units_from_leverage(self, leverage):
        url = f'https://api-fx{self.side}.oanda.com/v3/accounts/{self.account_id}/summary'        

        headers = {'Content-type':'application/json',
                   'Authorization': f'Bearer {self.api_token}'}
        
        response = requests.get(url, headers=headers)
        balance = response.json()['account']['balance']        
        
        url2 = f'https://api-fx{self.side}.oanda.com/v3/accounts/{self.account_id}/pricing?instruments={self.instrument[:-4]}_{self.account_currency}'

        response2 = requests.get(url2, headers=headers)         
        pprint(response2.json())
        price = response2.json()['prices'][0]['closeoutAsk']

        intrinsic_lev = float(price) / float(balance)
        units = leverage / intrinsic_lev                    

        return round(units, 2)     # short units should be negative!


    @retry(tries=2, delay=2)
    def stop_order_tp_sl(self, trigger_price, tp_price, sl_price, units, time_in_force=False):
        url = f'https://api-fx{self.side}.oanda.com/v3/accounts/{self.account_id}/orders'

        headers = {'Content-type':'application/json',
                   'Authorization': f'Bearer {self.api_token}'}

        if time_in_force == False:

            data = {'order':{'type':'STOP',                # or MARKET_IF_TOUCHED to avoid slippage?
                        'instrument':self.instrument,    
                        'units':units,   
                        'price':trigger_price,             # can be used with 'priceBound' to avoid being gapped
                        'timeInForce':'GTC',               
                        'positionFill':'DEFAULT',          # ...also guaranteed glso
                        'triggerCondition':'DEFAULT',
                        'takeProfitOnFill':{'price': tp_price},
                        'stopLossOnFill':{'timeInForce': 'GTC',     # is this right?
                                        'price': sl_price},}}
        else:

            data = {'order':{'type':'STOP',                    
                            'instrument':self.instrument,    
                            'units':units,   
                            'price':trigger_price,             
                            'timeInForce':'GTD',  
                            'gtdTime':generate(datetime.now(timezone.utc) + time_in_force),             
                            'positionFill':'DEFAULT',          
                            'triggerCondition':'DEFAULT',
                            'takeProfitOnFill':{'price': tp_price},
                            'stopLossOnFill':{'timeInForce': 'GTC', 
                                              'price': sl_price},}}

        response = requests.post(url, headers=headers, data=json.dumps(data))
        pprint(response.json())

        last_transaction_id = response.json()['lastTransactionID']

        return last_transaction_id


    @retry(tries=3, delay=1)
    def update_tp_sl(self, tp_price, sl_price):
        url = f'https://api-fx{self.side}.oanda.com/v3/accounts/{self.account_id}/openTrades'

        headers = {'Content-type':'application/json',
                   'Authorization': f'Bearer {self.api_token}'}
        
        response = requests.get(url, headers=headers)
        data = response.json()
        trade_id = [trade['id'] for trade in data['trades'] if trade['instrument'] == self.instrument][0]
        
        url2 = f'https://api-fx{self.side}.oanda.com/v3/accounts/{self.account_id}/trades/{trade_id}/orders'

        data = {'takeProfit':{'timeInForce':'GTC',
                              'price':tp_price},
                'stopLoss':{'timeInForce':'GTC',
                            'price':sl_price}}

        response = requests.put(url2, headers=headers, data=json.dumps(data))


    @retry(tries=3, delay=1)
    def get_open_positions(self):
        url = f'https://api-fx{self.side}.oanda.com/v3/accounts/{self.account_id}/openPositions'

        headers = {'Content-type':'application/json',
                   'Authorization': f'Bearer {self.api_token}'}

        response = requests.get(url, headers=headers)
        data = response.json()
        open_instruments = [position['instrument'] for position in data['positions']]

        return open_instruments

    
    @retry(tries=3, delay=1)
    def close_positions(self):
        url = f'https://api-fx{self.side}.oanda.com/v3/accounts/{self.account_id}/positions/{self.instrument}/close'

        headers = {'Content-type': 'application/json',
                   'Authorization': f'Bearer {self.api_token}'}

        data = {'longUnits':'ALL'}  
        data2 = {'shortUnits':'ALL'} 

        response = requests.put(url, headers=headers, data=json.dumps(data))
        response2 = requests.put(url, headers=headers, data=json.dumps(data2))


    @retry(tries=3, delay=1)
    def cancel_pending_orders(self):
        url = f'https://api-fx{self.side}.oanda.com/v3/accounts/{self.account_id}/pendingOrders'

        headers = {'Content-type':'application/json',
                   'Authorization': f'Bearer {self.api_token}'}
        
        response = requests.get(url, headers=headers)
        data = response.json()
        pre_sort = [order for order in data['orders'] if 'instrument' in order]
        pending_orders = [order['id'] for order in pre_sort if order['instrument'] == self.instrument]

        if len(pending_orders) > 0:
            for id in pending_orders:
                url2 = f'https://api-fx{self.side}.oanda.com/v3/accounts/{self.account_id}/orders/{id}/cancel'

                response = requests.put(url2, headers=headers)


    @retry(tries=3, delay=1)
    def stream_prices(self):
        url = f'https://stream-fx{self.side}.oanda.com/v3/accounts/{self.account_id}/pricing/stream?instruments={self.instrument}'

        headers = {'Content-type':'application/json',
                   'Authorization': f'Bearer {self.api_token}'}
        
        r = requests.get(url, headers=headers, stream=True)
        r.raise_for_status()

        for n, line in enumerate(r.iter_lines()):
            if line: 
                decoded_line = line.decode('utf-8')
                if "HEARTBEAT" not in decoded_line:
                    pprint(json.loads(decoded_line)['closeoutAsk'])
                    pprint(json.loads(decoded_line)['closeoutBid'])
                    print()

            if n == 20:
                break


    @retry(tries=2, delay=2)
    def one_cancels_other(self, time_in_force):
        end_time = generate(datetime.now(timezone.utc) + time_in_force)
        while True:
            sleep(1)
            status = self.get_trade_status()
            
            if status != 'none':
                self.cancel_pending_orders()
                break

            elif datetime.now(timezone.utc) > parser.parse(end_time):
                break


    @retry(tries=3, delay=1)
    def get_spread(self, instrument=False, as_percentage=False):

        if instrument:
            url = f'https://api-fx{self.side}.oanda.com/v3/accounts/{self.account_id}/pricing?instruments={instrument}'
        else:
            url = f'https://api-fx{self.side}.oanda.com/v3/accounts/{self.account_id}/pricing?instruments={self.instrument}'

        headers = {'Content-type':'application/json',
                   'Authorization': f'Bearer {self.api_token}'}

        response = requests.get(url, headers=headers)
        raw_spread = float(response.json()['prices'][0]['asks'][0]['price']) - float(response.json()['prices'][0]['bids'][0]['price'])

        if as_percentage:
            as_pct = (raw_spread / float(response.json()['prices'][0]['asks'][0]['price'])) * 100

            return float(f'{as_pct:.2}')
        
        else:
            
            return float(f'{raw_spread:.2}')


if __name__ == '__main__':

    oanda = OandaClient()

    pprint(oanda.available_instruments(get_spreads=True, as_percentage=True))

    # print(oanda.any_open_positions())
    # oanda.close_positions()
    # print(oanda.any_open_positions())

    # oanda.close_positions()
    # oanda.cancel_pending_orders()
    # sleep(2)
    # oanda.stop_order_tp_sl(7527, tp_price=7540, sl_price=7490, units=1, time_in_force=timedelta(days=0, hours=0, minutes=1))
    # oanda.stop_order_tp_sl(7518, tp_price=7480, sl_price=7540, units=-1, time_in_force=timedelta(days=0, hours=0, minutes=1))
    # oanda.one_cancels_other(time_in_force=timedelta(days=0, hours=0, minutes=1))

    # oanda.get_spread()

    # oanda.cancel_pending_orders()

    # print(oanda.get_trade_status())

    # df = oanda.get_candles(count=3000, price='M', granularity='M30', resample_to='24h', regular_hours_only=False, start_of_day='18h00min')  # 1D
    # df = oanda.get_candles(count=3000, price='M', granularity='M30', resample_to='12h', regular_hours_only=False, start_of_day='5h00min')  # 12H
    # df = oanda.get_candles(count=3000, price='M', granularity='M30', resample_to='4h', regular_hours_only=False, start_of_day='01h00min')  # 4H
    # df = oanda.get_candles(count=3000, price='M', granularity='M30', resample_to='3h', regular_hours_only=False, start_of_day='02h00min')  # 3H
    # df = oanda.get_candles(count=3000, price='M', granularity='M30', resample_to='2h', regular_hours_only=False, start_of_day='01h00min')  # 2H
    # df = oanda.get_candles(count=3000, price='M', granularity='M30', resample_to='1h', regular_hours_only=False, start_of_day='00h00min')  # 1H

    # oanda.close_positions()
    # sleep(2)

    # print(oanda.calculate_units_from_risk_percentage(risk_perc=0.1, entry_price=7480, sl_price=7440, foreign_conversion=False, max_leverage=False))

    # oanda.stop_order_tp_sl(7463, tp_price=7580, sl_price=7400, units=1, time_in_force=timedelta(days=0, hours=1, minutes=0))
    
    # oanda.stop_order_tp_sl(7480, tp_price=7460, sl_price=7490, units=-1)

    # sleep(5)

    # oanda.cancel_pending_orders()

    # oanda.update_tp_sl(7610, 7517)
    # sleep(5)
    
    # oanda.close_positions()

    # print(oanda.kelly_position_size(decimal_win_rate=0.7, average_win=0.93, average_loss=0.45))

