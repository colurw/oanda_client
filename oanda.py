import requests
import pandas as pd
from pprint import pprint
from flatten_dict import flatten
import time
import json


class OandaClient():
    def __init__(self):
        self.side = 'practice'   # or 'trade'
        self.api_token = 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx'
        self.account_id = '000-000-00000000-000'
        self.account_currency = 'GBP'
        self.instrument = 'SPX500_USD'
        self.leverage = 2


    def get_candles(self):
        url = f'https://api-fx{self.side}.oanda.com/v3/instruments/{self.instrument}/candles'

        headers = {'Content-type':'application/json',
                   'Authorization': f'Bearer {self.api_token}'}

        params = {'count':48,
                  'price':'M',
                  'granularity':'M30'}

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
        df = df.between_time('9:30', '16:29')

        ohlc_dict = {'open':'first', 
                     'high':'max', 
                     'low':'min', 
                     'close':'last', 
                     'finished':'any', 
                     'vol':'sum'}

        df = df.resample('1h', offset=0).apply(ohlc_dict).dropna(how='any')    # dropna needed?
        print(df.tail(25))

        return df


    def position_size(self):
        url = f'https://api-fx{self.side}.oanda.com/v3/accounts/{self.account_id}/summary'        

        headers = {'Content-type':'application/json',
                   'Authorization': f'Bearer {self.api_token}'}
        
        response = requests.get(url, headers=headers)
        balance = response.json()['account']['balance']
        
        url2 = f'https://api-fx{self.side}.oanda.com/v3/accounts/{self.account_id}/pricing?instruments={self.instrument[:-4]}_{self.account_currency}'

        response2 = requests.get(url2, headers=headers)
        price = response2.json()['prices'][0]['closeoutAsk']

        intrinsic_lev = float(price) / float(balance)
        units = round(self.leverage / intrinsic_lev, 1)

        return units


    def stop_limit_order_tp_sl(self, trigger_price, tp_price, sl_price):
        url = f'https://api-fx{self.side}.oanda.com/v3/accounts/{self.account_id}/orders'

        headers = {'Content-type':'application/json',
                   'Authorization': f'Bearer {self.api_token}'}

        data = {'order':{'type':'STOP',                     # or MARKET_IF_TOUCHED to avoid slippage?
                         'instrument':self.instrument,    
                         'units':self.position_size(),   
                         'price':trigger_price,             # can be used with 'priceBound' to avoid being gapped
                         'timeInForce':'GTC',               # or GTD with 'gtdTime':DateTime?
                         'positionFill':'DEFAULT',
                         'triggerCondition':'DEFAULT',
                         'takeProfitOnFill':{'price': tp_price},
                         'stopLossOnFill':{'timeInForce': 'GTC', 
                                           'price': sl_price},}}

        response = requests.post(url, headers=headers, data=json.dumps(data))
        pprint(response.json())

        self.last_transaction_id = response.json()['lastTransactionID']  


    def close_long_positions(self):
        url = f'https://api-fx{self.side}.oanda.com/v3/accounts/{self.account_id}/positions/{self.instrument}/close'

        headers = {'Content-type': 'application/json',
                   'Authorization': f'Bearer {self.api_token}'}

        data = {'longUnits':'ALL'}

        response = requests.put(url, headers=headers, data=json.dumps(data))
        pprint(response.json())


    def update_tp_sl(self, tp_price, sl_price):
        url = f'https://api-fx{self.side}.oanda.com/v3/accounts/{self.account_id}/openTrades'

        headers = {'Content-type':'application/json',
                   'Authorization': f'Bearer {self.api_token}'}
        
        response = requests.get(url, headers=headers)
        trade_id = response.json()['trades'][0]['stopLossOrder']['tradeID']
        
        url2 = f'https://api-fx{self.side}.oanda.com/v3/accounts/{self.account_id}/trades/{trade_id}/orders'

        data = {"takeProfit": {'timeInForce':'GTC',
                               'price': tp_price},
                "stopLoss": {'timeInForce':'GTC',
                             'price': sl_price}}

        response = requests.put(url2, headers=headers, data=json.dumps(data))


if __name__ == '__main__':

    oanda = OandaClient()

    oanda.get_candles()
    
    oanda.close_long_positions()
    time.sleep(2)

    oanda.stop_limit_order_tp_sl(5820, 6000, 5600)
    time.sleep(5)

    oanda.update_tp_sl(6005, 5605)
    time.sleep(5)
    
    oanda.close_long_positions()
