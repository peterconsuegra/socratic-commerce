#!/bin/bash

python3 -m venv venv
chown -R www-data:www-data /var/www/save_a_playa_data
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl daemon-reload
sudo systemctl restart save_a_playa_data
sudo systemctl reload nginx