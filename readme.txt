**Para tener la lista por año hacemos lo siguiente**

GENERATE PROMPT
python3 prompt.py 

RUN APP
flask run

RUN migrations
flask db upgrade

RUN PROMPT OUTPUT
python3 prompt.py

browse:
http://127.0.0.1:5000/

#INSTALL IN DEVELOPMENT
flask db init
flask db migrate
flask db upgrade
flask run

#INSTALL IN PRODUCTION 
1. Create linode UBUNTU 24.04LTS
2. Create ssh keys: ssh-keygen
3. Add ssh keys to bitbucket repository access
4. try in terminal git clone git@bitbucket.org:ozone777/save_a_playa_data.git
5. Copy and paste install.sh in server 
6. change permissions to installation scritp: chmod +x install.sh
5. run installation script: sudo ./install.sh

#INSTALL SSL
sudo apt install certbot python3-certbot-nginx -y
sudo certbot --nginx -d data.saveaplaya.org

#RESTART
sudo systemctl daemon-reload
sudo systemctl restart save_a_playa_data
sudo systemctl reload nginx

#deploy steps
cd /var/www
git pull branch
python3 -m venv venv
chown -R www-data:www-data /var/www/save_a_playa_data
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl daemon-reload
sudo systemctl restart save_a_playa_data
sudo systemctl reload nginx

TEST APP IN PRODUCTION
gunicorn "app:create_app()" --bind 0.0.0.0:8000

sudo systemctl status save_a_playa_data
journalctl -u save_a_playa_data -f

development API keys:
http://saveaplaya.petetesting.com/wp-json/woo-gender-analytics/v1/analytics
kczW5H0xWPktsjywG5prhOlYUJiRHPGalZB5q2Ll

http://saveaplaya.petelocal.net/wp-json/woo-gender-analytics/v1/o0zndyZfgQPVWOlqwDUrBU8q5uRWzP3Oo951T7D3


EXAMPLE PROMPTS

 Help me to create a method to display the total value orders with utm_source=facebook by the day in the HTML view /daily_arima 

 Now help me to display the forecasting using the most convenient calculation for online advertising for the graphic   <canvas id="googleSalesChart"></canvas> and the method get_daily_google_sales_trend