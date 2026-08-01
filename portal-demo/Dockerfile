FROM nginx:1.27-alpine

COPY portal-demo/nginx.conf /etc/nginx/conf.d/default.conf
COPY portal-demo/index.html portal-demo/storefront.html portal-demo/portal-config.js portal-demo/portal-config-ar.js portal-demo/sales.html /usr/share/nginx/html/
COPY lp-plano-lua /usr/share/nginx/html/lp-plano-lua

EXPOSE 80
