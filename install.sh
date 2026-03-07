#!/usr/bin/env bash

########################################
# Bash script to install and configure a
# Flask app on Ubuntu 24.04 LTS
########################################

set -e  # Exit on any error

APP_NAME="save_a_playa_data"
APP_USER="www-data"  # System user to run Gunicorn under (often www-data or nginx)
APP_DIR="/var/www/${APP_NAME}"
SERVER_IP=172.233.173.212
GIT_REPO="git@bitbucket.org:ozone777/save_a_playa_data.git"
GIT_BRANCH="deploy2"
SERVICE_FILE="/etc/systemd/system/${APP_NAME}.service"
NGINX_CONF="/etc/nginx/sites-available/${APP_NAME}"

echo "===> 1. System Update & Required Packages"
apt update && apt upgrade -y
apt install -y python3 python3-pip python3-venv git nginx

echo "===> 2. Clone the Repository"
# If the folder already exists, we skip. Otherwise, clone.
if [ ! -d "${APP_DIR}" ]; then
    mkdir -p "${APP_DIR}"
    rm -rf /var/www/"${APP_NAME}"
    git clone "${GIT_REPO}" "${APP_DIR}" -b "${GIT_BRANCH}"
else
    echo "Directory ${APP_DIR} already exists. Skipping clone step."
fi

cd "${APP_DIR}"

echo "===> 3. Create Virtual Environment & Install Dependencies"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
# If you have a requirements.txt in your repo, uncomment:
pip install -r requirements.txt
flask db upgrade
deactivate
#run migrations

cp data/all_orders.csv data/orders.csv

echo "===> 4. Create Gunicorn systemd Service"
cat << EOF > "${SERVICE_FILE}"
[Unit]
Description=Gunicorn instance to serve ${APP_NAME}
After=network.target

[Service]
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${APP_DIR}
Environment="PATH=${APP_DIR}/venv/bin"
# Adjust the gunicorn command as necessary if your app’s entry point is different:
ExecStart=${APP_DIR}/venv/bin/gunicorn --workers 3 --bind unix:${APP_DIR}/${APP_NAME}.sock wsgi:app

[Install]
WantedBy=multi-user.target
EOF

echo "===> 5. Reload systemd and Start Gunicorn service"
systemctl daemon-reload
systemctl enable "${APP_NAME}"
systemctl start "${APP_NAME}"

echo "===> 6. Configure Nginx Server Block"
cat << EOF > "${NGINX_CONF}"
server {
    listen 80;
    server_name ${SERVER_IP};  # Replace with your domain or IP if you have one

    # Max upload size (optional)
    client_max_body_size 50M;

    location / {
        include proxy_params;
        proxy_pass http://unix:${APP_DIR}/${APP_NAME}.sock;
    }

    # (Optional) If you want to serve static files directly via nginx, add:
    # location /static {
    #     alias ${APP_DIR}/app/static;
    # }
}
EOF



sudo unlink /etc/nginx/sites-enabled/default

chown -R www-data:www-data /var/www/"${APP_NAME}"

ln -sf "${NGINX_CONF}" "/etc/nginx/sites-enabled/${APP_NAME}"

echo "===> 7. Test Nginx configuration and reload"
nginx -t

echo "===> 8. (Optional) Configure UFW Firewall"
# Uncomment if you want to enable UFW and allow HTTP/HTTPS
# ufw allow 'Nginx Full'
# ufw enable

echo "===> Installation & Configuration Complete!"
echo "Your Flask app should now be served by Gunicorn (systemd) and available on port 80."

sudo systemctl daemon-reload
sudo systemctl restart save_a_playa_data
sudo systemctl reload nginx
